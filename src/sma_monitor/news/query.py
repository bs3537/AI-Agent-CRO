"""Build news queries from sidecar + bucket terms.

Exa is a neural search — the query is free text, not Boolean. We give it the
holding's identifiers (ticker, company name, aliases, brands, products) and a
handful of bucket-specific terms, and let Exa match semantically.

per_holding_query → for Tier A/B/C buckets, run once per (holding, bucket).
sector_query      → for Tier D buckets, run once per bucket with no entity.
"""
from __future__ import annotations

from ..portfolio.schema import Holding, Sidecar
from .buckets import Bucket


# Deduplicate the holding's identifiers in priority order: ticker first,
# then company name, then aliases/brands/products. Order matters because
# callers truncate the list when building queries.
def entity_terms(h: Holding | Sidecar) -> list[str]:
    """Identifiers for the holding, deduped, in priority order.

    Order matters because we truncate when building the query — the ticker
    and company name are most useful for recall; brand/product names trail.
    """
    raw = [h.ticker]
    if h.company_name:
        raw.append(h.company_name)
    raw.extend(h.aliases)
    raw.extend(h.brands)
    raw.extend(h.products)
    seen: set[str] = set()
    out: list[str] = []
    for t in raw:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


# Build the per-holding query string: top-N entity terms followed by top-M
# bucket search terms. Truncated to keep Exa queries focused — long queries
# can dilute neural-search relevance.
def per_holding_query(
    holding: Holding | Sidecar,
    bucket: Bucket,
    *,
    max_entity_terms: int = 4,
    max_bucket_terms: int = 5,
) -> str:
    entity = " ".join(entity_terms(holding)[:max_entity_terms])
    bucket_q = " ".join(bucket.search_terms[:max_bucket_terms])
    return f"{entity} {bucket_q}".strip()


# Build a sector-scope query (no entity terms). Used for #11 policy and
# #12 microstructure buckets where the news is industry-wide, not name-specific.
def sector_query(
    bucket: Bucket,
    *,
    sector: str = "biotech pharma",
    max_terms: int = 6,
) -> str:
    bucket_q = " ".join(bucket.search_terms[:max_terms])
    return f"{sector} {bucket_q}".strip()


# Build a literature query (W2 / bucket #10): the holding's drug/product and
# indication terms, which is what Scite searches on. Falls back to the company
# name. Returns "" when there's nothing to search (caller skips the holding).
def literature_query(h: Holding | Sidecar, *, max_terms: int = 4) -> str:
    terms = [*h.products, *h.indications]
    if not terms and h.company_name:
        terms = [h.company_name]
    return " ".join(terms[:max_terms]).strip()
