# scholarfocus-skill

Two agent skills over one shared bibliographic library.

| Skill | Object | Question it answers |
|---|---|---|
| [`skills/scholarfocus`](skills/scholarfocus/SKILL.md) | **people** | What does this researcher work on? Who with? What do they cite? |
| [`skills/litreview`](skills/litreview/SKILL.md) | **topics** | What is the state of the field on X? What are the key works? |

Both sit on `scholarlib/`, which wraps OpenAlex, CrossRef, CORE, Unpaywall, OpenAIRE,
Semantic Scholar and a local JSTOR metadata index behind one cache, one credit budget
and one deduplication layer.

## Setup

```bash
pip install -e .
cp config.example.yaml config.yaml    # then fill in emails and keys
```

`config.yaml` is gitignored. **Get the free OpenAlex API key** at
<https://openalex.org/settings/api> — it raises the daily budget roughly tenfold and
takes about thirty seconds.

## Usage

```bash
python -m scholarlib.cli.scholarfocus --researchers "Jane Doe"
python -m scholarlib.cli.litreview --query "multispecies ethnography" --dry-run
python -m scholarlib.cli.jstor_index build        # optional, one-off, ~30 min
```

## Data that does not live in this repo

The JSTOR metadata dump (1.3 GB gzipped, 12.7M records) is kept outside the working
tree — by default `~/data/jstor/`, configurable at `jstor.source_path`. The built
FTS5 index lands in `~/.local/share/scholarlib/`. Both are gitignored.

## API status

See [docs/api-status.md](docs/api-status.md) for live-verified credential status and
the measured OpenAlex credit costs that the budget logic is built around.

## Layout

```
scholarlib/           shared library
  apis/               one client per data source
  http/               BaseClient, cache, credit ledger
  pipeline/           retrieval and analysis stages
  render/             markdown / json / bibtex output
  jstor/              FTS5 index build and query
  cli/                entry points
skills/               the two SKILL.md entry points
tests/                fixtures and unit tests
```
