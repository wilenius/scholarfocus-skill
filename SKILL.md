---
name: scholarfocus-skill
description: Profile one or more researchers by name or ORCID ID. Fetches their works from OpenAlex, Semantic Scholar, CrossRef, and CORE, then outputs ranked research interests (keywords/topics), main collaborators (co-author network), and most-cited external works. Use when the user asks to map a researcher's intellectual interests, trace citation relationships, identify key collaborators, or compare the scholarly networks of multiple researchers.
license: MIT
compatibility: Requires Python 3.11+, internet access, and pip packages (requests, pyyaml). A config.yaml with API email/keys must exist in the skill root. See references/REFERENCE.md for setup details.
metadata:
  author: hwileniu
  version: "1.0"
---

# ScholarFocus

Build structured intelligence profiles of researchers from open bibliographic APIs — no full-text access required.

## When to use

- "Profile [researcher name]" / "What does [researcher] work on?"
- "Who does [researcher] collaborate with?"
- "What works does [researcher] cite most?"
- "Compare the research interests of [A] and [B]"
- "Map the citation network around [researcher]"

## How to run

```bash
# By name
python scripts/scholarfocus.py --researchers "Jane Doe" --config config.yaml

# By ORCID
python scripts/scholarfocus.py --researchers "0000-0002-1234-5678" --config config.yaml

# Multiple researchers (enables cross-network analysis)
python scripts/scholarfocus.py \
    --researchers "Alice Smith" "0000-0003-9876-5432" \
    --config config.yaml --output json

# JSON output for piping to other skills
python scripts/scholarfocus.py --researchers "Bob Jones" --output json
```

**Flags:**
- `--researchers` (required): one or more names or ORCID IDs
- `--config` (default: `config.yaml`): path to API key config
- `--output`: `markdown` (default, human-readable) or `json` (machine-readable)
- `--log-level`: `DEBUG` / `INFO` / `WARNING` / `ERROR` (default: `INFO`, written to stderr)

## Output

| Section | Content |
|---------|---------|
| Researcher profile | Name, ORCID, institution, works count, citation count, h-index |
| Research interests | Ranked keywords/topics from OpenAlex topics + concepts + S2AG fields |
| Research partners | Co-authors ranked by collaboration frequency |
| Cited external works | Most-cited non-self works across the researcher's corpus |
| Network summary | Shared interests and collaborators (multi-researcher only) |

## Data sources (priority order)

1. **OpenAlex** — author profiles, works, concepts/topics, co-authors, cited works (no key needed)
2. **Semantic Scholar** — abstracts, fields of study (optional key for higher rate limits)
3. **CrossRef** — DOI metadata, reference lists (no key needed)
4. **CORE** — open-access abstracts (API key required)

## Fallback procedures

1. **Researcher not found in OpenAlex** → script automatically retries in Semantic Scholar
2. **Both APIs fail** → ask user to confirm spelling or provide ORCID (look up at https://orcid.org)
3. **Enrich via Zotero skill** (if available): pull publication list, feed DOIs to CrossRef; JSON output is designed to merge with Zotero data
4. **Enrich via web search**: check Google Scholar, ResearchGate, or institutional page for publication list; combine with partial API data
5. **Partial results** are preferable to failure — report what was found with appropriate uncertainty markers

See [references/REFERENCE.md](references/REFERENCE.md) for full JSON schema, config options, and API key setup.
