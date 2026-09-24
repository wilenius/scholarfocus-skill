# scholarfocus-skill

Two agent skills over one shared bibliographic library, for Claude Code, OpenClaw
and any other agent – including humans! – that can run a shell command.

| Skill | Object | Question it answers |
|---|---|---|
| [`skills/scholarfocus`](skills/scholarfocus/SKILL.md) | **people** | What does this researcher work on? Who with? What do they cite? |
| [`skills/litreview`](skills/litreview/SKILL.md) | **topics** | What is the state of the field on X? What are the key works? |

Both sit on [`scholarlib`](https://pypi.org/project/scholarlib/), which wraps OpenAlex,
ORCID, CrossRef, CORE, Unpaywall, OpenAIRE, Semantic Scholar and an optional local
JSTOR metadata index behind one cache, one credit budget and one deduplication layer.

## Install

Two steps: the command-line tool, then the skills.

### 1. The tool

Install `scholarlib` as a standalone tool, not into your system Python — on Arch,
Manjaro, Debian, Ubuntu and Fedora the system interpreter is marked
externally-managed and a bare `pip install` will refuse with *"This environment is
externally managed"*. That refusal is correct; do not override it.

**macOS**

```bash
brew install uv          # or: brew install pipx && pipx ensurepath
uv tool install scholarlib
```

**Linux**

```bash
# Arch / Manjaro
sudo pacman -S uv        # or: sudo pacman -S python-pipx

# Debian / Ubuntu
sudo apt install pipx    # then use `pipx install` below

# Fedora
sudo dnf install uv

uv tool install scholarlib      # or: pipx install scholarlib
```

No `uv` or `pipx` available? `python3 -m venv ~/.venvs/scholarlib && ~/.venvs/scholarlib/bin/pip install scholarlib`
works anywhere, and the commands land in `~/.venvs/scholarlib/bin/`.

Verify, and create a config:

```bash
scholarfocus --version
scholarfocus --init-config     # writes ~/.config/scholarlib/config.yaml
```

Everything in that file is optional, but **get the free OpenAlex API key** at
<https://openalex.org/settings/api> — it raises the daily budget roughly tenfold and
takes about thirty seconds. Fill in an email address too; it puts you in the polite
pool at several APIs.

Faster fuzzy matching is worth having on large corpora: `uv tool install 'scholarlib[fast]'`.

### 2. The skills

**Claude Code**, as a plugin:

```
/plugin marketplace add wilenius/scholarfocus-skill
/plugin install scholarlib@wilenius-skills
```

**Anything else** — copy the skill directories wherever your assistant looks for
them:

```bash
git clone https://github.com/wilenius/scholarfocus-skill
cp -r scholarfocus-skill/skills/* ~/.claude/skills/
```

The skills call `scholarfocus` and `litreview` as ordinary commands, so they work
from any directory and need nothing else on the path.

## Usage

```bash
scholarfocus --researchers "Jane Doe"
scholarfocus --researchers "0000-0002-1234-5678"          # ORCID is always better
litreview --query "multispecies ethnography" --no-jstor --dry-run
```

`--dry-run` prints the credit plan and spends nothing. Use it first on an
unfamiliar topic.

### What is deterministic

The library makes no LLM calls. Retrieval, ranking, clustering and rendering are
plain code, and the output depends only on the API responses. The review prose is
written by the agent running the skill, from that output.

litreview's **thematic strands** (`--cluster`) are counts, not semantics:

- `topics` (narrative default): the most frequent OpenAlex topics across the corpus,
  up to 7 with at least 3 works each, become strands. Each work joins the strand of
  its highest-ranked topic. Labels are OpenAlex's topic names, used verbatim.
- `cocitation`: the references the corpus cites most become anchors. Each strand is
  the works citing one anchor, labelled with the anchor's title.

OpenAlex assigns topics with its own classifier, so strands inherit its taxonomy
and its errors. Treat them as a skeleton to rename, merge or split, not as findings.

## Zotero — recommended

**Off by default, and worth turning on.** litreview can read your Zotero library
over either Zotero's local API or its Web API, which changes the output in ways
nothing else can:

- Results you already own are marked, so a reading list separates what you have
  from what you need to find.
- Your Better BibTeX citekeys are reused verbatim in exported bibliographies, so
  they match the keys already in your documents.
- A collection can seed a review through free OpenAlex lookups — a high-precision
  starting set at zero credit cost.

For the default local backend, install [Zotero](https://www.zotero.org/download/),
then enable *Settings → Advanced → "Allow other applications on this computer to
communicate with Zotero"*. Set `zotero.enabled: true` in your config and pass
`--zotero mark` (or `both`).

For a server or other headless machine, use the Web API instead:

```yaml
zotero:
  enabled: true
  backend: web
  library_type: user
  library_id: "1234567"
  api_key: "…"
```

Create a read-only key at <https://www.zotero.org/settings/keys>. Instead of
putting credentials in YAML, you can export `ZOTERO_LIBRARY_ID`,
`ZOTERO_LIBRARY_TYPE` and `ZOTERO_API_KEY`; explicit config values take
precedence. A Web API item keeps its Better BibTeX citekey when that key is
available as `citationKey` or a `Citation Key:` line in Extra.

For conceptual search over your library — "find things I own that are *about*
this" — add [`zotero-mcp`](https://github.com/54yyyu/zotero-mcp), an MCP server
with a vector index over your library. It complements this integration rather
than replacing it: semantic results are approximate and may lag behind Zotero,
while `--zotero mark` and collection seeding require the exact local or Web API.
Feed DOIs found by semantic search back in via `--seed-doi`.

The integration degrades quietly: if the selected backend is unavailable,
litreview logs one warning and carries on.

## JSTOR — optional

litreview can check a local index of JSTOR metadata for books and chapters that
citation databases miss. It is the difference between a review that sees
monographs and one that does not, which matters most in the humanities and
qualitative social sciences.

It requires a metadata dump that **JSTOR distributes only to members of subscribing
institutions**, at <https://www.jstor.org/ta-support/metadata>. With an
institutional account you get a gzipped JSONL file of about 1.3 GB, which builds
into a ~6.7 GB SQLite FTS5 index in roughly eight minutes on a basic GPU-equipped
desktop computer:

```bash
# point jstor.source_path at the downloaded file, then
jstor-index build --all-disciplines     # or --disciplines Anthropology,History
```

Without it, pass `--no-jstor` and litreview will say what the gap costs. See
[references/JSTOR.md](skills/litreview/references/JSTOR.md) for the join rate and
what the index does and does not contain.

## Data that does not live in this repo

The JSTOR dump belongs outside the working tree — `~/data/jstor/` by default,
configurable at `jstor.source_path`. The built index lands in
`~/.local/share/scholarlib/`, the HTTP cache in `~/.cache/scholarlib/`. All are
gitignored.

## Development

```bash
git clone https://github.com/wilenius/scholarfocus-skill
cd scholarfocus-skill
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
```

A `config.yaml` at the repo root takes precedence over the user-level one, and is
gitignored. See [AGENTS.md](AGENTS.md) for versioning, release and packaging
conventions.

## API status

See [docs/api-status.md](docs/api-status.md) for live-verified credential status and
the measured OpenAlex credit costs that the budget logic is built around.

## Layout

```
scholarlib/           shared library, published to PyPI
  apis/               one client per data source
  http/               BaseClient, cache, credit ledger
  pipeline/           retrieval and analysis stages
  render/             markdown / json / bibtex output
  jstor/              FTS5 index build and query
  cli/                entry points
skills/               the two SKILL.md entry points
tests/                fixtures and unit tests
```

## License

MIT — see [LICENSE](LICENSE).
