"""
OpenAlex API client — primary data source.

Provides: author profiles, works, co-authors, referenced works, concepts/topics/keywords.
No API key required; email in polite pool gives higher rate limits.
Docs: https://docs.openalex.org
"""

import time
import logging
from typing import Optional
import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.openalex.org"

# Fields to select when fetching works (saves bandwidth)
WORK_FIELDS = (
    "id,doi,title,publication_year,type,"
    "authorships,concepts,topics,keywords,"
    "referenced_works,cited_by_count,"
    "abstract_inverted_index"
)

# Minimal fields for referenced-work lookups (bulk)
REF_WORK_FIELDS = "id,doi,title,publication_year,authorships,cited_by_count"


def reconstruct_abstract(inverted_index: Optional[dict]) -> str:
    """Reconstruct plain text from OpenAlex abstract_inverted_index format."""
    if not inverted_index:
        return ""
    positions: dict[int, str] = {}
    for word, pos_list in inverted_index.items():
        for pos in pos_list:
            positions[pos] = word
    return " ".join(positions[k] for k in sorted(positions))


class OpenAlexClient:
    def __init__(self, email: Optional[str] = None):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "ScholarFocus/1.0"})
        if email:
            self.session.params = {"mailto": email}  # type: ignore[assignment]

    def _get(self, url: str, params: Optional[dict] = None, retries: int = 3) -> Optional[dict]:
        for attempt in range(retries):
            try:
                resp = self.session.get(url, params=params, timeout=30)
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code == 429:
                    wait = 2 ** attempt
                    logger.warning("OpenAlex rate limit hit, waiting %ds", wait)
                    time.sleep(wait)
                    continue
                if resp.status_code == 404:
                    return None
                logger.warning("OpenAlex HTTP %s for %s", resp.status_code, url)
                return None
            except requests.RequestException as e:
                logger.warning("OpenAlex request error: %s", e)
                if attempt < retries - 1:
                    time.sleep(1)
        return None

    # ---- Author resolution ----

    def get_author_by_orcid(self, orcid: str) -> Optional[dict]:
        """Resolve author by ORCID (with or without URL prefix)."""
        orcid = orcid.replace("https://orcid.org/", "").strip()
        return self._get(f"{BASE_URL}/authors/https://orcid.org/{orcid}")

    def search_author(self, name: str, limit: int = 5) -> list[dict]:
        """Search authors by name; returns up to `limit` candidates."""
        data = self._get(f"{BASE_URL}/authors", {"search": name, "per-page": limit})
        return data.get("results", []) if data else []

    def get_author(self, identifier: str) -> Optional[dict]:
        """
        Resolve an author from an ORCID or display name.
        Returns the best-match author dict, or None.
        """
        # ORCID patterns: bare digits-dashes or full URL
        is_orcid = (
            identifier.startswith("0000-")
            or identifier.startswith("https://orcid.org/")
            or (len(identifier) == 19 and identifier.count("-") == 3)
        )
        if is_orcid:
            return self.get_author_by_orcid(identifier)
        # Name search — return the highest-works-count match
        candidates = self.search_author(identifier)
        if not candidates:
            return None
        return max(candidates, key=lambda a: a.get("works_count", 0))

    # ---- Works ----

    def get_author_works(
        self, author_id: str, max_works: int = 100
    ) -> list[dict]:
        """
        Fetch works authored by `author_id` (OpenAlex ID or URL).
        Returns up to `max_works` results, sorted by citation count desc.
        """
        works: list[dict] = []
        page = 1
        per_page = min(200, max_works)

        while len(works) < max_works:
            data = self._get(
                f"{BASE_URL}/works",
                {
                    "filter": f"author.id:{author_id}",
                    "sort": "cited_by_count:desc",
                    "per-page": per_page,
                    "page": page,
                    "select": WORK_FIELDS,
                },
            )
            if not data:
                break
            results = data.get("results", [])
            if not results:
                break
            works.extend(results)
            total = data.get("meta", {}).get("count", 0)
            if len(works) >= total or len(works) >= max_works:
                break
            page += 1
            time.sleep(0.12)

        return works[:max_works]

    def get_works_batch(self, work_ids: list[str]) -> list[dict]:
        """
        Fetch metadata for a batch of work IDs.
        `work_ids` may be full URLs or bare OpenAlex IDs (e.g. 'W12345').
        """
        if not work_ids:
            return []
        all_works: list[dict] = []
        chunk_size = 50

        for i in range(0, len(work_ids), chunk_size):
            chunk = work_ids[i : i + chunk_size]
            bare = [wid.replace("https://openalex.org/", "") for wid in chunk]
            ids_filter = "|".join(bare)
            data = self._get(
                f"{BASE_URL}/works",
                {"filter": f"ids.openalex:{ids_filter}", "per-page": chunk_size, "select": REF_WORK_FIELDS},
            )
            if data:
                all_works.extend(data.get("results", []))
            time.sleep(0.12)

        return all_works
