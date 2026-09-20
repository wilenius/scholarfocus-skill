"""Build the JSTOR FTS5 index from the JSONL dump.

The dump has no abstracts, no references and no full text, so this index is a
*discovery and coverage-check* tool, not a citation graph.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Iterator, Optional

from scholarlib import dedup

logger = logging.getLogger(__name__)

try:
    import orjson

    def _loads(line: str | bytes):
        return orjson.loads(line)
except ImportError:
    def _loads(line: str | bytes):
        return json.loads(line)


SCHEMA_VERSION = "1"

# Substantive scholarship. Book reviews are ~80% of anthropology records and
# ~90% of them have no title at all, so they are excluded unless asked for.
KEEP_SUBTYPES = frozenset({"research-article", "chapter", "introduction", "review-article"})
KEEP_TYPES = frozenset({"book"})
REVIEW_SUBTYPES = frozenset({"book-review"})

LANG_ALIASES = {
    "en": "eng", "fr": "fre", "de": "ger", "it": "ita", "es": "spa",
    "pt": "por", "he": "heb", "nl": "dut", "ru": "rus", "ja": "jpn",
    "zh": "chi", "ar": "ara", "sv": "swe", "da": "dan", "no": "nor",
    "fi": "fin", "pl": "pol", "tr": "tur", "cs": "cze", "hu": "hun",
}

FTS_SETUP = """
CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
  title, is_part_of, creators_string,
  content='items', content_rowid='rowid',
  tokenize = "unicode61 remove_diacritics 2"
);
"""


def parse_published_date(s: Optional[str]) -> tuple[Optional[int], Optional[int]]:
    """JSTOR ships a truncated 9-char 'YYYY-MM-D'.

    The day digit is truncated rather than zero-padded, so it is unusable.
    Only year and month are recoverable.
    """
    if not isinstance(s, str) or len(s) < 7:
        return (None, None)
    try:
        y = int(s[0:4])
        m = int(s[5:7])
    except (ValueError, IndexError):
        return (None, None)
    return (y if 1400 <= y <= 2035 else None, m if 1 <= m <= 12 else None)


def derive_parent_doi(doi: Optional[str], content_type: Optional[str]) -> Optional[str]:
    """Chapters carry the book's DOI plus a suffix: 10.2307/j.ctt1xp3mt7.16."""
    if not doi or content_type != "book_part":
        return None
    parts = str(doi).rsplit(".", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0]
    return None


def normalize_lang(langs) -> Optional[str]:
    if not langs:
        return None
    first = str(langs[0]).lower() if isinstance(langs, list) else str(langs).lower()
    return LANG_ALIASES.get(first, first)


def first_surname_of(rec: dict) -> Optional[str]:
    creators = rec.get("creators")
    if isinstance(creators, list) and creators:
        ordered = sorted(creators, key=lambda c: c.get("order") or 0)
        ln = ordered[0].get("last_name")
        if ln:
            return dedup.normalize_surname(ln)
    cs = rec.get("creators_string")
    if cs:
        return dedup.normalize_surname(str(cs).split(",")[0])
    return None


def should_keep(rec: dict, *, keep_reviews: bool,
                disciplines: Optional[frozenset[str]],
                languages: Optional[frozenset[str]]) -> bool:
    if not rec.get("title"):
        return False
    subtype = rec.get("content_subtype")
    ctype = rec.get("content_type")
    is_review = subtype in REVIEW_SUBTYPES
    if is_review:
        if not keep_reviews:
            return False
    elif not (subtype in KEEP_SUBTYPES or ctype in KEEP_TYPES):
        return False
    if disciplines:
        names = rec.get("discipline_names") or []
        if not any(d in disciplines for d in names):
            return False
    if languages:
        if normalize_lang(rec.get("languages")) not in languages:
            return False
    return True


def to_row(rec: dict) -> tuple:
    ids = rec.get("identifiers") or {}
    title = rec.get("title") or ""
    year, month = parse_published_date(rec.get("published_date"))
    surname = first_surname_of(rec)
    creators = rec.get("creators") or []
    return (
        rec.get("item_id"),
        rec.get("ithaka_doi"),
        derive_parent_doi(rec.get("ithaka_doi"), rec.get("content_type")),
        title,
        rec.get("is_part_of"),
        rec.get("creators_string"),
        surname,
        len(creators) if isinstance(creators, list) else None,
        year,
        month,
        rec.get("content_type"),
        rec.get("content_subtype"),
        rec.get("c5_section_type"),
        normalize_lang(rec.get("languages")),
        ids.get("print_issn") or ids.get("online_issn"),
        ids.get("print_isbn") or ids.get("online_isbn"),
        ids.get("journal_code"),
        rec.get("issue_volume"),
        rec.get("issue_number"),
        rec.get("url"),
        rec.get("licensing_status"),
        dedup.normalize_title(title),
        dedup.title_fingerprint(title),
        dedup.match_key(title, surname, year),
    )


_INSERT = """
INSERT OR REPLACE INTO items (
  item_id, ithaka_doi, parent_doi, title, is_part_of, creators_string,
  first_surname, n_creators, year, month, content_type, content_subtype,
  section_type, lang, issn, isbn, journal_code, volume, issue, url,
  licensing, title_norm, title_fp, match_key
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""


def _source_fingerprint(path: Path) -> str:
    """Hash the first 64 MB plus size — hashing 1.3 GB to detect a change is wasteful."""
    h = hashlib.sha256()
    h.update(str(path.stat().st_size).encode())
    with open(path, "rb") as f:
        h.update(f.read(64 * 1024 * 1024))
    return h.hexdigest()[:32]


def _meta_get(conn: sqlite3.Connection, k: str) -> Optional[str]:
    row = conn.execute("SELECT v FROM build_meta WHERE k=?", (k,)).fetchone()
    return row[0] if row else None


def _meta_set(conn: sqlite3.Connection, k: str, v) -> None:
    conn.execute(
        "INSERT INTO build_meta(k,v) VALUES (?,?) "
        "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
        (k, str(v)),
    )


def _open_db(db: Path) -> sqlite3.Connection:
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    schema = (Path(__file__).parent / "schema.sql").read_text()
    conn.executescript(schema)
    conn.commit()
    return conn


def build(
    source: Path,
    db: Path,
    *,
    disciplines: Optional[frozenset[str]] = None,
    languages: Optional[frozenset[str]] = None,
    keep_reviews: bool = False,
    resume: bool = False,
    force: bool = False,
    batch_size: int = 5000,
    progress_every: int = 250_000,
    limit: Optional[int] = None,
) -> dict:
    source = Path(source).expanduser()
    db = Path(db).expanduser()
    if not source.exists():
        raise FileNotFoundError(f"JSTOR source not found: {source}")

    filters = json.dumps({
        "disciplines": sorted(disciplines) if disciplines else None,
        "languages": sorted(languages) if languages else None,
        "keep_reviews": keep_reviews,
    }, sort_keys=True)
    fingerprint = _source_fingerprint(source)

    conn = _open_db(db)
    prev_fp = _meta_get(conn, "source_fingerprint")
    prev_status = _meta_get(conn, "status")
    prev_filters = _meta_get(conn, "filters_json")
    skip_lines = 0

    if prev_fp == fingerprint and prev_status == "complete" and prev_filters == filters and not force:
        stats = {"status": "up-to-date",
                 "records": int(_meta_get(conn, "records_kept") or 0)}
        logger.info("Index is up to date (%s records). Use --force to rebuild.",
                    stats["records"])
        conn.close()
        return stats

    if prev_fp == fingerprint and prev_status == "partial" and resume and prev_filters == filters:
        skip_lines = int(_meta_get(conn, "lines_read") or 0)
        logger.info("Resuming: skipping the first %s lines already indexed", f"{skip_lines:,}")
    elif prev_fp and prev_fp != fingerprint:
        logger.info("Source file changed — rebuilding (the OpenAlex join map is preserved)")
        conn.execute("DELETE FROM items")
        conn.execute("DELETE FROM item_disciplines")
        conn.commit()

    # Bulk-load pragmas; restored afterwards.
    conn.executescript("PRAGMA synchronous=OFF; PRAGMA journal_mode=MEMORY; "
                       "PRAGMA cache_size=-262144; PRAGMA temp_store=MEMORY;")

    _meta_set(conn, "schema_version", SCHEMA_VERSION)
    _meta_set(conn, "source_path", str(source))
    _meta_set(conn, "source_fingerprint", fingerprint)
    _meta_set(conn, "filters_json", filters)
    _meta_set(conn, "status", "partial")
    conn.commit()

    t0 = time.time()
    lines = kept = 0
    rows: list[tuple] = []
    disc_rows: list[tuple] = []

    def flush():
        if rows:
            conn.executemany(_INSERT, rows)
            rows.clear()
        if disc_rows:
            conn.executemany(
                "INSERT OR IGNORE INTO item_disciplines(item_id, discipline) VALUES (?,?)",
                disc_rows,
            )
            disc_rows.clear()

    with gzip.open(source, "rt", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            lines += 1
            if lines <= skip_lines:
                continue
            try:
                rec = _loads(line)
            except Exception:
                continue
            if not should_keep(rec, keep_reviews=keep_reviews,
                               disciplines=disciplines, languages=languages):
                continue
            rows.append(to_row(rec))
            item_id = rec.get("item_id")
            for d in (rec.get("discipline_names") or []):
                disc_rows.append((item_id, d))
            kept += 1

            if len(rows) >= batch_size:
                flush()
            if lines % progress_every == 0:
                el = time.time() - t0
                rate = (lines - skip_lines) / el if el else 0
                logger.info("  %s lines, %s kept, %.0f lines/s", f"{lines:,}", f"{kept:,}", rate)
                _meta_set(conn, "lines_read", lines)
                _meta_set(conn, "records_kept", kept)
                conn.commit()
            if limit and kept >= limit:
                break

    flush()
    conn.commit()

    logger.info("Creating indexes and building the full-text index...")
    conn.executescript((Path(__file__).parent / "indexes.sql").read_text())
    conn.executescript(FTS_SETUP)
    conn.execute("INSERT INTO items_fts(items_fts) VALUES('rebuild')")
    conn.commit()

    total = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    _meta_set(conn, "lines_read", lines)
    _meta_set(conn, "records_kept", total)
    _meta_set(conn, "status", "complete")
    _meta_set(conn, "built_at", int(time.time()))
    conn.commit()

    conn.executescript("PRAGMA synchronous=FULL; PRAGMA journal_mode=WAL;")
    conn.execute("ANALYZE")
    conn.execute("PRAGMA optimize")
    conn.commit()
    conn.close()

    elapsed = time.time() - t0
    logger.info("Done: %s lines read, %s records indexed in %.1f min",
                f"{lines:,}", f"{total:,}", elapsed / 60)
    return {
        "status": "complete",
        "lines_read": lines,
        "records": total,
        "elapsed_s": round(elapsed, 1),
        "db": str(db),
    }
