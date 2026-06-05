"""Thesis-document uploads (Workstream 4).

Stores per-ticker thesis documents under data/portfolio/uploads/{TICKER}/,
records each in the position_files table, and caches an extracted-text sidecar
(.txt) next to the original so downstream stages never re-parse a PDF/DOCX.
combined_text(ticker) feeds the W3 decision engine's thesis_doc_text slot.

Idempotent on content: the file's event_id is keyed on sha256(content), so
re-uploading an identical file is a no-op. Text extraction is best-effort —
.txt/.md need no dependencies; .pdf/.docx lazy-import pypdf / python-docx and
raise a clear UploadError (with an install hint) only when actually parsing
that type without the library present, so the package imports fine offline.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from ..db import connection
from ..identity import event_id
from ..paths import DATA_ROOT, UPLOADS_DIR

# File suffixes we accept. txt/md are read directly; pdf/docx are extracted
# via optional libraries. Anything else is rejected at save time.
SUPPORTED_SUFFIXES = {".txt", ".md", ".markdown", ".pdf", ".docx"}

# Cap on the text fed to the decision prompt so a large dossier can't blow the
# token budget; combined_text truncates to this many characters.
MAX_COMBINED_CHARS = 8000


# Raised for any upload-side failure (unsupported type, missing extractor lib,
# parse error). Callers (CLI / API) surface the message to the user.
class UploadError(RuntimeError):
    pass


# DDL for the position_files table. UNIQUE on (ticker, content_sha) makes
# re-uploading identical content a no-op while different content (even same
# filename) gets its own row. Paths are stored relative to DATA_ROOT so the
# data dir stays relocatable.
POSITION_FILES_SCHEMA = """
CREATE TABLE IF NOT EXISTS position_files (
    event_id      TEXT PRIMARY KEY,
    ticker        TEXT NOT NULL,
    filename      TEXT NOT NULL,
    stored_path   TEXT NOT NULL,
    text_path     TEXT,
    text_content  TEXT NOT NULL DEFAULT '',
    content_type  TEXT NOT NULL,
    content_sha   TEXT NOT NULL,
    byte_size     INTEGER NOT NULL,
    n_chars       INTEGER NOT NULL DEFAULT 0,
    uploaded_at   TEXT NOT NULL,
    UNIQUE (ticker, content_sha)
);

CREATE INDEX IF NOT EXISTS idx_position_files_ticker      ON position_files(ticker);
CREATE INDEX IF NOT EXISTS idx_position_files_uploaded_at ON position_files(uploaded_at);
"""


# Create the position_files table. Safe to call repeatedly.
def init_uploads_schema() -> None:
    with connection() as conn:
        conn.executescript(POSITION_FILES_SCHEMA)
        _ensure_column(
            conn,
            "position_files",
            "text_content",
            "TEXT NOT NULL DEFAULT ''",
        )


def _ensure_column(conn, table: str, column: str, ddl: str) -> None:
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


# Stable id for an uploaded file, keyed on (ticker, content hash) so identical
# re-uploads collapse to one row.
def file_event_id(ticker: str, content_sha: str) -> str:
    return event_id(
        {"kind": "position_file", "ticker": ticker.upper(), "content_sha": content_sha}
    )


# Reduce an arbitrary client filename to a safe basename (no path parts, no
# surprising characters) for on-disk storage.
def _safe_name(filename: str) -> str:
    base = Path(filename).name
    return re.sub(r"[^A-Za-z0-9._-]", "_", base) or "upload"


# Extract plain text from a supported document. txt/md are read as UTF-8;
# pdf/docx use optional libraries, lazy-imported so the package loads without
# them. Raises UploadError for unsupported types or a missing extractor lib.
def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md", ".markdown"}:
        return path.read_text(encoding="utf-8", errors="replace")
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as e:
            raise UploadError("PDF extraction needs `pypdf` (pip install pypdf)") from e
        reader = PdfReader(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages).strip()
    if suffix == ".docx":
        try:
            import docx
        except ImportError as e:
            raise UploadError(
                "DOCX extraction needs `python-docx` (pip install python-docx)"
            ) from e
        document = docx.Document(str(path))
        return "\n".join(p.text for p in document.paragraphs).strip()
    raise UploadError(f"unsupported file type: {suffix or '(none)'}")


# Save one uploaded document for a ticker: write the original + a cached .txt
# under data/portfolio/uploads/{TICKER}/, extract text, and record the row.
# Idempotent on content — re-uploading the same bytes returns the existing row.
def save_upload(ticker: str, filename: str, content: bytes) -> dict:
    init_uploads_schema()
    ticker = ticker.strip().upper()
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise UploadError(f"unsupported file type: {suffix or '(none)'}")

    content_sha = hashlib.sha256(content).hexdigest()
    eid = file_event_id(ticker, content_sha)

    # Content-prefixed name avoids collisions between same-named, differing files.
    safe = _safe_name(filename)
    dest_dir = UPLOADS_DIR / ticker
    dest_dir.mkdir(parents=True, exist_ok=True)
    stored = dest_dir / f"{content_sha[:12]}_{safe}"
    stored.write_bytes(content)

    # Extract text now and cache it; a failed extraction still records the file
    # (with empty text) so the upload itself isn't lost.
    text_path = stored.with_suffix(stored.suffix + ".txt")
    try:
        text = extract_text(stored)
    except UploadError:
        text = ""
    text_path.write_text(text, encoding="utf-8")

    record = {
        "event_id": eid,
        "ticker": ticker,
        "filename": safe,
        "stored_path": str(stored.relative_to(DATA_ROOT)),
        "text_path": str(text_path.relative_to(DATA_ROOT)),
        "text_content": text,
        "content_type": suffix,
        "content_sha": content_sha,
        "byte_size": len(content),
        "n_chars": len(text),
        "uploaded_at": datetime.now(UTC).isoformat(),
    }
    with connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO events(event_id, kind, ticker, first_seen, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                eid,
                "position_file",
                ticker,
                record["uploaded_at"],
                json.dumps(
                    {"filename": safe, "content_type": suffix, "n_chars": len(text)}
                ),
            ),
        )
        conn.execute(
            """INSERT OR IGNORE INTO position_files
               (event_id, ticker, filename, stored_path, text_path, text_content,
                content_type, content_sha, byte_size, n_chars, uploaded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                eid,
                ticker,
                safe,
                record["stored_path"],
                record["text_path"],
                text,
                suffix,
                content_sha,
                len(content),
                len(text),
                record["uploaded_at"],
            ),
        )
    return record


# Save an upload from a path on disk (CLI convenience wrapper around save_upload).
def save_upload_from_path(ticker: str, path: Path) -> dict:
    return save_upload(ticker, path.name, path.read_bytes())


# List a ticker's uploaded files (most recent first), or all files when no
# ticker is given. Returns raw rows for the CLI / API to format.
def list_files(ticker: str | None = None):
    init_uploads_schema()
    sql = "SELECT * FROM position_files"
    args: list = []
    if ticker:
        sql += " WHERE ticker = ?"
        args.append(ticker.strip().upper())
    sql += " ORDER BY uploaded_at DESC"
    with connection() as conn:
        return conn.execute(sql, args).fetchall()


# Read one file's cached extracted text by event_id, resolving the stored
# relative path against DATA_ROOT. Returns "" when the file or cache is gone.
def read_text(event_id_str: str) -> str:
    init_uploads_schema()
    with connection() as conn:
        row = conn.execute(
            "SELECT text_content, text_path FROM position_files WHERE event_id = ?",
            (event_id_str,),
        ).fetchone()
    if row is None:
        return ""
    if row["text_content"]:
        return row["text_content"]
    if not row["text_path"]:
        return ""
    p = DATA_ROOT / row["text_path"]
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


# Concatenate the cached text of every uploaded file for a ticker (most recent
# first), labeled by filename and truncated to MAX_COMBINED_CHARS. This is the
# thesis_doc_text the decision engine folds into its candidate bundle.
def combined_text(ticker: str, max_chars: int = MAX_COMBINED_CHARS) -> str:
    rows = list_files(ticker)
    parts: list[str] = []
    for r in rows:
        body = (r["text_content"] or "").strip()
        if not body and r["text_path"]:
            p = DATA_ROOT / r["text_path"]
            if p.exists():
                body = p.read_text(encoding="utf-8", errors="replace").strip()
        if body:
            parts.append(f"### {r['filename']}\n{body}")
    combined = "\n\n".join(parts)
    return combined[:max_chars] if len(combined) > max_chars else combined


# Remove one upload: delete the on-disk original + cached text and its row.
# Returns True when a row was deleted. Used by the API's file-delete endpoint.
def delete_file(event_id_str: str) -> bool:
    init_uploads_schema()
    with connection() as conn:
        row = conn.execute(
            "SELECT stored_path, text_path FROM position_files WHERE event_id = ?",
            (event_id_str,),
        ).fetchone()
        if row is None:
            return False
        for rel in (row["stored_path"], row["text_path"]):
            if rel:
                (DATA_ROOT / rel).unlink(missing_ok=True)
        conn.execute("DELETE FROM position_files WHERE event_id = ?", (event_id_str,))
        conn.execute("DELETE FROM events WHERE event_id = ?", (event_id_str,))
    return True
