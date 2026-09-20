"""Semantic Scholar client.

Disabled by default. OpenAlex covers ~92% of abstracts, so S2AG is an
enhancement rather than a dependency; its remaining edge is the `tldr`
summaries and its own fields-of-study labels.
"""

from __future__ import annotations

import logging
from typing import Optional

from scholarlib.http.base import BaseClient

logger = logging.getLogger(__name__)

BASE_URL = "https://api.semanticscholar.org/graph/v1"

AUTHOR_FIELDS = "authorId,name,externalIds,affiliations,paperCount,citationCount,hIndex"
PAPER_FIELDS = (
    "paperId,externalIds,title,abstract,year,venue,"
    "fieldsOfStudy,s2FieldsOfStudy,citationCount,tldr"
)


class SemanticScholarClient(BaseClient):
    name = "semantic_scholar"
    base_url = BASE_URL

    def __init__(self, api_key: Optional[str] = None, enabled: bool = False, **kw):
        super().__init__(**kw)
        self.api_key = api_key
        self.enabled = enabled
        # Unauthenticated S2AG 429s almost immediately.
        if not api_key:
            object.__setattr__(self.policy, "min_interval", 1.1)

    def _available(self) -> bool:
        return self.enabled

    def _unavailable_reason(self) -> str:
        return "semantic_scholar: disabled (enable with --enable-s2ag)"

    def _auth_headers(self) -> dict:
        return {"x-api-key": self.api_key} if self.api_key else {}

    def health(self) -> tuple[bool, str]:
        """Probe whether the key actually works. Returns (ok, message)."""
        was = self.enabled
        self.enabled = True
        try:
            data = self.get("/paper/search", {"query": "anthropology", "limit": 1,
                                              "fields": "title"}, use_cache=False)
        finally:
            self.enabled = was
        if data and data.get("data") is not None:
            return True, "ok"
        if not self.api_key:
            return False, "no API key configured"
        return False, "key rejected or rate-limited (403/429)"

    def get_paper(self, paper_id: str, fields: str = PAPER_FIELDS) -> Optional[dict]:
        """`paper_id` may be an S2 id or a `DOI:10.xxxx/yyy` string."""
        return self.get(f"/paper/{paper_id}", {"fields": fields})

    def get_paper_by_doi(self, doi: str, fields: str = PAPER_FIELDS) -> Optional[dict]:
        d = str(doi).replace("https://doi.org/", "").strip()
        return self.get_paper(f"DOI:{d}", fields)

    def get_abstract(self, doi: str) -> Optional[str]:
        p = self.get_paper_by_doi(doi, fields="abstract")
        return (p or {}).get("abstract")

    def get_tldr(self, doi: str) -> Optional[str]:
        p = self.get_paper_by_doi(doi, fields="tldr")
        tldr = (p or {}).get("tldr") or {}
        return tldr.get("text")

    def search_authors(self, name: str, limit: int = 10) -> list[dict]:
        data = self.get("/author/search", {"query": name, "limit": limit,
                                           "fields": AUTHOR_FIELDS})
        return (data or {}).get("data", [])

    def get_author_papers(self, author_id: str, max_papers: int = 100) -> list[dict]:
        out: list[dict] = []
        offset = 0
        while len(out) < max_papers:
            data = self.get(f"/author/{author_id}/papers", {
                "fields": PAPER_FIELDS, "limit": min(100, max_papers - len(out)),
                "offset": offset,
            })
            if not data:
                break
            rows = data.get("data") or []
            out.extend(rows)
            if not rows or "next" not in data:
                break
            offset = data["next"]
        return out[:max_papers]
