# AGENTS.md

Working notes for agents and humans making changes to this repo. Read this before
touching packaging, versions, or the skills.

## What this repo is

One Python package (`scholarlib`) published to PyPI, and two agent skills that are
thin wrappers around its console scripts.

```
scholarlib/           the library and CLIs (published to PyPI as `scholarlib`)
  apis/               one client per data source
  http/               BaseClient, response cache, credit ledger
  pipeline/           retrieval and analysis stages
  render/             markdown / json / bibtex output
  jstor/              FTS5 index build and query
  cli/                console-script entry points
skills/               scholarfocus/ and litreview/ — SKILL.md + references/
.claude-plugin/       plugin + marketplace manifests for Claude Code
tests/                offline fixtures and unit tests
```

Users install the package (`uv tool install scholarlib`) and the skills separately.
The skills are **not** shipped on PyPI, and the package does not depend on them.

## The public API is the CLI, not the Python

What downstream agents bind to, and what a version bump has to respect:

- **CLI flags** on `scholarfocus`, `litreview`, `jstor-index`
- **Exit codes** — 0 ok, 1 error, 2 budget exhausted (partial results still written
  to stdout), 3 config error, 4 ambiguous author (scholarfocus only), 5 JSTOR index
  missing (litreview only)
- **`result.json`** shape, including the `limitations` block

Renaming a flag or changing an exit code is a breaking change even if no Python
signature moved. The internal functions are not a stable API; change them freely.

## Skills must run from anywhere

A skill is installed to `~/.claude/skills/<name>/`, far from this repo. So:

- SKILL.md invokes **bare console scripts** (`scholarfocus --researchers …`), never
  `python -m scholarlib.cli.…`, and never assumes a working directory.
- Never reintroduce "run from the repo root" or `pip install -e .` into a SKILL.md.
- A skill's frontmatter `name` **must equal its directory name** or it will not load.
- Required frontmatter is `name` + `description` only; `license`, `compatibility` and
  `metadata` are optional spec fields. `metadata` values must be strings.
- Skills carry no version number — `--version` reports the package version instead.
- Keep `description` explicit about when *not* to use the skill; the two skills are
  easy to confuse and each one's description points at the other.

## Versioning

SemVer. The single source of truth is `__version__` in `scholarlib/__init__.py`;
`pyproject.toml` reads it via `[tool.setuptools.dynamic]`.

**`.claude-plugin/plugin.json` has its own `version` field that cannot be wired to
it. Bump it in the same commit or it silently drifts.**

While on `0.x`: breaking changes to the CLI, exit codes or `result.json` bump the
**minor**; fixes bump the **patch**. Move to `1.0.0` when `result.json` stops
changing shape — after that, breaking changes cost a major bump.

## Releasing

```bash
# 1. bump __version__ and plugin.json, commit
# 2. build and validate
rm -rf dist/
python -m build
twine check dist/*

# 3. rehearse on TestPyPI
twine upload --repository testpypi dist/*
uv tool install --index-url https://test.pypi.org/simple/ \
    --extra-index-url https://pypi.org/simple/ scholarlib
scholarfocus --version && uv tool uninstall scholarlib

# 4. publish for real, then tag
twine upload dist/*
git tag -a v0.3.0 -m "0.3.0" && git push --tags
```

The extra index is required because `requests` and `pyyaml` are not on TestPyPI. If
resolution ever picks up a wrong same-named package from TestPyPI, add
`--index-strategy unsafe-best-match`.

**A version number can never be reused on PyPI.** A botched `0.3.0` means `0.3.1`,
not a re-upload. That is what step 3 is for.

After the first real release, switch to Trusted Publishing (GitHub Actions on tag
push, no stored token): PyPI → project → Publishing.

## This is an Arch/Manjaro box (PEP 668)

The system Python is marked `EXTERNALLY-MANAGED`. `pip install` against it fails
with *"This environment is externally managed"*. This is correct behaviour, not a
bug, and `--break-system-packages` is never the answer.

- Install the tool: `uv tool install scholarlib` (or `pipx install scholarlib`)
- Develop: `python -m venv .venv && .venv/bin/pip install -e '.[dev]'`
- One-off run without installing: `uvx --from scholarlib scholarfocus --version`

Debian and Ubuntu ship the same marker, so **README install instructions must lead
with `uv`/`pipx`, not bare `pip`.**

## Packaging gotcha: non-Python files

Anything that is not a `.py` must be declared in `[tool.setuptools.package-data]`
or it is missing from the wheel while working fine from a clone. Currently:
`config.example.yaml`, `jstor/*.sql`. `jstor/build.py` reads its SQL with
`Path(__file__).parent / "schema.sql"`, so an undeclared file crashes only for
installed users — a class of bug the acceptance test below catches.

## Acceptance test before any release

A clone-based test proves nothing about an installed user. Run this:

```bash
python -m build
python -m venv /tmp/clean && /tmp/clean/bin/pip install dist/*.whl
mkdir -p /tmp/fakehome /tmp/neutral && cd /tmp/neutral
HOME=/tmp/fakehome PATH=/tmp/clean/bin:$PATH scholarfocus --init-config
HOME=/tmp/fakehome PATH=/tmp/clean/bin:$PATH litreview --query test --no-jstor --dry-run
```

Neutral cwd, empty `$HOME`, no config: that is the real first-run path. `--dry-run`
costs no API credits.

## Configuration

Resolution order, first hit wins:

1. `--config PATH`
2. `$SCHOLARLIB_CONFIG`
3. `config.yaml` beside the package root (i.e. a clone — this is the dev path)
4. `~/.config/scholarlib/config.yaml` (the installed-user path)

Every field is optional; with no config the tools run on a smaller budget.
`scholarfocus --init-config` writes the bundled template to the XDG path.

**`config.yaml` holds secrets and is gitignored. Never commit it, never paste keys
into docs or commit messages.** `docs/history/` contains an old API report whose
keys are already redacted — keep it that way.

## External data that is not in the repo

- **JSTOR** — a 1.3 GB gzipped metadata dump (12.7M records) that builds into a
  ~6.7 GB SQLite FTS5 index at `~/.local/share/scholarlib/`. Distributed by JSTOR
  to subscribing institutions only, at <https://www.jstor.org/ta-support/metadata>;
  there is no public download, so assume outside users do not have it. Optional,
  and far too large to ship. Keep litreview honest about the monograph coverage
  `--no-jstor` costs. Never add the dump or the index to the repo.
- **Zotero** — read through either the local HTTP API at `localhost:23119` or the
  authenticated Web API at `api.zotero.org`. **Off by default**
  (`zotero.enabled: false`, `--zotero off`) because most installations have no
  Zotero; failures must degrade, not crash. The separate `zotero-mcp` server
  complements these exact APIs rather than replacing them — semantic search is
  approximate and may lag, so it must not decide ownership or collection
  membership.

## Budget discipline

OpenAlex is usage-priced. Measured costs: singleton 0, filter/`cites:`/`group_by` 1,
batch of 50 IDs 1, search 10. Default cap is ~900/day without an API key, ~9000 with
the free one. When adding a pipeline stage:

- Prefer singletons and 50-ID batches over searches.
- Put anything that multiplies searches behind a flag with a conservative default
  (see `--jstor-join-limit`, which defaults to 25 because each probe costs 10).
- Make sure `--dry-run` still predicts the new cost.

## Tests

`python -m pytest` — 65 tests, all offline against fixtures. No test may hit the
network. Keep it that way: add fixtures rather than live calls.

## Commits

Imperative subject describing the change; body explains *why*, with measurements
where the change was driven by observed behaviour. No tool attribution or
assistant-specific boilerplate in commit messages.
