"""Credit classification — the numbers the whole budget strategy rests on."""

import pytest

from scholarlib.http.budget import (
    LIST, SEARCH, SINGLETON, BudgetExceeded, CreditLedger, classify, default_caps,
)


@pytest.mark.parametrize("path,params,expected", [
    ("/works", {"search": "ethnography"}, SEARCH),
    ("/works", {"filter": "title.search:ethnography"}, SEARCH),
    ("/works", {"filter": "cites:W123"}, LIST),
    ("/works", {"filter": "ids.openalex:W1|W2"}, LIST),
    ("/works", {"filter": "x", "group_by": "publication_year"}, LIST),
    ("/works", {}, LIST),
    ("/works/W2741809807", {}, SINGLETON),
    ("/works/doi:10.1234/xyz", {}, SINGLETON),
    ("/authors/A5023888391", {}, SINGLETON),
    ("/works/W1", {"select": "id,title"}, SINGLETON),
])
def test_classify(path, params, expected):
    assert classify(path, params) == expected


def test_cap_depends_on_api_key():
    assert default_caps({"apis": {"openalex": {}}})["openalex"] == 900
    assert default_caps({"apis": {"openalex": {"api_key": "k"}}})["openalex"] == 9000


def test_explicit_cap_overrides():
    cfg = {"apis": {"openalex": {}}, "budget": {"openalex": {"daily_cap": 50}}}
    assert default_caps(cfg)["openalex"] == 50


def test_reserve_raises_before_spending():
    ledger = CreditLedger(None, {"openalex": 15})
    ledger.commit("openalex", SEARCH, 10)
    ledger.reserve("openalex", LIST, 1)
    with pytest.raises(BudgetExceeded):
        ledger.reserve("openalex", SEARCH, 10)


def test_free_requests_never_blocked():
    ledger = CreditLedger(None, {"openalex": 0})
    ledger.reserve("openalex", SINGLETON, 0)
