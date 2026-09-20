"""OpenAlex credit budget tracking.

Measured 2026-09-20: singleton = 0 credits, filter/cites/group_by = 1,
batch of 50 ids = 1, search = 10. Daily budget resets at UTC midnight and is
~1000 credits without an API key, ~10000 with the free key.
"""

from __future__ import annotations

import datetime as _dt
import logging
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SEARCH = "search"
LIST = "list"
SINGLETON = "singleton"

# Cost per request class, in credits.
COSTS = {SEARCH: 10, LIST: 1, SINGLETON: 0}

# Leave headroom so a stray lookup never hits a hard wall.
CAP_WITHOUT_KEY = 900
CAP_WITH_KEY = 9000

SCHEMA = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS credits (
  day     TEXT NOT NULL,
  api     TEXT NOT NULL,
  klass   TEXT NOT NULL,
  credits INTEGER NOT NULL DEFAULT 0,
  calls   INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (day, api, klass)
);
"""


class BudgetExceeded(RuntimeError):
    """Raised before a request that would exceed the daily budget (exit code 2)."""


def _today() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")


ENTITIES = {
    "works", "authors", "sources", "institutions", "topics", "concepts",
    "publishers", "funders", "keywords", "domains", "fields", "subfields",
}

# Params that turn a request into a list query rather than a singleton fetch.
LIST_PARAMS = {
    "filter", "group_by", "group-by", "per-page", "per_page",
    "page", "cursor", "sample", "sort", "search",
}


def classify(path: str, params: Optional[dict] = None) -> str:
    """Classify an OpenAlex request by its credit cost.

    Singletons (/works/W123, /works/doi:10.x/y) are free; searches cost 10;
    everything else is a 1-credit list request.
    """
    params = params or {}
    lowered = {str(k).lower(): v for k, v in params.items()}

    if "search" in lowered and lowered["search"]:
        return SEARCH
    filt = str(lowered.get("filter", "") or "")
    if ".search:" in filt or filt.startswith("search."):
        return SEARCH

    # /works/W123  or  /works/doi:10.1234/xyz  -> the id may itself contain slashes
    parts = [seg for seg in path.strip("/").split("/") if seg]
    is_singleton = len(parts) >= 2 and parts[0].lower() in ENTITIES
    if is_singleton and not (set(lowered) & LIST_PARAMS):
        return SINGLETON
    return LIST


class CreditLedger:
    def __init__(self, path: Optional[Path], caps: Optional[dict[str, int]] = None):
        self.caps = caps or {}
        self.path = path
        self._conn: Optional[sqlite3.Connection] = None
        self.session_spend: dict[str, int] = {}
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(path))
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def cap(self, api: str) -> Optional[int]:
        return self.caps.get(api)

    def spent_today(self, api: str) -> int:
        if self._conn is None:
            return self.session_spend.get(api, 0)
        row = self._conn.execute(
            "SELECT COALESCE(SUM(credits),0) FROM credits WHERE day=? AND api=?",
            (_today(), api),
        ).fetchone()
        return int(row[0] or 0)

    def remaining(self, api: str) -> Optional[int]:
        cap = self.cap(api)
        if cap is None:
            return None
        return max(0, cap - self.spent_today(api))

    def reserve(self, api: str, klass: str, credits: int) -> None:
        if credits <= 0:
            return
        rem = self.remaining(api)
        if rem is not None and credits > rem:
            raise BudgetExceeded(
                f"{api}: this {klass} request costs {credits} credits but only "
                f"{rem} remain of today's {self.cap(api)} budget"
            )

    def commit(self, api: str, klass: str, credits: int) -> None:
        self.session_spend[api] = self.session_spend.get(api, 0) + credits
        if self._conn is None:
            return
        self._conn.execute(
            "INSERT INTO credits(day,api,klass,credits,calls) VALUES (?,?,?,?,1) "
            "ON CONFLICT(day,api,klass) DO UPDATE SET "
            "credits = credits + excluded.credits, calls = calls + 1",
            (_today(), api, klass, credits),
        )
        self._conn.commit()

    def estimate(self, plan: list[tuple[str, str, int]]) -> int:
        """plan: list of (label, klass, count) -> total credits."""
        return sum(COSTS.get(klass, 0) * count for _, klass, count in plan)

    def summary(self, api: str = "openalex") -> dict:
        return {
            "api": api,
            "spent_session": self.session_spend.get(api, 0),
            "spent_today": self.spent_today(api),
            "daily_cap": self.cap(api),
            "remaining": self.remaining(api),
        }

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def default_caps(cfg: dict) -> dict[str, int]:
    """Derive the OpenAlex cap from whether an API key is configured."""
    configured = (cfg.get("budget", {}).get("openalex", {}) or {}).get("daily_cap")
    if configured:
        return {"openalex": int(configured)}
    has_key = bool((cfg.get("apis", {}).get("openalex", {}) or {}).get("api_key"))
    return {"openalex": CAP_WITH_KEY if has_key else CAP_WITHOUT_KEY}
