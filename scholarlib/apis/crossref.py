"""CrossRef client. Free, polite pool via a mailto in the User-Agent."""

from __future__ import annotations

import logging
from typing import Optional

from scholarlib.http.base import BaseClient

logger = logging.getLogger(__name__)

BASE_URL = "https://api.crossref.org"

# Verified against the live API. `reference-count` is NOT a valid select field
# and returns HTTP 400 select-not-available -- that was the long-standing bug.
# The valid spellings are `references-count` and `is-referenced-by-count`.
SAFE_SELECT = (
    "DOI,title,author,issued,type,container-title,"
    "references-count,is-referenced-by-count,abstract"
)


class CrossRefClient(BaseClient):
    name = "crossref"
    base_url = BASE_URL

    def __init__(self, email: Optional[str] = None, **kw):
        super().__init__(contact_email=email, **kw)
        self.email = email

    def _auth_params(self) -> dict:
        return {"mailto": self.email} if self.email else {}

    def get_work_by_doi(self, doi: str) -> Optional[dict]:
        d = str(doi).replace("https://doi.org/", "").strip()
        data = self.get(f"/works/{d}")
        return (data or {}).get("message")

    def search_works_by_author(self, name: str, rows: int = 50,
                               *, select: Optional[str] = None) -> list[dict]:
        params: dict = {"query.author": name, "rows": min(rows, 1000)}
        if select:
            params["select"] = select
        data = self.get("/works", params)
        return (data or {}).get("message", {}).get("items", [])

    def search_works(self, query: str, rows: int = 50,
                     from_year: Optional[int] = None) -> list[dict]:
        params: dict = {"query.bibliographic": query, "rows": min(rows, 1000)}
        if from_year:
            params["filter"] = f"from-pub-date:{from_year}-01-01"
        data = self.get("/works", params)
        return (data or {}).get("message", {}).get("items", [])

    def get_references(self, doi: str) -> list[dict]:
        """Only publishers who deposit references expose them."""
        work = self.get_work_by_doi(doi)
        return (work or {}).get("reference", []) or []

    def get_abstract(self, doi: str) -> Optional[str]:
        """CrossRef abstracts are JATS-wrapped; strip the tags."""
        import re

        work = self.get_work_by_doi(doi)
        raw = (work or {}).get("abstract")
        if not raw:
            return None
        text = re.sub(r"<[^>]+>", " ", raw)
        text = re.sub(r"\s+", " ", text).strip()
        return text or None

    def enrich_doi_metadata(self, doi: str) -> Optional[dict]:
        """Normalise a CrossRef record into a flat shape."""
        work = self.get_work_by_doi(doi)
        if not work:
            return None
        authors = []
        for a in work.get("author", []) or []:
            given, family = a.get("given"), a.get("family")
            authors.append(" ".join(x for x in (given, family) if x) or a.get("name", ""))
        issued = ((work.get("issued") or {}).get("date-parts") or [[None]])[0]
        titles = work.get("title") or []
        containers = work.get("container-title") or []
        return {
            "doi": work.get("DOI"),
            "title": titles[0] if titles else None,
            "authors": [a for a in authors if a],
            "year": issued[0] if issued else None,
            "journal": containers[0] if containers else None,
            "type": work.get("type"),
            "reference_count": work.get("references-count"),
            "is_referenced_by_count": work.get("is-referenced-by-count"),
        }
