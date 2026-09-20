"""BibTeX / CSL-JSON / RIS export, built from Records so non-Zotero finds are included."""

from __future__ import annotations

import json
import re
from typing import Iterable

from scholarlib import dedup
from scholarlib.records import Record

_TYPE_TO_BIBTEX = {
    "article": "article", "journal-article": "article",
    "book": "book", "monograph": "book",
    "chapter": "incollection", "book-chapter": "incollection",
    "conference-paper": "inproceedings", "dissertation": "phdthesis",
    "preprint": "misc", "report": "techreport",
}

_TYPE_TO_CSL = {
    "article": "article-journal", "journal-article": "article-journal",
    "book": "book", "monograph": "book",
    "chapter": "chapter", "book-chapter": "chapter",
    "conference-paper": "paper-conference", "dissertation": "thesis",
    "preprint": "article", "report": "report",
}

_ESCAPE = {"&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_"}


def _tex(s) -> str:
    if s is None:
        return ""
    return "".join(_ESCAPE.get(ch, ch) for ch in str(s))


def citekey(rec: Record, taken: set[str]) -> str:
    if rec.zotero_citekey:
        base = rec.zotero_citekey
    else:
        surname = (rec.first_surname or "anon").split()[-1]
        surname = re.sub(r"[^a-z0-9]", "", dedup.normalize_text(surname)) or "anon"
        first_word = ""
        for tok in dedup.normalize_title(rec.title).split():
            if len(tok) > 3:
                first_word = tok
                break
        base = f"{surname}{rec.year or ''}{first_word}"
    key, n = base, 1
    while key in taken:
        n += 1
        key = f"{base}{chr(ord('a') + n - 2)}"
    taken.add(key)
    return key


def to_bibtex(records: Iterable[Record]) -> str:
    taken: set[str] = set()
    out = []
    for rec in records:
        entry = _TYPE_TO_BIBTEX.get(rec.type or "", "misc")
        key = citekey(rec, taken)
        fields = [("title", rec.title)]
        if rec.authors:
            fields.append(("author", " and ".join(rec.authors)))
        if rec.year:
            fields.append(("year", rec.year))
        if rec.venue:
            fields.append(("journal" if entry == "article" else "booktitle", rec.venue))
        if rec.doi:
            fields.append(("doi", rec.doi))
        if rec.oa_url:
            fields.append(("url", rec.oa_url))
        elif rec.jstor and rec.jstor.get("url"):
            fields.append(("url", rec.jstor["url"]))
        if rec.abstract:
            fields.append(("abstract", rec.abstract[:2000]))
        body = ",\n".join(f"  {k} = {{{_tex(v)}}}" for k, v in fields if v)
        out.append(f"@{entry}{{{key},\n{body}\n}}")
    return "\n\n".join(out) + "\n"


def to_csl_json(records: Iterable[Record]) -> str:
    taken: set[str] = set()
    items = []
    for rec in records:
        authors = []
        for name in rec.authors:
            parts = name.split()
            if len(parts) > 1:
                authors.append({"given": " ".join(parts[:-1]), "family": parts[-1]})
            else:
                authors.append({"literal": name})
        item = {
            "id": citekey(rec, taken),
            "type": _TYPE_TO_CSL.get(rec.type or "", "document"),
            "title": rec.title,
        }
        if authors:
            item["author"] = authors
        if rec.year:
            item["issued"] = {"date-parts": [[rec.year]]}
        if rec.venue:
            item["container-title"] = rec.venue
        if rec.doi:
            item["DOI"] = rec.doi
        if rec.oa_url:
            item["URL"] = rec.oa_url
        if rec.abstract:
            item["abstract"] = rec.abstract
        items.append(item)
    return json.dumps(items, indent=2, ensure_ascii=False)


_RIS_TYPE = {"article": "JOUR", "book": "BOOK", "chapter": "CHAP",
             "conference-paper": "CONF", "dissertation": "THES", "report": "RPRT"}


def to_ris(records: Iterable[Record]) -> str:
    out = []
    for rec in records:
        lines = [f"TY  - {_RIS_TYPE.get(rec.type or '', 'GEN')}"]
        for a in rec.authors:
            lines.append(f"AU  - {a}")
        if rec.title:
            lines.append(f"TI  - {rec.title}")
        if rec.year:
            lines.append(f"PY  - {rec.year}")
        if rec.venue:
            lines.append(f"T2  - {rec.venue}")
        if rec.doi:
            lines.append(f"DO  - {rec.doi}")
        if rec.oa_url:
            lines.append(f"UR  - {rec.oa_url}")
        if rec.abstract:
            lines.append(f"AB  - {rec.abstract}")
        lines.append("ER  - ")
        out.append("\n".join(lines))
    return "\n\n".join(out) + "\n"
