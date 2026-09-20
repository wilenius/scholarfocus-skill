"""OpenAlex client.

Credit costs measured 2026-09-20: singleton 0, list/cites/group_by 1,
batch of 50 ids 1, search 10. A free API key raises the daily budget from
~1000 to ~10000 credits.
"""

from __future__ import annotations

import logging
from typing import Iterable, Iterator, Optional

from scholarlib.http.base import BaseClient
from scholarlib.http.budget import COSTS, classify

logger = logging.getLogger(__name__)

BASE_URL = "https://api.openalex.org"

# Fields for researcher profiling (scholarfocus).
WORK_FIELDS = (
    "id,doi,title,publication_year,type,"
    "authorships,concepts,topics,keywords,"
    "referenced_works,cited_by_count,"
    "abstract_inverted_index"
)

# Minimal fields for bulk reference lookups.
REF_WORK_FIELDS = "id,doi,title,publication_year,authorships,cited_by_count"

# Literature review needs abstracts and the full citation edges.
# related_works matters because monographs have referenced_works_count == 0.
LITREVIEW_WORK_FIELDS = (
    "id,doi,title,display_name,publication_year,type,language,"
    "authorships,topics,concepts,keywords,"
    "referenced_works,referenced_works_count,related_works,"
    "cited_by_count,abstract_inverted_index,"
    "primary_location,best_oa_location,open_access"
)

BATCH_SIZE = 50


def reconstruct_abstract(inverted_index: Optional[dict]) -> Optional[str]:
    """Rebuild plain text from OpenAlex's inverted index.

    ~92% of works carry one, which is why Semantic Scholar is an
    enhancement rather than a dependency.
    """
    if not inverted_index:
        return None
    positions: list[tuple[int, str]] = []
    for word, idxs in inverted_index.items():
        for i in idxs:
            positions.append((i, word))
    if not positions:
        return None
    positions.sort()
    return " ".join(word for _, word in positions)


def short_id(work_id: str) -> str:
    """https://openalex.org/W123 -> W123"""
    return str(work_id).rstrip("/").rsplit("/", 1)[-1]


def chunked(items: Iterable, size: int) -> Iterator[list]:
    buf: list = []
    for it in items:
        buf.append(it)
        if len(buf) >= size:
            yield buf
            buf = []
    if buf:
        yield buf


class OpenAlexClient(BaseClient):
    name = "openalex"
    base_url = BASE_URL

    def __init__(self, email: Optional[str] = None, api_key: Optional[str] = None, **kw):
        super().__init__(contact_email=email, **kw)
        self.email = email
        self.api_key = api_key

    def _auth_params(self) -> dict:
        p = {}
        if self.email:
            p["mailto"] = self.email
        if self.api_key:
            p["api_key"] = self.api_key
        return p

    def _cost(self, path: str, params: dict) -> tuple[str, int]:
        klass = classify(path, params)
        return klass, COSTS.get(klass, 1)

    def _ttl(self, path: str, params: dict) -> int:
        """Cost drives TTL: searches are 10 credits, singletons are free."""
        klass = classify(path, params)
        if klass == "search":
            return 90 * 86400
        if klass == "singleton":
            return 14 * 86400
        return 30 * 86400

    # ---- authors --------------------------------------------------------

    def get_author_by_orcid(self, orcid: str) -> Optional[dict]:
        o = str(orcid).strip()
        if not o.startswith("http"):
            o = f"https://orcid.org/{o}"
        return self.get(f"/authors/{o}")

    def search_authors(self, name: str, limit: int = 10) -> list[dict]:
        data = self.get("/authors", {"search": name, "per-page": limit})
        return (data or {}).get("results", [])

    def get_author_by_id(self, author_id: str) -> Optional[dict]:
        return self.get(f"/authors/{short_id(author_id)}")

    # ---- works ----------------------------------------------------------

    def get_work(self, work_id: str, select: Optional[str] = None) -> Optional[dict]:
        """Free: singleton lookups cost 0 credits."""
        params = {"select": select} if select else None
        return self.get(f"/works/{short_id(work_id)}", params)

    def get_work_by_doi(self, doi: str, select: Optional[str] = None) -> Optional[dict]:
        """Free. Always prefer this over searching for a title."""
        d = str(doi).replace("https://doi.org/", "").strip()
        params = {"select": select} if select else None
        return self.get(f"/works/doi:{d}", params)

    def get_author_works(self, author_id: str, max_works: int = 100,
                         select: str = WORK_FIELDS) -> list[dict]:
        return list(self.get_paged(
            "/works",
            {"filter": f"author.id:{short_id(author_id)}",
             "select": select, "sort": "cited_by_count:desc"},
            page_size=min(max_works, 200), max_items=max_works,
        ))

    def get_works_batch(self, work_ids: Iterable[str],
                        select: str = REF_WORK_FIELDS) -> list[dict]:
        """50 works per credit — the cheapest way to hydrate known IDs."""
        ids = [short_id(w) for w in work_ids if w]
        out: list[dict] = []
        for chunk in chunked(ids, BATCH_SIZE):
            data = self.get("/works", {
                "filter": f"ids.openalex:{'|'.join(chunk)}",
                "per-page": BATCH_SIZE,
                "select": select,
            })
            if data:
                out.extend(data.get("results") or [])
        return out

    def search_works(self, query: str, *, limit: int = 50, from_year: Optional[int] = None,
                     to_year: Optional[int] = None, types: Optional[list[str]] = None,
                     languages: Optional[list[str]] = None,
                     select: str = LITREVIEW_WORK_FIELDS) -> list[dict]:
        """10 credits per page — the expensive operation. Use sparingly."""
        filters = []
        if from_year:
            filters.append(f"from_publication_date:{from_year}-01-01")
        if to_year:
            filters.append(f"to_publication_date:{to_year}-12-31")
        if types:
            filters.append(f"type:{'|'.join(types)}")
        if languages:
            filters.append(f"language:{'|'.join(languages)}")
        params = {"search": query, "per-page": min(limit, 200), "select": select}
        if filters:
            params["filter"] = ",".join(filters)
        data = self.get("/works", params)
        return (data or {}).get("results", [])

    def cited_by(self, work_ids: Iterable[str], *, max_items: int = 200,
                 from_year: Optional[int] = None,
                 select: str = LITREVIEW_WORK_FIELDS) -> list[dict]:
        """Forward snowballing. The OR-packed filter makes 50 seeds cost 1 credit."""
        ids = [short_id(w) for w in work_ids if w]
        out: list[dict] = []
        for chunk in chunked(ids, BATCH_SIZE):
            filt = f"cites:{'|'.join(chunk)}"
            if from_year:
                filt += f",from_publication_date:{from_year}-01-01"
            out.extend(self.get_paged(
                "/works", {"filter": filt, "select": select},
                page_size=200, max_items=max_items,
            ))
        return out

    def group_by(self, filter_expr: str, dimension: str) -> list[dict]:
        """Aggregate shape of a result set for 1 credit, fetching no works."""
        data = self.get("/works", {"filter": filter_expr, "group_by": dimension})
        return (data or {}).get("group_by", [])
