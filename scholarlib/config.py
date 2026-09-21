"""Configuration loading, path resolution and repo-root discovery."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)


class ConfigError(RuntimeError):
    """Raised when configuration is missing or malformed (exit code 3)."""


DEFAULT_CFG: dict = {
    "apis": {
        "openalex": {"email": None, "api_key": None},
        "semantic_scholar": {"api_key": None, "enabled": False},
        "crossref": {"email": None},
        "core": {"api_key": None},
        "unpaywall": {"email": None},
        "openaire": {},
    },
    "zotero": {
        "enabled": False,
        "base_url": "http://localhost:23119/api/users/0",
    },
    "jstor": {
        "source_path": None,
        "db_path": "~/.local/share/scholarlib/jstor.sqlite",
        "disciplines": None,
        "keep_reviews": False,
    },
    "cache": {
        "path": "~/.cache/scholarlib/http.sqlite",
        "enabled": True,
    },
    "budget": {
        "openalex": {"daily_cap": None},
    },
    "analysis": {
        "max_works_per_researcher": 100,
        "max_references_per_work": 50,
        "max_cited_works_fetched": 200,
        "max_singleton_topups": 50,
        "top_n_partners": 15,
        "top_n_cited_works": 20,
        "top_n_keywords": 25,
        "min_collaboration_count": 2,
        "concept_min_level": 1,
        "concept_max_level": 4,
    },
    "disambiguation": {
        "auto_accept": 0.75,
        "margin": 0.15,
    },
}


def repo_root(start: Optional[Path] = None) -> Path:
    """Walk upward until a directory containing scholarlib/__init__.py is found."""
    p = (start or Path(__file__).resolve()).parent
    for cand in [p, *p.parents]:
        if (cand / "scholarlib" / "__init__.py").is_file():
            return cand
    raise ConfigError(
        "Could not locate the scholarlib package root. "
        "Run from the repo root, or `pip install -e <repo>`."
    )


def find_config(explicit: Optional[str] = None) -> Optional[Path]:
    """Resolve the config file: --config > $SCHOLARLIB_CONFIG > repo root > XDG."""
    if explicit:
        p = Path(explicit).expanduser()
        if not p.exists():
            raise ConfigError(f"Config file not found: {p}")
        return p
    env = os.environ.get("SCHOLARLIB_CONFIG")
    if env and Path(env).expanduser().exists():
        return Path(env).expanduser()
    try:
        candidate = repo_root() / "config.yaml"
    except ConfigError:
        candidate = None
    if candidate and candidate.exists():
        return candidate
    xdg = Path("~/.config/scholarlib/config.yaml").expanduser()
    return xdg if xdg.exists() else None


USER_CONFIG_PATH = Path("~/.config/scholarlib/config.yaml")

TEMPLATE_PATH = Path(__file__).resolve().parent / "config.example.yaml"


def user_config_path() -> Path:
    """The per-user config location used when there is no repo to sit in."""
    return USER_CONFIG_PATH.expanduser()


def init_config(dest: Optional[str] = None, *, force: bool = False) -> Path:
    """Write the bundled config template to `dest` (default: the XDG path)."""
    target = Path(dest).expanduser() if dest else user_config_path()
    if target.exists() and not force:
        raise ConfigError(
            f"{target} already exists — edit it, or pass a different path."
        )
    if not TEMPLATE_PATH.is_file():
        raise ConfigError(f"Bundled config template is missing: {TEMPLATE_PATH}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(TEMPLATE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    return target


def deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base (in place) and return base."""
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def expand_path(value: Any) -> Optional[Path]:
    """Expand ~ and environment variables in a config path value."""
    if not value:
        return None
    return Path(os.path.expandvars(str(value))).expanduser()


def load_config(path: Optional[str] = None) -> dict:
    """Load config, layering the user file over DEFAULT_CFG."""
    cfg = deep_merge({}, DEFAULT_CFG)
    resolved = find_config(path)
    if resolved is None:
        logger.warning(
            "No config file found — using defaults (most APIs will be limited). "
            "Run `scholarfocus --init-config` to create %s",
            user_config_path(),
        )
        return cfg
    try:
        with open(resolved) as f:
            user_cfg = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"Could not parse {resolved}: {e}") from e
    if not isinstance(user_cfg, dict):
        raise ConfigError(f"{resolved} must contain a YAML mapping at the top level")
    deep_merge(cfg, user_cfg)
    cfg["_config_path"] = str(resolved)
    return cfg


def warn_if_no_openalex_key(cfg: dict) -> None:
    """OpenAlex is usage-priced; a free key is a 10x budget increase."""
    if not (cfg.get("apis", {}).get("openalex", {}) or {}).get("api_key"):
        logger.warning(
            "OpenAlex running without an API key: ~1000 credits/day (~$0.10). "
            "A free key at https://openalex.org/settings/api raises this ~10x. "
            "Put it under apis.openalex.api_key in %s",
            find_config() or user_config_path(),
        )


# Backwards-compatible aliases
_deep_merge = deep_merge
