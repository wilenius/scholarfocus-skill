"""
Semantic Scholar Academic Graph (S2AG) client — secondary data source.

Provides: abstracts, fields of study, TL;DR summaries, paper references/citations.
API key is optional but raises rate limit from 1 req/s to 10 req/s.
Docs: https://api.semanticscholar.org/api-docs/
"""

import time
import logging
from typing import Optional
import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.semanticscholar.org/graph/v1"

AUTHOR_FIELDS = "name,externalIds,affiliations,hIndex,citationCount,paperCount,papers"
PAPER_FIELDS = (
    "paperId,externalIds,title,year,abstract,tldr,"
    "fieldsOfStudy,s2FieldsOfStudy,authors,referenceCount,citationCount"
)
REF_FIELDS = "paperId,externalIds,title,year,authors,citationCount"


class SemanticScholarClient:
    def __init__(self, api_key: Optional[str] = None):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "ScholarFocus/1.0"})
        if api_key:
            self.session.headers["x-api-key"] = api_key
        self._delay = 0.15 if api_key else 1.1  # seconds between requests

    def _get(self, url: str, params: Optional[dict] = None, retries: int = 3) -> Optional[dict]:
        for attempt in range(retries):
            try:
                resp = self.session.get(url, params=params, timeout=30)
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code == 429:
                    wait = 5 * (attempt + 1)
                    logger.warning("S2AG rate limit, waiting %ds", wait)
                    time.sleep(wait)
                    continue
                if resp.status_code in (400, 404):
                    return None
                logger.warning("S2AG HTTP %s for %s", resp.status_code, url)
                return None
            except requests.RequestException as e:
                logger.warning("S2AG request error: %s", e)
                if attempt < retries - 1:
                    time.sleep(2)
        return None

    # ---- Author resolution ----

    def search_author(self, name: str, limit: int = 5) -> list[dict]:
        data = self._get(
            f"{BASE_URL}/author/search",
            {"query": name, "limit": limit, "fields": AUTHOR_FIELDS},
        )
        time.sleep(self._delay)
        return data.get("data", []) if data else []

    def get_author_by_orcid(self, orcid: str) -> Optional[dict]:
        orcid = orcid.replace("https://orcid.org/", "").strip()
        # S2AG doesn't have a direct ORCID endpoint; search by ORCID as query
        results = self.search_author(orcid, limit=3)
        for r in results:
            ext = r.get("externalIds") or {}
            if ext.get("ORCID") == orcid:
                return r
        return None

    def get_author(self, identifier: str) -> Optional[dict]:
        is_orcid = (
            identifier.startswith("0000-")
            or identifier.startswith("https://orcid.org/")
            or (len(identifier) == 19 and identifier.count("-") == 3)
        )
        if is_orcid:
            return self.get_author_by_orcid(identifier)
        results = self.search_author(identifier)
        if not results:
            return None
        return max(results, key=lambda a: a.get("citationCount", 0))

    # ---- Papers ----

    def get_author_papers(self, s2_author_id: str, max_papers: int = 100) -> list[dict]:
        papers: list[dict] = []
        offset = 0
        limit = min(100, max_papers)

        while len(papers) < max_papers:
            data = self._get(
                f"{BASE_URL}/author/{s2_author_id}/papers",
                {"fields": PAPER_FIELDS, "limit": limit, "offset": offset},
            )
            time.sleep(self._delay)
            if not data:
                break
            batch = data.get("data", [])
            if not batch:
                break
            papers.extend(batch)
            if not data.get("next"):
                break
            offset += limit

        return papers[:max_papers]

    def get_paper_references(self, paper_id: str, max_refs: int = 50) -> list[dict]:
        """Get the works that `paper_id` cites."""
        data = self._get(
            f"{BASE_URL}/paper/{paper_id}/references",
            {"fields": REF_FIELDS, "limit": min(max_refs, 500)},
        )
        time.sleep(self._delay)
        if not data:
            return []
        return [item.get("citedPaper", {}) for item in data.get("data", []) if item.get("citedPaper")]

    def get_paper(self, paper_id: str) -> Optional[dict]:
        """Fetch a single paper by S2 paper ID or DOI (prefix doi:)."""
        data = self._get(f"{BASE_URL}/paper/{paper_id}", {"fields": PAPER_FIELDS})
        time.sleep(self._delay)
        return data
