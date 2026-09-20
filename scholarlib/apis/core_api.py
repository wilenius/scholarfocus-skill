"""
CORE API client — quaternary data source (open-access full text & abstracts).

Provides: abstracts and full text for open-access papers when other sources lack them.
API key required — get one at https://core.ac.uk/services/api
Docs: https://api.core.ac.uk/docs/v3
"""

import time
import logging
from typing import Optional
import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.core.ac.uk/v3"


class COREClient:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "ScholarFocus/1.0"})
        if api_key:
            self.session.headers["Authorization"] = f"Bearer {api_key}"

    def _available(self) -> bool:
        if not self.api_key:
            logger.debug("CORE API key not configured, skipping")
            return False
        return True

    def _get(self, url: str, params: Optional[dict] = None, retries: int = 3) -> Optional[dict]:
        if not self._available():
            return None
        for attempt in range(retries):
            try:
                resp = self.session.get(url, params=params, timeout=30)
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code == 429:
                    wait = 5 * (attempt + 1)
                    logger.warning("CORE rate limit, waiting %ds", wait)
                    time.sleep(wait)
                    continue
                if resp.status_code in (400, 404):
                    return None
                if resp.status_code == 401:
                    logger.error("CORE: invalid API key")
                    return None
                logger.warning("CORE HTTP %s for %s", resp.status_code, url)
                return None
            except requests.RequestException as e:
                logger.warning("CORE request error: %s", e)
                if attempt < retries - 1:
                    time.sleep(2)
        return None

    def search_works(self, query: str, limit: int = 10) -> list[dict]:
        """Full-text/abstract search across CORE open-access corpus."""
        data = self._get(f"{BASE_URL}/search/works", {"q": query, "limit": limit})
        time.sleep(0.2)
        return data.get("results", []) if data else []

    def search_by_author(self, name: str, limit: int = 50) -> list[dict]:
        """Search CORE for papers by a given author name."""
        query = f'authors.name:"{name}"'
        return self.search_works(query, limit=limit)

    def get_work_by_doi(self, doi: str) -> Optional[dict]:
        """Retrieve a CORE work record by DOI."""
        doi = doi.replace("https://doi.org/", "").strip()
        data = self._get(f"{BASE_URL}/search/works", {"q": f'doi:"{doi}"', "limit": 1})
        time.sleep(0.2)
        results = data.get("results", []) if data else []
        return results[0] if results else None

    def get_abstract(self, doi: str) -> Optional[str]:
        """Try to retrieve an abstract for a DOI from CORE."""
        work = self.get_work_by_doi(doi)
        if not work:
            return None
        return work.get("abstract") or work.get("description")

    def enrich_missing_abstracts(self, works: list[dict]) -> list[dict]:
        """
        For each work dict that has a DOI but no abstract, try to fetch
        one from CORE. Mutates dicts in place. Returns the list.
        """
        for work in works:
            if work.get("abstract"):
                continue
            doi = work.get("doi")
            if not doi:
                continue
            abstract = self.get_abstract(doi)
            if abstract:
                work["abstract"] = abstract
        return works
