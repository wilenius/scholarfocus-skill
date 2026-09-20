# Zotero integration

Reads the local Zotero HTTP API at `http://localhost:23119/api/users/0/`.

**Requires**: Zotero desktop running, with *Settings → Advanced → "Allow other
applications on this computer to communicate with Zotero"* enabled. If it is not
reachable, litreview logs one warning and continues — a closed Zotero never
fails a run.

**The API is read-only.** Export writes a `.bib` file you import yourself; it
cannot push items into Zotero.

## Modes

| `--zotero` | Effect |
|---|---|
| `mark` (default) | Flag results you already own; adds a small ranking bonus and a "N of these are in your library" line |
| `seed` | Read a collection, extract DOIs, resolve them through **free** OpenAlex singleton lookups — zero credits for a high-precision seed set |
| `both` | Both |
| `off` | Skip entirely |

```bash
python -m scholarlib.cli.litreview --query "plantation ecologies" \
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

Better BibTeX citekeys come through the local API as `citationKey` and are
reused verbatim in exports, so a bibliography matches the keys already in your
documents. Records with no Zotero counterpart get a generated
`surnameYEARfirstword` key.

## Semantic search

Conceptual search over your library lives in the zotero-mcp server's vector
index, not in the local HTTP API. For "find things I own that are *about* this",
invoke the `zotero-mcp` skill, then feed the DOIs it returns back in via
`--seed-doi`. Exact ownership marking needs no agent action — `--zotero mark`
handles it.
