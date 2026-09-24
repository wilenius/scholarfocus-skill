---
name: scholarfocus
description: Profile one or more named researchers (people) by name or ORCID ID. Resolves the person in OpenAlex and ORCID, fetches their works, and outputs ranked research interests, their main co-authors, and the external works they cite most. Use when the user names a specific scholar and asks what they work on, who they collaborate with, what they cite, or how the networks of two named scholars compare. Do NOT use for topic-based literature searches or literature reviews — use the litreview skill for those.
license: MIT
metadata:
  author: wilenius
---

# ScholarFocus

Build structured intelligence profiles of **researchers** from open bibliographic APIs.

For reviewing the literature on a **topic**, use the `litreview` skill instead.

## When to use

- "Profile [researcher name]" / "What does [researcher] work on?"
- "Who does [researcher] collaborate with?"
- "What works does [researcher] cite most?"
- "Compare the research interests of [A] and [B]"

## Setup check

Run `scholarfocus --version` first. If the command is not found, install it with
`uv tool install scholarlib` (or `pip install scholarlib`) and try again. If a run
warns that no config file was found, `scholarfocus --init-config` writes a template
to `~/.config/scholarlib/config.yaml`; the tool still works without one, but on a
much smaller daily API budget.

## How to run

The command works from any directory.

```bash
# By name
scholarfocus --researchers "Jane Doe"

# By ORCID (always preferred — avoids name ambiguity entirely)
scholarfocus --researchers "0000-0002-1234-5678"

# Multiple researchers (enables cross-network analysis)
scholarfocus --researchers "Alice Smith" "0000-0003-9876-5432" --output json

# Disambiguation hints, when a name is common
scholarfocus --researchers "T Tammisto" \
    --field Anthropology --institution "Helsinki"
```

**Flags:** `--researchers` (required), `--config`, `--output markdown|json`,
`--log-level`, `--enable-s2ag`, `--no-cache`, plus `--author-id`, `--field`,
`--institution`, `--active-years` for disambiguation.

## Resolution

OpenAlex is tried first. When it is thin, missing or ambiguous, **ORCID** is
consulted — its public API needs no key and its work list is author-curated, so
it is markedly better for researchers OpenAlex has fragmented. Tuomas Tammisto
has 1 work in OpenAlex and 50 in ORCID.

Two failure modes this guards against, both of which used to produce confident
wrong answers:

- **Wrong person.** Picking the candidate with the most works resolved
  "Tuomas Tammisto" to a 1970s anaesthesiologist and reported his interests as
  Fentanyl and Halothane. Candidates are now scored on ORCID, institution,
  field fit and active years, and the tool exits 4 with a candidate list rather
  than guessing between plausibly different people.
- **Conflated records.** OpenAlex sometimes merges two researchers who share a
  surname and initial into one author record. When the work list comes from
  ORCID, that list is the authoritative scope: metrics are computed from those
  works and the OpenAlex aggregates are discarded with a warning, rather than
  reporting someone else's h-index.

If a profile looks thin or the warning mentions a conflation, supply the
researcher's ORCID directly — it is always the most reliable input.

## Output

| Section | Content |
|---------|---------|
| Researcher profile | Name, ORCID, institution, works count, citation count, h-index |
| Research interests | Ranked keywords/topics from OpenAlex topics + concepts |
| Research partners | Co-authors ranked by collaboration frequency |
| Cited external works | Most-cited non-self works across the researcher's corpus |
| Network summary | Shared interests and collaborators (multi-researcher only) |

## Exit codes

| Code | Meaning | What to do |
|---|---|---|
| 0 | success | — |
| 1 | no results / unexpected error | report the stderr message |
| 2 | OpenAlex daily credit budget exhausted | partial results were still written to stdout; retry tomorrow or add an API key |
| 3 | config error | fix `config.yaml` |
| 4 | **ambiguous author** | stdout holds `{"status":"ambiguous","candidates":[…]}` — show the candidates to the user and re-run with `--author-id` |

## Fallback procedures

1. **Ambiguous name (exit 4)** → present the candidate list with institution and top
   topics, ask which is meant, re-run with `--author-id`. Never silently pick one.
2. **Not found** → ask the user to confirm spelling or supply an ORCID (https://orcid.org).
3. **Enrich via the Zotero skill** if available: pull the publication list, feed DOIs to CrossRef.
4. **Enrich via web search**: Google Scholar, ResearchGate, or an institutional page.
5. **Partial results are preferable to failure** — report what was found, with uncertainty markers.

See [references/REFERENCE.md](references/REFERENCE.md) for the full JSON schema and config options.
