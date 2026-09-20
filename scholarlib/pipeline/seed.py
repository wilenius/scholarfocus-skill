"""Seeding: the only stage that spends 10-credit searches, so it is deliberate."""

from __future__ import annotations

import logging
from typing import Optional

from scholarlib.adapters import from_openalex_work
from scholarlib.apis.openalex import LITREVIEW_WORK_FIELDS
from scholarlib.pipeline.context import Context
from scholarlib.records import Record

logger = logging.getLogger(__name__)


def seed_from_dois(ctx: Context, dois: list[str]) -> list[Record]:
    """Free: singleton lookups cost 0 credits. Always prefer this."""
    oa = ctx.clients["openalex"]
    out = []
    for doi in dois:
        w = oa.get_work_by_doi(doi, select=LITREVIEW_WORK_FIELDS)
        if w:
            out.append(from_openalex_work(w, provenance="seed:doi"))
        else:
            ctx.warn(f"DOI not found in OpenAlex: {doi}")
    logger.info("Seeded %d works from DOIs (0 credits)", len(out))
    ctx.count("seed_doi", len(out))
    return out


def seed_from_ids(ctx: Context, ids: list[str]) -> list[Record]:
    """Free."""
    oa = ctx.clients["openalex"]
    out = []
    for wid in ids:
        w = oa.get_work(wid, select=LITREVIEW_WORK_FIELDS)
        if w:
            out.append(from_openalex_work(w, provenance="seed:id"))
    ctx.count("seed_id", len(out))
    return out


def seed_from_queries(ctx: Context, queries: list[str], *, max_seeds: int = 50,
                      from_year: Optional[int] = None, to_year: Optional[int] = None,
                      types: Optional[list[str]] = None,
                      languages: Optional[list[str]] = None) -> list[Record]:
    """10 credits per query. Cached for 90 days."""
    oa = ctx.clients["openalex"]
    out: list[Record] = []
    per = max(1, max_seeds // max(1, len(queries)))
    for q in queries:
        works = oa.search_works(q, limit=per, from_year=from_year, to_year=to_year,
                                types=types, languages=languages)
        out.extend(from_openalex_work(w, provenance="seed:query") for w in works)
        logger.info("Query %r -> %d works", q, len(works))
    ctx.count("seed_query", len(out))
    return out


def seed_from_zotero(ctx: Context, collection: Optional[str] = None) -> list[Record]:
    """Free and high precision: resolve owned DOIs through singleton lookups."""
    z = ctx.clients["zotero"]
    if not z.available():
        return []
    key = None
    if collection:
        c = z.collection_by_name(collection)
        if not c:
            ctx.warn(f"Zotero collection not found: {collection}")
            return []
        key = (c.get("data") or {}).get("key") or c.get("key")
    items = z.load_library(collection_key=key)
    dois = [r.doi for r in items if r.doi]
    logger.info("Zotero seed: %d items, %d with DOIs", len(items), len(dois))
    resolved = seed_from_dois(ctx, dois) if dois else []
    for r in resolved:
        r.in_zotero = True
        r.provenance.append("seed:zotero")
    ctx.count("seed_zotero", len(resolved))
    return resolved


def seed_from_jstor(ctx: Context, queries: list[str], *, limit: int = 25,
                    disciplines: Optional[list[str]] = None,
                    from_year: Optional[int] = None,
                    to_year: Optional[int] = None) -> list[Record]:
    """Free: the local index costs no API credits at all."""
    if ctx.jstor_index is None:
        return []
    out: list[Record] = []
    for q in queries:
        out.extend(ctx.jstor_index.search(
            q, disciplines=disciplines, from_year=from_year,
            to_year=to_year, limit=limit))
    logger.info("JSTOR seed: %d records (0 credits)", len(out))
    ctx.count("seed_jstor", len(out))
    return out
