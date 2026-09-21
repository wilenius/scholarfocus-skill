"""Open-access resolution via Unpaywall: can the user actually read this?"""

from __future__ import annotations

import logging

from scholarlib import dedup
from scholarlib.pipeline.context import Context
from scholarlib.progress import heartbeat
from scholarlib.records import Record

logger = logging.getLogger(__name__)


def enrich(ctx: Context, records: list[Record], *, limit: int = 300) -> list[Record]:
    up = ctx.clients.get("unpaywall")
    if up is None or not up._available():
        ctx.warn("Unpaywall skipped: no email configured (apis.unpaywall.email)")
        return records

    # Unpaywall is asked only what OpenAlex could not already answer.
    #
    # OpenAlex ships `open_access`/`best_oa_location` in the list response and
    # the adapter already reads it, and OpenAlex ingests Unpaywall's own data.
    # So re-probing a record OpenAlex affirmatively marked `closed` asks the
    # same source twice: measured over 300 such records it produced 0 links.
    # Probe only where the status is genuinely unknown.
    #
    # Books are excluded outright. Unpaywall is article-centric: it reports
    # Kelty's Two Bits and Coleman's Coding Freedom as closed although both are
    # free and CC-licensed, and it 404s on some university-press book DOIs.
    # For monographs the access signals that work are `in_zotero` and the JSTOR
    # match, both already computed at zero cost.
    skipped_books = 0
    targets = []
    for r in records:
        if not r.doi or r.oa_url or dedup.is_jstor_internal_doi(r.doi):
            continue
        if r.type in ("book", "book-chapter"):
            skipped_books += 1
            continue
        if r.oa_status:  # OpenAlex already resolved this one
            continue
        targets.append(r)
    targets = targets[:limit]
    if skipped_books:
        logger.info("Unpaywall: skipped %d book/chapter record(s); "
                    "monograph access comes from JSTOR and Zotero instead",
                    skipped_books)
    found = 0
    for rec in heartbeat(targets, "unpaywall", logger=logger, min_units=50):
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
