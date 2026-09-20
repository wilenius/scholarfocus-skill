# ScholarFocus

An agentic skill for profiling researchers: their intellectual interests, collaboration networks, and the external works they engage with most.

Designed to be invoked by OpenClaw or any LLM agent system, or run directly from the command line. All data is retrieved from public bibliographic APIs — no full-text access needed.

## What it produces

For each researcher (identified by name or ORCID):

| Output | Source |
|--------|--------|
| Research interests (ranked keywords/topics) | OpenAlex topics + concepts + S2AG fields of study |
| Main research partners (co-authors) | OpenAlex co-authorship data |
| Top cited external works | OpenAlex referenced works aggregated across corpus |
| Cross-researcher network (shared interests, shared collaborators) | Computed from the above when multiple researchers given |

## Setup

```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml
# Edit config.yaml — at minimum add your email for API polite pools
```

## Usage

```bash
# By name
python scripts/scholarfocus.py --researchers "Yoshua Bengio"

# By ORCID
python scripts/scholarfocus.py --researchers "0000-0002-7211-4439"

# Multiple researchers (enables cross-network analysis)
python scripts/scholarfocus.py \
    --researchers "Geoffrey Hinton" "Yann LeCun" \
    --output json > network.json

# Custom config + verbose logging
python scripts/scholarfocus.py \
    --researchers "Ada Lovelace" \
    --config /path/to/config.yaml \
    --log-level DEBUG
```

## Data sources

| Source | Role | Key | Rate limit |
|--------|------|-----|------------|
| [OpenAlex](https://openalex.org) | Primary — author profiles, works, concepts, citations | None (email for polite pool) | 10 req/s polite |
| [Semantic Scholar](https://www.semanticscholar.org/product/api) | Secondary — abstracts, fields of study | Optional | 1 req/s free / 10 req/s with key |
| [CrossRef](https://www.crossref.org/documentation/retrieve-metadata/rest-api/) | Tertiary — DOI metadata, reference lists | None (email for polite pool) | Polite pool |
| [CORE](https://core.ac.uk/services/api) | Quaternary — open-access abstracts when others lack them | Required | Varies |

## Config reference

See `config.example.yaml` for all options. Key settings:

```yaml
apis:
  openalex:
    email: you@example.com          # Enables polite pool (strongly recommended)
  semantic_scholar:
    api_key: null                   # Optional; 10x rate limit with key
  core:
    api_key: null                   # Required to use CORE

analysis:
  max_works_per_researcher: 100     # Increase for prolific researchers
  top_n_partners: 15
  top_n_cited_works: 20
  top_n_keywords: 25
  min_collaboration_count: 2        # Minimum shared works to list a partner
```

## Agent integration

When invoked by OpenClaw or another agent:

1. Run with `--output json` and pipe stdout to the next skill
2. stderr contains progress logs — safe to display or discard
3. If a researcher isn't found in any API, the script exits with code 1 and logs the error to stderr
4. Fallback chain: OpenAlex → S2AG → (agent uses Zotero or web search)

See `skill.md` for the full agent-readable skill definition and fallback procedures.

## Project structure

```
scholarfocus-skill/
├── SKILL.md                    # Agent Skills spec entry point (agentskills.io)
├── scripts/
│   ├── apis/
│   │   ├── openalex.py         # OpenAlex API client
│   │   ├── semantic_scholar.py # Semantic Scholar API client
│   │   ├── crossref.py         # CrossRef API client
│   │   └── core_api.py         # CORE API client
│   ├── analyze.py              # Network & keyword analysis
│   └── scholarfocus.py         # Main CLI + orchestration
├── references/
│   └── REFERENCE.md            # Full config reference, JSON schema, API details
├── config.example.yaml
├── requirements.txt
└── README.md
```
