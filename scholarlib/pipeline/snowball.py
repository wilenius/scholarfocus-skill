"""Citation chaining.

Forward (cites:) and backward (referenced_works) are both cheap. Lateral
(related_works) matters because monographs carry referenced_works_count == 0,
making it the only cheap recall route for exactly the books that backward
chaining cannot see.
"""

from __future__ import annotations

import logging
from typing import Optional

from scholarlib.adapters import from_openalex_work
from scholarlib.apis.openalex import LITREVIEW_WORK_FIELDS, short_id
from scholarlib.http.budget import BudgetExceeded
from scholarlib.pipeline.context import Context
from scholarlib.records import Record

logger = logging.getLogger(__name__)


def _hydrate(ctx: Context, ids: list[str], provenance: str) -> list[Record]:
    """Resolve OpenAlex IDs to full records.

    'free' mode uses 0-credit singletons (slower but unbounded); 'fast' mode
    batches 50 per credit.
    """
    oa = ctx.clients["openalex"]
    ids = list(dict.fromkeys(short_id(i) for i in ids if i))
    if not ids:
        return []
    out: list[Record] = []
    if ctx.budget_mode == "free":
        for wid in ids:
            w = oa.get_work(wid, select=LITREVIEW_WORK_FIELDS)
            if w:
                out.append(from_openalex_work(w, provenance=provenance))
    else:
        for w in oa.get_works_batch(ids, select=LITREVIEW_WORK_FIELDS):
            out.append(from_openalex_work(w, provenance=provenance))
    return out


def forward(ctx: Context, seeds: list[Record], *, max_per_seed: int = 200,
            from_year: Optional[int] = None) -> list[Record]:
    """Works citing the seeds. OR-packed, so 50 seeds cost 1 credit per page."""
    oa = ctx.clients["openalex"]
    ids = [r.openalex_id for r in seeds if r.openalex_id]
    if not ids:
        return []
    works = oa.cited_by(ids, max_items=max_per_seed * max(1, len(ids) // 50 + 1),
                        from_year=from_year, select=LITREVIEW_WORK_FIELDS)
    out = [from_openalex_work(w, provenance="snowball:forward") for w in works]
    logger.info("Forward snowball: %d works citing %d seeds", len(out), len(ids))
    ctx.count("forward", len(out))
    return out


def backward(ctx: Context, seeds: list[Record], *, max_per_seed: int = 100) -> list[Record]:
    """Works the seeds cite. Books have none, which is reported, not hidden."""
    ref_ids: list[str] = []
    books_without_refs = 0
    for r in seeds:
        if r.referenced_works:
            ref_ids.extend(r.referenced_works[:max_per_seed])
        elif r.type in ("book", "monograph"):
            books_without_refs += 1
    if books_without_refs:
        ctx.warn(
            f"{books_without_refs} book record(s) have no reference list in OpenAlex; "
            "backward citation chaining is unavailable for monographs."
        )
    out = _hydrate(ctx, ref_ids, "snowball:backward")
    logger.info("Backward snowball: %d cited works", len(out))
    ctx.count("backward", len(out))
    return out


def lateral(ctx: Context, seeds: list[Record], *, max_per_seed: int = 10) -> list[Record]:
    """related_works: the cheap recall substitute for books."""
    ids: list[str] = []
    for r in seeds:
        ids.extend(r.related_works[:max_per_seed])
    out = _hydrate(ctx, ids, "snowball:lateral")
    logger.info("Lateral expansion: %d related works", len(out))
    ctx.count("lateral", len(out))
    return out


def snowball(ctx: Context, seeds: list[Record], *, depth: int = 1,
             do_forward: bool = True, do_backward: bool = True,
             do_lateral: bool = False, max_per_seed: int = 100,
             from_year: Optional[int] = None) -> list[Record]:
    """BFS over the citation graph, stopping cleanly when the budget runs out."""
    collected: list[Record] = []
    frontier = seeds
    for level in range(depth):
        logger.info("Snowball depth %d/%d (%d seeds)", level + 1, depth, len(frontier))
        found: list[Record] = []
        try:
            if do_forward:
                found += forward(ctx, frontier, max_per_seed=max_per_seed,
                                 from_year=from_year)
            if do_backward:
                found += backward(ctx, frontier, max_per_seed=max_per_seed)
            if do_lateral:
                found += lateral(ctx, frontier)
        except BudgetExceeded as e:
            ctx.truncated = {"reason": "budget", "stage": f"snowball:depth{level + 1}",
                             "detail": str(e)}
            ctx.warn(f"Budget exhausted during snowballing: {e}")
            collected += found
            break
        collected += found
        seen = {r.key for r in seeds} | {r.key for r in collected}
        frontier = [r for r in found if r.key not in seen] or found
        if not frontier:
            break
    return collected
