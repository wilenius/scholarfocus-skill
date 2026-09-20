#!/usr/bin/env python3
"""
ScholarFocus — researcher network & interest profiler.

Usage:
    python -m scholarlib.cli.scholarfocus --researchers "Jane Doe" "0000-0002-1234-5678" \\
        --config config.yaml --output markdown

Outputs a structured profile to stdout (JSON or Markdown).
Progress/warnings go to stderr so stdout is always machine-parseable in JSON mode.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Optional


from scholarlib.apis.openalex import WORK_FIELDS, OpenAlexClient
from scholarlib.apis.semantic_scholar import SemanticScholarClient
from scholarlib.apis.core_api import COREClient
from scholarlib import dedup
from scholarlib.config import ConfigError, load_config, warn_if_no_openalex_key
from scholarlib.http.budget import BudgetExceeded
from scholarlib.pipeline.context import build_context
from scholarlib.pipeline.disambiguate import (
    AmbiguousAuthor, resolve_author, resolve_via_orcid)
from scholarlib.pipeline import profile as analyze


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(levelname)s  %(message)s",
)
logger = logging.getLogger(__name__)

EXIT_OK, EXIT_ERR, EXIT_BUDGET, EXIT_CONFIG, EXIT_AMBIGUOUS = 0, 1, 2, 3, 4


# ---------------------------------------------------------------------------
# Researcher resolution
# ---------------------------------------------------------------------------

def _fill_missing_abstracts(core, works: list[dict]) -> int:
    """Fill abstracts from CORE for works that still lack one."""
    filled = 0
    for w in works:
        if w.get("abstract") or w.get("abstract_inverted_index"):
            continue
        doi = w.get("doi")
        if not doi:
            continue
        text = core.get_abstract(doi)
        if text:
            w["abstract"] = text
            filled += 1
    if filled:
        logger.info("  CORE filled %d missing abstracts", filled)
    return filled


def resolve_researcher(identifier: str, oa, s2, *, orcid=None,
                       hints: Optional[dict] = None,
                       min_works: int = 5) -> dict:
    """Resolve a name or ORCID to one author.

    OpenAlex is tried first. When it comes back thin or ambiguous, ORCID is
    consulted: its records are self-registered and self-curated, so it is
    markedly better for researchers OpenAlex has fragmented.
    """
    hints = hints or {}
    logger.info("Resolving researcher: %s", identifier)

    author = None
    ambiguous: Optional[AmbiguousAuthor] = None
    try:
        author = resolve_author(
            oa, identifier,
            institution=hints.get("institution"),
            field_hint=hints.get("field"),
            active_years=hints.get("active_years"),
            author_id=hints.get("author_id"),
        )
    except AmbiguousAuthor as e:
        ambiguous = e
    except ValueError as e:
        logger.info("  OpenAlex: %s", e)

    thin = (author or {}).get("works_count", 0) < min_works
    if orcid is not None and not hints.get("author_id") and (author is None or thin or ambiguous):
        why = ("ambiguous in OpenAlex" if ambiguous
               else "not found in OpenAlex" if author is None
               else f"only {author.get('works_count', 0)} work(s) in OpenAlex")
        logger.info("  %s — trying ORCID", why)
        via = resolve_via_orcid(oa, orcid, identifier,
                                institution=hints.get("institution"))
        if via is not None:
            logger.info("  Resolved via ORCID: %s", via.get("display_name"))
            return {"source": "orcid", "data": via}

    if author is None:
        if ambiguous:
            raise ambiguous
        raise ValueError(f"Could not resolve researcher {identifier!r}")

    logger.info("  Found in OpenAlex: %s (%s)",
                author.get("display_name"), author.get("id"))
    if (author.get("works_count") or 0) < min_works:
        logger.warning(
            "  %s has only %d work(s) in OpenAlex; the profile will be thin.",
            author.get("display_name"), author.get("works_count") or 0)
    return {"source": "openalex", "data": author}


# ---------------------------------------------------------------------------
# Works fetching
# ---------------------------------------------------------------------------

def _orcid_id_of(author: dict) -> Optional[str]:
    o = author.get("orcid")
    return str(o).replace("https://orcid.org/", "").strip() if o else None


def _hydrate_orcid_works(records, oa, max_works: int) -> list[dict]:
    """Turn ORCID Records into OpenAlex-shaped works.

    DOIs are resolved through OpenAlex singleton lookups, which cost 0 credits,
    so a full curated bibliography can be hydrated for free. Works with no DOI,
    or no OpenAlex counterpart, are kept as minimal stubs rather than dropped --
    they are often the non-English material that makes ORCID worth consulting.
    """
    works: list[dict] = []
    hydrated = 0
    for rec in records[:max_works]:
        w = None
        if rec.doi:
            w = oa.get_work_by_doi(rec.doi, select=WORK_FIELDS)
        if w:
            works.append(w)
            hydrated += 1
        else:
            works.append({
                "id": f"orcid:{rec.doi or rec.title}",
                "doi": rec.doi,
                "title": rec.title,
                "publication_year": rec.year,
                "type": rec.type,
                "authorships": [],
                "concepts": [], "topics": [], "keywords": [],
                "referenced_works": [],
                "cited_by_count": 0,
            })
    logger.info("  ORCID: %d/%d works hydrated from OpenAlex (0 credits)",
                hydrated, len(works))
    return works


def verify_author_against_works(author: dict, works: list[dict],
                                orcid_client=None) -> dict:
    """Scope an ORCID-resolved profile to the ORCID work list.

    OpenAlex author records are sometimes conflations of two researchers who
    share a surname and initial. A5037591905 "T Tammisto" holds 68 anaesthesia
    papers *and* 22 anthropology papers, so an authorship check cannot detect
    the problem -- the ID legitimately appears on both.

    When the work list came from ORCID, that list is the authoritative scope.
    Metrics are therefore computed from those works, and the OpenAlex record's
    aggregates (works_count, h-index) are discarded rather than reported for a
    researcher who did not earn them.
    """
    author_id = str(author.get("id") or "")
    hydrated = [w for w in works if not str(w.get("id", "")).startswith("orcid:")]
    orcid_id = _orcid_id_of(author)

    oa_works = author.get("works_count") or 0
    if author_id.startswith("https://openalex.org/") and oa_works > max(10, 2 * len(works)):
        logger.warning(
            "OpenAlex author %s (%s) lists %d works, but ORCID %s lists %d. "
            "That record is probably a conflation of more than one researcher, "
            "so its citation metrics are being discarded rather than attributed "
            "to the wrong person.",
            author_id.rsplit("/", 1)[-1], author.get("display_name"),
            oa_works, orcid_id, len(works),
        )

    name = author.get("display_name")
    institutions = author.get("last_known_institutions") or []
    if orcid_client is not None and orcid_id:
        try:
            person = orcid_client.get_person(orcid_id) or {}
            given = (((person.get("name") or {}).get("given-names") or {}).get("value"))
            family = (((person.get("name") or {}).get("family-name") or {}).get("value"))
            if given or family:
                name = " ".join(x for x in (given, family) if x)
            emp = orcid_client.get_employments(orcid_id)
            if emp:
                institutions = [{"display_name": e} for e in emp]
        except Exception as e:
            logger.debug("ORCID person lookup failed: %s", e)

    return {
        "id": author.get("id"),
        "display_name": name,
        "orcid": author.get("orcid") or (f"https://orcid.org/{orcid_id}" if orcid_id else None),
        "works_count": len(works),
        "cited_by_count": sum((w.get("cited_by_count") or 0) for w in hydrated),
        "last_known_institutions": institutions,
        "summary_stats": {},
        "_scope": "orcid",
        "_openalex_works_count": oa_works or None,
    }


def fetch_works_orcid(author: dict, oa, orcid, max_works: int) -> list[dict]:
    oid = _orcid_id_of(author)
    if not oid or orcid is None:
        return []
    return _hydrate_orcid_works(orcid.get_works(oid), oa, max_works)


def merge_orcid_works(works: list[dict], author: dict, oa, orcid,
                      max_works: int) -> list[dict]:
    """Add ORCID-listed works that OpenAlex's author record missed."""
    oid = _orcid_id_of(author)
    if not oid or orcid is None:
        return works
    try:
        orcid_recs = orcid.get_works(oid)
    except Exception as e:
        logger.debug("ORCID works lookup failed: %s", e)
        return works
    if not orcid_recs:
        return works

    have_dois = {dedup.normalize_doi(w.get("doi")) for w in works}
    have_dois.discard(None)
    have_titles = {dedup.title_main(w.get("title")) for w in works}
    missing = [r for r in orcid_recs
               if (r.doi not in have_dois)
               and (dedup.title_main(r.title) not in have_titles)]
    if not missing:
        return works
    room = max(0, max_works - len(works))
    if room <= 0:
        return works
    extra = _hydrate_orcid_works(missing[:room], oa, room)
    logger.info("  ORCID added %d work(s) OpenAlex did not list", len(extra))
    return works + extra


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
    top_works = sorted(works, key=lambda w: (w.get("cited_by_count") or 0), reverse=True)[:50]
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
        paper = s2.get_paper_by_doi(doi)
        if not paper:
            continue
        if not has_abstract and paper.get("abstract"):
            w["abstract"] = paper["abstract"]
            enriched += 1
        if not has_refs:
            refs = []  # reference lists come from OpenAlex referenced_works
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

def run(identifiers: list[str], cfg: dict, output_format: str,
        hints: Optional[dict] = None, *, use_cache: bool = True,
        enable_s2ag: bool = False) -> str:
    analysis_cfg = cfg.get("analysis", {})
    max_works = analysis_cfg.get("max_works_per_researcher", 100)
    max_refs  = analysis_cfg.get("max_references_per_work", 50)
    top_n_cited = analysis_cfg.get("top_n_cited_works", 20)

    ctx  = build_context(cfg, use_cache=use_cache, enable_s2ag=enable_s2ag)
    oa   = ctx.clients["openalex"]
    s2   = ctx.clients["semantic_scholar"]
    core = ctx.clients["core"]
    orcid = ctx.clients.get("orcid")
    warn_if_no_openalex_key(cfg)

    researcher_results: list[dict] = []
    all_researcher_author_ids: set[str] = set()
    all_researcher_work_ids: set[str] = set()

    # --- Phase 1: Resolve & fetch ---
    for ident in identifiers:
        try:
            resolved = resolve_researcher(ident, oa, s2, orcid=orcid, hints=hints)
        except ValueError as e:
            logger.error("%s — skipping", e)
            continue

        source = resolved["source"]
        author = resolved["data"]
        author_id = author.get("id") or ""
        all_researcher_author_ids.add(author_id)

        if source == "orcid":
            works = fetch_works_orcid(author, oa, orcid, max_works)
            author = verify_author_against_works(author, works, orcid)
            author_id = author.get("id") or ""
        elif source == "openalex":
            works = fetch_works_openalex(author_id, oa, max_works)
            # A curated ORCID list can be much fuller than OpenAlex's.
            if orcid is not None and author.get("orcid") and len(works) < max_works:
                works = merge_orcid_works(works, author, oa, orcid, max_works)
        else:
            s2_id = author.get("authorId") or ""
            works = fetch_works_s2ag(s2_id, s2, max_works)

        # Enrich with S2AG if OpenAlex is primary (adds abstracts + some refs)
        if source == "openalex" and s2:
            works = enrich_abstracts_s2(works, s2, max_refs)

        # Enrich missing abstracts from CORE
        if core.api_key:
            _fill_missing_abstracts(core, works)

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
    max_fetch = analysis_cfg.get("max_cited_works_fetched", 200)
    oa_ref_ids = [i for i in all_ext_ref_ids if i.startswith("https://openalex.org/")]
    fetched_works = oa.get_works_batch(oa_ref_ids[:max_fetch])

    # DOI-prefixed pseudo-IDs used to be discarded outright. OpenAlex singleton
    # lookups are free, so resolve them instead of throwing the data away.
    doi_ref_ids = [i for i in all_ext_ref_ids if i.startswith("doi:")]
    max_topups = analysis_cfg.get("max_singleton_topups", 50)
    for ref in doi_ref_ids[:max_topups]:
        w = oa.get_work_by_doi(ref[4:])
        if w:
            fetched_works.append(w)

    # Any top-ranked ID the batch call missed: free to retry individually.
    got = {w.get("id") for w in fetched_works}
    missed = [i for i in oa_ref_ids[:max_fetch] if i not in got][:max_topups]
    for ref in missed:
        w = oa.get_work(ref)
        if w:
            fetched_works.append(w)
    if missed:
        logger.info("  Topped up %d works the batch fetch missed (0 credits)", len(missed))

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
        "--researchers", nargs="+", required=True, metavar="NAME_OR_ORCID",
        help="Researcher display name(s) or ORCID IDs (e.g. '0000-0002-1234-5678')",
    )
    parser.add_argument("--config", metavar="PATH",
                        help="Path to YAML config file (default: config.yaml at the repo root)")
    parser.add_argument("--output", choices=["markdown", "json"], default="markdown",
                        help="Output format (default: markdown)")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        default="INFO", help="Verbosity, written to stderr (default: INFO)")

    g = parser.add_argument_group("disambiguation")
    g.add_argument("--author-id", help="Resolve directly to this OpenAlex author ID")
    g.add_argument("--field", help="Expected field, e.g. Anthropology")
    g.add_argument("--institution", help="Expected institution, e.g. Helsinki")
    g.add_argument("--active-years", help="Expected active period, e.g. 2005-2025")

    g = parser.add_argument_group("data sources")
    g.add_argument("--enable-s2ag", action="store_true",
                   help="Use Semantic Scholar for abstracts (off by default)")
    g.add_argument("--no-cache", action="store_true", help="Bypass the response cache")

    args = parser.parse_args()
    logging.getLogger().setLevel(args.log_level)

    active_years = None
    if args.active_years:
        try:
            lo, hi = args.active_years.split("-")
            active_years = (int(lo), int(hi))
        except ValueError:
            logger.error("--active-years must look like 2005-2025")
            sys.exit(EXIT_CONFIG)

    hints = {"author_id": args.author_id, "field": args.field,
             "institution": args.institution, "active_years": active_years}

    try:
        cfg = load_config(args.config)
        print(run(args.researchers, cfg, args.output, hints,
                  use_cache=not args.no_cache, enable_s2ag=args.enable_s2ag))
    except AmbiguousAuthor as e:
        # stdout stays machine-readable; the human-facing table goes to stderr.
        logger.error("Ambiguous author %r — refusing to guess. Candidates:", e.query)
        for c in e.candidates:
            inst = (c.institutions or ["—"])[0]
            logger.error("  %-42s  %-28s  %s", c.name[:42], inst[:28],
                         ", ".join(c.topics[:2]))
        logger.error("Re-run with --author-id <id>, or narrow with "
                     "--field / --institution.")
        print(json.dumps({"status": "ambiguous", "query": e.query,
                          "candidates": [c.to_dict() for c in e.candidates]},
                         indent=2, ensure_ascii=False))
        sys.exit(EXIT_AMBIGUOUS)
    except ConfigError as e:
        logger.error("Configuration problem: %s", e)
        sys.exit(EXIT_CONFIG)
    except BudgetExceeded as e:
        logger.error("OpenAlex daily credit budget exhausted: %s", e)
        sys.exit(EXIT_BUDGET)
    except KeyboardInterrupt:
        logger.error("Interrupted")
        sys.exit(EXIT_ERR)
    except Exception as e:
        logger.error("ScholarFocus failed: %s", e)
        logger.debug("traceback", exc_info=True)
        sys.exit(EXIT_ERR)


if __name__ == "__main__":
    main()
