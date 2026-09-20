#!/usr/bin/env python3
"""Build a literature review on a topic.

Data goes to stdout; logs go to stderr, so JSON output stays parseable.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

from scholarlib.config import ConfigError, expand_path, load_config, warn_if_no_openalex_key
from scholarlib.http.budget import COSTS, BudgetExceeded
from scholarlib.pipeline import abstracts, cluster, jstor_coverage, oa_links, rank, seed, snowball
from scholarlib.pipeline.context import build_context
from scholarlib.records import Record, dedup_records
from scholarlib.render import bib, json_out, narrative, systematic

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger("litreview")

EXIT_OK, EXIT_ERR, EXIT_BUDGET, EXIT_CONFIG, EXIT_AMBIGUOUS, EXIT_INDEX = 0, 1, 2, 3, 4, 5


def _open_jstor(args, cfg, ctx) -> None:
    if args.no_jstor:
        return
    db = args.jstor_index or (cfg.get("jstor") or {}).get("db_path")
    if not db:
        return
    path = Path(str(db)).expanduser()
    if not path.exists():
        ctx.warn(f"JSTOR index not found at {path}; continuing without it. "
                 "Build it with: python -m scholarlib.cli.jstor_index build")
        return
    from scholarlib.jstor.query import JstorIndex
    try:
        ctx.jstor_index = JstorIndex(path)
    except Exception as e:
        ctx.warn(f"Could not open the JSTOR index ({e}); continuing without it.")


def _plan(args, ctx) -> list[tuple[str, str, int]]:
    """Credit plan for --dry-run, using the measured per-class costs."""
    mode = args.budget_mode or ctx.budget_mode
    queries = len(args.query or [])
    plan = [("seed searches", "search", queries)]
    if args.seed_doi:
        plan.append(("seed DOI singletons", "singleton", len(args.seed_doi)))
    if not args.no_forward:
        plan.append(("forward snowball pages", "list", max(1, args.depth * 4)))
    if not args.no_backward:
        klass = "singleton" if mode == "free" else "list"
        plan.append((f"backward hydration ({mode})", klass,
                     max(1, args.max_results // 50)))
    if not args.no_jstor:
        plan.append(("JSTOR join probes", "search", args.jstor_join_limit))
    return plan


def run(args, cfg) -> int:
    ctx = build_context(
        cfg, use_cache=not args.no_cache, refresh=args.refresh,
        budget_override=args.budget_credits, budget_mode=args.budget_mode,
        enable_s2ag=args.enable_s2ag,
    )
    warn_if_no_openalex_key(cfg)
    _open_jstor(args, cfg, ctx)

    if args.dry_run:
        plan = _plan(args, ctx)
        total = ctx.ledger.estimate(plan)
        remaining = ctx.ledger.remaining("openalex")
        print("Planned OpenAlex spend:")
        for label, klass, n in plan:
            print(f"  {label:<34} {n:>4} x {COSTS.get(klass, 0):>2} = "
                  f"{n * COSTS.get(klass, 0):>5} credits  ({klass})")
        print(f"  {'':<34} {'':>4}   {'':>2}   {'-' * 5}")
        print(f"  {'TOTAL':<34} {'':>4}   {'':>2}   {total:>5} credits")
        print(f"\nRemaining today: {remaining} / {ctx.ledger.cap('openalex')}")
        if remaining is not None and total > remaining:
            print("\nThis plan exceeds the remaining budget. Lower --jstor-join-limit "
                  "(each probe is a 10-credit search), reduce --query terms, or "
                  "prefer --seed-doi (free).")
            return EXIT_BUDGET
        print("\nWithin budget.")
        return EXIT_OK

    queries = list(args.query or [])
    types = args.types.split(",") if args.types else None
    languages = args.languages.split(",") if args.languages else None
    disciplines = args.jstor_disciplines.split(",") if args.jstor_disciplines else None

    pool: list[Record] = []
    try:
        # --- seed ---
        if args.seed_doi:
            pool += seed.seed_from_dois(ctx, args.seed_doi)
        if args.seed_openalex_id:
            pool += seed.seed_from_ids(ctx, args.seed_openalex_id)
        if args.zotero in ("seed", "both"):
            pool += seed.seed_from_zotero(ctx, args.seed_zotero_collection)
        if queries:
            pool += seed.seed_from_queries(
                ctx, queries, max_seeds=args.max_seeds,
                from_year=args.from_year, to_year=args.to_year,
                types=types, languages=languages)
        if not pool:
            logger.error("No seeds found. Give --query or --seed-doi.")
            return EXIT_ERR

        seeds, _ = dedup_records(pool)
        logger.info("Seeds after dedup: %d", len(seeds))

        # --- snowball ---
        found = snowball.snowball(
            ctx, seeds, depth=args.depth,
            do_forward=not args.no_forward, do_backward=not args.no_backward,
            do_lateral=args.lateral, max_per_seed=args.max_per_seed,
            from_year=args.from_year)
        corpus = seeds + found

        # --- JSTOR ---
        if ctx.jstor_index is not None:
            corpus = jstor_coverage.annotate(ctx, corpus)
            if queries and not args.no_jstor_discovery:
                corpus += jstor_coverage.discover(
                    ctx, queries, corpus, join_limit=args.jstor_join_limit,
                    disciplines=disciplines, from_year=args.from_year,
                    to_year=args.to_year, types=types)

        # --- dedup ---
        corpus, dstats = dedup_records(corpus)
        logger.info("Corpus: %d records after dedup (%d duplicates removed)",
                    len(corpus), dstats["duplicates_removed"])

        # --- enrich ---
        corpus = abstracts.assemble(ctx, corpus, level=args.abstracts,
                                    limit=args.abstract_limit)
        if not args.no_unpaywall:
            corpus = oa_links.enrich(ctx, corpus)

        # --- Zotero ownership ---
        if args.zotero in ("mark", "both"):
            z = ctx.clients["zotero"]
            if z.available():
                from scholarlib.apis.zotero_local import ZoteroLookup
                lookup = ZoteroLookup(z.load_library())
                ctx.zotero_lookup = lookup
                owned = 0
                for rec in corpus:
                    m = lookup.match(rec)
                    if m:
                        rec.in_zotero = True
                        rec.zotero_key = m.zotero_key
                        rec.zotero_citekey = m.zotero_citekey
                        owned += 1
                logger.info("Zotero: %d/%d records already in your library",
                            owned, len(corpus))
                ctx.stats["zotero_owned"] = owned

    except BudgetExceeded as e:
        ctx.truncated = ctx.truncated or {"reason": "budget", "stage": "retrieval",
                                          "detail": str(e)}
        ctx.warn(f"Budget exhausted: {e}")
        if not pool:
            logger.error("Budget exhausted before anything was retrieved.")
            return EXIT_BUDGET
        corpus, dstats = dedup_records(pool)

    # --- rank, screen, cluster ---
    corpus = rank.score(ctx, corpus, mode=args.mode, queries=queries, seeds=seeds)
    kept, excluded = rank.screen(
        ctx, corpus, include_if=args.include_if, exclude_if=args.exclude_if,
        min_score=args.min_score, max_results=args.max_results)
    method = args.cluster or ("topics" if args.mode == "narrative" else "none")
    clusters = cluster.cluster(ctx, kept, method=method)
    timeline = cluster.timeline(ctx, kept)

    result = json_out.build_result(
        ctx, kept, mode=args.mode,
        query={"queries": queries, "seed_dois": args.seed_doi or [],
               "from_year": args.from_year, "to_year": args.to_year,
               "types": types, "languages": languages, "depth": args.depth,
               "include_if": args.include_if or [], "exclude_if": args.exclude_if or []},
        clusters=clusters, excluded=excluded, dedup_stats=dstats, timeline=timeline)

    # --- output ---
    outputs = {
        "json": lambda: json_out.dumps(result),
        "markdown": lambda: (narrative.render(result) if args.mode == "narrative"
                             else systematic.render(result)),
        "bibtex": lambda: bib.to_bibtex(kept),
        "csl": lambda: bib.to_csl_json(kept),
        "ris": lambda: bib.to_ris(kept),
    }
    if args.out_dir:
        d = Path(args.out_dir).expanduser()
        d.mkdir(parents=True, exist_ok=True)
        ext = {"json": "json", "markdown": "md", "bibtex": "bib", "csl": "json", "ris": "ris"}
        for name, fn in outputs.items():
            p = d / f"review.{ext[name]}" if name != "csl" else d / "review.csl.json"
            p.write_text(fn(), encoding="utf-8")
            logger.info("Wrote %s", p)
    else:
        print(outputs[args.output]())

    b = result["budget"]
    logger.info("OpenAlex: %s credits spent, %s saved by cache, %s remaining today",
                b.get("spent_session"), b.get("credits_saved_by_cache"), b.get("remaining"))
    return EXIT_BUDGET if ctx.truncated else EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="litreview",
        description="Build a literature review on a topic or research question.")

    g = p.add_argument_group("seeding")
    g.add_argument("--query", action="append", help="Topic query (repeatable)")
    g.add_argument("--seed-doi", action="append", help="Known key work by DOI (free)")
    g.add_argument("--seed-openalex-id", action="append", help="Known key work by OpenAlex ID")
    g.add_argument("--seed-zotero-collection", help="Zotero collection name to seed from")
    g.add_argument("--max-seeds", type=int, default=50)

    g = p.add_argument_group("scope")
    g.add_argument("--mode", choices=["narrative", "systematic"], default="narrative")
    g.add_argument("--from-year", "--since", dest="from_year", type=int)
    g.add_argument("--to-year", type=int)
    g.add_argument("--languages", help="Comma-separated language codes")
    g.add_argument("--types", help="Comma-separated work types")
    g.add_argument("--include-if", action="append", help="e.g. 'year>=2000' (systematic)")
    g.add_argument("--exclude-if", action="append", help="e.g. 'type==preprint'")

    g = p.add_argument_group("retrieval")
    g.add_argument("--depth", "--snowball-depth", dest="depth", type=int, default=1)
    g.add_argument("--no-forward", action="store_true")
    g.add_argument("--no-backward", action="store_true")
    g.add_argument("--lateral", action="store_true",
                   help="Expand via related_works (the only recall route for books)")
    g.add_argument("--max-per-seed", type=int, default=100)
    g.add_argument("--max-results", "--max-works", dest="max_results", type=int, default=200)

    g = p.add_argument_group("jstor")
    g.add_argument("--jstor-index", help="Path to jstor.sqlite")
    g.add_argument("--no-jstor", action="store_true")
    g.add_argument("--no-jstor-discovery", action="store_true")
    g.add_argument("--jstor-join-limit", type=int, default=25,
                   help="Max OpenAlex title probes; each costs 10 credits (default 25)")
    g.add_argument("--jstor-disciplines", help="Comma-separated discipline filter")

    g = p.add_argument_group("enrichment")
    g.add_argument("--abstracts", choices=["none", "cheap", "full"], default="cheap")
    g.add_argument("--abstract-limit", type=int, default=200)
    g.add_argument("--no-unpaywall", action="store_true")
    g.add_argument("--zotero", choices=["off", "mark", "seed", "both"], default="mark")
    g.add_argument("--enable-s2ag", action="store_true")

    g = p.add_argument_group("output")
    g.add_argument("--cluster", choices=["none", "topics", "cocitation", "both"])
    g.add_argument("--min-score", type=float)
    g.add_argument("--output", choices=["markdown", "json", "bibtex", "csl", "ris"],
                   default="markdown")
    g.add_argument("--out-dir", help="Write every format into this directory")

    g = p.add_argument_group("budget")
    g.add_argument("--budget-credits", type=int)
    g.add_argument("--budget-mode", choices=["free", "fast"], default=None)
    g.add_argument("--dry-run", action="store_true",
                   help="Print the credit plan and exit without spending")
    g.add_argument("--no-cache", action="store_true")
    g.add_argument("--refresh", action="store_true")

    p.add_argument("--config")
    p.add_argument("--log-level", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p


def main() -> None:
    args = build_parser().parse_args()
    logging.getLogger().setLevel(getattr(logging, args.log_level))
    if not (args.query or args.seed_doi or args.seed_openalex_id
            or args.seed_zotero_collection):
        logger.error("Nothing to search for: give --query, --seed-doi, "
                     "--seed-openalex-id or --seed-zotero-collection")
        sys.exit(EXIT_ERR)
    try:
        cfg = load_config(args.config)
        sys.exit(run(args, cfg))
    except ConfigError as e:
        logger.error("Configuration problem: %s", e)
        sys.exit(EXIT_CONFIG)
    except BudgetExceeded as e:
        logger.error("Budget exhausted: %s", e)
        sys.exit(EXIT_BUDGET)
    except KeyboardInterrupt:
        logger.error("Interrupted")
        sys.exit(EXIT_ERR)
    except Exception as e:
        logger.error("Failed: %s", e)
        logger.debug("traceback", exc_info=True)
        sys.exit(EXIT_ERR)


if __name__ == "__main__":
    main()
