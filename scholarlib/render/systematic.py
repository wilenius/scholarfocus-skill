"""Systematic rendering: reproducible, counted, and explicit about its limits."""

from __future__ import annotations


def render(result: dict, *, max_table: int = 200) -> str:
    q = result.get("query") or {}
    flow = result.get("flow") or {}
    records = result.get("records") or []
    topic = ", ".join(q.get("queries") or []) or "the topic"

    L: list[str] = []
    L.append(f"# Systematic review: {topic}")
    L.append("")
    L.append(f"*Generated {result.get('generated_at', '')}. "
             f"Schema {result.get('schema_version')}.*")
    L.append("")

    L.append("## Search strategy")
    L.append("")
    L.append("| Parameter | Value |")
    L.append("|---|---|")
    for label, key in [("Queries", "queries"), ("Seed DOIs", "seed_dois"),
                       ("From year", "from_year"), ("To year", "to_year"),
                       ("Types", "types"), ("Languages", "languages"),
                       ("Snowball depth", "depth")]:
        val = q.get(key)
        if val:
            L.append(f"| {label} | `{val}` |")
    L.append("")

    L.append("## Sources searched")
    L.append("")
    L.append("| Source | Used | Detail |")
    L.append("|---|---|---|")
    for name, info in (result.get("sources") or {}).items():
        used = "yes" if info.get("used") else "no"
        detail = ", ".join(f"{k}={v}" for k, v in info.items()
                           if k not in ("used",) and v not in (None, ""))
        L.append(f"| {name} | {used} | {detail or '—'} |")
    L.append("")

    L.append("## Flow")
    L.append("")
    L.append("| Stage | n |")
    L.append("|---|---|")
    for stage, n in (flow.get("identified") or {}).items():
        L.append(f"| Identified — {stage} | {n} |")
    L.append(f"| **Identified total** | **{flow.get('identified_total', 0)}** |")
    L.append(f"| Duplicates removed | {flow.get('duplicates_removed', 0)} |")
    L.append(f"| Screened | {flow.get('screened', 0)} |")
    for ex in flow.get("excluded") or []:
        L.append(f"| Excluded — {ex.get('reason')} | {ex.get('n')} |")
    L.append(f"| **Included** | **{flow.get('included', 0)}** |")
    L.append("")

    L.append("## Included studies")
    L.append("")
    L.append("| # | Year | Authors | Title | Type | Cited by | OA | In library |")
    L.append("|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(records[:max_table], 1):
        authors = r.get("authors") or []
        who = authors[0] if authors else "—"
        if len(authors) > 1:
            who += " et al."
        title = (r.get("title") or "").replace("|", "\\|")[:80]
        L.append(
            f"| {i} | {r.get('year') or '—'} | {who[:28]} | {title} | "
            f"{r.get('type') or '—'} | {r.get('cited_by_count') if r.get('cited_by_count') is not None else '—'} | "
            f"{r.get('oa_status') or '—'} | {'yes' if r.get('in_zotero') else '—'} |"
        )
    if len(records) > max_table:
        L.append(f"| … | | | *{len(records) - max_table} further records in the JSON* | | | | |")
    L.append("")

    warnings = result.get("warnings") or []
    if warnings:
        L.append("## Warnings")
        L.append("")
        for w in warnings:
            L.append(f"- {w}")
        L.append("")

    L.append("## Limitations")
    L.append("")
    for lim in result.get("limitations") or []:
        L.append(f"- {lim}")
    L.append("")

    b = result.get("budget") or {}
    L.append(f"*OpenAlex credits: {b.get('spent_session', 0)} spent, "
             f"{b.get('credits_saved_by_cache', 0)} saved by cache.*")
    return "\n".join(L)
