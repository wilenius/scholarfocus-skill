"""Per-API retry, backoff and pacing policies.

The four original clients differed only in backoff shape, which statuses they
swallowed quietly, and their inter-request sleep. That is data, not code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class RetryPolicy:
    retries: int = 3
    backoff: Literal["exp", "linear"] = "exp"
    base_delay: float = 1.0
    max_delay: float = 120.0
    jitter: float = 0.25
    min_interval: float = 0.0
    timeout: float = 30.0
    retry_statuses: frozenset[int] = field(
        default_factory=lambda: frozenset({429, 500, 502, 503, 504})
    )
    quiet_statuses: frozenset[int] = field(default_factory=lambda: frozenset({404}))
    respect_retry_after: bool = True

    def delay_for(self, attempt: int) -> float:
        """Seconds to wait before retry `attempt` (0-based)."""
        if self.backoff == "exp":
            d = self.base_delay * (2 ** attempt)
        else:
            d = self.base_delay * (attempt + 1)
        return min(d, self.max_delay)


POLICIES: dict[str, RetryPolicy] = {
    "openalex": RetryPolicy(backoff="exp", base_delay=2.0, min_interval=0.10),
    "crossref": RetryPolicy(backoff="linear", base_delay=3.0, min_interval=0.12),
    "core": RetryPolicy(
        backoff="linear",
        base_delay=5.0,
        min_interval=0.20,
        quiet_statuses=frozenset({400, 404}),
    ),
    "semantic_scholar": RetryPolicy(
        backoff="linear",
        base_delay=5.0,
        min_interval=1.10,
        quiet_statuses=frozenset({400, 403, 404}),
    ),
    "unpaywall": RetryPolicy(min_interval=0.10),
    "openaire": RetryPolicy(base_delay=2.0, min_interval=0.20),
    "zotero": RetryPolicy(retries=1, min_interval=0.0, timeout=5.0),
}


def policy_for(name: str) -> RetryPolicy:
    return POLICIES.get(name, RetryPolicy())
