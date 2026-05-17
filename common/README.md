# common

Repository-wide shared runtime helpers and config bundles reused by multiple local tools and services.

## Purpose

Use `common/` for code and config that are:

- shared by more than one top-level project
- not specific to Telegram-only workflows
- part of a stable runtime contract that should behave the same in both repo and deployed service-root copies

## Current Contents

- [sqlite.py](./sqlite.py)  
  Shared SQLite connection and transaction helpers with repository-wide defaults for autocommit, WAL mode, busy timeout, and short explicit write transactions.

- [config/sqlite.toml](./config/sqlite.toml)  
  Shared SQLite runtime defaults consumed by the helper layer.

- [process.py](./process.py) and [ttl_runner.py](./ttl_runner.py)  
  Shared non-daemon process runtime helpers for hard wall-clock TTL, process-group shutdown, and reusable timeout defaults.

- [config/process.toml](./config/process.toml)  
  Shared defaults for one-shot process TTL, grace-period shutdown, polling, and timeout exit behavior.

## Scope Rules

- keep project-specific business logic out of `common/`
- keep engine/runtime primitives here when they are intended for reuse across multiple projects
- when a deployed runtime copies code into a service root, deploy the matching `common/` bundle together with the project code

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
