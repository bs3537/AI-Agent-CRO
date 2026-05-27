"""W4 tests — thesis-document text extraction + identity.

Pure-function coverage that needs no DB or global DATA_ROOT redirect (which
would leak into sibling tests). save_upload / list_files / combined_text and
the engine wiring are exercised end-to-end by the offline sandbox recipe.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from sma_monitor.portfolio.uploads import (  # noqa: E402
    SUPPORTED_SUFFIXES,
    UploadError,
    extract_text,
    file_event_id,
)


# .txt files are read straight through as UTF-8.
def test_extract_txt(tmp_path):
    p = tmp_path / "thesis.txt"
    p.write_text("Durable CF franchise; pipeline optional.", encoding="utf-8")
    assert "Durable CF franchise" in extract_text(p)


# .md is treated like plain text (markdown is kept verbatim).
def test_extract_markdown(tmp_path):
    p = tmp_path / "notes.md"
    p.write_text("# Thesis\n\n- point one\n- point two", encoding="utf-8")
    out = extract_text(p)
    assert "point one" in out and "# Thesis" in out


# .docx is parsed via python-docx (installed) into joined paragraph text.
def test_extract_docx(tmp_path):
    docx = pytest.importorskip("docx")
    p = tmp_path / "memo.docx"
    d = docx.Document()
    d.add_paragraph("First paragraph of the thesis.")
    d.add_paragraph("Second paragraph with the catalyst.")
    d.save(str(p))
    out = extract_text(p)
    assert "First paragraph" in out and "catalyst" in out


# An unsupported extension raises UploadError rather than silently returning "".
def test_extract_unsupported(tmp_path):
    p = tmp_path / "image.png"
    p.write_bytes(b"\x89PNG\r\n")
    with pytest.raises(UploadError):
        extract_text(p)


# The accepted-types set is exactly the documented four families.
def test_supported_suffixes():
    assert SUPPORTED_SUFFIXES == {".txt", ".md", ".markdown", ".pdf", ".docx"}


# file_event_id is content-addressed: identical content → same id, regardless
# of how many times it's uploaded; different content → different id.
def test_file_event_id_content_addressed():
    a = file_event_id("VRTX", "abc123")
    b = file_event_id("VRTX", "abc123")
    c = file_event_id("VRTX", "def456")
    assert a == b and a != c
    # Ticker normalization: case doesn't change the id.
    assert file_event_id("vrtx", "abc123") == a
