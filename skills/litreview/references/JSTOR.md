# The JSTOR metadata index

## What it is and is not

A local SQLite FTS5 index over the JSTOR metadata dump: **12,678,570 source
records, 6,600,244 indexed**, built in about 8 minutes.

It holds **no abstracts, no references and no full text**. It cannot support
citation chaining. It is a *discovery and coverage-check* index, and its value is
precisely where OpenAlex is weak:

- **137,882 anthropology records**, of which **13,212 are books or chapters** —
  the format citation databases index worst.
- **Human-curated `discipline_names`**, which are more precise about
  disciplinary boundaries than OpenAlex's algorithmic topics.
- **Multilingual**: 5.4M English, but also 370k French, 335k German, 214k
  Italian, 149k Spanish, 71k Hebrew.
- Year range 1665–2027.

## Getting the dump

JSTOR distributes its metadata to subscribing institutions through
<https://www.jstor.org/ta-support/metadata>. **You need an institutional account
with JSTOR access**; there is no public download. The dump arrives as a gzipped
JSONL file of roughly 1.3 GB.

Keep it outside the working tree — `~/data/jstor/` by default — and point
`jstor.source_path` in your config at it. The file is gitignored by pattern, but
it has no business in a repository regardless.

This index is genuinely optional. Without it both skills still work; litreview
simply sees fewer books, which is what `--no-jstor` announces.

## Building it

```bash
jstor-index build --all-disciplines
```

Roughly 8 minutes and **6.7 GB** on disk for every discipline. Narrow it if that
is too much:

```bash
jstor-index build --disciplines Anthropology,Sociology,History
```

The build is re-runnable: an unchanged source is a no-op, an interrupted run
resumes with `--resume`, and a changed source rebuilds while **preserving the
OpenAlex join map**, which is expensive to regenerate.

### What gets indexed

Kept: `research-article`, `chapter`, `introduction`, `review-article`, and whole
books. Dropped: `book-review`, `misc`, `Other`, `frontmatter`, `backmatter`,
`toc`, `index`, `references`, and contributed content.

That exclusion matters: **book reviews are ~80% of the anthropology records and
~90% of them have no title at all.** `--keep-reviews` brings them back — useful
for reception history ("what did the discipline make of this book?") — but they
swamp full-text ranking, so they are off by default.

## Querying it

```bash
jstor-index stats
jstor-index search --text "multispecies ethnography" \
    --disciplines Anthropology --limit 10
jstor-index match --title "..." --author Tsing --year 2015
```

Searches return in well under a second.

## The join problem — read this before trusting coverage numbers

JSTOR's `ithaka_doi` values are **mostly not registered DOIs**. `10.2307/<digits>`
and `10.2307/community.*` are JSTOR-internal identifiers: they 404 in CrossRef
and do not resolve in OpenAlex. Book and chapter DOIs (`10.2307/j.*`,
`10.2307/jj.*`) *are* registered.

So the same article sits in JSTOR under `10.2307/667845` and in OpenAlex under
the publisher's `10.1525/aa.1962.64.6.02a00040`, and **the two do not join by
DOI**. The join is by normalised title plus author surname plus year, and it
succeeds for roughly **58%** of titled anthropology records.

The 42% that fail are not noise. They skew German-language, older, and
diacritic-heavy — which is to say, **they are largely the records JSTOR exists to
add.** Treat "not joined" as "possibly unique to JSTOR", not as "bad data".

### Why the join is rate-limited

JSTOR-row → OpenAlex requires `filter=title.search:`, a **10-credit search**.
Joining even 100 records costs a full day's budget without an API key. So:

- `--jstor-join-limit` defaults to **25** probes per run.
- Every outcome is memoised permanently in `jstor_openalex_map`, **including
  misses** — a confirmed "no match" cost 10 credits and must never be re-paid.
- The reverse direction (OpenAlex record → local FTS) is free and runs for
  everything.

## Fields

`item_id`, `ithaka_doi`, `parent_doi`, `title`, `is_part_of`, `creators_string`,
`first_surname`, `year`, `month`, `content_type`, `content_subtype`,
`section_type`, `lang`, `issn`, `isbn`, `journal_code`, `volume`, `issue`, `url`,
`title_norm`, `title_fp`, `match_key`.

Two quirks worth knowing:

- **`published_date` is a truncated 9-character string** like `2017-01-0`. The
  day digit is cut off rather than zero-padded, so it is garbage. Only year and
  month are stored.
- **Chapters link to their parent book by DOI prefix** — chapter
  `10.2307/j.ctt1xp3mt7.16` belongs to book `10.2307/j.ctt1xp3mt7`. That edge is
  derived at build time into `parent_doi`.
