# common

Repository-wide shared runtime helpers and config bundles reused by multiple local tools and services.

## Purpose

Use `common/` for code and config that are:

- shared by more than one top-level project
- not specific to Telegram-only workflows
- part of a stable runtime contract that should behave the same in both repo and deployed service-root copies

## Current Contents

- [python_runtime.py](./python_runtime.py), [python_runtime.sh](./python_runtime.sh)
  and [config/python.toml](./config/python.toml)
  Shared installed-Python selection and observed runtime bindings. The policy
  owns the bootstrap floor, implementation, architecture and GIL requirements;
  project metadata and locked artifacts retain their stricter constraints.
  See [Python runtime selection](#python-runtime-selection).

- [http.py](./http.py)
  Opt-in, run-scoped HTTP connection pools using `urllib3`. The context closes
  every pool on exit, preserves per-request timeouts, verifies TLS, and follows
  no redirects or automatic retries. Only direct connections are supported:
  configured HTTP, HTTPS, ALL or SOCKS proxies cause a clear startup error
  without exposing their addresses or credentials. A `no_proxy` list alone
  does not block direct connections. OS exception types and errno are retained
  so callers can distinguish permanent failures from real timeouts.
  Received HTTP error status and headers remain authoritative when reading
  the diagnostic body fails; an unavailable body does not become a network error.
  Callers retain their retry and uncertain-send policies. Only consumers that
  enable `pooled_http_session` need the `urllib3` dependency.

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
  On the main thread, default TERM/INT cancellation immediately stops the owned
  child group, including during nested supervision or cleanup. External TERM
  returns `128 + signal` and records `cancelled`; default INT retains
  `KeyboardInterrupt` after cleanup. Ordinary TTL expiry keeps TERM/grace/KILL.
  Application signal handlers and ignored signals remain caller-owned; temporary
  handlers are restored. Background-thread callers retain their existing
  application-owned signal/cancellation policy.

- [config/process.toml](./config/process.toml)  
  Shared defaults for one-shot process TTL, grace-period shutdown, polling, and timeout exit behavior.

## Scope Rules

- keep project-specific business logic out of `common/`
- keep engine/runtime primitives here when they are intended for reuse across multiple projects
- when a deployed runtime copies code into a service root, deploy the matching `common/` bundle together with the project code

## Python Runtime Selection

From the repository root, inspect the available compatible Python with:

```bash
source common/python_runtime.sh
python_runtime_select
```

The shell bootstrap only needs an installed Python with stdlib `tomllib`.
The Python owner reads the shared policy and resolves the application
interpreter under the shared process TTL. Selection precedence is explicit
`--python`, `PYTHON_BIN`, optional project-local `[python].executable`, native
Homebrew Python, then `python3` on PATH. An invalid explicit or local choice
fails instead of falling back. Project config is supplied through `--config`.

Use `--pyproject` to check the owning project's `requires-python` through
public pip dry-run/report. The temporary wheel contains only derived metadata
and is never installed. Pip warnings are treated as unverified compatibility;
this prevents invalid metadata from being silently ignored. Audited wheel
sets must additionally pass their real offline, hash-locked install plan.
Basic policy selection alone does not prove that application dependencies work.

The default result is JSON. `--path-only` is its shell-friendly path view;
`--record` atomically saves private installation evidence. This evidence
contains observed interpreter identity and input digests, not editable defaults
or secrets. A venv executable remains a venv path; its base identity is recorded
separately. Installers must check project dependencies and imports before
replacing a working runtime.

Installed service runners consume the validated binding or explicit venv path.
They do not rediscover Python, apply ambient overrides or install packages at
startup. A changed minor version or ABI requires an explicit environment rebuild
and canonical redeployment. Stateless CLI calls may resolve at invocation.
Copy this common bundle and config with each deployed consumer that needs it;
the repository remains the editable owner.

The policy floor is Python 3.11 because bootstrap reads TOML with stdlib
`tomllib`. This is a bootstrap requirement, not a claim that every dependency
supports that minimum. Project metadata and installed distributions impose
additional constraints. The policy requires native ARM64 CPython with the GIL.

`python_runtime_prepare --venv /absolute/path/to/env --requirements /path/to/requirements.txt`
preflights a fresh wheel plan before replacing an incompatible environment.
It checks installed metadata, dependency consistency and requested `--import`
modules. Compatible environments are reused; changed base, ABI or architecture
requires reconstruction. A failed preparation restores the previous directory.
Automatic selection checks every destination's reuse or fresh dependency plan
before accepting a candidate. If its wheels are incompatible, discovery continues
to the next candidate; invalid explicit or local choices still fail. Healthy
existing environments can be reused without requiring fresh wheels. Preparation
rechecks the selected candidate before replacement; it does not switch Python
after starting to change an environment.
`--venv` can be repeated to prepare multiple consumers from one selected base.
`PIP_CONSTRAINT` freezes reviewed versions; `PIP_FIND_LINKS` supplies previously
reviewed wheels. Preparation never builds dependencies from source.

Prepare shared environments from the repository root with
`bash common/setup_python.sh tests` or `bash common/setup_python.sh validation`.
Their dependencies remain owned by the existing project requirements files.

## Python Runtime Upgrades

1. Install the desired Python separately; the resolver never installs interpreters.
2. Review project metadata and pinned wheel compatibility. Missing compatible
   audited artifacts require a separate decision; keep the working runtime.
3. Set `PYTHON_BIN` or project-local `[python].executable` if discovery should
   choose a specific installed interpreter. Reuse reviewed constraints and wheels.
4. Run the existing canonical installers or shared setup helper explicitly.
   Service runners keep their fixed binding until redeployment.
5. Run the component checks and verify actual executable, ABI, imports, copied
   shared code and service state. Preserve data and configuration throughout.
6. Remove old runtimes only after verifying every consumer and recovery path.

Generated `.python-runtime.json` records are observations, not editable policy.
Do not change pins, hashes, audited archives or wheel metadata to bypass a failure.

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
