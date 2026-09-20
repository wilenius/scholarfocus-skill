"""Zotero local HTTP API bridge.

Read-only, and only answers while the Zotero desktop app is running with
Settings -> Advanced -> "Allow other applications on this computer to
communicate with Zotero" enabled.
"""

from __future__ import annotations

import logging
from typing import Iterator, Optional

from scholarlib import dedup
from scholarlib.http.base import BaseClient
from scholarlib.records import Record

logger = logging.getLogger(__name__)

DEFAULT_BASE = "http://localhost:23119/api/users/0"

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


class ZoteroLocalClient(BaseClient):
    name = "zotero"

    def __init__(self, base_url: str = DEFAULT_BASE, enabled: bool = True, **kw):
        super().__init__(**kw)
        self.base_url = base_url
        self.enabled = enabled
        self._checked: Optional[bool] = None

    def _available(self) -> bool:
        return self.enabled

    def available(self) -> bool:
        """Probe once; cache the answer for the run."""
        if self._checked is None:
            if not self.enabled:
                self._checked = False
            else:
                self._checked = self.get("/items", {"limit": 1}, use_cache=False) is not None
                if not self._checked:
                    logger.warning(
                        "Zotero is not reachable at %s. Start Zotero and enable "
                        'Settings -> Advanced -> "Allow other applications on this '
                        'computer to communicate with Zotero". Continuing without it.',
                        self.base_url,
                    )
        return self._checked

    def collections(self) -> list[dict]:
        return self.get("/collections", {"limit": 100}, use_cache=False) or []

    def collection_by_name(self, name: str) -> Optional[dict]:
        target = (name or "").strip().casefold()
        for c in self.collections():
            if str((c.get("data") or {}).get("name", "")).strip().casefold() == target:
                return c
        return None

    def items(self, *, collection_key: Optional[str] = None,
              limit: int = 100, max_items: int = 10000) -> Iterator[dict]:
        path = f"/collections/{collection_key}/items" if collection_key else "/items"
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
            zotero_citekey=(d.get("citationKey") or None),
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
                if dedup.title_similarity(rec.title, cand.title) >= threshold:
                    return cand
        return None
