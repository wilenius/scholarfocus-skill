# Zotero integration

Reads Zotero through either its local HTTP API or its authenticated Web API.

**Off by default.** Most installations have no Zotero, so `zotero.enabled` is
`false` in the config and `--zotero` defaults to `off`. Turn both on after
choosing a backend — this integration is worth the setup.

## Backends

| Backend | Configuration | Best fit |
|---|---|---|
| `local` (default) | Zotero desktop running, with *Settings → Advanced → "Allow other applications on this computer to communicate with Zotero"* enabled | A workstation running Zotero |
| `web` | Zotero library ID and a read-only API key | Servers, containers and other headless agents |

Local configuration needs only `enabled: true`. For Web API access:

```yaml
zotero:
  enabled: true
  backend: web
  library_type: user       # or group
  library_id: "1234567"
  api_key: "…"
```

The Web API fields can instead come from `ZOTERO_LIBRARY_TYPE`,
`ZOTERO_LIBRARY_ID` and `ZOTERO_API_KEY`. Explicit config values take
precedence. Create a read-only key at <https://www.zotero.org/settings/keys> and
never commit it. If the selected backend cannot be reached, litreview logs one
warning and continues — Zotero never fails the whole run.

**The API is read-only.** Export writes a `.bib` file you import yourself; it
cannot push items into Zotero.

## Modes

| `--zotero` | Effect |
|---|---|
| `mark` | Flag results you already own; adds a small ranking bonus and a "N of these are in your library" line |
| `seed` | Read a collection, extract DOIs, resolve them through **free** OpenAlex singleton lookups — zero credits for a high-precision seed set |
| `both` | Both |
| `off` (default) | Skip entirely |

```bash
litreview --query "plantation ecologies" \
    --zotero both --seed-zotero-collection "Plantationocene"
```

## Ownership matching

DOI first, then a blocked match on (surname, year ±1) verified by title
similarity. Only 30% of a typical library carries DOIs, so the title path does
most of the work.

Matching compares **both the full title and the main title**, taking the better
score. This is not a refinement — without it the matcher fails on ordinary data:

> Zotero holds *"The mushroom at the end of the world: on the possibility of life
> in capitalist ruins"*. A citation gives *"The Mushroom at the End of the
> World"*. Full-string similarity is **0.605**, below any usable threshold, so
> the book reads as *not owned*. Compared on main titles it is an exact match.

The looser main-title comparison only ever runs inside a (surname, year) block,
and is refused when either main title is too short to be evidence, so
*"Introduction: X"* and *"Introduction: Y"* stay apart.

## Citation keys

Better BibTeX citekeys come through the local API as `citationKey` and are reused
verbatim in exports. With the Web API, a key is retained when Zotero returns
`citationKey` or Better BibTeX has persisted it as a `Citation Key:` line in the
item's Extra field. Records without an available key get a generated
`surnameYEARfirstword` key.

## Semantic search

Conceptual search over your library belongs in a Zotero MCP server's vector
index, not in exact API matching. For "find things I own that are *about* this",
use the available Zotero semantic-search tool, then feed its DOIs back in via
`--seed-doi`.

Do not use semantic hits to decide whether a result is already owned or whether
it belongs to a collection. Vector matches are approximate and the index may lag
behind the live library. `--zotero mark` and `--seed-zotero-collection` use the
selected exact API backend for those tasks.
