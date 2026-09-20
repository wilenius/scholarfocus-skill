"""
CrossRef API client — tertiary data source.

Provides: DOI metadata, reference lists, journal info, publisher data.
No API key needed; email in polite pool gives higher rate limits.
Docs: https://api.crossref.org/swagger-ui/index.html
"""

import time
import logging
from typing import Optional
import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.crossref.org"


class CrossRefClient:
    def __init__(self, email: Optional[str] = None):
        self.session = requests.Session()
        ua = "ScholarFocus/1.0"
        if email:
            ua += f" (mailto:{email})"
        self.session.headers.update({"User-Agent": ua})

    def _get(self, url: str, params: Optional[dict] = None, retries: int = 3) -> Optional[dict]:
        for attempt in range(retries):
            try:
                resp = self.session.get(url, params=params, timeout=30)
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code == 429:
                    wait = 3 * (attempt + 1)
                    logger.warning("CrossRef rate limit, waiting %ds", wait)
                    time.sleep(wait)
                    continue
                if resp.status_code == 404:
                    return None
                logger.warning("CrossRef HTTP %s for %s", resp.status_code, url)
                return None
            except requests.RequestException as e:
                logger.warning("CrossRef request error: %s", e)
                if attempt < retries - 1:
                    time.sleep(1)
        return None

    def get_work_by_doi(self, doi: str) -> Optional[dict]:
        """Retrieve full CrossRef metadata for a DOI."""
        doi = doi.replace("https://doi.org/", "").strip()
        data = self._get(f"{BASE_URL}/works/{doi}")
        time.sleep(0.12)
        return data.get("message") if data else None

    def search_works_by_author(self, name: str, rows: int = 50) -> list[dict]:
        """Search CrossRef works by author name."""
        data = self._get(
            f"{BASE_URL}/works",
            {"query.author": name, "rows": rows, "select": "DOI,title,author,published,reference-count"},
        )
        time.sleep(0.12)
        return data.get("message", {}).get("items", []) if data else []

    def get_references(self, doi: str) -> list[dict]:
        """
        Get the reference list for a DOI (works that this paper cites).
        CrossRef only exposes references for participating publishers.
        Returns list of reference dicts with keys: DOI, unstructured, author, title, year.
        """
        work = self.get_work_by_doi(doi)
        if not work:
            return []
        return work.get("reference", [])

    def enrich_doi_metadata(self, doi: str) -> Optional[dict]:
        """Return a simplified metadata dict for a DOI."""
        work = self.get_work_by_doi(doi)
        if not work:
            return None
        authors = []
        for a in work.get("author", []):
            name_parts = [a.get("given", ""), a.get("family", "")]
            authors.append(" ".join(p for p in name_parts if p))
        date_parts = (work.get("published", {}) or {}).get("date-parts", [[]])
        year = date_parts[0][0] if date_parts and date_parts[0] else None
        return {
            "doi": doi,
            "title": " ".join(work.get("title", [])),
            "authors": authors,
            "year": year,
            "journal": work.get("container-title", [None])[0],
            "reference_count": work.get("reference-count", 0),
            "is_referenced_by_count": work.get("is-referenced-by-count", 0),
        }
