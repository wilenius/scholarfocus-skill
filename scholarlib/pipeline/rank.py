"""Scoring and screening."""

from __future__ import annotations

import logging
import math
import re
from typing import Optional

from scholarlib import dedup
from scholarlib.pipeline.context import Context
from scholarlib.records import Record

logger = logging.getLogger(__name__)

WEIGHTS = {
    "narrative": {
        "impact": 0.30, "seed_proximity": 0.25, "query_match": 0.15,
        "recency": 0.10, "zotero": 0.10, "book_bonus": 0.10,
    },
    "systematic": {
        "query_match": 0.40, "seed_proximity": 0.20, "impact": 0.15,
        "recency": 0.15, "zotero": 0.05, "book_bonus": 0.05,
    },
}


def _query_match(rec: Record, terms: set[str]) -> float:
    if not terms:
        return 0.0
    hay = set(dedup.normalize_text(
        f"{rec.title or ''} {' '.join(rec.topics)} {(rec.abstract or '')[:600]}"
    ).split())
    return len(terms & hay) / len(terms)


def score(ctx: Context, records: list[Record], *, mode: str = "narrative",
          queries: Optional[list[str]] = None, seeds: Optional[list[Record]] = None,
          current_year: int = 2026) -> list[Record]:
    w = WEIGHTS.get(mode, WEIGHTS["narrative"])
    terms = set()
    for q in (queries or []):
        terms |= set(dedup.normalize_text(q).split())

    seed_keys = {r.key for r in (seeds or [])}
    seed_refs: set[str] = set()
    for r in (seeds or []):
        seed_refs |= set(r.referenced_works) | set(r.related_works)

    max_cites = max((r.cited_by_count or 0) for r in records) if records else 0
    log_max = math.log1p(max_cites) or 1.0

    for rec in records:
        parts: dict[str, float] = {}
        parts["impact"] = (math.log1p(rec.cited_by_count or 0) / log_max) * w["impact"]

        prox = 0.0
        if rec.key in seed_keys:
            prox = 1.0
        elif rec.openalex_id and rec.openalex_id in seed_refs:
            prox = 0.7
        elif any(p.startswith("snowball") for p in rec.provenance):
            prox = 0.4
        parts["seed_proximity"] = prox * w["seed_proximity"]

        parts["query_match"] = _query_match(rec, terms) * w["query_match"]

        if rec.year:
            age = max(0, current_year - rec.year)
            parts["recency"] = max(0.0, 1.0 - age / 40.0) * w["recency"]
        else:
            parts["recency"] = 0.0

        parts["zotero"] = (1.0 if rec.in_zotero else 0.0) * w["zotero"]
        parts["book_bonus"] = (1.0 if rec.type in ("book", "chapter") else 0.0) * w["book_bonus"]

        rec.score_parts = {k: round(v, 4) for k, v in parts.items()}
        rec.score = round(sum(parts.values()), 4)

    records.sort(key=lambda r: r.score, reverse=True)
    return records


_EXPR = re.compile(r"^\s*(\w+)\s*(>=|<=|==|!=|>|<|~)\s*(.+?)\s*$")


def _evaluate(rec: Record, expr: str) -> Optional[bool]:
    """Evaluate a simple `field op value` criterion. Returns None if unusable."""
    m = _EXPR.match(expr)
    if not m:
        return None
    fld, op, val = m.groups()
    actual = getattr(rec, fld, None)
    if op == "~":
        return dedup.normalize_text(val) in dedup.normalize_text(str(actual or ""))
    if actual is None:
        return None
    try:
        a, b = float(actual), float(val)
    except (TypeError, ValueError):
        a, b = dedup.normalize_text(str(actual)), dedup.normalize_text(val)
    return {">=": a >= b, "<=": a <= b, ">": a > b, "<": a < b,
            "==": a == b, "!=": a != b}[op]


def screen(ctx: Context, records: list[Record], *,
           include_if: Optional[list[str]] = None,
           exclude_if: Optional[list[str]] = None,
           min_score: Optional[float] = None,
           max_results: Optional[int] = None) -> tuple[list[Record], list[dict]]:
    """Apply criteria, counting every exclusion for the flow summary."""
    excluded: list[dict] = []
    kept = records

    for expr in (include_if or []):
        before = len(kept)
        kept = [r for r in kept if _evaluate(r, expr) is not False]
        if before - len(kept):
            excluded.append({"reason": f"fails include: {expr}", "n": before - len(kept)})

    for expr in (exclude_if or []):
        before = len(kept)
        kept = [r for r in kept if _evaluate(r, expr) is not True]
        if before - len(kept):
            excluded.append({"reason": f"matches exclude: {expr}", "n": before - len(kept)})

    if min_score is not None:
        before = len(kept)
        kept = [r for r in kept if r.score >= min_score]
        if before - len(kept):
            excluded.append({"reason": f"below --min-score {min_score}",
                             "n": before - len(kept)})

    if max_results is not None and len(kept) > max_results:
        excluded.append({"reason": f"beyond --max-results {max_results}",
                         "n": len(kept) - max_results})
        kept = kept[:max_results]

    return kept, excluded
