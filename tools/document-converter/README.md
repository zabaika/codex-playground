# Document Converter

Local converter and index builder for document folders.

It keeps source files read-only, mirrors supported documents into readable
derived files, extracts reusable assets and tables, and writes JSONL indexes for
search or knowledge-base ingestion.

## Usage

```bash
tools/document-converter/bin/document-converter SOURCE_DIR DEST_DIR --dry-run --build-index
tools/document-converter/bin/document-converter SOURCE_DIR DEST_DIR --force --build-index
```

Run `--dry-run --build-index` before a bulk conversion. It reports inventory,
dependencies, skipped files, planned outputs, and safety checks without writing
files.

Run `--force --build-index` for real conversion and indexing. Index-building
write runs require `--force` so readable outputs and index files are regenerated
from the same source snapshot.

## Runtime

Run the converter outside sandboxed execution for real conversions. LibreOffice
needs normal macOS process access; sandboxed runs can make `soffice` abort and
produce fallback or failed conversions instead of primary HTML output.

## Inputs

Supported source formats: `.docx`, `.odt`, `.pdf`, `.xlsx`, `.pptx`.

Standalone image files are skipped as sources. Images embedded in supported
documents are handled as generated assets.

`SOURCE_DIR` and `DEST_DIR` must be separate trees. The tool rejects nesting in
either direction, including source paths inside generated destination folders.

## Outputs

```text
DEST_DIR/
  ...mirrored structure...
    document.html
    spreadsheet.md
    presentation.md

  assets/
    by-hash/
      image-<hash>.<ext>

  tables/
    document-slug-<path-hash>.tables.json

  index/
    documents.jsonl
    chunks.jsonl
    tables.jsonl
    conversion-report.json
    conversion-summary.md
```

Reports live under `DEST_DIR/index/`. The destination root contains converted
content plus generated support folders.

If two sources would map to the same readable output path, the later output gets
a numeric suffix such as `document-2.html`.

## Conversion

- DOCX and ODT convert to HTML through LibreOffice.
- DOCX has fallback recovery paths when primary conversion fails. Successful
  recovery is recorded in the report.
- PDF remains the readable source; index data and detected table JSON are
  extracted separately.
- XLSX converts to Markdown plus structured table JSON.
- PPTX converts to Markdown slide text plus extracted slide images.

## Index

Index rows use relative source paths. Absolute source and destination roots are
stored once in `index/conversion-report.json`.

`documents.jsonl` stores one row per converted source document.

`chunks.jsonl` stores searchable text chunks. Full table payloads are not
embedded in chunks; table chunks point to `table_json_path` and
`table_payload_pointer`.

`tables.jsonl` stores one metadata row per extracted table. Full table payloads
are stored in one `tables/<output-slug>-<path-hash>.tables.json` file per source
document.

Files that fail conversion do not contribute `documents`, `chunks`, or `tables`
rows.

## Safety

- Source files are opened only for reading.
- Dry-run writes nothing.
- Symlink files in `SOURCE_DIR` are skipped, and symlink directories are not
  traversed.
- Generated destination paths are not written, deleted, or regenerated through
  symlinks.
- Generated `assets/`, `tables/`, and `index/` paths are preflight-checked
  before write runs.
- After a real run, the report records whether source file size and `mtime`
  remained unchanged.
- Per-file conversion failures are recorded in the report and do not stop other
  source files. Newly created generated files for failed sources are rolled back
  where possible.

## Success Check

For a successful full regeneration, check `index/conversion-summary.md` and
`index/conversion-report.json` for:

- `source_unchanged: true`
- no failed conversions
- no missing table packs
- no missing asset links
