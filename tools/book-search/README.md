# Book Search

Small dependency-free CLI helpers for finding book catalogue links used in
editorial reading-list workflows.

The group currently contains:

- `bin/litres-lookup`: searches Litres result pages and extracts book cards.
- `bin/bookmate-lookup`: searches the Bookmate API and extracts catalogue objects.

Both tools return candidates, not guaranteed canonical matches. Review title and
author before inserting a link into a note.

## Litres Lookup

Find Litres book pages from search queries:

```bash
tools/book-search/bin/litres-lookup "Джедайские техники Максим Дорофеев"
tools/book-search/bin/litres-lookup --format json "Пятая дисциплина Питер Сенге"
tools/book-search/bin/litres-lookup --limit 3 --format markdown "Думай медленно решай быстро"
tools/book-search/bin/litres-lookup --include-audio "Джедайские техники Максим Дорофеев"
```

Behavior:

- fetches the Litres search page for each query
- extracts visible result cards from server-rendered HTML
- returns text book pages by default
- ignores podcast pages
- includes audiobook pages only with `--include-audio`
- falls back from Python `urllib` to system `curl` when Litres returns `403`

Parser check against a saved Litres HTML page:

```bash
tools/book-search/bin/litres-lookup --html-file /path/to/search.html "ignored query"
```

## Bookmate Lookup

Find Bookmate catalogue pages through the Bookmate search API:

```bash
tools/book-search/bin/bookmate-lookup "Я так и знал Голдратт"
tools/book-search/bin/bookmate-lookup --limit 3 --format markdown "Спиральная динамика"
tools/book-search/bin/bookmate-lookup --kind audiobooks "Нормальные люди"
```

Behavior:

- calls the Bookmate API endpoint `/p/api/v5/search`
- returns `books` results by default
- can also return `audiobooks`, `comicbooks`, `authors`, `series`,
  `bookshelves`, and `users`
- preserves the original query in every output row

Bookmate is protected by Cloudflare. Direct CLI requests may return a challenge
instead of JSON. When that happens, save the API JSON response from a browser
session and parse it with `--api-json-file`.

Parser check against a saved Bookmate API response:

```bash
tools/book-search/bin/bookmate-lookup --api-json-file /path/to/search.json "ignored query"
```

## Output Formats

Both tools support `tsv`, `json`, and `markdown`.

Litres default `tsv` output:

```text
query<TAB>title<TAB>author<TAB>url
```

Bookmate default `tsv` output:

```text
query<TAB>kind<TAB>title<TAB>authors<TAB>url<TAB>readers_count
```

## Known Limits

- Litres can return summaries, infographics, or Smart Reading entries before the
  original book. Use `--limit 3` or higher when the first result is not the book
  itself.
- The same title may appear as several Litres text book pages with different
  IDs. The tool keeps them as separate candidates.
- Bookmate can return summaries, unrelated fiction, or neighboring topic books.
  Review `title` and `authors` before using a result.
- Bookmate Cloudflare protection can block direct CLI requests.
