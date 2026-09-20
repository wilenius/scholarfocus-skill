"""Shared HTTP client: retry, pacing, caching and credit accounting in one place."""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Iterator, Optional

import requests

from scholarlib.http.budget import BudgetExceeded, CreditLedger
from scholarlib.http.cache import HttpCache, cache_key
from scholarlib.http.policy import RetryPolicy, policy_for

logger = logging.getLogger(__name__)

USER_AGENT = "scholarlib/0.2 (+https://github.com/hwileniu/scholarfocus-skill)"


class BaseClient:
    """Base for every API client.

    Subclasses set `name` and `base_url`, and may override `_auth_params`,
    `_auth_headers`, `_available`, `_cost` and `_ttl`.
    """

    name: str = "base"
    base_url: str = ""
    default_ttl: int = 7 * 86400

    def __init__(
        self,
        *,
        policy: Optional[RetryPolicy] = None,
        cache: Optional[HttpCache] = None,
        ledger: Optional[CreditLedger] = None,
        session: Optional[requests.Session] = None,
        user_agent: str = USER_AGENT,
        contact_email: Optional[str] = None,
    ):
        self.policy = policy or policy_for(self.name)
        self.cache = cache
        self.ledger = ledger
        self.contact_email = contact_email
        self.session = session or requests.Session()
        ua = user_agent
        if contact_email:
            ua = f"{user_agent} (mailto:{contact_email})"
        self.session.headers.update({"User-Agent": ua})
        self._last_request_at = 0.0

    # ---- subclass hooks -------------------------------------------------

    def _auth_params(self) -> dict:
        return {}

    def _auth_headers(self) -> dict:
        return {}

    def _available(self) -> bool:
        return True

    def _cost(self, path: str, params: dict) -> tuple[str, int]:
        """Return (class_label, credits) for this request."""
        return ("free", 0)

    def _ttl(self, path: str, params: dict) -> int:
        return self.default_ttl

    def _unavailable_reason(self) -> str:
        return f"{self.name}: not configured"

    # ---- internals ------------------------------------------------------

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"

    def _pace(self) -> None:
        gap = self.policy.min_interval - (time.monotonic() - self._last_request_at)
        if gap > 0:
            time.sleep(gap)

    def _sleep_for_retry(self, resp: Optional[requests.Response], attempt: int) -> None:
        delay = self.policy.delay_for(attempt)
        if resp is not None and self.policy.respect_retry_after:
            ra = resp.headers.get("Retry-After")
            if ra:
                try:
                    delay = max(delay, min(float(ra), self.policy.max_delay))
                except ValueError:
                    pass
        if self.policy.jitter:
            delay *= 1 + random.uniform(0, self.policy.jitter)
        logger.warning("%s: retrying in %.1fs (attempt %d)", self.name, delay, attempt + 1)
        time.sleep(delay)

    # ---- the one method everyone calls ----------------------------------

    def get(
        self,
        path: str,
        params: Optional[dict] = None,
        *,
        ttl: Optional[int] = None,
        use_cache: bool = True,
        refresh: bool = False,
    ) -> Optional[dict]:
        if not self._available():
            logger.debug("%s", self._unavailable_reason())
            return None

        params = dict(params or {})
        url = self._url(path)
        klass, credits = self._cost(path, params)

        key = cache_key(self.name, "GET", url, params)
        if self.cache is not None and use_cache and not refresh:
            hit = self.cache.get(key)
            if hit is not None:
                logger.debug("%s: cache hit (%s, saved %d credits)", self.name, path, credits)
                return hit

        if self.ledger is not None and credits:
            self.ledger.reserve(self.name, klass, credits)

        request_params = {**params, **self._auth_params()}
        headers = self._auth_headers()

        resp: Optional[requests.Response] = None
        for attempt in range(self.policy.retries):
            self._pace()
            try:
                resp = self.session.get(
                    url, params=request_params, headers=headers,
                    timeout=self.policy.timeout,
                )
            except requests.RequestException as e:
                logger.warning("%s: request error: %s", self.name, e)
                self._last_request_at = time.monotonic()
                if attempt < self.policy.retries - 1:
                    self._sleep_for_retry(None, attempt)
                    continue
                return None
            finally:
                self._last_request_at = time.monotonic()

            status = resp.status_code
            if status == 200:
                if self.ledger is not None and credits:
                    self.ledger.commit(self.name, klass, credits)
                try:
                    payload = resp.json()
                except ValueError:
                    logger.warning("%s: non-JSON response from %s", self.name, url)
                    return None
                if self.cache is not None and use_cache:
                    self.cache.put(
                        key, self.name, url, status, payload,
                        ttl if ttl is not None else self._ttl(path, params),
                        cost=credits,
                    )
                return payload

            if status in self.policy.quiet_statuses:
                if status in (401, 403):
                    logger.error("%s: authentication rejected (HTTP %s)", self.name, status)
                return None

            if status in self.policy.retry_statuses and attempt < self.policy.retries - 1:
                logger.warning("%s: HTTP %s for %s", self.name, status, path)
                self._sleep_for_retry(resp, attempt)
                continue

            logger.warning("%s: HTTP %s for %s", self.name, status, path)
            return None
        return None

    def get_paged(
        self,
        path: str,
        params: Optional[dict] = None,
        *,
        page_size: int = 200,
        max_items: int = 1000,
        results_key: str = "results",
    ) -> Iterator[dict]:
        """Cursor-paginate a list endpoint (OpenAlex-style). Page paging caps at
        10,000 results; cursor paging does not."""
        params = dict(params or {})
        params["per-page"] = min(page_size, 200)
        cursor = "*"
        fetched = 0
        while fetched < max_items:
            params["cursor"] = cursor
            data = self.get(path, params)
            if not data:
                return
            rows = data.get(results_key) or []
            if not rows:
                return
            for row in rows:
                if fetched >= max_items:
                    return
                yield row
                fetched += 1
            cursor = (data.get("meta") or {}).get("next_cursor")
            if not cursor:
                return
