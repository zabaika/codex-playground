# KB Index

Local indexing and retrieval tool for an Obsidian knowledge base, designed to avoid a full vault scan on every lookup.

## What it does

- builds and updates a local note index
- stores note-level metadata and lead-summary retrieval signals
- provides `FTS5`-based retrieval over indexed notes
- exposes CLI entrypoints for `build`, `update`, `search`, and `status`
- supports scheduled auto-update on macOS through `launchd`
- supports title-first note lookup and indexed tag discovery

## Scope

This project is responsible for:

- local index build and update
- note-level metadata storage
- `FTS5` retrieval
- operator-facing CLI commands
- scheduled auto-update through `launchd` on macOS

This project does not currently provide:

- vector search
- embeddings
- an `MCP` server
- a separate search daemon
- deep graph expansion over wikilinks

## Layout

```text
tools/kb-index/
├── README.md
├── src/
│   └── kb_index/
├── tests/
├── config/
└── data/
```

Directory roles:

- `src/kb_index/`: indexer, search, CLI, and support modules
- `tests/`: unit and integration tests
- `config/`: runtime config and examples
- `data/`: local runtime artifacts

## Runtime files

Project-local runtime artifacts in `data/`:

- `kb_index.sqlite`
- `kb_index_state.json`
- `data/launchd/auto_update.startup.log`
- `data/launchd/auto_update.stdout.log`
- `data/launchd/auto_update.stderr.log`

When auto-update is installed, the service uses a separate runtime root:

- `~/Library/Application Support/kb_index_service`

That service root contains:

- a runtime copy of `src/kb_index`
- a runtime copy of `common/`
- a symlinked `config/runtime.local.toml`
- the shell runner used by `launchd`

Canonical operational logs still stay in project-local `data/launchd/`, not in the service root.

## Config

The CLI reads `config/runtime.local.toml` first. Explicit CLI arguments may override paths for one run.

Main config areas:

- vault scope through `include_roots`, `exclude_roots`, and `exclude_globs`
- retrieval defaults and ranking weights
- note-type weights and exact-title bonuses
- scheduled auto-update under `[auto_update]`
- one-shot auto-update TTL and shutdown behavior under `[auto_update]`

The default number of search results is controlled by `retrieval.default_limit`.

## CLI commands

Available commands:

- `build_kb_index`
- `update_kb_index`
- `search_kb`
- `list_kb_tags`
- `status_kb_index`
- `install_kb_index_auto_update`
- `status_kb_index_auto_update`
- `uninstall_kb_index_auto_update`

### Search behavior

Retrieval is config-driven. Ranking weights, note-type weights, and exact-title bonuses are not hardcoded in `search.py`.

Search uses indexed note signals such as:

- `title`
- lead summary
- `headings`
- `tags`
- `links_out`

`links_out` acts as a weak graph-aware signal for related-note discovery, so notes that already link to a target concept or adjacent node can rank higher in related-note workflows.

### Title-first lookup

Use the title-oriented mode when the workflow already knows or almost knows the target note title:

```bash
search_kb --mode title-first --note-type concept "Known note title"
```

This should be preferred over direct filename scans when the note should be resolved through the index.

### Tag discovery

Use `list_kb_tags` for indexed tag inspection:

```bash
list_kb_tags --config-path /absolute/path/to/runtime.local.toml --json
list_kb_tags --config-path /absolute/path/to/runtime.local.toml --tag developer-productivity --json
list_kb_tags --config-path /absolute/path/to/runtime.local.toml --prefix developer --json
```

Use it when you need to:

- list all tags currently used in the knowledge base
- check whether a specific tag already exists
- inspect neighboring tags before creating a new one

## Indexing corpus

The indexing corpus is defined in `config/runtime.local.toml` through:

- `include_roots`
- `exclude_roots`
- `exclude_globs`

Auto-update scheduling is also configured in `config/runtime.local.toml` under `[auto_update]`.

Current setup supports one scheduler mode:

- `launchd` on macOS, which periodically runs the same canonical `update_kb_index`

Current setup also keeps the scheduled run bounded:

- auto-update is a one-shot `launchd` job, not a daemon
- the generated runner uses `common/ttl_runner.py`
- hard TTL and shutdown signals come from `[auto_update]` in `runtime.local.toml`

## Retrieval model

Knowledge workflows should use a two-step path:

1. `search`
2. `read`

Full vault scans should remain a fallback only when the index is missing, broken, or clearly stale.

## Auto-update

Auto-update does not introduce a separate indexing daemon. It only runs the existing `update_kb_index` on a schedule so that:

- new notes are picked up without a manual rebuild
- changed notes are reindexed incrementally
- deleted notes are removed through the same canonical path

Commands:

- `install_kb_index_auto_update --config-path ...`
- `status_kb_index_auto_update --config-path ...`
- `uninstall_kb_index_auto_update --config-path ...`

The installer does not run code directly from `Documents/Playground`. Instead, it copies the runtime layer into `~/Library/Application Support/kb_index_service`, keeps `runtime.local.toml` as a symlink to the repository config, generates the shell runner there, and registers that runner in `launchd`.

`[auto_update]` also owns the hard runtime ceiling for scheduled runs:

- `run_total_timeout_seconds`
- `termination_grace_seconds`
- `poll_interval_seconds`
- `timeout_exit_code`
- `term_signal`
- `kill_signal`

These values are intentionally project-local even though `common/config/process.toml` provides defaults, so `kb-index` can keep a much shorter timeout budget than `digest`.

`status_kb_index` also shows `configured_auto_update`, so retrieval settings and schedule settings are visible together. `status_kb_index_auto_update` reports the installed launch agent, service root, and canonical project-local log directory.

### Reload after config changes

If `config/runtime.local.toml` changes and `launchd` needs to pick up new settings such as:

- `auto_update.interval_minutes`
- `launchd_label`
- other service-root deployment settings

rerun the canonical installer:

```bash
install_kb_index_auto_update --config-path /absolute/path/to/runtime.local.toml
```

Rerunning the installer:

- refreshes the runtime copy in `~/Library/Application Support/kb_index_service`
- refreshes the shared `common/` runtime copy used by the launchd runner
- recreates the `runtime.local.toml` symlink to the chosen source-of-truth config
- regenerates the shell runner
- reinstalls the `launchd` plist

Use it as the canonical way to restart the service and reload config.

Verification after reload:

```bash
status_kb_index_auto_update --config-path /absolute/path/to/runtime.local.toml
status_kb_index --config-path /absolute/path/to/runtime.local.toml
```

## Freshness model

Index freshness is maintained through two paths:

1. `scheduled auto-update`
   - `launchd` runs incremental `update_kb_index` on the interval from `auto_update.interval_minutes`
2. `post-write sync`
   - knowledge-base skills that created or updated notes and know `paths.kb_index_config` can call `update_kb_index` once at the end of the run

This split ensures that:

- external vault changes do not wait for a manual rebuild
- notes just created through a skill enter the index immediately instead of waiting for the next schedule

## Future improvements

The current stage-1 core is already implemented and in use.

Optional future improvements may include:

1. a `watch` mode on top of the current `launchd` schedule
2. `read_kb` as a standardized note-read CLI over an already found shortlist
3. deeper graph expansion over wikilinks if related-note discovery outgrows the current `links_out` signal
4. a vector or hybrid semantic layer if a real need appears later
