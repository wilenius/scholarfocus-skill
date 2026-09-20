"""
Analysis logic for ScholarFocus.

Takes raw data collected from APIs and produces structured summaries:
- research_partners:  ranked co-authors
- cited_works:        frequently cited external works
- research_interests: aggregated keywords/concepts/topics
- network_summary:    cross-researcher connections (when multiple researchers)
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _author_display_name(authorship: dict) -> str:
    author = authorship.get("author") or {}
    return author.get("display_name") or ""


def _author_id(authorship: dict) -> str:
    author = authorship.get("author") or {}
    return author.get("id") or ""


def _institutions(authorship: dict) -> list[str]:
    return [
        inst.get("display_name", "")
        for inst in (authorship.get("institutions") or [])
        if inst.get("display_name")
    ]


# ---------------------------------------------------------------------------
# Per-researcher analysis
# ---------------------------------------------------------------------------

def build_researcher_profile(
    author: dict,
    works: list[dict],
    researcher_work_ids: set[str],
    researcher_author_ids: set[str],
    cfg: dict,
) -> dict:
    """
    Derive research partners, cited external works, and keywords from the
    researcher's OpenAlex author record and their list of works.

    Args:
        author:               OpenAlex author dict
        works:                list of OpenAlex work dicts for this author
        researcher_work_ids:  set of all work IDs belonging to any focal researcher
                              (used to filter self/group citations)
        researcher_author_ids: set of OpenAlex author IDs for all focal researchers
        cfg:                  analysis config dict

    Returns a dict with keys:
        profile, partners, cited_works, keywords
    """
    top_n_partners  = cfg.get("top_n_partners", 15)
    top_n_cited     = cfg.get("top_n_cited_works", 20)
    top_n_keywords  = cfg.get("top_n_keywords", 25)
    min_collab      = cfg.get("min_collaboration_count", 2)
    concept_min_lvl = cfg.get("concept_min_level", 1)
    concept_max_lvl = cfg.get("concept_max_level", 4)

    # ---- Profile ----
    summary = author.get("summary_stats") or {}
    profile = {
        "id": author.get("id"),
        "name": author.get("display_name"),
        "orcid": author.get("orcid"),
        "works_count": author.get("works_count", 0),
        "cited_by_count": author.get("cited_by_count", 0),
        "h_index": summary.get("h_index"),
        "i10_index": summary.get("i10_index"),
        "institutions": [
            inst.get("display_name")
            for inst in (author.get("last_known_institutions") or [])
            if inst.get("display_name")
        ],
    }

    # ---- Co-authors (research partners) ----
    partner_counts: Counter = Counter()
    partner_meta: dict[str, dict] = {}

    for work in works:
        for auth in work.get("authorships", []):
            aid = _author_id(auth)
            if not aid or aid in researcher_author_ids:
                continue
            partner_counts[aid] += 1
            if aid not in partner_meta:
                partner_meta[aid] = {
                    "id": aid,
                    "name": _author_display_name(auth),
                    "institutions": _institutions(auth),
                }

    partners = []
    for aid, count in partner_counts.most_common():
        if count < min_collab:
            break
        if len(partners) >= top_n_partners:
            break
        meta = partner_meta[aid]
        partners.append({**meta, "collaboration_count": count})

    # ---- External citations ----
    # Count how often each external work ID is cited across the corpus.
    ext_cite_counts: Counter = Counter()
    for work in works:
        for ref_id in work.get("referenced_works", []):
            if ref_id not in researcher_work_ids:
                ext_cite_counts[ref_id] += 1

    top_ref_ids = [wid for wid, _ in ext_cite_counts.most_common(top_n_cited * 2)]

    # ---- Keywords / concepts / topics ----
    # Aggregate concept scores across all works.
    concept_score_sum: defaultdict[str, float] = defaultdict(float)
    concept_work_count: Counter = Counter()
    concept_level: dict[str, int] = {}

    topic_score_sum: defaultdict[str, float] = defaultdict(float)
    topic_work_count: Counter = Counter()

    keyword_counts: Counter = Counter()

    for work in works:
        # OpenAlex concepts (being phased out but widely available)
        for c in work.get("concepts", []):
            level = c.get("level", 2)
            if not (concept_min_lvl <= level <= concept_max_lvl):
                continue
            name = c.get("display_name")
            score = c.get("score", 0.0)
            if not name:
                continue
            concept_score_sum[name] += score
            concept_work_count[name] += 1
            concept_level[name] = level

        # OpenAlex topics (newer, more specific)
        for t in work.get("topics", []):
            name = t.get("display_name")
            score = t.get("score", 0.0)
            if not name:
                continue
            topic_score_sum[name] += score
            topic_work_count[name] += 1
            # Also add subfield/field for context
            for key in ("subfield", "field"):
                parent = (t.get(key) or {}).get("display_name")
                if parent:
                    topic_score_sum[parent] += score * 0.5
                    topic_work_count[parent] += 1

        # OpenAlex keywords (free text, post-2024)
        for kw in work.get("keywords", []):
            text = kw.get("keyword") if isinstance(kw, dict) else str(kw)
            if text:
                keyword_counts[text] += 1

    # Merge concepts + topics into unified keyword list
    merged: defaultdict[str, float] = defaultdict(float)
    merged_works: Counter = Counter()
    merged_source: dict[str, str] = {}

    for name, score in concept_score_sum.items():
        merged[name] += score
        merged_works[name] += concept_work_count[name]
        merged_source[name] = "concept"

    for name, score in topic_score_sum.items():
        merged[name] += score
        merged_works[name] += topic_work_count[name]
        merged_source[name] = merged_source.get(name, "topic")

    for kw, count in keyword_counts.items():
        merged[kw] += count * 0.5  # treat keyword hits as moderate signal
        merged_works[kw] += count
        merged_source[kw] = merged_source.get(kw, "keyword")

    keywords = sorted(merged.items(), key=lambda x: x[1], reverse=True)[:top_n_keywords]
    keyword_list = [
        {
            "keyword": name,
            "score": round(score, 3),
            "work_count": merged_works[name],
            "source": merged_source.get(name, "unknown"),
        }
        for name, score in keywords
    ]

    return {
        "profile": profile,
        "partners": partners,
        "top_external_ref_ids": top_ref_ids,
        "ext_cite_counts": dict(ext_cite_counts),
        "keywords": keyword_list,
    }


# ---------------------------------------------------------------------------
# Multi-researcher network analysis
# ---------------------------------------------------------------------------

def build_network_summary(researcher_results: list[dict]) -> dict:
    """
    Given a list of per-researcher result dicts (from build_researcher_profile),
    find shared collaborators, shared cited works, and shared keywords.
    """
    if len(researcher_results) < 2:
        return {}

    # Shared collaborators: co-authors who appear with multiple focal researchers
    partner_id_sets: list[set[str]] = [
        {p["id"] for p in r["partners"]}
        for r in researcher_results
    ]
    shared_partner_ids = partner_id_sets[0].intersection(*partner_id_sets[1:])

    shared_partners = []
    seen: set[str] = set()
    for r in researcher_results:
        for p in r["partners"]:
            if p["id"] in shared_partner_ids and p["id"] not in seen:
                seen.add(p["id"])
                shared_partners.append(p)

    # Shared cited works
    cite_id_sets: list[set[str]] = [
        set(r["top_external_ref_ids"])
        for r in researcher_results
    ]
    shared_cited_ids = cite_id_sets[0].intersection(*cite_id_sets[1:])

    # Shared keywords (by name)
    kw_sets: list[set[str]] = [
        {k["keyword"] for k in r["keywords"]}
        for r in researcher_results
    ]
    shared_kws = kw_sets[0].intersection(*kw_sets[1:])

    # Aggregate shared keyword scores
    kw_score_total: defaultdict[str, float] = defaultdict(float)
    kw_work_total: Counter = Counter()
    for r in researcher_results:
        for k in r["keywords"]:
            if k["keyword"] in shared_kws:
                kw_score_total[k["keyword"]] += k["score"]
                kw_work_total[k["keyword"]] += k["work_count"]

    shared_keywords = sorted(kw_score_total.items(), key=lambda x: x[1], reverse=True)
    shared_keyword_list = [
        {"keyword": kw, "combined_score": round(s, 3), "combined_work_count": kw_work_total[kw]}
        for kw, s in shared_keywords
    ]

    return {
        "shared_collaborators": shared_partners,
        "shared_cited_work_ids": list(shared_cited_ids),
        "shared_keywords": shared_keyword_list,
    }


# ---------------------------------------------------------------------------
# Citation work metadata merging
# ---------------------------------------------------------------------------

def compile_cited_works(
    researcher_results: list[dict],
    fetched_works: list[dict],
    top_n: int = 20,
) -> list[dict]:
    """
    Merge citation counts across researchers, attach fetched metadata,
    and return ranked list of top cited external works.

    `fetched_works` should come from OpenAlexClient.get_works_batch().
    """
    # Aggregate citation counts across all researchers
    combined_counts: Counter = Counter()
    for r in researcher_results:
        for wid, count in r["ext_cite_counts"].items():
            combined_counts[wid] += count

    # Build a lookup from work ID to metadata
    work_meta: dict[str, dict] = {}
    for w in fetched_works:
        wid = w.get("id") or ""
        if wid:
            work_meta[wid] = w

    cited_works = []
    for wid, count in combined_counts.most_common():
        if len(cited_works) >= top_n:
            break
        meta = work_meta.get(wid, {})
        authors = []
        for auth in meta.get("authorships", [])[:5]:
            name = _author_display_name(auth)
            if name:
                authors.append(name)
        cited_works.append(
            {
                "openalex_id": wid,
                "doi": meta.get("doi"),
                "title": meta.get("title"),
                "year": meta.get("publication_year"),
                "authors": authors,
                "global_citation_count": meta.get("cited_by_count"),
                "cited_by_focal_researchers": count,
            }
        )

    return cited_works
