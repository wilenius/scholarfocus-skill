"""Shared state threaded through every pipeline stage."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from scholarlib.config import expand_path
from scholarlib.http.budget import CreditLedger, default_caps
from scholarlib.http.cache import HttpCache

logger = logging.getLogger(__name__)


@dataclass
class Context:
    cfg: dict
    cache: Optional[HttpCache] = None
    ledger: Optional[CreditLedger] = None
    clients: dict = field(default_factory=dict)
    stats: dict = field(default_factory=dict)
    identified: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    truncated: Optional[dict] = None
    budget_mode: str = "fast"
    jstor_index: Any = None
    zotero_lookup: Any = None

    def warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)
        logger.warning("%s", msg)

    def count(self, stage: str, n: int) -> None:
        self.identified[stage] = self.identified.get(stage, 0) + n


def build_context(cfg: dict, *, use_cache: bool = True, refresh: bool = False,
                  budget_override: Optional[int] = None,
                  budget_mode: Optional[str] = None,
                  enable_s2ag: bool = False) -> Context:
    """Wire up cache, ledger and every client from config."""
    from scholarlib.apis.core_api import COREClient
    from scholarlib.apis.crossref import CrossRefClient
    from scholarlib.apis.openaire import OpenAIREClient
    from scholarlib.apis.openalex import OpenAlexClient
    from scholarlib.apis.semantic_scholar import SemanticScholarClient
    from scholarlib.apis.unpaywall import UnpaywallClient
    from scholarlib.apis.zotero_local import ZoteroLocalClient

    apis = cfg.get("apis") or {}
    cache_cfg = cfg.get("cache") or {}
    cache = HttpCache(
        expand_path(cache_cfg.get("path")),
        enabled=use_cache and cache_cfg.get("enabled", True),
    )
    caps = default_caps(cfg)
    if budget_override is not None:
        caps["openalex"] = budget_override
    ledger = CreditLedger(expand_path(cache_cfg.get("path")), caps)

    oa_cfg = apis.get("openalex") or {}
    has_key = bool(oa_cfg.get("api_key"))
    mode = budget_mode or ("fast" if has_key else "free")

    common = {"cache": cache, "ledger": ledger}
    s2_cfg = apis.get("semantic_scholar") or {}
    clients = {
        "openalex": OpenAlexClient(
            email=oa_cfg.get("email"), api_key=oa_cfg.get("api_key"), **common),
        "crossref": CrossRefClient(
            email=(apis.get("crossref") or {}).get("email"), **common),
        "core": COREClient(api_key=(apis.get("core") or {}).get("api_key"), **common),
        "openaire": OpenAIREClient(**common),
        "unpaywall": UnpaywallClient(
            email=(apis.get("unpaywall") or {}).get("email"), **common),
        "semantic_scholar": SemanticScholarClient(
            api_key=s2_cfg.get("api_key"),
            enabled=enable_s2ag or bool(s2_cfg.get("enabled")),
            **common),
        "zotero": ZoteroLocalClient(
            base_url=(cfg.get("zotero") or {}).get("base_url",
                                                   "http://localhost:23119/api/users/0"),
            enabled=bool((cfg.get("zotero") or {}).get("enabled", True)),
            **common),
    }
    return Context(cfg=cfg, cache=cache, ledger=ledger, clients=clients,
                   budget_mode=mode)
