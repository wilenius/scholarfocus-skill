PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS items (
  rowid           INTEGER PRIMARY KEY,
  item_id         TEXT NOT NULL UNIQUE,
  ithaka_doi      TEXT,
  parent_doi      TEXT,
  title           TEXT NOT NULL,
  is_part_of      TEXT,
  creators_string TEXT,
  first_surname   TEXT,
  n_creators      INTEGER,
  year            INTEGER,
  month           INTEGER,
  content_type    TEXT,
  content_subtype TEXT,
  section_type    TEXT,
  lang            TEXT,
  issn            TEXT,
  isbn            TEXT,
  journal_code    TEXT,
  volume          TEXT,
  issue           TEXT,
  url             TEXT,
  licensing       TEXT,
  title_norm      TEXT NOT NULL,
  title_fp        TEXT NOT NULL,
  match_key       TEXT
);

CREATE TABLE IF NOT EXISTS item_disciplines (
  item_id    TEXT NOT NULL,
  discipline TEXT NOT NULL,
  PRIMARY KEY (item_id, discipline)
);

-- Permanent: each row can cost a 10-credit OpenAlex search, so it is never
-- TTL-expired and must survive index rebuilds. Misses are cached too.
CREATE TABLE IF NOT EXISTS jstor_openalex_map (
  item_id     TEXT PRIMARY KEY,
  openalex_id TEXT,
  doi         TEXT,
  score       REAL,
  method      TEXT,
  checked_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS build_meta (k TEXT PRIMARY KEY, v TEXT);
