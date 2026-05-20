import hashlib
import json
from typing import Any


def event_id(canonical_fields: dict[str, Any]) -> str:
    """sha256 of canonical JSON-encoded fields.

    The hinge for idempotency (Phase 6) and the feedback loop (Phase 7).
    Pass only fields that uniquely and stably identify the artifact —
    timestamps of when *we* observed it do not belong here, only fields
    intrinsic to the artifact itself.
    """
    canonical = json.dumps(
        canonical_fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def article_event_id(url: str, title: str, lede: str) -> str:
    """Phase 2 article identity: URL + sha of title+lede."""
    return event_id({"url": url, "title": title, "lede": lede})
