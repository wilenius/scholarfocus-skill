"""The canonical record shape every source adapter produces and every stage consumes."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Iterable, Optional

from scholarlib import dedup


@dataclass
class Record:
    key: str = ""
    doi: Optional[str] = None
    openalex_id: Optional[str] = None
    jstor_item_id: Optional[str] = None
    title: Optional[str] = None
    title_norm: Optional[str] = None
    authors: list[str] = field(default_factory=list)
    first_surname: Optional[str] = None
    year: Optional[int] = None
    venue: Optional[str] = None
    type: Optional[str] = None
    language: Optional[str] = None
    abstract: Optional[str] = None
    abstract_source: Optional[str] = None
    cited_by_count: Optional[int] = None
    referenced_works: list[str] = field(default_factory=list)
    related_works: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    oa_status: Optional[str] = None
    oa_url: Optional[str] = None
    in_zotero: bool = False
    zotero_key: Optional[str] = None
    zotero_citekey: Optional[str] = None
    jstor: Optional[dict] = None
    provenance: list[str] = field(default_factory=list)
    cluster: Optional[str] = None
    score: float = 0.0
    score_parts: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.doi = dedup.normalize_doi(self.doi)
        self.title_norm = dedup.normalize_title(self.title)
        if not self.first_surname and self.authors:
            self.first_surname = dedup.normalize_surname(self.authors[0])
        if not self.key:
            self.key = record_key(self)

    def to_dict(self) -> dict:
        return asdict(self)


def record_key(rec: Record) -> str:
    """Stable identity, most reliable signal first."""
    if rec.doi and not dedup.is_jstor_internal_doi(rec.doi):
        return f"doi:{rec.doi}"
    if rec.openalex_id:
        return f"openalex:{rec.openalex_id.rsplit('/', 1)[-1]}"
    if rec.doi:
        return f"doi:{rec.doi}"
    if rec.jstor_item_id:
        return f"jstor:{rec.jstor_item_id}"
    return (
        f"title:{dedup.title_fingerprint(rec.title)}"
        f"|{rec.first_surname or ''}|{rec.year or ''}"
    )


def _prefer(a: Any, b: Any) -> Any:
    return a if a not in (None, "", [], {}) else b


def merge_records(a: Record, b: Record) -> Record:
    """Field-wise union preferring the richer value."""
    out = Record(
        key=a.key or b.key,
        doi=_prefer(
            a.doi if a.doi and not dedup.is_jstor_internal_doi(a.doi) else None,
            _prefer(b.doi, a.doi),
        ),
        openalex_id=_prefer(a.openalex_id, b.openalex_id),
        jstor_item_id=_prefer(a.jstor_item_id, b.jstor_item_id),
        title=_prefer(a.title, b.title),
        authors=a.authors or b.authors,
        first_surname=_prefer(a.first_surname, b.first_surname),
        year=_prefer(a.year, b.year),
        venue=_prefer(a.venue, b.venue),
        type=_prefer(a.type, b.type),
        language=_prefer(a.language, b.language),
        cited_by_count=_prefer(a.cited_by_count, b.cited_by_count),
        referenced_works=list(dict.fromkeys([*a.referenced_works, *b.referenced_works])),
        related_works=list(dict.fromkeys([*a.related_works, *b.related_works])),
        topics=list(dict.fromkeys([*a.topics, *b.topics])),
        oa_status=_prefer(a.oa_status, b.oa_status),
        oa_url=_prefer(a.oa_url, b.oa_url),
        in_zotero=a.in_zotero or b.in_zotero,
        zotero_key=_prefer(a.zotero_key, b.zotero_key),
        zotero_citekey=_prefer(a.zotero_citekey, b.zotero_citekey),
        jstor=_prefer(a.jstor, b.jstor),
        provenance=list(dict.fromkeys([*a.provenance, *b.provenance])),
        cluster=_prefer(a.cluster, b.cluster),
        score=max(a.score, b.score),
        score_parts={**b.score_parts, **a.score_parts},
    )
    # Longest abstract wins, and carries its own source label.
    ca = a.abstract or ""
    cb = b.abstract or ""
    if len(ca) >= len(cb):
        out.abstract, out.abstract_source = (a.abstract, a.abstract_source)
    else:
        out.abstract, out.abstract_source = (b.abstract, b.abstract_source)
    if not out.abstract:
        out.abstract_source = None
    # The winning DOI may differ from either input's, so identity is recomputed.
    out.key = record_key(out)
    return out


def dedup_records(
    records: Iterable[Record],
    *,
    title_threshold: float = 0.93,
    year_slack: int = 1,
) -> tuple[list[Record], dict]:
    """Two passes: exact key merge, then blocked fuzzy merge.

    Returns (records, stats); systematic mode needs the duplicate count.
    """
    records = list(records)
    stats = {"input": len(records), "merged_exact": 0, "merged_fuzzy": 0}

    by_key: dict[str, Record] = {}
    for r in records:
        k = r.key or record_key(r)
        if k in by_key:
            by_key[k] = merge_records(by_key[k], r)
            stats["merged_exact"] += 1
        else:
            by_key[k] = r

    # Blocked fuzzy pass: only compare within (surname, year +/- slack).
    remaining = list(by_key.values())
    buckets: dict[tuple, list[Record]] = {}
    for r in remaining:
        for dy in range(-year_slack, year_slack + 1):
            y = (r.year + dy) if r.year else None
            buckets.setdefault((r.first_surname, y), []).append(r)

    merged_into: dict[str, str] = {}
    final: dict[str, Record] = {r.key: r for r in remaining}
    for bucket in buckets.values():
        if len(bucket) < 2:
            continue
        for i in range(len(bucket)):
            for j in range(i + 1, len(bucket)):
                a, b = bucket[i], bucket[j]
                ka = merged_into.get(a.key, a.key)
                kb = merged_into.get(b.key, b.key)
                if ka == kb or ka not in final or kb not in final:
                    continue
                ra, rb = final[ka], final[kb]
                if not (dedup.titles_comparable(ra.title) and dedup.titles_comparable(rb.title)):
                    continue
                # Two different *registered* DOIs mean two different works.
                # JSTOR-internal DOIs are not registered identifiers, so the
                # same work legitimately carries one alongside a publisher DOI.
                da = ra.doi if ra.doi and not dedup.is_jstor_internal_doi(ra.doi) else None
                db = rb.doi if rb.doi and not dedup.is_jstor_internal_doi(rb.doi) else None
                if da and db and da != db:
                    continue
                if dedup.title_similarity(ra.title, rb.title) >= title_threshold:
                    final[ka] = merge_records(ra, rb)
                    del final[kb]
                    merged_into[kb] = ka
                    stats["merged_fuzzy"] += 1

    # Keys may have been rewritten by merges; collapse once more on identity.
    collapsed: dict[str, Record] = {}
    for r in final.values():
        k = r.key or record_key(r)
        if k in collapsed:
            collapsed[k] = merge_records(collapsed[k], r)
            stats["merged_exact"] += 1
        else:
            collapsed[k] = r

    out = list(collapsed.values())
    stats["output"] = len(out)
    stats["duplicates_removed"] = stats["input"] - stats["output"]
    return out, stats
