"""OpenAIRE client. Free, no key.

Strong on European, multilingual and repository-held material, which matters
because a fifth of the JSTOR corpus is non-English. The response JSON is deeply
nested and inconsistently typed, so everything goes through _first().
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from scholarlib.http.base import BaseClient

logger = logging.getLogger(__name__)

BASE_URL = "https://api.openaire.eu/search"


def _first(node: Any, *path: str) -> Any:
    """Walk a path, tolerating dict-or-list-of-dicts at every level."""
    cur = node
    for key in path:
        if isinstance(cur, list):
            cur = cur[0] if cur else None
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    if isinstance(cur, list):
        cur = cur[0] if cur else None
    return cur


def _text(node: Any) -> Optional[str]:
    """OpenAIRE wraps scalars as {'$': value}."""
    if isinstance(node, list):
        node = node[0] if node else None
    if isinstance(node, dict):
        node = node.get("$")
    if node is None:
        return None
    s = str(node).strip()
    return s or None


class OpenAIREClient(BaseClient):
    name = "openaire"
    base_url = BASE_URL

    def _results(self, data: Optional[dict]) -> list[dict]:
        res = _first(data, "response", "results")
        if not isinstance(res, dict):
            return []
        rows = res.get("result")
        if rows is None:
            return []
        return rows if isinstance(rows, list) else [rows]

    def _entity(self, row: dict) -> Optional[dict]:
        ent = _first(row, "metadata", "oaf:entity", "oaf:result")
        return ent if isinstance(ent, dict) else None

    def search(self, query: str, *, size: int = 20,
               from_year: Optional[int] = None,
               to_year: Optional[int] = None) -> list[dict]:
        params: dict = {"keywords": query, "size": min(size, 100), "format": "json"}
        if from_year:
            params["fromDateAccepted"] = f"{from_year}-01-01"
        if to_year:
            params["toDateAccepted"] = f"{to_year}-12-31"
        return self._results(self.get("/publications", params))

    def get_by_doi(self, doi: str) -> Optional[dict]:
        d = str(doi).replace("https://doi.org/", "").strip()
        rows = self._results(self.get("/publications", {"doi": d, "format": "json", "size": 1}))
        return rows[0] if rows else None

    def get_abstract(self, doi: str) -> Optional[str]:
        row = self.get_by_doi(doi)
        if not row:
            return None
        ent = self._entity(row)
        if not ent:
            return None
        text = _text(ent.get("description"))
        if text and len(text) > 20:
            return text
        return None
