# codex-token-monitor

`codex-token-monitor` is a local Codex plugin that packages one reusable skill and a helper script for watching rollout token usage in realtime.

When the monitor runs inside the Codex integrated terminal, it first pins itself
to the current thread via `CODEX_THREAD_ID`. Project `cwd` matching is only a
fallback for terminals that do not expose a thread id.

## Why plugin first

According to the official Codex docs:

- skills are the authoring format for reusable workflows
- plugins are the installable distribution unit

This plugin follows that model:

- the workflow lives as a skill under `skills/codex-token-monitor/`
- the implementation lives in `scripts/codex_token_monitor.py`
- the plugin wrapper makes it installable through a marketplace

## What it shows

- active rollout file for the selected project
- cumulative tokens: input, cached input, output, reasoning, total
- latest delta from `last_token_usage`
- rate-limit usage, remaining percentage, and reset time
- file freshness and rough throughput

## Data Sources

The monitor intentionally combines two scopes of telemetry:

- `delta` is thread-local and comes from the pinned rollout session for the current monitor process
- `limits` are global-at-account scope and come from the freshest known rollout across all discovered Codex sessions

This split exists because Codex rate limits are shared across chats, while the
token delta is only meaningful inside one specific thread.

Current source details:

- `delta`: latest `event_msg.payload.type == "token_count"` -> `info.last_token_usage` from the pinned rollout
- `tokens`: latest `info.total_token_usage` from the pinned rollout
- `limits`: latest `rate_limits` from the freshest rollout JSONL seen under `~/.codex/sessions/**/rollout-*.jsonl`
- `session`: thread identity of the pinned rollout only

Important limitation:

- the monitor does not currently read the same internal Codex UI rate-limit store
- if Codex UI receives a fresher internal account update before it appears in any rollout JSONL, the UI can still lead the monitor briefly

## Run from the source checkout

```bash
python3 plugins/codex-token-monitor/scripts/codex_token_monitor.py --cwd "$PWD"
```

You can also pin a specific Codex thread manually:

```bash
python3 plugins/codex-token-monitor/scripts/codex_token_monitor.py --thread-id "$CODEX_THREAD_ID"
```

## Run inside the Codex app terminal

Open the integrated terminal for the current thread with `Cmd+J` or the
terminal icon in the top-right corner of the Codex app window, then run:

```bash
python3 plugins/codex-token-monitor/scripts/codex_token_monitor.py --cwd "$PWD"
```

Useful built-in terminal shortcuts:

- `Cmd+J`: toggle the integrated terminal panel
- `Ctrl+L`: clear the terminal
- `Cmd+N`: open a new thread if you want a separate thread-local terminal just for the monitor

This matches the official Codex app behavior for the integrated terminal and
keyboard shortcuts.

## Run from the Codex action button

This repository now includes a repo-local Codex local environment config at:

- `.codex/environments/environment.toml`

It defines one header action:

- `Token monitor`

In the Codex app this action appears under the `Run` button for this project
and launches the monitor in the integrated terminal with:

```bash
python3 plugins/codex-token-monitor/scripts/codex_token_monitor.py --cwd "$PWD"
```

If the action is not visible yet, open the project again or open Codex
Settings -> Local Environments for this workspace so the app reloads the
project-local `.codex/environments/environment.toml`.

Print one snapshot:

```bash
python3 plugins/codex-token-monitor/scripts/codex_token_monitor.py --cwd "$PWD" --once
```

List candidate sessions for the current project or thread:

```bash
python3 plugins/codex-token-monitor/scripts/codex_token_monitor.py --list-sessions
```

Show the detailed format:

```bash
python3 plugins/codex-token-monitor/scripts/codex_token_monitor.py --cwd "$PWD" --mode full
```

## Output formats

The monitor has two render modes:

- `brief`: default mode for constant on-screen use
- `full`: expanded mode for inspection and troubleshooting

### Brief mode

Example:

```text
age     limits 10m 5s
delta   in +210,755 | out +195 | total +210,950
limits  day 91% left r 282m | week 86% left r 8016m
```

Field meanings:

- `age.limits`: age of the latest `rate_limits` snapshot
- `delta.in`: latest per-update `input_tokens`
- `delta.out`: latest per-update `output_tokens`
- `delta.total`: latest per-update `total_tokens`
- `limits.day`: remaining percentage in the primary rate-limit window
- `limits.week`: remaining percentage in the secondary rate-limit window
- `left`: remaining percentage before that window is exhausted
- `r`: time until reset in minutes

Source mapping in `brief`:

- `delta.*`: from the current pinned thread rollout
- `limits.*`: from the freshest known rollout globally, not necessarily the current thread

Brief mode intentionally omits:

- session name
- session id
- rollout filename
- cumulative tokens row
- cached input
- reasoning tokens
- plan name
- freshness and throughput lines

Brief mode now treats `delta` and `limits` as different scopes on purpose:

- `delta` comes from the current pinned thread
- `limits` come from the freshest known rollout across all sessions, so they stay as current as possible even if another chat produced the latest rate-limit update

## Session Diagnostics

When several Codex chats share the same project `cwd`, use:

```bash
python3 plugins/codex-token-monitor/scripts/codex_token_monitor.py --list-sessions
```

Output format:

- `thread`: current preferred `CODEX_THREAD_ID`, if available
- `cwd`: current project scope used for fallback matching
- `*`: the session currently pinned by `--thread-id` or `CODEX_THREAD_ID`
- each listed row shows `session_id | thread_name | file age | rollout filename`

### Full mode

Example:

```text
age     limits 10m 5s
session Добавь realtime-статус токенов | sid 019daacd-d004-77c1-8c43-880e765537ef | rollout-...
tokens  in 6,689,927 | cache 5,613,056 | out 59,499 | rsn 15,995 | total 6,749,426
delta   in +209,784 | cache +209,536 | out +322 | rsn +35 | total +210,106
limits  plan plus | day 9.0% used 91.0% left reset 03:17:14 CEST (300m) | week 14.0% used 86.0% left reset 12:12:00 CEST (10080m)
time    event <1s | file <1s | events 535 | tok_events 20 | 173282 tok/min | 1.1 upd/min
```

Field meanings:

- `age.limits`: age of the latest `rate_limits` snapshot
- `session`: current thread name
- `sid`: Codex session id from `session_meta.payload.id`
- `rollout-...jsonl`: active rollout filename
- `tokens.in`: cumulative `input_tokens`
- `tokens.cache`: cumulative `cached_input_tokens`
- `tokens.out`: cumulative `output_tokens`
- `tokens.rsn`: cumulative `reasoning_output_tokens`
- `tokens.total`: cumulative `total_tokens`
- `delta.*`: latest per-update values from `last_token_usage`
- `plan`: plan type from `rate_limits.plan_type`
- `day`: primary rate-limit window
- `week`: secondary rate-limit window
- `used`: used percent inside that limit window
- `left`: remaining percent inside that limit window
- `reset`: wall-clock reset time followed by the configured window size in minutes
- `time.event`: age of the latest parsed event
- `time.file`: age of the rollout file mtime
- `events`: total parsed events in the rollout file
- `tok_events`: parsed `token_count` events kept in memory
- `tok/min`: throughput based on the rolling sample window
- `upd/min`: update frequency based on the rolling sample window

`full` keeps the current thread identity visible, but the rate-limit block still prefers the freshest known rollout globally when that is newer than the current thread snapshot.

Source mapping in `full`:

- `session`: pinned thread rollout
- `tokens`: pinned thread rollout
- `delta`: pinned thread rollout
- `limits`: freshest known rollout globally

## Color thresholds

Colors are enabled only in TTY mode.

- rate limits: color is still based on used percent even when `brief` shows `left`; green `<60% used`, yellow `60-84.9% used`, red `>=85% used`
- freshness lag: green `<5s`, yellow `5-29.9s`, red `>=30s`

## Reset handling

`rate_limits` are only refreshed when Codex emits a new `token_count` event.
If a reset time has already passed but no newer `token_count` has arrived yet,
the monitor now rolls that window forward to the next interval and clears the
stale usage instead of rendering the old percentage with `r 0m`.

## Repo-local plugin wiring

This repository includes `.agents/plugins/marketplace.json`, which points at:

- `./plugins/codex-token-monitor`

That follows the documented repo-marketplace layout for local plugins.

## Install as a personal plugin

```bash
bash plugins/codex-token-monitor/scripts/install_local_plugin.sh
```

This copies the plugin into `~/.codex/plugins/codex-token-monitor`, updates `~/.agents/plugins/marketplace.json`, and leaves the workflow available across repositories after a Codex restart.
