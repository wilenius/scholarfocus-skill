"""Abstract assembly.

OpenAlex first: ~92% of works carry an inverted index, and decoding it is
free because the field already ships in the list response.
"""

from __future__ import annotations

import logging
from typing import Optional

from scholarlib.pipeline.context import Context
from scholarlib.progress import heartbeat
from scholarlib.records import Record

logger = logging.getLogger(__name__)

CHAIN = ("openalex", "core", "openaire", "crossref", "semantic_scholar")


def assemble(ctx: Context, records: list[Record], *, level: str = "cheap",
             chain: tuple = CHAIN, limit: int = 200) -> list[Record]:
    """level='cheap' stops after OpenAlex; 'full' walks the fallback chain."""
    have = sum(1 for r in records if r.abstract)
    logger.info("Abstracts: %d/%d already present from OpenAlex", have, len(records))
    ctx.stats.setdefault("abstracts", {})["openalex"] = have

    if level == "none" or level == "cheap":
        return records

    missing = [r for r in records if not r.abstract and r.doi][:limit]
    if not missing:
        return records
    logger.info("Filling %d missing abstracts via %s",
                len(missing), ", ".join(c for c in chain if c != "openalex"))

    counts: dict[str, int] = {}
    for rec in heartbeat(missing, "abstracts", logger=logger, min_units=50):
        for source in chain:
            if source == "openalex" or rec.abstract:
                continue
            client = ctx.clients.get(source)
            if client is None:
                continue
            try:
                text = client.get_abstract(rec.doi)
            except Exception as e:  # a dead source must not kill the run
                logger.debug("%s abstract lookup failed: %s", source, e)
                continue
            if text and len(text) > 20:
                rec.abstract = text
                rec.abstract_source = source
                rec.provenance.append(f"abstract:{source}")
                counts[source] = counts.get(source, 0) + 1
                break
    for k, v in counts.items():
        ctx.stats.setdefault("abstracts", {})[k] = v
    logger.info("Abstract fallbacks filled: %s", counts or "none")
    return records
