#!/usr/bin/env python3
"""
ScholarFocus — researcher network & interest profiler.

Usage:
    python src/scholarfocus.py --researchers "Jane Doe" "0000-0002-1234-5678" \\
        --config config.yaml --output markdown

Outputs a structured profile to stdout (JSON or Markdown).
Progress/warnings go to stderr so stdout is always machine-parseable in JSON mode.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Optional

import yaml

# Local imports (run from repo root or src/ on PYTHONPATH)
try:
    from apis.openalex import OpenAlexClient
    from apis.semantic_scholar import SemanticScholarClient
    from apis.core_api import COREClient
    import analyze
except ImportError:
    # Allow running as `python scholarfocus.py` from inside src/
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    from apis.openalex import OpenAlexClient
    from apis.semantic_scholar import SemanticScholarClient
    from apis.core_api import COREClient
    import analyze


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(levelname)s  %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_CFG: dict = {
    "apis": {
        "openalex": {"email": None},
        "semantic_scholar": {"api_key": None},
        "crossref": {"email": None},
        "core": {"api_key": None},
    },
    "analysis": {
        "max_works_per_researcher": 100,
        "max_references_per_work": 50,
        "top_n_partners": 15,
        "top_n_cited_works": 20,
        "top_n_keywords": 25,
        "min_collaboration_count": 2,
        "concept_min_level": 1,
        "concept_max_level": 4,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base (in place) and return base."""
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def load_config(path: Optional[str]) -> dict:
    cfg = _deep_merge({}, DEFAULT_CFG)
    if path:
        p = Path(path)
        if p.exists():
            with open(p) as f:
                user_cfg = yaml.safe_load(f) or {}
            _deep_merge(cfg, user_cfg)
        else:
            logger.warning("Config file not found: %s — using defaults", path)
    return cfg


# ---------------------------------------------------------------------------
# Researcher resolution
# ---------------------------------------------------------------------------

def resolve_researcher(identifier: str, oa: OpenAlexClient, s2: SemanticScholarClient) -> dict:
    """
    Try to find a researcher in OpenAlex (primary) then S2AG (fallback).
    Returns a dict with at least: source, id, name.
    Raises ValueError if not found anywhere.
    """
    logger.info("Resolving researcher: %s", identifier)
    author = oa.get_author(identifier)
    if author:
        logger.info("  Found in OpenAlex: %s (%s)", author.get("display_name"), author.get("id"))
        return {"source": "openalex", "data": author}

    logger.warning("  Not found in OpenAlex, trying Semantic Scholar…")
    s2_author = s2.get_author(identifier)
    if s2_author:
        logger.info("  Found in S2AG: %s", s2_author.get("name"))
        return {"source": "semantic_scholar", "data": s2_author}

    raise ValueError(f"Could not resolve researcher '{identifier}' in any API")


# ---------------------------------------------------------------------------
# Works fetching
# ---------------------------------------------------------------------------

def fetch_works_openalex(author_id: str, oa: OpenAlexClient, max_works: int) -> list[dict]:
    logger.info("  Fetching up to %d works from OpenAlex…", max_works)
    works = oa.get_author_works(author_id, max_works=max_works)
    logger.info("  Retrieved %d works", len(works))
    return works


def fetch_works_s2ag(s2_author_id: str, s2: SemanticScholarClient, max_works: int) -> list[dict]:
    logger.info("  Fetching up to %d papers from Semantic Scholar…", max_works)
    papers = s2.get_author_papers(s2_author_id, max_papers=max_works)
    logger.info("  Retrieved %d papers", len(papers))
    # Normalise to a structure compatible with OpenAlex enough for analysis
    normalised = []
    for p in papers:
        authors_raw = p.get("authors", [])
        authorships = [
            {
                "author": {"id": f"s2:{a.get('authorId','')}", "display_name": a.get("name","")},
                "institutions": [],
            }
            for a in authors_raw
        ]
        normalised.append(
            {
                "id": f"s2:{p.get('paperId','')}",
                "doi": (p.get("externalIds") or {}).get("DOI"),
                "title": p.get("title"),
                "publication_year": p.get("year"),
                "authorships": authorships,
                "concepts": [],
                "topics": [{"display_name": f} for f in (p.get("fieldsOfStudy") or [])],
                "keywords": [],
                "referenced_works": [],  # would need separate calls per paper
                "cited_by_count": p.get("citationCount", 0),
                "abstract": p.get("abstract"),
            }
        )
    return normalised


def enrich_abstracts_s2(
    works: list[dict], s2: SemanticScholarClient, max_refs: int
) -> list[dict]:
    """
    For works that have a DOI but no abstract, try S2AG.
    Also attempt to fill referenced_works from S2AG when missing.
    Limits API calls to works with the most citations.
    """
    top_works = sorted(works, key=lambda w: w.get("cited_by_count", 0), reverse=True)[:50]
    enriched = 0
    for w in top_works:
        doi = w.get("doi")
        has_abstract = bool(
            w.get("abstract")
            or w.get("abstract_inverted_index")
        )
        has_refs = bool(w.get("referenced_works"))
        if has_abstract and has_refs:
            continue
        if not doi:
            continue
        paper = s2.get_paper(f"DOI:{doi}")
        if not paper:
            continue
        if not has_abstract and paper.get("abstract"):
            w["abstract"] = paper["abstract"]
            enriched += 1
        if not has_refs:
            refs = s2.get_paper_references(paper["paperId"], max_refs=max_refs)
            for ref in refs:
                ref_doi = (ref.get("externalIds") or {}).get("DOI")
                if ref_doi:
                    w.setdefault("referenced_works", []).append(f"doi:{ref_doi}")
    if enriched:
        logger.info("  Enriched %d abstracts from S2AG", enriched)
    return works


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def format_markdown(result: dict) -> str:
    lines: list[str] = []

    def section(title: str):
        lines.append(f"\n## {title}\n")

    researchers = result.get("researchers", [])
    title_names = " & ".join(r["profile"]["name"] or "Unknown" for r in researchers)
    lines.append(f"# ScholarFocus Report: {title_names}\n")
    lines.append(f"*Generated by ScholarFocus using OpenAlex / S2AG / CrossRef / CORE*\n")

    # Per-researcher summaries
    for r in researchers:
        p = r["profile"]
        section(f"Researcher: {p.get('name', 'Unknown')}")
        if p.get("orcid"):
            lines.append(f"- **ORCID:** {p['orcid']}")
        if p.get("institutions"):
            lines.append(f"- **Institutions:** {', '.join(p['institutions'])}")
        lines.append(f"- **Works:** {p.get('works_count', '?')}  |  "
                     f"**Citations:** {p.get('cited_by_count', '?')}  |  "
                     f"**h-index:** {p.get('h_index', '?')}")

        if r.get("keywords"):
            section("Research Interests (keywords)")
            for kw in r["keywords"]:
                lines.append(f"- **{kw['keyword']}** (score: {kw['score']}, {kw['work_count']} works)")

        if r.get("partners"):
            section("Main Research Partners (co-authors)")
            for p_ in r["partners"]:
                inst_str = f" — {', '.join(p_['institutions'][:2])}" if p_.get("institutions") else ""
                lines.append(
                    f"- **{p_['name']}**{inst_str}  ·  {p_['collaboration_count']} co-authored works"
                )

    # Cited works (global, merged across researchers)
    if result.get("cited_works"):
        section("Top Cited External Works")
        for i, w in enumerate(result["cited_works"], 1):
            authors = ", ".join(w.get("authors", [])[:3])
            if len(w.get("authors", [])) > 3:
                authors += " et al."
            doi_str = f" [DOI: {w['doi']}]" if w.get("doi") else ""
            lines.append(
                f"{i}. **{w.get('title', 'Unknown title')}**  \n"
                f"   {authors} ({w.get('year', '?')}){doi_str}  \n"
                f"   *Cited by focal researcher(s): {w.get('cited_by_focal_researchers', '?')}  "
                f"|  Global citations: {w.get('global_citation_count', '?')}*\n"
            )

    # Network connections (multi-researcher only)
    net = result.get("network_summary", {})
    if net:
        if net.get("shared_keywords"):
            section("Shared Research Interests")
            for kw in net["shared_keywords"][:15]:
                lines.append(f"- **{kw['keyword']}** (combined score: {kw['combined_score']})")

        if net.get("shared_collaborators"):
            section("Shared Collaborators")
            for p_ in net["shared_collaborators"]:
                lines.append(f"- **{p_['name']}** ({p_.get('collaboration_count', '?')} works)")

    return "\n".join(lines)


def format_json(result: dict) -> str:
    # Remove internal-only fields before serialising
    clean = dict(result)
    for r in clean.get("researchers", []):
        r.pop("top_external_ref_ids", None)
        r.pop("ext_cite_counts", None)
    return json.dumps(clean, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def run(identifiers: list[str], cfg: dict, output_format: str) -> str:
    api_cfg  = cfg.get("apis", {})
    analysis_cfg = cfg.get("analysis", {})
    max_works = analysis_cfg.get("max_works_per_researcher", 100)
    max_refs  = analysis_cfg.get("max_references_per_work", 50)
    top_n_cited = analysis_cfg.get("top_n_cited_works", 20)

    oa   = OpenAlexClient(email=api_cfg.get("openalex", {}).get("email"))
    s2   = SemanticScholarClient(api_key=api_cfg.get("semantic_scholar", {}).get("api_key"))
    core = COREClient(api_key=api_cfg.get("core", {}).get("api_key"))
    # CrossRef (CrossRefClient) is available for import in agent-driven enrichment steps

    researcher_results: list[dict] = []
    all_researcher_author_ids: set[str] = set()
    all_researcher_work_ids: set[str] = set()

    # --- Phase 1: Resolve & fetch ---
    for ident in identifiers:
        try:
            resolved = resolve_researcher(ident, oa, s2)
        except ValueError as e:
            logger.error("%s — skipping", e)
            continue

        source = resolved["source"]
        author = resolved["data"]
        author_id = author.get("id") or ""
        all_researcher_author_ids.add(author_id)

        if source == "openalex":
            works = fetch_works_openalex(author_id, oa, max_works)
        else:
            s2_id = author.get("authorId") or ""
            works = fetch_works_s2ag(s2_id, s2, max_works)

        # Enrich with S2AG if OpenAlex is primary (adds abstracts + some refs)
        if source == "openalex" and s2:
            works = enrich_abstracts_s2(works, s2, max_refs)

        # Enrich missing abstracts from CORE
        if core.api_key:
            core.enrich_missing_abstracts(works)

        # Collect all work IDs (to filter self-citations later)
        for w in works:
            wid = w.get("id") or ""
            if wid:
                all_researcher_work_ids.add(wid)

        researcher_results.append(
            {"author": author, "works": works}
        )

    if not researcher_results:
        raise RuntimeError("No researchers could be resolved from any API.")

    # --- Phase 2: Analyse ---
    analysed: list[dict] = []
    all_ext_ref_ids: set[str] = set()

    for rr in researcher_results:
        result = analyze.build_researcher_profile(
            author=rr["author"],
            works=rr["works"],
            researcher_work_ids=all_researcher_work_ids,
            researcher_author_ids=all_researcher_author_ids,
            cfg=analysis_cfg,
        )
        all_ext_ref_ids.update(result["top_external_ref_ids"])
        analysed.append(result)

    # --- Phase 3: Fetch cited-work metadata ---
    logger.info("Fetching metadata for top %d external cited works…", len(all_ext_ref_ids))
    # Only fetch OpenAlex IDs (skip s2: or doi: prefixed IDs from S2AG fallback path)
    oa_ref_ids = [i for i in all_ext_ref_ids if i.startswith("https://openalex.org/")]
    fetched_works = oa.get_works_batch(oa_ref_ids[:200])  # cap at 200 IDs

    cited_works = analyze.compile_cited_works(analysed, fetched_works, top_n=top_n_cited)

    # --- Phase 4: Network summary ---
    network_summary = analyze.build_network_summary(analysed) if len(analysed) > 1 else {}

    # Assemble final result
    final = {
        "researchers": [
            {
                "profile":   r["profile"],
                "keywords":  r["keywords"],
                "partners":  r["partners"],
                # Keep internal fields; format_json strips them
                "top_external_ref_ids": r["top_external_ref_ids"],
                "ext_cite_counts":      r["ext_cite_counts"],
            }
            for r in analysed
        ],
        "cited_works": cited_works,
        "network_summary": network_summary,
    }

    if output_format == "json":
        return format_json(final)
    return format_markdown(final)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="ScholarFocus — build a researcher network profile from open bibliographic APIs."
    )
    parser.add_argument(
        "--researchers",
        nargs="+",
        required=True,
        metavar="NAME_OR_ORCID",
        help="Researcher display name(s) or ORCID IDs (e.g. '0000-0002-1234-5678')",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        metavar="PATH",
        help="Path to YAML config file (default: config.yaml)",
    )
    parser.add_argument(
        "--output",
        choices=["markdown", "json"],
        default="markdown",
        help="Output format (default: markdown)",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
    )
    args = parser.parse_args()

    logging.getLogger().setLevel(args.log_level)

    cfg = load_config(args.config)
    try:
        output = run(args.researchers, cfg, args.output)
        print(output)
    except RuntimeError as e:
        logger.error("ScholarFocus failed: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
