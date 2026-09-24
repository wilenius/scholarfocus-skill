---
name: litreview
description: Build a literature review on a topic or research question. Searches OpenAlex, snowballs citations forward and backward, checks a local JSTOR metadata index for books and book chapters that citation databases miss, assembles abstracts, adds open-access links, and outputs either a narrative/interpretive review (Annual Review style) or a systematic review with screening counts. Use when the user asks for a literature review, a state of the field, the key works on a topic, a reading list, or a bibliography on a subject. Do NOT use to profile a named individual researcher — use the scholarfocus skill for that.
license: MIT
metadata:
  author: wilenius
---

# litreview

Survey the literature on a **topic**. For profiling a named **person**, use `scholarfocus`.

## When to use

- "Review the literature on [topic]"
- "What's the state of the field on [question]?"
- "What are the key works on [topic]?"
- "Build me a bibliography / reading list on [topic]"
- "How has the conversation about [concept] changed since [year]?"

## Setup check

Run `litreview --version` first. If the command is not found, install it with
`uv tool install scholarlib` (or `pip install scholarlib`) and try again. If a run
warns that no config file was found, `litreview --init-config` writes a template to
`~/.config/scholarlib/config.yaml`; the tool still works without one, but on a much
smaller daily API budget.

**Unless the user has built the local JSTOR index, pass `--no-jstor`.** It is a
large optional dataset that most installations do not have; without it the tool
exits 5. See [references/JSTOR.md](references/JSTOR.md) for what that costs you.

On a headless machine, Zotero can use its Web API instead of a running desktop
app. See [references/ZOTERO.md](references/ZOTERO.md); semantic Zotero search is
complementary and must not be used for exact ownership or collection membership.

## How to run

The command works from any directory.

```bash
# Narrative review (default) — Annual Review of Anthropology style
litreview --query "multispecies ethnography" --since 2010 --no-jstor

# Seed from works you already know are central (costs 0 API credits)
litreview --query "plantation ecologies" --no-jstor \
    --seed-doi 10.1215/22011919-3615934 --seed-doi 10.1525/ae.2015.42.1.1

# Systematic mode, with screening counts
litreview --query "infrastructure anthropology" --no-jstor \
    --mode systematic --since 2015 --output json

# Check the credit cost before spending anything
litreview --query "…" --dry-run
```

**Always run `--dry-run` first** for an unfamiliar topic. It prints the credit plan
and costs nothing.

## Modes

| | `--mode narrative` (default) | `--mode systematic` |
|---|---|---|
| Shape | Thematic strands, lineages, temporal arcs, key texts | Reproducible queries, screening counts, flow summary |
| Snowball depth | 2 | 1 |
| Books | Surfaced as a distinct *Monographs and edited volumes* section | Counted in the coverage check |
| Output | Prose skeleton + ranked evidence per strand | Flow table, inclusion criteria, included-studies table |

Both modes produce the **same** `result.json`; only the rendering differs.

## Budget

OpenAlex is usage-priced. Measured costs: **singleton = 0 credits, filter/`cites:`/`group_by` = 1,
batch of 50 IDs = 1, search = 10.** Default budget is ~1000 credits/day without an API key and
~10,000 with the free key from <https://openalex.org/settings/api>.

Practical rules the tool follows, and that you should respect when choosing flags:
- Prefer `--seed-doi` over extra `--query` terms — known DOIs resolve for free.
- `--jstor-join-limit` defaults to 25 because each join probe is a 10-credit search.
- Re-running the same review is nearly free; responses are cached.

## Exit codes

| Code | Meaning | What to do |
|---|---|---|
| 0 | success | — |
| 1 | no results / unexpected error | report the stderr message |
| 2 | budget exhausted | **partial results were still written** — report them and say they are truncated |
| 3 | config error | fix `config.yaml` |
| 5 | JSTOR index missing or stale | build it (see references/JSTOR.md) or pass `--no-jstor` |

## Known coverage limits — state these in any review you produce

- **Monographs have no reference lists in OpenAlex** (`referenced_works_count = 0`), so backward
  citation chaining does not work for books. Forward chaining and `related_works` do.
- **No Scopus, Web of Science, PubMed or grey-literature database** is searched. Do not call the
  output PRISMA-compliant.
- Screening is on title, abstract and metadata only — **no full text is assessed**.
- The JSTOR index has no abstracts or references; it is a discovery and coverage check, and its
  records join to OpenAlex by title at roughly a 58% rate.
- **Without the JSTOR index** (`--no-jstor`, the common case) the review sees only what OpenAlex
  indexes. Monograph coverage is materially worse, which matters most in the humanities and
  qualitative social sciences. Say so in the review.

The tool emits a `limitations` block in its JSON. Carry it into the prose you write.

## References

- [references/RETRIEVAL.md](references/RETRIEVAL.md) — pipeline stages, flags, JSON schema
- [references/JSTOR.md](references/JSTOR.md) — building and using the local index
- [references/ZOTERO.md](references/ZOTERO.md) — seeding from and exporting to your library
