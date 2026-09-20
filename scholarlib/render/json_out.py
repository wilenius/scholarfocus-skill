"""The shared result document. Both modes produce this; only rendering differs."""

from __future__ import annotations

import datetime as _dt
import json
from typing import Optional

from scholarlib.pipeline.context import Context
from scholarlib.records import Record

SCHEMA_VERSION = "1.0"


def build_result(ctx: Context, records: list[Record], *, mode: str,
                 query: dict, clusters: Optional[list[dict]] = None,
                 excluded: Optional[list[dict]] = None,
                 dedup_stats: Optional[dict] = None,
                 timeline: Optional[dict] = None) -> dict:
    ledger = ctx.ledger
    cache = ctx.cache
    budget = ledger.summary() if ledger else {}
    identified = dict(ctx.identified)

    sources = {}
    for name, client in (ctx.clients or {}).items():
        used = bool(getattr(client, "_available", lambda: True)())
        entry = {"used": used}
        if name == "semantic_scholar" and not used:
            entry["reason"] = "disabled (enable with --enable-s2ag)"
        if name == "zotero":
            entry["used"] = ctx.zotero_lookup is not None
            if ctx.zotero_lookup is not None:
                entry["library_items"] = len(ctx.zotero_lookup)
        sources[name] = entry
    if ctx.jstor_index is not None:
        st = ctx.jstor_index.stats()
        sources["jstor"] = {
            "used": True,
            "records": st.get("records"),
            "matched": ctx.stats.get("jstor_matched", 0),
            "discovered": ctx.stats.get("jstor_discovered", 0),
            "joined": ctx.stats.get("jstor_joined", 0),
        }
    else:
        sources["jstor"] = {"used": False, "reason": "no index built"}

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "mode": mode,
        "query": query,
        "sources": sources,
        "budget": {
            **budget,
            "credits_saved_by_cache": (cache.saved_credits if cache else 0),
            "cache_hit_rate": (cache.stats().get("session_hit_rate") if cache else None),
            "truncated": ctx.truncated,
        },
        "flow": {
            "identified": identified,
            "identified_total": sum(identified.values()),
            "duplicates_removed": (dedup_stats or {}).get("duplicates_removed", 0),
            "screened": (dedup_stats or {}).get("output", len(records)),
            "excluded": excluded or [],
            "included": len(records),
        },
        "timeline": timeline or {},
        "clusters": clusters or [],
        "records": [r.to_dict() for r in records],
        "warnings": ctx.warnings,
        "limitations": build_limitations(ctx, records),
    }


def build_limitations(ctx: Context, records: list[Record]) -> list[str]:
    """Generated, not boilerplate: says what was actually queried today."""
    used = [n for n, c in (ctx.clients or {}).items()
            if getattr(c, "_available", lambda: True)()]
    if ctx.jstor_index is not None:
        st = ctx.jstor_index.stats()
        built = (st.get("build") or {}).get("built_at")
        when = ""
        if built:
            try:
                when = _dt.datetime.fromtimestamp(int(built)).strftime(" built %Y-%m-%d")
            except (ValueError, OSError):
                when = ""
        used.append(f"JSTOR metadata index (local,{when or ' local'})")
    out = [
        "Sources searched: " + ", ".join(sorted(used)) + ".",
        "No Scopus, Web of Science, PubMed or grey-literature database was searched; "
        "this is not a PRISMA-compliant systematic review.",
        "Screening was performed on title, abstract and metadata only. "
        "No full text was assessed.",
    ]
    books = [r for r in records if r.type in ("book", "chapter")]
    no_refs = [r for r in books if not r.referenced_works]
    if no_refs:
        out.append(
            f"{len(no_refs)} of {len(books)} book/chapter records carry no reference "
            "list in OpenAlex, so backward citation chaining could not run for them. "
            "Forward citation and related-works expansion were used instead."
        )
    if ctx.jstor_index is not None:
        out.append(
            "The JSTOR index holds no abstracts, references or full text; it is a "
            "discovery and coverage check. Its records join to OpenAlex by title, "
            "which succeeds for roughly 58% of them."
        )
    if ctx.truncated:
        out.append(
            f"Retrieval was truncated at stage '{ctx.truncated.get('stage')}' "
            f"({ctx.truncated.get('reason')}); coverage is incomplete."
        )
    return out


def dumps(result: dict) -> str:
    return json.dumps(result, indent=2, ensure_ascii=False, default=str)
