"""CORE client. Key is optional but raises the rate limit from 10/min to 150/min."""

from __future__ import annotations

import logging
from typing import Optional

from scholarlib.http.base import BaseClient

logger = logging.getLogger(__name__)

BASE_URL = "https://api.core.ac.uk/v3"


class COREClient(BaseClient):
    name = "core"
    base_url = BASE_URL

    def __init__(self, api_key: Optional[str] = None, **kw):
        super().__init__(**kw)
        self.api_key = api_key
        if not api_key:
            logger.debug("CORE: no API key — rate limit is 10/min instead of 150/min")

    def _auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def search_works(self, query: str, limit: int = 10) -> list[dict]:
        data = self.get("/search/works", {"q": query, "limit": limit})
        return (data or {}).get("results", [])

    def get_work_by_doi(self, doi: str) -> Optional[dict]:
        d = str(doi).replace("https://doi.org/", "").strip()
        results = self.search_works(f'doi:"{d}"', limit=1)
        return results[0] if results else None

    def get_abstract(self, doi: str) -> Optional[str]:
        w = self.get_work_by_doi(doi)
        if not w:
            return None
        return w.get("abstract") or w.get("description") or None
