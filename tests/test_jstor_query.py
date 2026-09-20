"""JSTOR FTS query construction."""

from scholarlib.jstor.query import _fts_query, _fts_tokens


class TestFtsQuery:
    def test_tokens_are_quoted(self):
        assert _fts_query("multispecies ethnography") == '"multispecies" "ethnography"'

    def test_or_operator(self):
        assert _fts_query("plantation ethnography", operator="OR") == \
            '"plantation" OR "ethnography"'

    def test_fts_syntax_characters_are_stripped(self):
        """Unescaped FTS5 operators would raise sqlite3.OperationalError."""
        for raw in ['a "quoted" phrase', "a (paren) b", "a*b", "col:val", "a^b", "a-b"]:
            q = _fts_query(raw)
            assert '(' not in q and ')' not in q and '*' not in q
            assert ':' not in q and '^' not in q
            # every quote is one we added around a token
            assert q.count('"') % 2 == 0

    def test_empty_input(self):
        assert _fts_query("") == ""
        assert _fts_query("   ") == ""
        assert _fts_tokens("") == []

    def test_single_token_has_no_fallback_need(self):
        assert len(_fts_tokens("ethnography")) == 1
