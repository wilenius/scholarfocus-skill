CREATE INDEX IF NOT EXISTS ix_items_match_key ON items(match_key);
CREATE INDEX IF NOT EXISTS ix_items_title_fp  ON items(title_fp);
CREATE INDEX IF NOT EXISTS ix_items_year      ON items(year);
CREATE INDEX IF NOT EXISTS ix_items_parent    ON items(parent_doi);
CREATE INDEX IF NOT EXISTS ix_items_surname   ON items(first_surname, year);
CREATE INDEX IF NOT EXISTS ix_items_doi       ON items(ithaka_doi);
CREATE INDEX IF NOT EXISTS ix_disc            ON item_disciplines(discipline, item_id);
CREATE INDEX IF NOT EXISTS ix_map_oa          ON jstor_openalex_map(openalex_id);
