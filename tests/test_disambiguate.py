"""Author resolution: the layer that used to return the wrong person."""

import pytest

from scholarlib.pipeline.disambiguate import (
    AuthorCandidate, _field_matches, _same_person, is_orcid, looks_like_orcid,
    orcid_checksum_ok, score_candidate,
)


class TestOrcidValidation:
    @pytest.mark.parametrize("orcid", [
        "0000-0002-1825-0097",          # Josiah Carberry, the canonical test iD
        "0000-0001-9767-7832",
        "https://orcid.org/0000-0002-1825-0097",
    ])
    def test_valid(self, orcid):
        assert is_orcid(orcid)

    def test_placeholder_rejected_by_checksum(self):
        """OpenAlex holds an author record carrying this value."""
        assert looks_like_orcid("0000-0000-0000-0000")
        assert not orcid_checksum_ok("0000-0000-0000-0000")
        assert not is_orcid("0000-0000-0000-0000")

    def test_names_are_not_orcids(self):
        assert not is_orcid("Anna Tsing")


class TestFieldMatching:
    @pytest.mark.parametrize("hint,topics,expected", [
        # OpenAlex labels the topic "Anthropological Studies", so a plain
        # substring test for "Anthropology" would penalise the right person.
        ("Anthropology", ["Anthropological Studies and Insights"], True),
        ("Sociology", ["Sociological Theory"], True),
        ("Archaeology", ["Archaeological Research and Methods"], True),
        ("Anthropology", ["Anesthesia and Sedative Agents"], False),
        ("Anthropology", ["Diverse Aspects of Tourism Research"], False),
    ])
    def test_stem_matching(self, hint, topics, expected):
        assert _field_matches(hint, topics) is expected


class TestScoring:
    def _cand(self, name, topics, works, insts=()):
        return AuthorCandidate(id="x", name=name, topics=list(topics),
                               works_count=works, institutions=list(insts))

    def test_works_count_no_longer_decides(self):
        """The original bug: 'Tuomas Tammisto' resolved to a 1970s
        anaesthesiologist because that record had 198 works."""
        anthropologist = score_candidate(
            self._cand("Tuomas Tammisto", ["Anthropological Studies and Insights"], 1),
            name="Tuomas Tammisto", field_hint="Anthropology")
        anaesthetist = score_candidate(
            self._cand("T Tammisto", ["Anesthesia and Sedative Agents"], 198),
            name="Tuomas Tammisto", field_hint="Anthropology")
        assert anthropologist.score > anaesthetist.score

    def test_institution_hint_helps(self):
        with_inst = score_candidate(
            self._cand("A B", ["Topic"], 5, ["University of Helsinki"]),
            name="A B", institution="Helsinki")
        without = score_candidate(
            self._cand("A B", ["Topic"], 5, ["Tampere University"]),
            name="A B", institution="Helsinki")
        assert with_inst.score > without.score


class TestSamePerson:
    def _c(self, name, insts=()):
        return AuthorCandidate(id="x", name=name, institutions=list(insts))

    def test_split_records_for_one_person(self):
        """OpenAlex routinely holds several records for one researcher."""
        assert _same_person(
            self._c("Heikki Wilenius", ["University of Helsinki"]),
            self._c("Heikki Wilenius", ["University of Helsinki"]))

    def test_missing_institution_is_not_a_conflict(self):
        assert _same_person(self._c("Tuomas Tammisto"),
                            self._c("Tuomas Tammisto", ["University of Helsinki"]))

    def test_different_names_are_different_people(self):
        assert not _same_person(self._c("T Tammisto"), self._c("Tuomas Tammisto"))

    def test_conflicting_institutions_are_different_people(self):
        assert not _same_person(
            self._c("J Smith", ["Harvard University"]),
            self._c("J Smith", ["Tampere University"]))
