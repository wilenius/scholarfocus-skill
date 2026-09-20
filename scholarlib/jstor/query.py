"""Query the local JSTOR index.

The free direction is OpenAlex-record -> local FTS lookup. The reverse
(JSTOR row -> OpenAlex) needs a 10-credit title search, so it lives in the
pipeline behind a hard cap and is memoised in jstor_openalex_map.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from pathlib import Path
from typing import Iterable, Optional

from scholarlib import dedup
from scholarlib.records import Record

logger = logging.getLogger(__name__)

_TYPE_MAP = {
    "article": "article",
    "book": "book",
    "book_part": "chapter",
}

# FTS5 treats these as syntax; strip them from user text.
_FTS_UNSAFE = re.compile(r'["\'()*:^-]')


def _fts_tokens(text: str) -> list[str]:
    cleaned = _FTS_UNSAFE.sub(" ", text or "")
    return [t for t in cleaned.split() if t]


def _fts_query(text: str, *, operator: str = "AND") -> str:
    """Build an FTS5 query.

    FTS5 ANDs bare terms. The index covers only title, journal and author --
    there are no abstracts -- so requiring every term in that little text is
    very strict: "plantation ethnography" matches nothing across 6.6M records
    even though each word alone matches over a thousand.
    """
    tokens = _fts_tokens(text)
    if not tokens:
        return ""
    joiner = " OR " if operator == "OR" else " "
    return joiner.join(f'"{t}"' for t in tokens)


class JstorIndex:
    def __init__(self, db_path: Path):
        self.path = Path(db_path).expanduser()
        if not self.path.exists():
            raise FileNotFoundError(
                f"JSTOR index not found at {self.path}. Build it with: "
                "python -m scholarlib.cli.jstor_index build"
            )
        self.conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row

    # ---- introspection --------------------------------------------------

    def stats(self) -> dict:
        c = self.conn
        meta = {r["k"]: r["v"] for r in c.execute("SELECT k, v FROM build_meta")}
        return {
            "path": str(self.path),
            "records": c.execute("SELECT COUNT(*) FROM items").fetchone()[0],
            "by_type": dict(c.execute(
                "SELECT content_type, COUNT(*) FROM items GROUP BY 1 ORDER BY 2 DESC"
            ).fetchall()),
            "top_disciplines": dict(c.execute(
                "SELECT discipline, COUNT(*) FROM item_disciplines "
                "GROUP BY 1 ORDER BY 2 DESC LIMIT 12"
            ).fetchall()),
            "languages": dict(c.execute(
                "SELECT lang, COUNT(*) FROM items GROUP BY 1 ORDER BY 2 DESC LIMIT 10"
            ).fetchall()),
            "year_range": tuple(c.execute(
                "SELECT MIN(year), MAX(year) FROM items WHERE year IS NOT NULL"
            ).fetchone()),
            "openalex_map_rows": c.execute(
                "SELECT COUNT(*) FROM jstor_openalex_map"
            ).fetchone()[0],
            "build": {k: meta.get(k) for k in
                      ("status", "built_at", "records_kept", "filters_json", "source_path")},
        }

    # ---- retrieval ------------------------------------------------------

    def _row_to_record(self, row: sqlite3.Row, score: float = 0.0) -> Record:
        return Record(
            doi=row["ithaka_doi"],
            jstor_item_id=row["item_id"],
            title=row["title"],
            authors=[row["creators_string"]] if row["creators_string"] else [],
            first_surname=row["first_surname"],
            year=row["year"],
            venue=row["is_part_of"],
            type=_TYPE_MAP.get(row["content_type"], row["content_type"]),
            language=row["lang"],
            provenance=["jstor"],
            score=score,
            jstor={
                "item_id": row["item_id"],
                "url": row["url"],
                "content_type": row["content_type"],
                "content_subtype": row["content_subtype"],
                "parent_doi": row["parent_doi"],
                "isbn": row["isbn"],
                "issn": row["issn"],
            },
        )

    def search(
        self,
        text: str,
        *,
        disciplines: Optional[Iterable[str]] = None,
        types: Optional[Iterable[str]] = None,
        from_year: Optional[int] = None,
        to_year: Optional[int] = None,
        languages: Optional[Iterable[str]] = None,
        limit: int = 50,
    ) -> list[Record]:
        if not _fts_tokens(text):
            return []
        rows = self._search_raw(text, "AND", disciplines, types, from_year,
                                to_year, languages, limit)
        if not rows and len(_fts_tokens(text)) > 1:
            # bm25 still ranks records matching more terms highest, so the
            # looser pass degrades relevance rather than precision.
            logger.debug("No AND match for %r; retrying with OR", text)
            rows = self._search_raw(text, "OR", disciplines, types, from_year,
                                    to_year, languages, limit)
        return [self._row_to_record(r) for r in rows]

    def _search_raw(self, text, operator, disciplines, types, from_year,
                    to_year, languages, limit):
        q = _fts_query(text, operator=operator)
        if not q:
            return []
        sql = [
            "SELECT i.*, bm25(items_fts) AS rank FROM items_fts",
            "JOIN items i ON i.rowid = items_fts.rowid",
            "WHERE items_fts MATCH ?",
        ]
        params: list = [q]
        if disciplines:
            marks = ",".join("?" * len(list(disciplines)))
            sql.append(
                f"AND i.item_id IN (SELECT item_id FROM item_disciplines "
                f"WHERE discipline IN ({marks}))"
            )
            params.extend(disciplines)
        if types:
            wanted = {k for k, v in _TYPE_MAP.items() if v in set(types)} | set(types)
            sql.append(f"AND i.content_type IN ({','.join('?' * len(wanted))})")
            params.extend(sorted(wanted))
        if from_year:
            sql.append("AND i.year >= ?")
            params.append(from_year)
        if to_year:
            sql.append("AND i.year <= ?")
            params.append(to_year)
        if languages:
            langs = list(languages)
            sql.append(f"AND i.lang IN ({','.join('?' * len(langs))})")
            params.extend(langs)
        sql.append("ORDER BY rank LIMIT ?")
        params.append(limit)

        return self.conn.execute(" ".join(sql), params).fetchall()

    def find_by_doi(self, doi: str) -> Optional[Record]:
        d = str(doi).strip()
        row = self.conn.execute(
            "SELECT * FROM items WHERE ithaka_doi = ? LIMIT 1", (d,)
        ).fetchone()
        return self._row_to_record(row) if row else None

    def find_match(
        self, rec: Record, *, threshold: float = 0.90, year_slack: int = 1
    ) -> tuple[Optional[Record], float, str]:
        """OpenAlex record -> JSTOR row. Costs nothing; this is the default direction."""
        if rec.doi:
            hit = self.find_by_doi(rec.doi)
            if hit:
                return hit, 1.0, "doi"

        if not dedup.titles_comparable(rec.title):
            return None, 0.0, "title-too-generic"

        # 1. exact blocking key
        mk = dedup.match_key(rec.title, rec.first_surname, rec.year)
        row = self.conn.execute(
            "SELECT * FROM items WHERE match_key = ? LIMIT 1", (mk,)
        ).fetchone()
        if row:
            return self._row_to_record(row, 1.0), 1.0, "match_key"

        # 2. (surname, year +/- slack) block
        if rec.first_surname and rec.year:
            rows = self.conn.execute(
                "SELECT * FROM items WHERE first_surname = ? AND year BETWEEN ? AND ?",
                (rec.first_surname, rec.year - year_slack, rec.year + year_slack),
            ).fetchall()
            best, best_score = None, 0.0
            for r in rows:
                s = dedup.best_title_similarity(rec.title, r["title"])
                if s > best_score:
                    best, best_score = r, s
            if best is not None and best_score >= threshold:
                return self._row_to_record(best, best_score), best_score, "surname_year"

        # 3. FTS on the main title, verified by surname
        cands = self.search(dedup.title_main(rec.title) or rec.title, limit=20)
        best, best_score = None, 0.0
        for c in cands:
            s = dedup.best_title_similarity(rec.title, c.title)
            if rec.first_surname and c.first_surname and rec.first_surname != c.first_surname:
                s *= 0.7
            if rec.year and c.year and abs(rec.year - c.year) > year_slack:
                s *= 0.9
            if s > best_score:
                best, best_score = c, s
        if best is not None and best_score >= threshold:
            best.score = best_score
            return best, best_score, "fts"

        return None, best_score, "none"

    def coverage(self, records: Iterable[Record]) -> dict:
        """How much of a result set JSTOR also holds, and what it adds."""
        records = list(records)
        matched, unmatched = [], []
        for r in records:
            hit, score, method = self.find_match(r)
            (matched if hit else unmatched).append(r)
        return {
            "checked": len(records),
            "in_jstor": len(matched),
            "not_in_jstor": len(unmatched),
            "coverage_rate": round(len(matched) / len(records), 3) if records else 0.0,
        }

    # ---- the expensive direction's memo table ---------------------------

    def get_map(self, item_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM jstor_openalex_map WHERE item_id = ?", (item_id,)
        ).fetchone()
        return dict(row) if row else None

    def close(self) -> None:
        self.conn.close()


def put_map(db_path: Path, item_id: str, openalex_id: Optional[str],
            doi: Optional[str], score: float, method: str) -> None:
    """Write-side of the join memo. Misses are recorded too — a confirmed
    'no match' is worth 10 credits and must never be paid for twice."""
    conn = sqlite3.connect(str(Path(db_path).expanduser()))
    conn.execute(
        "INSERT INTO jstor_openalex_map(item_id, openalex_id, doi, score, method, checked_at) "
        "VALUES (?,?,?,?,?,?) ON CONFLICT(item_id) DO UPDATE SET "
        "openalex_id=excluded.openalex_id, doi=excluded.doi, score=excluded.score, "
        "method=excluded.method, checked_at=excluded.checked_at",
        (item_id, openalex_id, doi, score, method, int(time.time())),
    )
    conn.commit()
    conn.close()
