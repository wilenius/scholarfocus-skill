# ScholarFocus — Technical Reference

> **Updated 2026-09-20.** Code now lives in the `scholarlib` package; run
> `python -m scholarlib.cli.scholarfocus` from the repo root. See
> `docs/api-status.md` for live credential status and OpenAlex credit costs.

## Setup

```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml
# Edit config.yaml — add your email at minimum
python -m scholarlib.cli.scholarfocus --researchers "Test User" --output json
```

## Config file reference (`config.yaml`)

```yaml
apis:
  openalex:
    email: you@example.com        # Polite pool — strongly recommended, no key needed

  semantic_scholar:
    api_key: null                  # Optional; 10x rate limit with key
                                   # Get one at: https://www.semanticscholar.org/product/api

  crossref:
    email: you@example.com        # Polite pool — no key needed

  core:
    api_key: null                  # Required to use CORE
                                   # Get one at: https://core.ac.uk/services/api

analysis:
  max_works_per_researcher: 100   # Increase for prolific researchers (500+ papers)
  max_references_per_work: 50     # Per-paper reference depth for citation analysis
  top_n_partners: 15              # Collaborators to report
  top_n_cited_works: 20           # Cited external works to report
  top_n_keywords: 25              # Research interest keywords to report
  min_collaboration_count: 2      # Min shared works to list a collaborator
  concept_min_level: 1            # OpenAlex concept hierarchy floor (0=broadest)
  concept_max_level: 4            # OpenAlex concept hierarchy ceiling
```

## JSON output schema

```json
{
  "researchers": [
    {
      "profile": {
        "id": "https://openalex.org/A12345",
        "name": "Jane Doe",
        "orcid": "https://orcid.org/0000-0002-1234-5678",
        "works_count": 87,
        "cited_by_count": 2340,
        "h_index": 22,
        "i10_index": 45,
        "institutions": ["MIT"]
      },
      "keywords": [
        {
          "keyword": "Machine Learning",
          "score": 45.2,
          "work_count": 34,
          "source": "topic"
        }
      ],
      "partners": [
        {
          "id": "https://openalex.org/A99999",
          "name": "John Smith",
          "institutions": ["Stanford"],
          "collaboration_count": 12
        }
      ]
    }
  ],
  "cited_works": [
    {
      "openalex_id": "https://openalex.org/W98765",
      "doi": "10.1000/example",
      "title": "Attention Is All You Need",
      "year": 2017,
      "authors": ["Vaswani, A.", "Shazeer, N."],
      "global_citation_count": 95000,
      "cited_by_focal_researchers": 8
    }
  ],
  "network_summary": {
    "shared_collaborators": [
      {"id": "...", "name": "...", "institutions": [], "collaboration_count": 5}
    ],
    "shared_cited_work_ids": ["https://openalex.org/W12345"],
    "shared_keywords": [
      {"keyword": "Deep Learning", "combined_score": 88.4, "combined_work_count": 60}
    ]
  }
}
```

`network_summary` is only populated when two or more researchers are profiled in the same run.

## Keyword sources

Keywords are aggregated from three OpenAlex fields, merged by name:

| Source | Field | Weight |
|--------|-------|--------|
| `topics` (primary) | `display_name`, `subfield`, `field` | Full score + 0.5× for parent levels |
| `concepts` (legacy) | `display_name` at levels 1–4 | Full score |
| `keywords` (free text) | Raw keyword string | 0.5× (treated as moderate signal) |

S2AG `fieldsOfStudy` are used as topics in the S2AG fallback path.

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/scholarfocus.py` | Main CLI — orchestrates all steps |
| `scripts/apis/openalex.py` | OpenAlex API client |
| `scripts/apis/semantic_scholar.py` | Semantic Scholar API client |
| `scripts/apis/crossref.py` | CrossRef API client |
| `scripts/apis/core_api.py` | CORE API client |
| `scripts/analyze.py` | Network + keyword analysis |

## API rate limits

| API | Free limit | With key |
|-----|-----------|---------|
| OpenAlex | ~10 req/s (polite pool with email) | — |
| Semantic Scholar | 1 req/s | 10 req/s |
| CrossRef | ~50 req/s (polite pool with email) | — |
| CORE | varies | varies |

The script inserts appropriate delays between requests and retries on 429 responses.

## Extending with CrossRef

`scripts/apis/crossref.py` is fully implemented but not called in the main pipeline.
To enrich results with CrossRef reference data:

```python
from apis.crossref import CrossRefClient
cr = CrossRefClient(email="you@example.com")
refs = cr.get_references("10.1000/example-doi")
meta = cr.enrich_doi_metadata("10.1000/example-doi")
```

This is the intended integration point for a future Zotero bridge.
