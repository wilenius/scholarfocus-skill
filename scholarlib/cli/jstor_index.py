#!/usr/bin/env python3
"""Build and query the local JSTOR metadata index."""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path

from scholarlib.config import ConfigError, expand_path, load_config

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger("jstor-index")


def _db_path(args, cfg) -> Path:
    p = args.db or (cfg.get("jstor") or {}).get("db_path")
    if not p:
        raise ConfigError("No JSTOR db path: pass --db or set jstor.db_path in config.yaml")
    return Path(str(p)).expanduser()


def cmd_build(args, cfg) -> int:
    from scholarlib.jstor.build import build

    src = args.source or (cfg.get("jstor") or {}).get("source_path")
    if not src:
        raise ConfigError("No JSTOR source: pass --source or set jstor.source_path")
    disciplines = None
    if args.disciplines and not args.all_disciplines:
        disciplines = frozenset(d.strip() for d in args.disciplines.split(",") if d.strip())
    elif not args.all_disciplines:
        cfg_disc = (cfg.get("jstor") or {}).get("disciplines")
        if cfg_disc:
            disciplines = frozenset(cfg_disc)
    languages = frozenset(x.strip() for x in args.languages.split(",")) if args.languages else None

    stats = build(
        Path(str(src)).expanduser(),
        _db_path(args, cfg),
        disciplines=disciplines,
        languages=languages,
        keep_reviews=args.keep_reviews or (cfg.get("jstor") or {}).get("keep_reviews", False),
        resume=args.resume,
        force=args.force,
        progress_every=args.progress_every,
        limit=args.limit,
    )
    print(json.dumps(stats, indent=2))
    return 0


def cmd_stats(args, cfg) -> int:
    from scholarlib.jstor.query import JstorIndex

    idx = JstorIndex(_db_path(args, cfg))
    print(json.dumps(idx.stats(), indent=2))
    return 0


def cmd_search(args, cfg) -> int:
    from scholarlib.jstor.query import JstorIndex

    idx = JstorIndex(_db_path(args, cfg))
    rows = idx.search(
        args.text,
        disciplines=args.disciplines.split(",") if args.disciplines else None,
        types=args.types.split(",") if args.types else None,
        from_year=args.from_year,
        to_year=args.to_year,
        limit=args.limit or 20,
    )
    if args.json:
        print(json.dumps([r.to_dict() for r in rows], indent=2, ensure_ascii=False))
    else:
        for r in rows:
            who = r.first_surname or "?"
            print(f"{r.year or '????'}  {who:<18.18}  [{r.type or '?':<9.9}]  {r.title}")
            if r.jstor:
                print(f"        {r.jstor.get('url')}")
    return 0


def cmd_match(args, cfg) -> int:
    from scholarlib.jstor.query import JstorIndex
    from scholarlib.records import Record

    idx = JstorIndex(_db_path(args, cfg))
    probe = Record(title=args.title, authors=[args.author] if args.author else [], year=args.year)
    hit, score, method = idx.find_match(probe)
    print(json.dumps({
        "matched": hit is not None,
        "score": round(score, 3),
        "method": method,
        "record": hit.to_dict() if hit else None,
    }, indent=2, ensure_ascii=False))
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Build and query the local JSTOR metadata index.")
    ap.add_argument("--config", help="Path to config.yaml")
    ap.add_argument("--db", help="Path to the index database")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="Build or refresh the index")
    b.add_argument("--source", help="Path to the .jsonl.gz dump")
    b.add_argument("--disciplines", help="Comma-separated discipline allowlist")
    b.add_argument("--all-disciplines", action="store_true", help="Index every discipline")
    b.add_argument("--languages", help="Comma-separated ISO-639-2/B codes")
    b.add_argument("--keep-reviews", action="store_true",
                   help="Also index book reviews (~90%% are untitled)")
    b.add_argument("--resume", action="store_true", help="Continue an interrupted build")
    b.add_argument("--force", action="store_true", help="Rebuild even if up to date")
    b.add_argument("--progress-every", type=int, default=250_000)
    b.add_argument("--limit", type=int, help="Stop after N kept records (for testing)")
    b.set_defaults(func=cmd_build)

    s = sub.add_parser("stats", help="Show what is in the index")
    s.set_defaults(func=cmd_stats)

    q = sub.add_parser("search", help="Full-text search the index")
    q.add_argument("--text", required=True)
    q.add_argument("--disciplines")
    q.add_argument("--types")
    q.add_argument("--from-year", type=int)
    q.add_argument("--to-year", type=int)
    q.add_argument("--limit", type=int)
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=cmd_search)

    m = sub.add_parser("match", help="Find the JSTOR record matching a title/author/year")
    m.add_argument("--title", required=True)
    m.add_argument("--author")
    m.add_argument("--year", type=int)
    m.set_defaults(func=cmd_match)

    args = ap.parse_args()
    try:
        cfg = load_config(args.config)
        sys.exit(args.func(args, cfg))
    except ConfigError as e:
        logger.error("Configuration problem: %s", e)
        sys.exit(3)
    except FileNotFoundError as e:
        logger.error("%s", e)
        sys.exit(5)
    except sqlite3.OperationalError as e:
        logger.error("Index not usable (%s). Build it first: "
                     "python -m scholarlib.cli.jstor_index build", e)
        sys.exit(5)
    except KeyboardInterrupt:
        logger.error("Interrupted — rerun with --resume to continue")
        sys.exit(1)
    except Exception as e:
        logger.error("Failed: %s", e)
        logger.debug("traceback", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
