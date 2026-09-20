"""Convert each source's native shape into the canonical Record."""

from __future__ import annotations

from typing import Optional

from scholarlib.apis.openalex import reconstruct_abstract
from scholarlib.records import Record


def _authors_from_openalex(work: dict) -> list[str]:
    out = []
    for a in work.get("authorships") or []:
        name = ((a.get("author") or {}).get("display_name"))
        if name:
            out.append(name)
    return out


def _topics_from_openalex(work: dict) -> list[str]:
    names = []
    for t in (work.get("topics") or [])[:5]:
        if t.get("display_name"):
            names.append(t["display_name"])
    for c in (work.get("concepts") or [])[:5]:
        if c.get("display_name") and c["display_name"] not in names:
            names.append(c["display_name"])
    return names


def from_openalex_work(work: dict, *, provenance: Optional[str] = None) -> Record:
    abstract = reconstruct_abstract(work.get("abstract_inverted_index"))
    loc = work.get("primary_location") or {}
    src = loc.get("source") or {}
    best_oa = work.get("best_oa_location") or {}
    oa = work.get("open_access") or {}
    return Record(
        doi=work.get("doi"),
        openalex_id=work.get("id"),
        title=work.get("title") or work.get("display_name"),
        authors=_authors_from_openalex(work),
        year=work.get("publication_year"),
        venue=src.get("display_name"),
        type=work.get("type"),
        language=work.get("language"),
        abstract=abstract,
        abstract_source="openalex" if abstract else None,
        cited_by_count=work.get("cited_by_count"),
        referenced_works=list(work.get("referenced_works") or []),
        related_works=list(work.get("related_works") or []),
        topics=_topics_from_openalex(work),
        oa_status=oa.get("oa_status"),
        oa_url=best_oa.get("pdf_url") or best_oa.get("landing_page_url"),
        provenance=[provenance] if provenance else [],
    )


def from_crossref_item(item: dict, *, provenance: str = "crossref") -> Record:
    titles = item.get("title") or []
    containers = item.get("container-title") or []
    authors = []
    for a in item.get("author") or []:
        nm = " ".join(x for x in (a.get("given"), a.get("family")) if x) or a.get("name")
        if nm:
            authors.append(nm)
    issued = ((item.get("issued") or {}).get("date-parts") or [[None]])[0]
    return Record(
        doi=item.get("DOI"),
        title=titles[0] if titles else None,
        authors=authors,
        year=issued[0] if issued else None,
        venue=containers[0] if containers else None,
        type=item.get("type"),
        cited_by_count=item.get("is-referenced-by-count"),
        provenance=[provenance],
    )


def from_s2_paper(paper: dict, *, provenance: str = "semantic_scholar") -> Record:
    ext = paper.get("externalIds") or {}
    tldr = (paper.get("tldr") or {}).get("text")
    abstract = paper.get("abstract") or tldr
    return Record(
        doi=ext.get("DOI"),
        title=paper.get("title"),
        year=paper.get("year"),
        venue=paper.get("venue"),
        abstract=abstract,
        abstract_source=("semantic_scholar" if paper.get("abstract")
                         else ("s2_tldr" if tldr else None)),
        cited_by_count=paper.get("citationCount"),
        topics=list(paper.get("fieldsOfStudy") or []),
        provenance=[provenance],
    )
