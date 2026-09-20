"""Author resolution with scored candidates.

The original code picked max(works_count) among search hits, which silently
returned the wrong person: "Tuomas Tammisto" resolved to a 1970s
anaesthesiologist and produced a profile of Fentanyl, Halothane and Pentazocine.
works_count is now a small tie-breaker, not the decision.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from scholarlib import dedup

logger = logging.getLogger(__name__)

_ORCID_RE = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{3}[\dXx]$")


class AmbiguousAuthor(Exception):
    """Top candidates are too close to choose between (exit code 4)."""

    def __init__(self, query: str, candidates: list["AuthorCandidate"]):
        super().__init__(f"Ambiguous author: {query!r}")
        self.query = query
        self.candidates = candidates


@dataclass
class AuthorCandidate:
    id: str
    name: str
    orcid: Optional[str] = None
    institutions: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    works_count: int = 0
    cited_by_count: int = 0
    first_year: Optional[int] = None
    last_year: Optional[int] = None
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    raw: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "orcid": self.orcid,
            "institutions": self.institutions, "topics": self.topics[:5],
            "works_count": self.works_count, "cited_by_count": self.cited_by_count,
            "active_years": [self.first_year, self.last_year],
            "score": round(self.score, 3), "reasons": self.reasons,
        }


def orcid_checksum_ok(orcid: str) -> bool:
    """Validate an ORCID's ISO 7064 MOD 11-2 check digit.

    Needed because placeholder values like 0000-0000-0000-0000 are
    syntactically well-formed, and OpenAlex has ingested author records
    carrying them.
    """
    s = str(orcid).strip().replace("https://orcid.org/", "").replace("-", "")
    if len(s) != 16:
        return False
    total = 0
    for ch in s[:15]:
        if not ch.isdigit():
            return False
        total = (total + int(ch)) * 2
    expected = (12 - total % 11) % 11
    check = 10 if s[15] in "Xx" else (int(s[15]) if s[15].isdigit() else -1)
    return check == expected


def is_orcid(identifier: str) -> bool:
    """True only for a syntactically valid ORCID with a correct check digit."""
    s = str(identifier).strip().replace("https://orcid.org/", "")
    return bool(_ORCID_RE.match(s)) and orcid_checksum_ok(s)


def looks_like_orcid(identifier: str) -> bool:
    """Shape only, ignoring the check digit."""
    s = str(identifier).strip().replace("https://orcid.org/", "")
    return bool(_ORCID_RE.match(s))


_STEM_MIN = 5


def _field_matches(field_hint: str, topics: list[str]) -> bool:
    """Match a field hint against topic labels by word stem.

    OpenAlex labels topics as "Anthropological Studies and Insights", so a
    plain substring test for "Anthropology" fails and would *penalise* the
    correct candidate. Compare on shared word prefixes instead.
    """
    wants = [w for w in dedup.normalize_text(field_hint).split() if len(w) >= _STEM_MIN]
    if not wants:
        wants = dedup.normalize_text(field_hint).split()
    for topic in topics:
        for have in dedup.normalize_text(topic).split():
            for want in wants:
                n = min(len(want), len(have), 8)
                if n >= _STEM_MIN and want[:n] == have[:n]:
                    return True
    return False


def _institutions_of(author: dict) -> list[str]:
    out = []
    for inst in (author.get("last_known_institutions") or []):
        if inst.get("display_name"):
            out.append(inst["display_name"])
    for aff in (author.get("affiliations") or [])[:5]:
        nm = (aff.get("institution") or {}).get("display_name")
        if nm and nm not in out:
            out.append(nm)
    return out


def _topics_of(author: dict) -> list[str]:
    out = []
    for t in (author.get("topics") or [])[:8]:
        if t.get("display_name"):
            out.append(t["display_name"])
    for c in (author.get("x_concepts") or [])[:8]:
        nm = c.get("display_name")
        if nm and nm not in out:
            out.append(nm)
    return out


def to_candidate(author: dict) -> AuthorCandidate:
    years = [c.get("year") for c in (author.get("counts_by_year") or []) if c.get("year")]
    return AuthorCandidate(
        id=author.get("id", ""),
        name=author.get("display_name", ""),
        orcid=author.get("orcid"),
        institutions=_institutions_of(author),
        topics=_topics_of(author),
        works_count=author.get("works_count") or 0,
        cited_by_count=author.get("cited_by_count") or 0,
        first_year=min(years) if years else None,
        last_year=max(years) if years else None,
        raw=author,
    )


def score_candidate(cand: AuthorCandidate, *, name: str,
                    institution: Optional[str] = None,
                    field_hint: Optional[str] = None,
                    active_years: Optional[tuple[int, int]] = None) -> AuthorCandidate:
    score = 0.0
    reasons: list[str] = []

    nq, nc = dedup.normalize_text(name), dedup.normalize_text(cand.name)
    if nq and nq == nc:
        score += 0.30
        reasons.append("exact name match")
    elif dedup.normalize_surname(name) == dedup.normalize_surname(cand.name):
        score += 0.15
        reasons.append("surname match only")

    if institution:
        want = dedup.normalize_text(institution)
        if any(want in dedup.normalize_text(i) for i in cand.institutions):
            score += 0.25
            reasons.append(f"institution matches {institution!r}")

    if field_hint:
        if _field_matches(field_hint, cand.topics):
            score += 0.25
            reasons.append(f"works in {field_hint!r}")
        else:
            score -= 0.15
            reasons.append(f"no topic matching {field_hint!r}")

    if active_years and cand.first_year and cand.last_year:
        lo, hi = active_years
        if cand.last_year >= lo and cand.first_year <= hi:
            score += 0.15
            reasons.append("active in the expected period")
        else:
            score -= 0.10
            reasons.append(
                f"active {cand.first_year}-{cand.last_year}, expected {lo}-{hi}")

    # Demoted from decisive to cosmetic: this is what caused the original bug.
    if cand.works_count:
        import math
        score += min(0.05, math.log1p(cand.works_count) / 200)

    cand.score = score
    cand.reasons = reasons
    return cand


def _same_person(a: "AuthorCandidate", b: "AuthorCandidate") -> bool:
    """Do two close candidates look like split records for one person?

    OpenAlex routinely holds several author records for the same researcher.
    Refusing to choose between those is unhelpful; refusing to choose between
    genuinely different people is the whole point. The distinguishing signal is
    a *conflict*: same normalised name and no contradicting institution
    evidence means duplicates. If both name forms differ, or both list
    institutions and none overlap, they are treated as different people.
    """
    if dedup.normalize_text(a.name) != dedup.normalize_text(b.name):
        return False
    ia = {dedup.normalize_text(x) for x in a.institutions}
    ib = {dedup.normalize_text(x) for x in b.institutions}
    if ia and ib and not (ia & ib):
        return False
    return True


def resolve_author(
    oa,
    identifier: str,
    *,
    institution: Optional[str] = None,
    field_hint: Optional[str] = None,
    active_years: Optional[tuple[int, int]] = None,
    author_id: Optional[str] = None,
    auto_accept: float = 0.75,
    margin: float = 0.15,
    limit: int = 10,
) -> dict:
    """Resolve a name or ORCID to one OpenAlex author.

    Raises AmbiguousAuthor rather than guessing when the top two are close.
    """
    if author_id:
        author = oa.get_author_by_id(author_id)
        if author:
            return author
        raise ValueError(f"OpenAlex author id not found: {author_id}")

    if looks_like_orcid(identifier) and not is_orcid(identifier):
        raise ValueError(
            f"{identifier} is not a valid ORCID: the check digit is wrong. "
            "Verify it at https://orcid.org/"
        )

    if is_orcid(identifier):
        author = oa.get_author_by_orcid(identifier)
        # OpenAlex can answer an unknown ORCID with an unrelated author, so the
        # returned record is checked against what was actually asked for.
        if author:
            want = str(identifier).replace("https://orcid.org/", "").strip().upper()
            got = str(author.get("orcid") or "").replace("https://orcid.org/", "").strip().upper()
            if got and got != want:
                logger.debug("OpenAlex returned ORCID %s for a request for %s", got, want)
                author = None
        if author:
            logger.info("Resolved by ORCID: %s", author.get("display_name"))
            return author
        # Name-searching an ORCID string is meaningless, so this is an error
        # rather than a fallback.
        raise ValueError(
            f"ORCID {identifier} is not in OpenAlex. Check it at "
            f"https://orcid.org/{str(identifier).replace('https://orcid.org/', '')}, "
            "or search by name instead."
        )

    results = oa.search_authors(identifier, limit=limit)
    if not results:
        raise ValueError(f"No OpenAlex author found for {identifier!r}")

    cands = [
        score_candidate(to_candidate(a), name=identifier, institution=institution,
                        field_hint=field_hint, active_years=active_years)
        for a in results
    ]
    cands.sort(key=lambda c: c.score, reverse=True)

    top = cands[0]
    runner = cands[1] if len(cands) > 1 else None

    if top.score >= auto_accept and (runner is None or top.score - runner.score >= margin):
        logger.info("Resolved %r -> %s (score %.2f: %s)",
                    identifier, top.name, top.score, "; ".join(top.reasons))
        return top.raw

    if runner is not None and top.score - runner.score < margin:
        if _same_person(top, runner):
            # Split records for one researcher: take the richer profile.
            best = max((c for c in cands if _same_person(c, top)),
                       key=lambda c: (c.works_count, c.cited_by_count))
            logger.info(
                "OpenAlex holds several author records for %r; using the richest "
                "(%s works). Pass --author-id to pick a specific one.",
                identifier, best.works_count)
            return best.raw
        raise AmbiguousAuthor(identifier, cands[:5])

    # Single weak candidate: accept but say so.
    logger.warning("Low-confidence match for %r -> %s (score %.2f). "
                   "Pass --field or --institution to disambiguate.",
                   identifier, top.name, top.score)
    return top.raw


def find_orcid_by_name(orcid_client, name: str) -> list[dict]:
    """Ask ORCID for iDs matching a name.

    ORCID records are self-registered, so an iD found here is a far stronger
    identifier than an OpenAlex name-search hit.
    """
    try:
        return orcid_client.search_by_name(name)
    except Exception as e:
        logger.debug("ORCID name search failed: %s", e)
        return []


def resolve_via_orcid(oa, orcid_client, name: str, *,
                      institution: Optional[str] = None) -> Optional[dict]:
    """Resolve a name through ORCID, then look the iD up in OpenAlex.

    Returns the OpenAlex author when the iD resolves there, otherwise a
    synthetic author record built from the ORCID profile so the run can
    continue on ORCID data alone.
    """
    hits = find_orcid_by_name(orcid_client, name)
    if not hits:
        return None
    if institution:
        want = dedup.normalize_text(institution)
        narrowed = [h for h in hits
                    if any(want in dedup.normalize_text(i)
                           for i in (h.get("institutions") or []))]
        hits = narrowed or hits
    if len(hits) > 1:
        logger.info("ORCID returned %d candidates for %r; using the first (%s)",
                    len(hits), name, hits[0].get("orcid"))
    hit = hits[0]
    orcid = hit.get("orcid")
    if not orcid:
        return None

    author = oa.get_author_by_orcid(orcid)
    if author:
        logger.info("Resolved %r via ORCID %s -> OpenAlex %s",
                    name, orcid, author.get("display_name"))
        return author

    logger.info("ORCID %s has no OpenAlex author record; continuing on ORCID data",
                orcid)
    return {
        "id": f"orcid:{orcid}",
        "display_name": hit.get("name") or name,
        "orcid": f"https://orcid.org/{orcid}",
        "works_count": 0,
        "cited_by_count": 0,
        "last_known_institutions": [
            {"display_name": i} for i in (hit.get("institutions") or [])
        ],
        "_orcid_only": True,
    }
