# common

Repository-wide shared runtime helpers and config bundles reused by multiple local tools and services.

## Purpose

Use `common/` for code and config that are:

- shared by more than one top-level project
- not specific to Telegram-only workflows
- part of a stable runtime contract that should behave the same in both repo and deployed service-root copies

## Current Contents

- [json_io.py](./json_io.py)
  Shared atomic UTF-8 file replacement with a JSON adapter, private temporary files, and file/directory fsync.

- [sqlite.py](./sqlite.py)  
  Shared SQLite connection and transaction helpers with repository-wide defaults for autocommit, WAL mode, busy timeout, and short explicit write transactions.

- [config/sqlite.toml](./config/sqlite.toml)  
  Shared SQLite runtime defaults consumed by the helper layer.

- [process.py](./process.py) and [ttl_runner.py](./ttl_runner.py)  
  Shared non-daemon process runtime helpers for hard wall-clock TTL, process-group shutdown, and reusable timeout defaults.
  By default (`--sleep-policy interrupt`), TTL includes host sleep on macOS/Linux and sleep interruptions have a distinct exit code.
  Opt-in `--sleep-policy pause` resumes after sleep and enforces TTL against elapsed awake time, including network waits and retries; host sleep does not consume that budget.
  Audit diagnostics include `sleep_policy`, `elapsed_seconds` (including sleep), `sleep_seconds`, and `ttl_elapsed_seconds` (the time counted against TTL).
  Optional `caffeinate -i -s` runs only for the worker lifetime, with diagnostics in the audit.
  Python callers can supply a nonblocking `stop_requested` callback and an explicit child `env`; shutdown cleans remaining owned descendants even when the root has already exited.

- [config/process.toml](./config/process.toml)  
  Shared defaults for one-shot process TTL, grace-period shutdown, polling, and timeout exit behavior.

## Scope Rules

- keep project-specific business logic out of `common/`
- keep engine/runtime primitives here when they are intended for reuse across multiple projects
- when a deployed runtime copies code into a service root, deploy the matching `common/` bundle together with the project code

## Atomic File Contract

`json_io.write_text_atomic(path, text)` writes UTF-8 through a same-directory private
temporary file (mode `0600`), flushes and fsyncs it, replaces the target, then fsyncs
the directory. `write_json_atomic` serializes JSON with a final newline and delegates
to the same writer; `sync_directory` is also available to recovery callers.

Encoding, serialization and filesystem errors propagate. A failure before replacement
preserves an existing target and removes the temporary file. A directory-fsync failure
after replacement leaves the new file visible; callers must verify/reconcile that state
rather than treating the exception as a rollback. This helper does not own application
artifact identities, overwrite permissions, retention or delivery receipts.

## Tests

```bash
.venv-test-gap-detection/bin/python -m pytest common/tests -q
```

Check consuming projects when shared behavior changes. A copied service must receive
the matching helper/config bundle through its canonical installer.

## Canonical Ownership

`common/` is the canonical owner only for reusable low-level runtime primitives and their shared config bundles.

`common/` must not own:

- project-specific business logic
- domain entities or lifecycle semantics
- command handlers or product-specific workflows
- repositories or migrations for one project
- quality gates, approval logic, or UI-specific behavior

## Extraction Rule

Move code into `common/` only when all of the following are true:

- it is reused or clearly intended for reuse by multiple top-level projects
- it is domain-agnostic
- it defines a stable runtime contract
- it should behave the same in both the repo and deployed runtime copies

Project-local thin wrappers over `common/` are allowed.
Project-local forks of the same low-level runtime behavior should be treated as drift and avoided.

## Source Of Truth

- use [RULEBOOK.md](../RULEBOOK.md) for the shared architectural and database rules behind this folder
- use this folder only for reusable implementation and config, not for redefining project-local behavior
