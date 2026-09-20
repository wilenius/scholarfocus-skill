"""ORCID public API client.

Free, no key. The works list is *author-curated*, which makes it far more
complete than OpenAlex for researchers whose records OpenAlex has fragmented.
Tuomas Tammisto, for instance, has 50 works in ORCID and 1 in OpenAlex.

It carries no citation counts or topics, so it pairs with OpenAlex: ORCID
supplies the authoritative work list, OpenAlex hydrates the DOIs (free, since
singleton lookups cost 0 credits).
"""

from __future__ import annotations

import logging
from typing import Optional

from scholarlib.http.base import BaseClient
from scholarlib.records import Record

logger = logging.getLogger(__name__)

BASE_URL = "https://pub.orcid.org/v3.0"

_TYPE_MAP = {
    "journal-article": "article",
    "book": "book",
    "book-chapter": "chapter",
    "book-review": "review",
    "conference-paper": "conference-paper",
    "dissertation-thesis": "dissertation",
    "preprint": "preprint",
    "report": "report",
    "edited-book": "book",
}


def _val(node, *path):
    cur = node
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


class OrcidClient(BaseClient):
    name = "orcid"
    base_url = BASE_URL
    default_ttl = 14 * 86400

    def _auth_headers(self) -> dict:
        return {"Accept": "application/json"}

    def search_by_name(self, name: str, *, rows: int = 10) -> list[dict]:
        """Find ORCID iDs for a display name. Free, no key."""
        parts = str(name).strip().split()
        if len(parts) >= 2:
            given, family = " ".join(parts[:-1]), parts[-1]
            q = f'family-name:{family} AND given-names:{given}'
        else:
            q = f'family-name:{parts[0]}' if parts else ""
        if not q:
            return []
        data = self.get("/expanded-search/", {"q": q, "rows": rows})
        out = []
        for r in (data or {}).get("expanded-result") or []:
            out.append({
                "orcid": r.get("orcid-id"),
                "name": " ".join(x for x in (r.get("given-names"),
                                             r.get("family-names")) if x),
                "institutions": list(r.get("institution-name") or []),
            })
        return out

    def get_person(self, orcid: str) -> Optional[dict]:
        return self.get(f"/{_clean(orcid)}/person")

    def get_employments(self, orcid: str) -> list[str]:
        data = self.get(f"/{_clean(orcid)}/employments")
        out = []
        for grp in (data or {}).get("affiliation-group") or []:
            for s in grp.get("summaries") or []:
                nm = _val(s, "employment-summary", "organization", "name")
                if nm and nm not in out:
                    out.append(nm)
        return out

    def get_works(self, orcid: str, *, max_works: int = 500) -> list[Record]:
        """The author-curated work list, as canonical Records."""
        data = self.get(f"/{_clean(orcid)}/works")
        groups = (data or {}).get("group") or []
        out: list[Record] = []
        for g in groups[:max_works]:
            summaries = g.get("work-summary") or []
            if not summaries:
                continue
            s = summaries[0]
            title = _val(s, "title", "title", "value")
            if not title:
                continue
            year = _val(s, "publication-date", "year", "value")
            ids = {e.get("external-id-type"): e.get("external-id-value")
                   for e in (_val(g, "external-ids", "external-id") or [])}
            out.append(Record(
                doi=ids.get("doi"),
                title=title,
                year=int(year) if year and str(year).isdigit() else None,
                venue=_val(s, "journal-title", "value"),
                type=_TYPE_MAP.get(s.get("type"), s.get("type")),
                provenance=["orcid"],
            ))
        logger.info("ORCID %s: %d works (%d with DOIs)",
                    _clean(orcid), len(out), sum(1 for r in out if r.doi))
        return out


def _clean(orcid: str) -> str:
    return str(orcid).strip().replace("https://orcid.org/", "")
