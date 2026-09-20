"""Open-access resolution via Unpaywall: can the user actually read this?"""

from __future__ import annotations

import logging

from scholarlib import dedup
from scholarlib.pipeline.context import Context
from scholarlib.records import Record

logger = logging.getLogger(__name__)


def enrich(ctx: Context, records: list[Record], *, limit: int = 300) -> list[Record]:
    up = ctx.clients.get("unpaywall")
    if up is None or not up._available():
        ctx.warn("Unpaywall skipped: no email configured (apis.unpaywall.email)")
        return records

    # JSTOR-internal DOIs are not registered, so Unpaywall cannot resolve them.
    targets = [
        r for r in records
        if r.doi and not r.oa_url and not dedup.is_jstor_internal_doi(r.doi)
    ][:limit]
    found = 0
    for rec in targets:
        status, url = up.oa_for(rec.doi)
        if status:
            rec.oa_status = rec.oa_status or status
        if url:
            rec.oa_url = url
            rec.provenance.append("oa:unpaywall")
            found += 1
    logger.info("Unpaywall: %d/%d records gained a full-text link", found, len(targets))
    ctx.stats["oa_links"] = found
    return records
