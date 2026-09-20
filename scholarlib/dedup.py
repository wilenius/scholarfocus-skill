"""DOI and title normalisation, fuzzy matching and record merging.

The original code deduplicated only by exact identity, which is why DOIs stored
variously as `10.x`, `https://doi.org/10.x` and `doi:10.x` never merged.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Iterable, Optional

try:  # optional accelerator
    from rapidfuzz.fuzz import token_set_ratio as _token_set_ratio

    def _similarity(a: str, b: str) -> float:
        return _token_set_ratio(a, b) / 100.0
except ImportError:  # stdlib fallback
    from difflib import SequenceMatcher

    def _similarity(a: str, b: str) -> float:
        return SequenceMatcher(None, a, b).ratio()


_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
_DOI_PREFIXES = (
    "https://doi.org/", "http://doi.org/",
    "https://dx.doi.org/", "http://dx.doi.org/",
    "doi:", "DOI:",
)

# Name particles that belong to the surname.
_PARTICLES = {"van", "von", "de", "del", "della", "der", "den", "da", "di",
              "du", "la", "le", "ten", "ter", "bin", "ibn", "al"}

_SUBTITLE_SPLIT = re.compile(r"\s*[:–—]\s|\s+-\s+")


def normalize_doi(doi: Optional[str]) -> Optional[str]:
    """Strip resolver prefixes and casefold. Returns None if it isn't a DOI."""
    if not doi:
        return None
    s = str(doi).strip()
    for p in _DOI_PREFIXES:
        if s.lower().startswith(p.lower()):
            s = s[len(p):]
            break
    s = s.strip().rstrip(".,;)").casefold()
    return s if _DOI_RE.match(s) else None


def is_jstor_internal_doi(doi: Optional[str]) -> bool:
    """JSTOR mints 10.2307/<digits> and 10.2307/community.* internally; these are
    not registered in CrossRef and do not resolve in OpenAlex. Book and chapter
    DOIs (10.2307/j.*, 10.2307/jj.*) *are* registered."""
    d = normalize_doi(doi)
    if not d or not d.startswith("10.2307/"):
        return False
    tail = d[len("10.2307/"):]
    return tail.isdigit() or tail.startswith("community.")


def normalize_text(s: Optional[str]) -> str:
    """NFKD fold, drop diacritics and punctuation, casefold, collapse whitespace."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.casefold()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def normalize_title(title: Optional[str]) -> str:
    return normalize_text(title)


def title_main(title: Optional[str]) -> str:
    """Text before the first subtitle separator. JSTOR and OpenAlex disagree
    about subtitles constantly, so the main title is the more stable key."""
    if not title:
        return ""
    return normalize_text(_SUBTITLE_SPLIT.split(str(title), 1)[0])


def title_fingerprint(title: Optional[str]) -> str:
    return hashlib.sha1(normalize_title(title).encode("utf-8")).hexdigest()[:16]


def normalize_surname(name: Optional[str]) -> Optional[str]:
    """Extract and normalise a surname from 'Given Family' or 'Family, Given'."""
    if not name:
        return None
    s = str(name).strip()
    if "," in s:
        s = s.split(",", 1)[0]
    tokens = normalize_text(s).split()
    if not tokens:
        return None
    # Absorb leading particles: "van der Berg" -> "van der berg"
    out = [tokens[-1]]
    i = len(tokens) - 2
    while i >= 0 and tokens[i] in _PARTICLES:
        out.insert(0, tokens[i])
        i -= 1
    return " ".join(out)


def match_key(title: Optional[str], surname: Optional[str], year: Optional[int]) -> str:
    """Blocking key. Mandatory: all-pairs fuzzy matching does not scale."""
    toks = normalize_title(title).split()[:6]
    return f"{surname or ''}|{year or ''}|{' '.join(toks)}"


def title_similarity(a: Optional[str], b: Optional[str]) -> float:
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return _similarity(na, nb)


MIN_TITLE_TOKENS = 3


def titles_comparable(title: Optional[str]) -> bool:
    """Refuse to match on titles too generic to be evidence (e.g. 'Cannibalism')."""
    return len(normalize_title(title).split()) >= MIN_TITLE_TOKENS
