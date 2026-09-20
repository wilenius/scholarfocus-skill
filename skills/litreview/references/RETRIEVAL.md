# Retrieval: stages, flags and output

## Pipeline

```
seed ──► snowball ──► JSTOR ──► dedup ──► abstracts ──► OA links ──► Zotero ──► rank/screen ──► cluster ──► render
```

Every stage takes and returns `list[Record]` and appends to `Record.provenance`,
so the JSON can always answer "why is this here".

Both modes run the same retrieval and emit the same `result.json`. Only the
renderer differs.

## OpenAlex credit costs (measured 2026-09-20)

| Call | Credits |
|---|---|
| `/works/W123` or `/works/doi:…` singleton | **0** |
| `filter=ids.openalex:A\|B\|…` (50 ids) | **1** |
| `filter=cites:…` forward snowballing | **1** |
| `group_by=publication_year` | **1** |
| `search=` or `title.search:` | **10** |

Budget: ~1,000 credits/day without an API key, ~10,000 with the free key from
<https://openalex.org/settings/api>.

A representative run — one query, one seed DOI, depth 1, 2,019 records
identified — cost **99 credits**. Re-running it cost **1**, because the
10-credit search came from cache.

### The rules the stages follow

1. **Never search where a singleton will do.** 200 DOIs cost 0 credits as
   singleton lookups, 4 as batched lists, and 2,000 if you search their titles.
   Prefer `--seed-doi` over extra `--query` terms.
2. **OR-pack filters.** `filter=cites:W1|…|W50` is 1 credit for 50 seeds.
3. **Search is scarce.** It is spent only on seeding and the capped JSTOR join.
4. **`--budget-mode free`** resolves everything through 0-credit singletons —
   slower, but it makes the key-less path usable rather than merely degraded.
   `fast` batches 50 per credit. Defaults by whether a key is configured.
5. **`--dry-run` first.** It prints the plan and refuses to start if the plan
   exceeds the remaining budget.

## Stages

**Seed.** `--seed-doi` (free) › `--seed-zotero-collection` (free) ›
`--query` (10 credits each). The local JSTOR index also seeds at zero cost.

**Snowball.** Forward (`cites:`), backward (`referenced_works`) and lateral
(`related_works`). `--lateral` matters more than it looks: monographs carry
`referenced_works_count == 0`, so related-works is the only cheap recall route
for exactly the books backward chaining cannot see.

**JSTOR.** Free annotation of what JSTOR also holds, plus capped discovery of
books and chapters the citation graph missed. See [JSTOR.md](JSTOR.md).

**Dedup.** DOI normalisation across all resolver forms, then a blocked fuzzy
pass on (surname, year ± 1). Returns a duplicate count for the flow summary.

**Abstracts.** OpenAlex's inverted index first — ~92% coverage and free,
because the field already ships in the list response. `--abstracts full` walks
the CORE → OpenAIRE → CrossRef fallback chain for the remainder.

**OA links.** Unpaywall per DOI. JSTOR-internal DOIs are skipped; they are not
registered and cannot resolve.

**Zotero.** See [ZOTERO.md](ZOTERO.md).

**Rank and screen.** Narrative weights impact and seed proximity; systematic
weights query match. `--include-if`/`--exclude-if` take simple `field op value`
expressions (`year>=2000`, `type==book`, `title~plantation`) and every
exclusion is counted.

## Output

`--output markdown|json|bibtex|csl|ris`, or `--out-dir` to write all of them.

The JSON carries `schema_version`, `query`, `sources`, `budget`, `flow`,
`timeline`, `clusters`, `records`, `warnings` and `limitations`.

`limitations` is generated from the run, not boilerplate: it names the sources
actually queried and counts the book records that had no reference list. Carry
it into any prose you write.

`flow` gives per-stage identified counts, duplicates removed, and every
exclusion with its reason — a PRISMA-style summary without the PRISMA claim.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | success |
| 1 | no results / unexpected error |
| 2 | budget exhausted — **partial results were still written** |
| 3 | config error |
| 5 | JSTOR index missing or stale |

Exit 2 is not a failure to report as one: the retrieved subset is on stdout and
`budget.truncated` says where it stopped.
