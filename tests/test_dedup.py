"""Identity, normalisation and merge behaviour — the layer the old code lacked."""

import pytest

from scholarlib import dedup
from scholarlib.records import Record, dedup_records, merge_records


class TestDoi:
    @pytest.mark.parametrize("raw", [
        "10.1525/aa.1962.64.6",
        "10.1525/AA.1962.64.6",
        "https://doi.org/10.1525/aa.1962.64.6",
        "http://dx.doi.org/10.1525/AA.1962.64.6",
        "doi:10.1525/aa.1962.64.6",
        "  10.1525/aa.1962.64.6.  ",
    ])
    def test_all_forms_normalise_identically(self, raw):
        assert dedup.normalize_doi(raw) == "10.1525/aa.1962.64.6"

    @pytest.mark.parametrize("raw", [None, "", "not-a-doi", "10.1/x", "12.3456/x"])
    def test_non_dois_rejected(self, raw):
        assert dedup.normalize_doi(raw) is None

    def test_jstor_internal_detection(self):
        assert dedup.is_jstor_internal_doi("10.2307/667845")
        assert dedup.is_jstor_internal_doi("10.2307/community.37317744")
        # Book and chapter DOIs are genuinely registered.
        assert not dedup.is_jstor_internal_doi("10.2307/j.ctt1xp3mt7")
        assert not dedup.is_jstor_internal_doi("10.2307/jj.31550135.7")
        assert not dedup.is_jstor_internal_doi("10.1525/aa.1962.64.6")


class TestTitles:
    def test_diacritics_folded(self):
        assert dedup.normalize_title("A Kekchí Will") == "a kekchi will"
        assert dedup.normalize_title("Bronzezeitliche Gräber") == "bronzezeitliche graber"

    def test_typographic_punctuation_stripped(self):
        a = dedup.normalize_title("“I Can’t Tell You”: Conflict")
        b = dedup.normalize_title('"I Can\'t Tell You": Conflict')
        assert a == b

    def test_subtitle_split(self):
        assert dedup.title_main("The Mushroom at the End of the World: On the Possibility") == \
            "the mushroom at the end of the world"

    def test_generic_titles_refused(self):
        assert not dedup.titles_comparable("Cannibalism")
        assert dedup.titles_comparable("Economic Theory and Economic Anthropology")


class TestSurnames:
    @pytest.mark.parametrize("raw,expected", [
        ("Anna Tsing", "tsing"),
        ("Tsing, Anna Lowenhaupt", "tsing"),
        ("Jan van der Berg", "van der berg"),
        ("Tania Murray Li", "li"),
    ])
    def test_surname_extraction(self, raw, expected):
        assert dedup.normalize_surname(raw) == expected


class TestDedup:
    def test_doi_variants_merge_and_keep_richest_fields(self):
        out, stats = dedup_records([
            Record(doi="https://doi.org/10.1525/AE.2015.42.1",
                   title="Friction: An Ethnography of Global Connection",
                   authors=["Anna Tsing"], year=2005),
            Record(doi="doi:10.1525/ae.2015.42.1", title="Friction",
                   authors=["Anna Tsing"], year=2005, cited_by_count=3151),
        ])
        assert len(out) == 1
        assert out[0].cited_by_count == 3151
        assert out[0].title.startswith("Friction: An")

    def test_jstor_internal_doi_merges_with_publisher_doi(self):
        """The core JSTOR join case: one work, two identifiers."""
        out, _ = dedup_records([
            Record(doi="10.2307/667845", jstor_item_id="abc",
                   title="Economic Theory and Economic Anthropology",
                   authors=["S Cook"], year=1966),
            Record(doi="10.1525/aa.1962.64.6", openalex_id="https://openalex.org/W99",
                   title="Economic Theory and Economic Anthropology",
                   authors=["Scott Cook"], year=1966, cited_by_count=76),
        ])
        assert len(out) == 1
        r = out[0]
        assert r.key == "doi:10.1525/aa.1962.64.6", "publisher DOI must win identity"
        assert r.jstor_item_id == "abc" and r.openalex_id and r.cited_by_count == 76

    def test_distinct_registered_dois_stay_separate(self):
        out, _ = dedup_records([
            Record(doi="10.1000/aaa", title="Some Entirely Different Paper Title",
                   authors=["Scott Cook"], year=1966),
            Record(doi="10.1000/bbb", title="Some Entirely Different Paper Title",
                   authors=["Scott Cook"], year=1966),
        ])
        assert len(out) == 2

    def test_generic_titles_never_fuzzy_merge(self):
        out, _ = dedup_records([
            Record(title="Cannibalism", authors=["X Y"], year=1970),
            Record(title="Cannibalism", authors=["A B"], year=1971),
        ])
        assert len(out) == 2

    def test_longest_abstract_wins_with_its_source(self):
        m = merge_records(
            Record(doi="10.1000/x", title="T", abstract="short", abstract_source="crossref"),
            Record(doi="10.1000/x", title="T", abstract="a much longer abstract",
                   abstract_source="openalex"),
        )
        assert m.abstract == "a much longer abstract"
        assert m.abstract_source == "openalex"

    def test_stats_report_duplicate_count(self):
        _, stats = dedup_records([
            Record(doi="10.1000/x", title="Paper One Title Here", year=2000),
            Record(doi="10.1000/x", title="Paper One Title Here", year=2000),
            Record(doi="10.1000/y", title="Paper Two Title Here", year=2001),
        ])
        assert stats["input"] == 3
        assert stats["output"] == 2
        assert stats["duplicates_removed"] == 1


class TestSubtitleMatching:
    """Sources disagree about subtitles; matching must survive that.

    Regression: a Zotero library holding the full title of Tsing's 2015 book
    failed to match a citation giving only the main title. Full-string
    similarity is 0.61 there, below any sane threshold.
    """

    FULL = ("The mushroom at the end of the world: "
            "on the possibility of life in capitalist ruins")
    SHORT = "The Mushroom at the End of the World"

    def test_full_string_similarity_is_insufficient(self):
        assert dedup.title_similarity(self.FULL, self.SHORT) < 0.93

    def test_main_title_comparison_rescues_it(self):
        assert dedup.best_title_similarity(self.FULL, self.SHORT) == 1.0

    def test_generic_main_titles_do_not_match_alone(self):
        """'Introduction: X' vs 'Introduction: Y' must not collapse."""
        assert dedup.best_title_similarity(
            "Introduction: Multispecies Worlds", "Introduction: Something Else"
        ) < 0.93

    def test_short_main_titles_fall_back_to_full_comparison(self):
        assert dedup.best_title_similarity(
            "Cannibalism: A History", "Cannibalism: An Ethnography"
        ) < 0.93
