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

## Scope Rules

- keep project-specific business logic out of `common/`
- keep engine/runtime primitives here when they are intended for reuse across multiple projects
- when a deployed runtime copies code into a service root, deploy the matching `common/` bundle together with the project code

## Source Of Truth

- use [RULEBOOK.md](../RULEBOOK.md) for the shared architectural and database rules behind this folder
- use this folder only for reusable implementation and config, not for redefining project-local behavior
