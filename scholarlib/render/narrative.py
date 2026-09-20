"""Narrative/interpretive rendering: the Annual Review shape."""

from __future__ import annotations

from typing import Optional

from scholarlib.records import Record


def _cite(rec: dict) -> str:
    authors = rec.get("authors") or []
    if not authors:
        who = "Anon."
    elif len(authors) == 1:
        who = authors[0]
    elif len(authors) == 2:
        who = f"{authors[0]} & {authors[1]}"
    else:
        who = f"{authors[0]} et al."
    year = rec.get("year") or "n.d."
    return f"{who} ({year})"


def _entry(rec: dict, *, show_score: bool = False) -> str:
    bits = [f"- **{rec.get('title') or 'Untitled'}** — {_cite(rec)}"]
    meta = []
    if rec.get("venue"):
        meta.append(f"*{rec['venue']}*")
    if rec.get("cited_by_count") is not None:
        meta.append(f"{rec['cited_by_count']:,} citations")
    if rec.get("type") in ("book", "chapter"):
        meta.append(rec["type"])
    if rec.get("in_zotero"):
        ck = rec.get("zotero_citekey")
        meta.append(f"**in your library**{f' [`{ck}`]' if ck else ''}")
    if rec.get("oa_url"):
        meta.append(f"[full text]({rec['oa_url']})")
    elif rec.get("oa_status") and rec["oa_status"] != "closed":
        meta.append(rec["oa_status"])
    if rec.get("jstor"):
        meta.append(f"[JSTOR]({rec['jstor'].get('url')})")
    if show_score:
        meta.append(f"score {rec.get('score', 0):.2f}")
    if meta:
        bits.append(f"  {' · '.join(meta)}")
    abstract = rec.get("abstract")
    if abstract:
        text = abstract[:300].rsplit(" ", 1)[0]
        bits.append(f"  > {text}…")
    return "\n".join(bits)


def render(result: dict, *, top_per_cluster: int = 8,
           show_scores: bool = False) -> str:
    q = result.get("query") or {}
    records = result.get("records") or []
    by_key = {r["key"]: r for r in records}
    topic = ", ".join(q.get("queries") or []) or "the topic"

    L: list[str] = []
    L.append(f"# Literature review: {topic}")
    L.append("")
    flow = result.get("flow") or {}
    span = [r.get("year") for r in records if r.get("year")]
    scope = f"{len(records)} works"
    if span:
        scope += f", {min(span)}–{max(span)}"
    L.append(f"*{scope}. Generated {result.get('generated_at', '')}.*")
    L.append("")

    # Shape of the field over time
    timeline = result.get("timeline") or {}
    if timeline:
        recent = {y: n for y, n in timeline.items() if n}
        if len(recent) > 3:
            peak = max(recent, key=lambda y: recent[y])
            L.append(f"The corpus spans {min(recent)}–{max(recent)}, "
                     f"peaking in {peak} ({recent[peak]} works).")
            L.append("")

    owned = [r for r in records if r.get("in_zotero")]
    if owned:
        L.append(f"**{len(owned)} of these {len(records)} works are already in your "
                 f"Zotero library.**")
        L.append("")

    # Thematic strands
    clusters = result.get("clusters") or []
    if clusters:
        L.append("## Thematic strands")
        L.append("")
        for c in clusters:
            yr = c.get("year_range") or []
            span_s = f" ({yr[0]}–{yr[1]})" if yr and yr[0] and yr[1] else ""
            L.append(f"### {c['label']}{span_s}")
            kw = c.get("keywords") or []
            if kw:
                L.append(f"*Also tagged: {', '.join(kw[:4])}*")
            L.append(f"*{c['size']} works*")
            L.append("")
            for key in c.get("record_keys", [])[:top_per_cluster]:
                rec = by_key.get(key)
                if rec:
                    L.append(_entry(rec, show_score=show_scores))
            L.append("")

    # Books: the part citation databases handle worst
    books = [r for r in records if r.get("type") in ("book", "chapter")]
    if books:
        L.append("## Monographs and edited volumes")
        L.append("")
        L.append("*Citation databases index books poorly — OpenAlex carries no "
                 "reference lists for them — so this section is assembled from "
                 "forward citations, related works and the JSTOR index.*")
        L.append("")
        for rec in books[:12]:
            L.append(_entry(rec, show_score=show_scores))
        L.append("")

    # Anything not placed in a strand
    unclustered = [r for r in records if not r.get("cluster")
                   and r.get("type") not in ("book", "chapter")]
    if unclustered:
        L.append("## Further works")
        L.append("")
        for rec in unclustered[:12]:
            L.append(_entry(rec, show_score=show_scores))
        L.append("")

    warnings = result.get("warnings") or []
    if warnings:
        L.append("## Notes on coverage")
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
             f"{b.get('credits_saved_by_cache', 0)} saved by cache, "
             f"{b.get('remaining', '?')} remaining today.*")
    return "\n".join(L)
