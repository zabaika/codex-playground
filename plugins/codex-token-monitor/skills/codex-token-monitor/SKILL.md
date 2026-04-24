---
name: codex-token-monitor
description: Launch or inspect a local realtime token monitor for Codex rollout JSONL sessions. Use when the user wants active-session token totals, per-turn deltas, rate-limit status, or rollout-file discovery without patching Codex UI.
---

# Codex Token Monitor

## Overview

Use this skill when the user wants realtime visibility into Codex token usage without modifying Codex UI. The monitor is an external sidecar TUI that reads the canonical rollout JSONL files already written by Codex under `~/.codex/sessions/`.

The workflow is:

1. resolve the active rollout file, preferring the current Codex thread via `CODEX_THREAD_ID` and falling back to matching the current project path to `session_meta.payload.cwd`
2. read `session_meta` plus `event_msg.payload.type == "token_count"`
3. display:
   - active session file
   - cumulative tokens
   - per-update delta
   - rate limits
   - throughput and freshness

## Source Of Truth

- Treat the rollout JSONL as the only canonical telemetry source.
- Do not create parallel token logs, cache files, or convenience summaries if the same facts are already present in the rollout stream.
- Prefer `last_token_usage` for the delta block.
- Use cumulative totals only for the totals block and throughput calculations.
- If the first `token_count` event contains only `rate_limits` and no `info`, keep the rate-limit block visible and leave token fields blank until a later event arrives.

Telemetry scope rules:

- `delta` is thread-local and must come from the pinned rollout session
- `tokens` are thread-local and must come from the pinned rollout session
- `limits` are account-global in meaning, so they should prefer the freshest known rollout across all sessions rather than blindly using the current thread snapshot

Current practical source mapping:

- pinned rollout:
  - `session`
  - `tokens`
  - `delta`
- freshest known rollout globally:
  - `limits`

Known limitation:

- this skill still relies on rollout JSONL rather than the internal Codex UI rate-limit store
- if the UI receives a newer internal account update before any rollout writes a new `rate_limits` payload, UI values may briefly be fresher

## Run The Monitor

From the repository checkout:

```bash
python3 plugins/codex-token-monitor/scripts/codex_token_monitor.py --cwd "$PWD"
```

If the terminal already exposes the current thread id, the script will pin to it
automatically. You can also pass it explicitly:

```bash
python3 plugins/codex-token-monitor/scripts/codex_token_monitor.py --thread-id "$CODEX_THREAD_ID"
```

Inside the Codex app, prefer the built-in integrated terminal for the current
thread:

1. open the terminal panel with `Cmd+J` or the terminal icon in the top-right
2. run:

```bash
python3 plugins/codex-token-monitor/scripts/codex_token_monitor.py --cwd "$PWD"
```

Useful terminal shortcuts:

- `Cmd+J`: toggle terminal
- `Ctrl+L`: clear terminal
- `Cmd+N`: open a separate thread if you want a dedicated thread-local terminal for the monitor

This repository also defines a repo-local Codex local environment action in:

- `.codex/environments/environment.toml`

The action name is:

- `Token monitor`

Use it when the user wants one-click launch from the Codex app header `Run`
button instead of typing the command manually in the terminal.

For a single snapshot instead of live follow mode:

```bash
python3 plugins/codex-token-monitor/scripts/codex_token_monitor.py --cwd "$PWD" --once
```

To list candidate sessions when multiple chats share the same project:

```bash
python3 plugins/codex-token-monitor/scripts/codex_token_monitor.py --list-sessions
```

To pin an exact rollout file:

```bash
python3 plugins/codex-token-monitor/scripts/codex_token_monitor.py --file "[ROLLOUT_JSONL]"
```

To force the expanded renderer:

```bash
python3 plugins/codex-token-monitor/scripts/codex_token_monitor.py --cwd "$PWD" --mode full
```

## Skill Behavior

1. Resolve the skill directory from this `SKILL.md`.
2. If the user asked to run the monitor, use the local script under `../../scripts/`.
3. If the user asked only for architecture or troubleshooting, inspect:
   - `~/.codex/sessions/**/rollout-*.jsonl`
   - `~/.codex/session_index.jsonl` when it helps map thread names
4. Prefer thread-scoped rollout discovery via `CODEX_THREAD_ID` when available. Use project-scoped `cwd` matching only as a fallback.
5. If there is no matching live rollout for the current project, report that clearly instead of guessing.
6. If rollout selection is ambiguous across multiple chats in one project, use `--list-sessions` before guessing.

## Output Contract

The monitor has two output modes.

### `brief` mode

- Default mode for постоянного использования.
- Keep width near 80 characters.
- Show exactly these logical rows:
  - `age`
  - `delta`
  - `limits`

Field contract:

- `age.limits`: age of the latest `rate_limits` snapshot
- `delta.in`: latest per-update input tokens
- `delta.out`: latest per-update output tokens
- `delta.total`: latest per-update total tokens
- `limits.day`: primary limit remaining percent plus reset in minutes
- `limits.week`: secondary limit remaining percent plus reset in minutes

Source contract in `brief`:

- `delta.*`: pinned thread rollout
- `limits.*`: freshest known rollout globally

Omit in `brief`:

- session name
- session id
- rollout filename
- cumulative tokens row
- cached input
- reasoning tokens
- plan name
- `time`

### `full` mode

- Inspection mode for debugging and detailed review.
- Show these logical rows:
  - `age`
  - `session`
  - `tokens`
  - `delta`
  - `limits`
  - `time`

Field contract:

- `age.limits`: age of the latest `rate_limits` snapshot
- `session`: thread name, session id, rollout filename
- `tokens`: input, cached input, output, reasoning, total
- `delta`: input, cached input, output, reasoning, total
- `limits.plan`: plan type
- `limits.day`: primary limit used percent, remaining percent, wall-clock reset time, window minutes
- `limits.week`: secondary limit used percent, remaining percent, wall-clock reset time, window minutes
- `time.event`: age of the latest event
- `time.file`: age of rollout file mtime
- `time.events`: parsed event count
- `time.tok_events`: retained token-count sample count
- `time.tok/min`: rolling token throughput
- `time.upd/min`: rolling update frequency

Keep the display read-only. This skill is for observability, not for altering Codex state.

Limit source rule:

- `delta` remains thread-local
- `limits` should prefer the freshest known rollout globally when that sample is newer than the current thread snapshot

Field source contract in `full`:

- `session`: pinned thread rollout
- `tokens`: pinned thread rollout
- `delta`: pinned thread rollout
- `limits`: freshest known rollout globally

Rate-limit freshness rule:

- `rate_limits` arrive only with `token_count`
- if a window reset has already passed and no newer `token_count` exists yet, do not keep rendering the expired percentage with `r 0m`
- instead, roll the reset forward to the next interval and clear stale usage until the next authoritative update arrives
