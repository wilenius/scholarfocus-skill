"""JSTOR coverage check.

The free direction (OpenAlex record -> local FTS) runs for everything. The
expensive direction (JSTOR row -> OpenAlex, a 10-credit title search) is hard
capped and permanently memoised, misses included.
"""

from __future__ import annotations

import logging
from typing import Optional

from scholarlib import dedup
from scholarlib.adapters import from_openalex_work
from scholarlib.apis.openalex import LITREVIEW_WORK_FIELDS
from scholarlib.http.budget import BudgetExceeded
from scholarlib.jstor.query import put_map
from scholarlib.pipeline.context import Context
from scholarlib.records import Record

logger = logging.getLogger(__name__)


def annotate(ctx: Context, records: list[Record]) -> list[Record]:
    """Mark which records JSTOR also holds. Costs nothing."""
    idx = ctx.jstor_index
    if idx is None:
        return records
    hits = 0
    for rec in records:
        hit, score, method = idx.find_match(rec)
        if hit and hit.jstor:
            rec.jstor = {**hit.jstor, "match_score": round(score, 3),
                         "match_method": method}
            rec.provenance.append("jstor:matched")
            hits += 1
    logger.info("JSTOR: %d/%d records also held in JSTOR", hits, len(records))
    ctx.stats["jstor_matched"] = hits
    return records


def discover(ctx: Context, queries: list[str], seen: list[Record], *,
             limit: int = 50, join_limit: int = 25,
             disciplines: Optional[list[str]] = None,
             from_year: Optional[int] = None,
             to_year: Optional[int] = None,
             types: Optional[list[str]] = None) -> list[Record]:
    """Find books and chapters JSTOR has that the citation graph missed.

    Each OpenAlex join probe is a 10-credit search, hence `join_limit`.
    """
    idx = ctx.jstor_index
    if idx is None:
        return []
    oa = ctx.clients["openalex"]

    seen_keys = {r.key for r in seen}
    seen_titles = {dedup.title_main(r.title) for r in seen if r.title}

    candidates: list[Record] = []
    for q in queries:
        for rec in idx.search(q, disciplines=disciplines, types=types,
                              from_year=from_year, to_year=to_year, limit=limit):
            if rec.key in seen_keys:
                continue
            tm = dedup.title_main(rec.title)
            if tm and tm in seen_titles:
                continue
            candidates.append(rec)
            seen_keys.add(rec.key)

    # Books and chapters first: that is where OpenAlex is actually weak.
    candidates.sort(key=lambda r: (r.type not in ("book", "chapter"), -(r.year or 0)))
    logger.info("JSTOR discovery: %d candidates not already in the corpus",
                len(candidates))

    joined = attempted = 0
    for rec in candidates:
        if attempted >= join_limit:
            break
        item_id = rec.jstor_item_id
        memo = idx.get_map(item_id) if item_id else None
        if memo is not None:
            if memo.get("openalex_id"):
                rec.openalex_id = memo["openalex_id"]
                rec.doi = memo.get("doi") or rec.doi
                joined += 1
            continue  # a cached miss is worth 10 credits; never re-pay it

        if not dedup.titles_comparable(rec.title):
            continue
        attempted += 1
        try:
            works = oa.search_works(dedup.title_main(rec.title) or rec.title,
                                    limit=3, select=LITREVIEW_WORK_FIELDS)
        except BudgetExceeded as e:
            ctx.truncated = {"reason": "budget", "stage": "jstor:join", "detail": str(e)}
            ctx.warn(f"Budget exhausted during the JSTOR join: {e}")
            break

        best, best_score = None, 0.0
        for w in works:
            cand = from_openalex_work(w)
            s = dedup.best_title_similarity(rec.title, cand.title)
            if rec.first_surname and cand.first_surname and \
                    rec.first_surname != cand.first_surname:
                s *= 0.7
            if s > best_score:
                best, best_score = cand, s

        if best is not None and best_score >= 0.90:
            rec.openalex_id = best.openalex_id
            rec.doi = best.doi or rec.doi
            rec.cited_by_count = best.cited_by_count
            rec.abstract = rec.abstract or best.abstract
            rec.abstract_source = rec.abstract_source or best.abstract_source
            rec.referenced_works = best.referenced_works
            rec.related_works = best.related_works
            rec.provenance.append("jstor:joined")
            joined += 1
            put_map(idx.path, item_id, best.openalex_id, best.doi, best_score, "title_search")
        else:
            put_map(idx.path, item_id, None, None, best_score, "none")

    unjoined = len(candidates) - joined
    if attempted >= join_limit and unjoined > 0:
        ctx.warn(
            f"{unjoined} JSTOR record(s) were not joined to OpenAlex: the join "
            f"limit of {join_limit} probes was reached (each costs a 10-credit "
            f"search). Raise --jstor-join-limit to resolve more."
        )
    logger.info("JSTOR join: %d joined to OpenAlex, %d probes spent", joined, attempted)
    ctx.stats["jstor_discovered"] = len(candidates)
    ctx.stats["jstor_joined"] = joined
    ctx.count("jstor", len(candidates))
    return candidates
