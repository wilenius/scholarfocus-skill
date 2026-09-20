"""Thematic clustering for narrative mode.

Topic-based clustering uses OpenAlex topic labels; co-citation clustering
groups works that the corpus cites together.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from typing import Optional

from scholarlib.pipeline.context import Context
from scholarlib.records import Record

logger = logging.getLogger(__name__)


def by_topics(records: list[Record], *, max_clusters: int = 7,
              min_size: int = 3) -> list[dict]:
    """Assign each record to its strongest shared topic."""
    counts = Counter()
    for r in records:
        counts.update(r.topics[:3])
    if not counts:
        return []
    labels = [t for t, n in counts.most_common(max_clusters * 3) if n >= min_size]
    labels = labels[:max_clusters]
    if not labels:
        return []

    rank = {t: i for i, t in enumerate(labels)}
    members: dict[str, list[Record]] = defaultdict(list)
    for r in records:
        best = min((t for t in r.topics if t in rank), key=lambda t: rank[t], default=None)
        if best:
            members[best].append(r)

    clusters = []
    for i, label in enumerate(labels):
        rows = members.get(label) or []
        if len(rows) < min_size:
            continue
        rows.sort(key=lambda r: r.score, reverse=True)
        cid = f"c{i + 1}"
        for r in rows:
            r.cluster = cid
        kw = Counter()
        for r in rows:
            kw.update(t for t in r.topics if t != label)
        clusters.append({
            "id": cid,
            "label": label,
            "keywords": [k for k, _ in kw.most_common(5)],
            "size": len(rows),
            "record_keys": [r.key for r in rows],
            "representative_keys": [r.key for r in rows[:3]],
            "year_range": [
                min((r.year for r in rows if r.year), default=None),
                max((r.year for r in rows if r.year), default=None),
            ],
        })
    return clusters


def by_cocitation(records: list[Record], *, max_clusters: int = 7,
                  min_size: int = 3) -> list[dict]:
    """Group by the most-shared references across the corpus."""
    ref_counts = Counter()
    for r in records:
        ref_counts.update(r.referenced_works)
    anchors = [ref for ref, n in ref_counts.most_common(max_clusters * 2) if n >= min_size]
    anchors = anchors[:max_clusters]
    if not anchors:
        return []

    by_key = {r.key: r for r in records}
    title_of = {r.openalex_id: r.title for r in records if r.openalex_id}
    clusters = []
    assigned: set[str] = set()
    for i, anchor in enumerate(anchors):
        rows = [r for r in records
                if anchor in r.referenced_works and r.key not in assigned]
        if len(rows) < min_size:
            continue
        rows.sort(key=lambda r: r.score, reverse=True)
        cid = f"cc{i + 1}"
        for r in rows:
            r.cluster = cid
            assigned.add(r.key)
        clusters.append({
            "id": cid,
            "label": title_of.get(anchor) or f"works citing {anchor.rsplit('/', 1)[-1]}",
            "anchor": anchor,
            "size": len(rows),
            "record_keys": [r.key for r in rows],
            "representative_keys": [r.key for r in rows[:3]],
        })
    return clusters


def timeline(ctx: Context, records: list[Record]) -> dict:
    """Publication counts per year — the shape of the conversation over time."""
    years = Counter(r.year for r in records if r.year)
    return {str(y): n for y, n in sorted(years.items())}


def cluster(ctx: Context, records: list[Record], *, method: str = "topics",
            max_clusters: int = 7) -> list[dict]:
    if method == "none":
        return []
    if method == "cocitation":
        return by_cocitation(records, max_clusters=max_clusters)
    if method == "both":
        return by_topics(records, max_clusters=max_clusters) + \
            by_cocitation(records, max_clusters=max_clusters)
    return by_topics(records, max_clusters=max_clusters)
