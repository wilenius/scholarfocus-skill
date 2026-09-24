"""Read-only Zotero bridge for the local and Web APIs.

Both APIs expose the same item and collection shapes.  The local backend only
answers while Zotero desktop is running; the web backend works headlessly and
authenticates with Zotero's API key header.
"""

from __future__ import annotations

import logging
import os
from typing import Iterator, Optional

from scholarlib import dedup
from scholarlib.config import ConfigError
from scholarlib.http.base import BaseClient
from scholarlib.records import Record

logger = logging.getLogger(__name__)

DEFAULT_BASE = "http://localhost:23119/api/users/0"
WEB_API_ROOT = "https://api.zotero.org"

_TYPE_MAP = {
    "journalArticle": "article",
    "book": "book",
    "bookSection": "chapter",
    "conferencePaper": "conference-paper",
    "thesis": "dissertation",
    "preprint": "preprint",
    "report": "report",
}

_SKIP_TYPES = {"attachment", "note", "annotation"}


class ZoteroClient(BaseClient):
    name = "zotero"

    def __init__(self, base_url: Optional[str] = None, enabled: bool = True,
                 backend: str = "local", library_type: Optional[str] = None,
                 library_id: Optional[str] = None,
                 api_key: Optional[str] = None, **kw):
        super().__init__(**kw)
        self.backend = (backend or "local").strip().lower()
        if self.backend not in {"local", "web"}:
            raise ConfigError("zotero.backend must be 'local' or 'web'")

        self.library_type = (
            library_type or os.environ.get("ZOTERO_LIBRARY_TYPE") or "user"
        ).strip().lower()
        if self.backend == "web" and self.library_type not in {"user", "group"}:
            raise ConfigError("zotero.library_type must be 'user' or 'group'")
        self.library_id = str(
            library_id or os.environ.get("ZOTERO_LIBRARY_ID") or ""
        ).strip()
        self.api_key = api_key or os.environ.get("ZOTERO_API_KEY")

        if self.backend == "web":
            scope = "users" if self.library_type == "user" else "groups"
            self.base_url = base_url or (
                f"{WEB_API_ROOT}/{scope}/{self.library_id}"
                if self.library_id else WEB_API_ROOT
            )
        else:
            self.base_url = base_url or DEFAULT_BASE
        self.enabled = enabled
        self._checked: Optional[bool] = None

    def _available(self) -> bool:
        return self.enabled and (self.backend == "local" or bool(self.library_id))

    def _auth_headers(self) -> dict:
        if self.backend != "web":
            return {}
        headers = {"Zotero-API-Version": "3"}
        if self.api_key:
            headers["Zotero-API-Key"] = self.api_key
        return headers

    def available(self) -> bool:
        """Probe once; cache the answer for the run."""
        if self._checked is None:
            if not self.enabled:
                self._checked = False
            elif self.backend == "web" and not self.library_id:
                self._checked = False
                logger.warning(
                    "Zotero Web API is not configured: set zotero.library_id "
                    "or ZOTERO_LIBRARY_ID. Continuing without it."
                )
            else:
                self._checked = self.get("/items", {"limit": 1}, use_cache=False) is not None
                if not self._checked:
                    if self.backend == "web":
                        logger.warning(
                            "Zotero Web API is not reachable at %s. Check the "
                            "library ID, API key and network connection. Continuing "
                            "without it.", self.base_url,
                        )
                    else:
                        logger.warning(
                            "Zotero is not reachable at %s. Start Zotero and enable "
                            'Settings -> Advanced -> "Allow other applications on this '
                            'computer to communicate with Zotero". Continuing without it.',
                            self.base_url,
                        )
        return self._checked

    def collections(self) -> list[dict]:
        out: list[dict] = []
        start = 0
        while True:
            rows = self.get(
                "/collections", {"limit": 100, "start": start}, use_cache=False
            ) or []
            out.extend(rows)
            if len(rows) < 100:
                return out
            start += len(rows)

    def collection_by_name(self, name: str) -> Optional[dict]:
        target = (name or "").strip().casefold()
        for c in self.collections():
            if str((c.get("data") or {}).get("name", "")).strip().casefold() == target:
                return c
        return None

    def items(self, *, collection_key: Optional[str] = None,
              limit: int = 100, max_items: int = 10000) -> Iterator[dict]:
        path = f"/collections/{collection_key}/items" if collection_key else "/items"
        limit = min(max(1, limit), 100)
        start = 0
        while start < max_items:
            rows = self.get(path, {"limit": limit, "start": start}, use_cache=False)
            if not rows:
                return
            for r in rows:
                yield r
            if len(rows) < limit:
                return
            start += len(rows)

    @staticmethod
    def to_record(item: dict) -> Optional[Record]:
        d = item.get("data") or {}
        itype = d.get("itemType")
        if not itype or itype in _SKIP_TYPES:
            return None
        creators = [
            " ".join(x for x in (c.get("firstName"), c.get("lastName")) if x) or c.get("name", "")
            for c in (d.get("creators") or [])
            if c.get("creatorType") == "author"
        ]
        year = None
        date = str(d.get("date") or "")
        for chunk in date.replace("-", " ").replace("/", " ").split():
            if len(chunk) == 4 and chunk.isdigit():
                year = int(chunk)
                break
        citekey = d.get("citationKey") or None
        if not citekey:
            for line in str(d.get("extra") or "").splitlines():
                label, sep, value = line.partition(":")
                if sep and label.strip().casefold() == "citation key":
                    citekey = value.strip() or None
                    break
        return Record(
            doi=d.get("DOI"),
            title=d.get("title"),
            authors=[c for c in creators if c],
            year=year,
            venue=d.get("publicationTitle") or d.get("bookTitle") or d.get("publisher"),
            type=_TYPE_MAP.get(itype, itype),
            language=d.get("language") or None,
            abstract=d.get("abstractNote") or None,
            abstract_source="zotero" if d.get("abstractNote") else None,
            in_zotero=True,
            zotero_key=d.get("key"),
            zotero_citekey=citekey,
            provenance=["zotero"],
        )

    def load_library(self, collection_key: Optional[str] = None) -> list[Record]:
        if not self.available():
            return []
        out: list[Record] = []
        for item in self.items(collection_key=collection_key):
            rec = self.to_record(item)
            if rec and (rec.doi or rec.title):
                out.append(rec)
        logger.info("Zotero: loaded %d items", len(out))
        return out


# Backwards-compatible name for callers that imported the original local-only
# client.  The default backend remains local.
ZoteroLocalClient = ZoteroClient


class ZoteroLookup:
    """DOI-first, then blocked title match, for 'do I already own this?'."""

    def __init__(self, records: list[Record]):
        self.by_doi: dict[str, Record] = {}
        self.by_block: dict[tuple, list[Record]] = {}
        for r in records:
            if r.doi:
                self.by_doi[r.doi] = r
            self.by_block.setdefault((r.first_surname, r.year), []).append(r)

    def __len__(self) -> int:
        return len(self.by_doi) + sum(len(v) for v in self.by_block.values())

    def match(self, rec: Record, *, threshold: float = 0.93,
              year_slack: int = 1) -> Optional[Record]:
        if rec.doi and rec.doi in self.by_doi:
            return self.by_doi[rec.doi]
        if not dedup.titles_comparable(rec.title):
            return None
        for dy in range(-year_slack, year_slack + 1):
            y = (rec.year + dy) if rec.year else None
            for cand in self.by_block.get((rec.first_surname, y), []):
                if dedup.best_title_similarity(rec.title, cand.title) >= threshold:
                    return cand
        return None
