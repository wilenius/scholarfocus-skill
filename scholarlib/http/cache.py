"""SQLite response cache.

TTL is driven by *cost*, not volatility: an OpenAlex search costs 10 credits and a
singleton costs nothing, so searches are cached hardest.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
import zlib
from pathlib import Path
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)

# Parameters that must never enter the cache key.
_AUTH_PARAMS = {"mailto", "api_key", "api-key", "email", "key", "x-api-key", "apikey"}

SCHEMA = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS responses (
  key        TEXT PRIMARY KEY,
  api        TEXT NOT NULL,
  url        TEXT NOT NULL,
  status     INTEGER NOT NULL,
  body       BLOB,
  fetched_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  cost       INTEGER NOT NULL DEFAULT 0,
  hits       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_resp_expires ON responses(expires_at);
CREATE INDEX IF NOT EXISTS ix_resp_api     ON responses(api);
"""


def cache_key(api: str, method: str, url: str, params: Mapping[str, Any]) -> str:
    clean = {
        str(k).lower(): v
        for k, v in (params or {}).items()
        if str(k).lower() not in _AUTH_PARAMS and v is not None
    }
    canonical = json.dumps(clean, sort_keys=True, separators=(",", ":"), default=str)
    raw = f"{api}|{method.upper()}|{url}|{canonical}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class HttpCache:
    def __init__(self, path: Optional[Path], enabled: bool = True):
        self.enabled = enabled and path is not None
        self.path = path
        self._conn: Optional[sqlite3.Connection] = None
        self.saved_credits = 0
        self.hits = 0
        self.misses = 0
        if self.enabled:
            assert path is not None
            path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(path))
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def get(self, key: str) -> Optional[dict]:
        if not self.enabled or self._conn is None:
            return None
        row = self._conn.execute(
            "SELECT body, expires_at, cost FROM responses WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            self.misses += 1
            return None
        body, expires_at, cost = row
        if expires_at < time.time():
            self.misses += 1
            return None
        self._conn.execute("UPDATE responses SET hits = hits + 1 WHERE key = ?", (key,))
        self._conn.commit()
        self.hits += 1
        self.saved_credits += cost or 0
        try:
            return json.loads(zlib.decompress(body).decode("utf-8"))
        except Exception:
            logger.debug("Corrupt cache entry %s — treating as miss", key[:12])
            self.misses += 1
            return None

    def put(self, key: str, api: str, url: str, status: int,
            payload: Any, ttl: int, cost: int = 0) -> None:
        if not self.enabled or self._conn is None:
            return
        now = int(time.time())
        blob = zlib.compress(json.dumps(payload, default=str).encode("utf-8"))
        self._conn.execute(
            "INSERT OR REPLACE INTO responses "
            "(key, api, url, status, body, fetched_at, expires_at, cost, hits) "
            "VALUES (?,?,?,?,?,?,?,?,COALESCE("
            "  (SELECT hits FROM responses WHERE key = ?), 0))",
            (key, api, url, status, blob, now, now + ttl, cost, key),
        )
        self._conn.commit()

    def stats(self) -> dict:
        if not self.enabled or self._conn is None:
            return {"enabled": False}
        row = self._conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(cost*hits),0), COALESCE(SUM(hits),0) FROM responses"
        ).fetchone()
        total = self.hits + self.misses
        return {
            "enabled": True,
            "path": str(self.path),
            "entries": row[0],
            "credits_saved_lifetime": row[1],
            "lifetime_hits": row[2],
            "session_hits": self.hits,
            "session_misses": self.misses,
            "session_hit_rate": round(self.hits / total, 3) if total else 0.0,
            "session_credits_saved": self.saved_credits,
        }

    def prune(self) -> int:
        if not self.enabled or self._conn is None:
            return 0
        cur = self._conn.execute(
            "DELETE FROM responses WHERE expires_at < ?", (int(time.time()),)
        )
        self._conn.commit()
        return cur.rowcount

    def clear(self) -> int:
        if not self.enabled or self._conn is None:
            return 0
        cur = self._conn.execute("DELETE FROM responses")
        self._conn.commit()
        return cur.rowcount

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
