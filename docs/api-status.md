# API status

**Last verified:** 2026-09-20 (live calls against every endpoint)

No key material in this file — only configured yes/no and observed behaviour.

## Credentials

| API | Status | Key needed | Notes |
|---|---|---|---|
| **OpenAlex** | ✅ working | email + free API key | Usage-priced since Feb 2026. See costs below. |
| **CrossRef** | ✅ working | email only (polite pool) | Earlier "HTTP 400" was a client bug, not a credential issue. |
| **CORE** | ✅ working | API key **valid** | Key raises the limit from 10/min to 150/min. |
| **Semantic Scholar** | ❌ key dead | new key applied for | 403 on every endpoint. Unauthenticated returns 429 immediately. |
| **Unpaywall** | ✅ working | email only | OA status + PDF locations per DOI. |
| **OpenAIRE** | ✅ working | none | Deeply nested JSON; needs a defensive normaliser. |
| Lens.org | ⏭️ not used | token | Corpus largely derived from Crossref/PubMed/MAG enhanced with OpenAlex; differentiator is patent linkage, irrelevant here. |

## OpenAlex credit costs (measured)

Budget: **1000 credits/day** (=$0.10) with `mailto` only; **~10,000/day** (=$1) with a free key
from <https://openalex.org/settings/api>.

| Call shape | Credits |
|---|---|
| `/works/W123` singleton | **0** (free, unlimited) |
| `filter=ids.openalex:A\|B\|…` (up to 50 ids) | **1** — 50 works per credit |
| `filter=cites:W123` (forward snowballing) | **1** |
| plain `filter=…` list | 1 |
| `group_by=publication_year` / `primary_topic.id` | 1 |
| `search=…` or `title.search:…` | **10** |

Consequences, which the code is built around:

- Resolve known DOIs/IDs via **singletons** (0 credits), never via search.
- OR-pack filters: `filter=cites:W1|W2|…|W50` is 1 credit for 50 seeds.
- Search is the scarce resource — cache it hardest, and substitute the local JSTOR index
  or the Zotero library where possible.

## Coverage notes

- OpenAlex abstract coverage measured at **~92%** (46/50) on a social-science sample, via
  `abstract_inverted_index`. This is why Semantic Scholar is an enhancement, not a dependency.
- `related_works` present on **50/50** sampled works; `referenced_works` on 43/50.
- **Monographs have `referenced_works_count = 0`** — forward citation chaining works for books,
  backward chaining does not. `related_works` is the cheap recall substitute.
