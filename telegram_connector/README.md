# Telegram Connector

Local Telegram toolkit for channel history ingestion, OCR, CSV export, scheduled digests, and a small Telegram Bot API bridge.

## Sources Of Truth

- [../RULEBOOK.md](../RULEBOOK.md): repository-wide runtime, config, install, and service rules
- [./AGENTS.md](./AGENTS.md): project-specific coding and maintenance guidance
- [config/runtime.example.toml](./config/runtime.example.toml): canonical runtime config shape, defaults, comments, and clamped ranges
- [config/digest_prompts.toml](./config/digest_prompts.toml): version-controlled digest prompt bundle
- [config/analysis_prompts.toml](./config/analysis_prompts.toml): versioned analytics templates, command rubrics, and detail instructions
- [../telegram_shared/README.md](../telegram_shared/README.md): shared Telegram helper boundaries and retry semantics
- `--help` on the Python entrypoints: exact CLI syntax

For history, digest and analytics maintenance, use the [module ownership map](./AGENTS.md#architecture).
History and digest entrypoints preserve the CLI and public calls while configuration,
storage, session adapters, rendering and pipelines have dedicated local owners.
`telegram_analysis.py` remains the public worker CLI and composition root;
settings, evidence validation, rendering and pipeline stages have dedicated owners.

## What It Does

- reads channel history through a Telegram user session with Telethon
- stores channel, message, media, sync, and AI usage data in local SQLite
- downloads media and runs Tesseract OCR for images
- exports saved channel history to CSV
- builds OpenAI digests over stored history and delivers per-channel summaries to Telegram
- runs bounded `/topics`, `/search` and `/qa` analytics over explicitly selected channels
- runs a Bot API bridge for chat commands such as `/digest`, `/tail`, `/update`, `/exportcsv`, and `/ocr`
- records latest digest launch state in `data/launchd/digest.last_attempt.json`

## Runtime Config

Create `config/runtime.local.toml` from [config/runtime.example.toml](./config/runtime.example.toml). Keep local secrets and machine-specific paths out of git.

The README intentionally does not restate every config key. Use `runtime.example.toml` for the complete schema, comments, and clamped ranges. The operator-facing settings most often changed are:

- `telethon.*`, `auth.default_mode`, and `channels.default_list` for account access and default channel selection
- `bridge.allowed_chat_ids`, `bridge.allowed_user_ids`, and `bridge.allowed_usernames` for bot command access control
- `bridge.worker_process_timeout_seconds` for bridge-launched command lifetime
- `bridge.send_message_retry_attempts` and `bridge.send_message_retry_backoff_seconds` for transient Telegram `sendMessage` failures, including network errors, timeouts, and Telegram HTTP 5xx
- `digest.*`, `digest_ai.*`, and `digest_limits.*` for scheduled digest window, AI batching, sync budget, process TTL, and bounded OpenAI retry delays
- `digest_prompts.file` for the prompt bundle used by digest
- `analysis.*` and `analysis_prompts.file` for analytics budgets, detail profiles, and local templates; missing new keys use the tracked example defaults, while invalid new values fail validation
- `sync.sync_limit` and `sync.batch_size` for non-digest sync runs
- `ocr.*` for OCR defaults and subprocess timeout

`telegram.default_chat_id` can stay empty until you send one message to the bot and discover the chat id through `listen`.

### Secrets

Prefer Keychain references in `runtime.local.toml`, for example:

```toml
[secrets]
bot_token = "keychain://telegram-connector/bot_token"
api_hash = "keychain://telegram-connector/api_hash"
```

Useful Keychain commands:

```bash
security add-generic-password -U -s telegram-connector -a bot_token -w '<token>'
security find-generic-password -s telegram-connector -a bot_token -w
```

## Operation

Start the bridge manually:

```bash
python3 telegram_connector/telegram_bridge.py listen --run-commands
```

Install or refresh the macOS LaunchAgents:

```bash
bash telegram_connector/scripts/install_launch_agent.sh
```

To inspect a copied runtime without loading or restarting services, pass
`--staging-root /absolute/path/to/staging`. The installer writes the runtime and
plists there and does not call `launchctl`; this is a fixture check, not a live
deployment. Configured relative digest/analysis prompt bundles are copied at
their configured paths. Absolute prompt paths remain external references;
relative paths must resolve inside the service root.

Reload only the already-installed bridge LaunchAgent:

```bash
bash telegram_connector/scripts/restart_launch_agent.sh
```

Use the installer after code changes, `telegram_shared` changes, runtime config changes, prompt-bundle changes, or scheduled digest config changes. Use restart only when installed code and config are already current.

The installer preserves existing launchd logs and the latest digest audit. Clear them only when explicitly required:

```bash
bash telegram_connector/scripts/clear_launch_logs.sh
```

Each digest run initializes and migrates its SQLite schema before reading or writing usage data; no separate manual migration command is required after an installed upgrade.

Launchd logs:

- `telegram_connector/data/launchd/bridge.startup.log`
- `telegram_connector/data/launchd/bridge.stdout.log`
- `telegram_connector/data/launchd/bridge.stderr.log`
- `telegram_connector/data/launchd/digest.startup.log`
- `telegram_connector/data/launchd/digest.stdout.log`
- `telegram_connector/data/launchd/digest.stderr.log`
- `telegram_connector/data/launchd/digest.last_attempt.json`

## Commands

Bot command quick reference:

- `/help`: command syntax, options and examples
- `/infoext [NIE] [submission-date] [birth-year]`: check now and notify the monitor's configured recipient.
  Date: `DD/MM/YYYY`; birth year: `YYYY`. Omitted fields use config; supplied fields
  select a one-time query without changing the monitored state. Requires the installed
  InfoExt monitor; `infoext` without `/` also works.
  See [setup and query behavior](../infoext-monitor/README.md#check-from-telegram).
- `/agent-stats`: recent local Digest AI usage and prompt-cache summary; see [shared metric definitions](../telegram_shared/README.md#ai-usage-metrics)
- `/top-models [limit] [debug]`: configured external free-model ranking
- `/backfill [channel] [limit] [since=...] [until=...] [media] [bot|user|auto]`: historical load into SQLite
- `/tail [channel] [limit] [since=...] [until=...] [media|ocr|read] [bot|user|auto]`: latest window sync
- `/update [channel] [limit] [since=...] [until=...] [media|ocr|read] [bot|user|auto]`: import only newer messages
- `/ocrhistory [channel] [limit] [since=...] [until=...] [bot|user|auto]`: tail + media download + OCR
- `/digest [channel] [since=...] [until=...] [today|yesterday|week|month|-Nd] [bot|user|auto]`: sync + AI digest + Telegram delivery
- `/topics <channels> [since=...] [until=...] [options]`: key events, changes and topics
- `/search <channels> [since=...] [until=...] "query" [options]`: supported answers to a question
- `/qa <channels> [since=...] [until=...] [options]`: practical questions and supported solutions
- `/status <job_id>`, `/cancel <job_id>`, `/resume <job_id>`: analytic job controls; see [recovery](#analytics-commands)
- `/exportcsv [channel] [limit|since=... until=...] [bot|user|auto]`: export saved history to CSV
- `/ocr [limit] [channel] [since=...] [until=...]`: OCR already-downloaded pending images

Common CLI examples:

```bash
python3 telegram_connector/telegram_bridge.py get-me
python3 telegram_connector/telegram_history_client.py doctor
python3 telegram_connector/telegram_history_client.py init-db
python3 telegram_connector/telegram_history_client.py sync --mode tail --channel @vcnews --limit 100
python3 telegram_connector/telegram_digest.py run --channel @vcnews,@refugecard --auth-mode user
python3 telegram_connector/telegram_history_client.py export-csv --channel @vcnews --since 2026-03-15 --until 2026-03-16
python3 telegram_connector/telegram_history_client.py ocr-pending --limit 100
```

Command notes:

- history/digest commands can use `[channels].default_list`; analytics require explicit channels, including comma-separated lists
- private groups and channels without a public username can use Bot API-style ids such as `-1001449711572`
- `since` and `until` work across sync, digest, analytics, OCR, and export commands; date-only bounds mean start/end of day UTC
- `week`, `month`, `today`, `yesterday`, and `-Nd` aliases are supported where date windows are accepted
- `media` downloads media; `ocr` implies image media download and OCR
- `read` is optional and only has effect in `user` auth mode
- if auth is omitted in a bot command, `user` is used by default

## Analytics Commands

All three commands use the same bounded history, extraction, grouping and synthesis
workflow over SQLite. Missing requested history is downloaded in pages; messages and
intermediate records are never submitted as one unbounded model request. All AI-stage
instructions come from `analysis_prompts.file`; the model and reasoning settings reuse
`processing.model` and `digest_ai.*`. Ordinary channel history is in scope; comments in
linked discussion groups are excluded.

```text
/topics @channel1,@channel2 since=week --detail short
/search @channel1 since=month "Which banks were discussed?" --mode fast
/qa @channel1 since=2026-09-01 until=2026-09-10 --detail full
```

Channels are mandatory; `channels.default_list` is never substituted. Periods use the
existing UTC `since`/`until` rules and aliases; omitted bounds inherit `digest.since`
and `digest.until`. Admission freezes the current-time cutoff.

- `--detail short|normal|full` selects generation instructions, API output budget and
  presentation size; the default is `normal`. Short and normal return Telegram text.
  Full writes Markdown under the configured repo-relative scratch directory and sends
  only its relative path as the final Telegram payload.
- `--limit N` caps result items and `--sources N` caps sources per item, within the
  selected profile and system ceilings. Defaults and validated ranges belong to
  [runtime.example.toml](config/runtime.example.toml), not separate per-command settings.
- `/search --mode fast|full` is independent of detail. Fast is the default: it preserves
  the question, expands terms, searches FTS and restores bounded context. Full scans
  the available window without lexical preselection, subject to the same budgets.
  Candidate coverage and budget/error-limited processing are labelled explicitly;
  zero fast matches do not establish absence from the whole archive.

Topics describes events and their development. QA extracts practical questions and
solutions, including standalone answers and personal experience without reply links;
an unanswered question is insufficient evidence. Results retain source references,
conditions and conflicting accounts rather than filling gaps with external knowledge.

The bridge queues requests durably and remains responsive. One separate progress
message per job is updated at the configured interval; `/status <job_id>` shows stage,
coverage and cumulative usage. Controls require the original owner and current ACL.
`/cancel` requests a bounded worker stop; `/resume` keeps checkpoints and spent budgets.
Failed jobs with retained checkpoints may resume after repair. Cancelled jobs remain
terminal, and an unknown send outcome requires inspection rather than automatic resend.

Changed source data invalidates dependent analysis and any unsent delivery plan. If a
part was confirmed or its send outcome is unknown, automatic recomputation stops and
preserves the old result and receipts. An explicit `/resume` can restore a missing full
report from retained data without repeating a confirmed Telegram send; user-edited files
are preserved. Cache eviction removes cross-job reuse eligibility while retaining result
and failed-job recovery dependencies until job retention expires.

The public worker CLI applies the saved bridge hard TTL on each launch, including
resume, with shared TERM/grace/KILL process-group cleanup. Copied-runtime upgrades use
the canonical installer. Jobs retain frozen prompt/schema/algorithm identities;
incompatible execution-policy changes require a fresh request.

## Digest Behavior

Digest runs are per channel: prep-sync, optional OCR, AI analysis, and Telegram delivery are isolated by channel where possible.

- output is delivered to `telegram.default_chat_id`
- The scheduled digest uses the shared runner's `--sleep-policy pause`: it resumes after host sleep, and `digest.run_total_timeout_seconds` limits elapsed awake time rather than including sleep. This is an intentional exception to the default sleep-inclusive TTL; waits and retries while awake still consume the budget.
- Telethon connection budgets live in `[telethon]` (see the example config). Sleep is excluded; transient failures retry with a fresh client after cleanup. Permanent errors or failed cleanup stop retries. Logs contain only failures and recovery; final failures reach the audit and Telegram.
- Digest reuses HTTP connections separately for AI requests and Telegram delivery
  during each run, closing the pools on exit. Existing request timeouts and retry
  policies still apply; processing and delivery remain sequential.
  Pool initialization failures are recorded as failed runs in the latest digest audit.
- channels below `digest.min_messages_for_ai` send a short loaded-without-analysis note
- channels that fit `digest_ai.*` budgets use one direct OpenAI request; larger windows fall back to chronological batches and a final summary
- history reads start at the selected period's upper boundary, so newer posts do not spend its `sync_limit`; exhausting the cap before proving the lower boundary produces an incomplete-coverage warning, including for empty local results
- transient OpenAI network/OS failures, `408`, retryable `429`, `500`/`502`/`503`/`504`, and edge/origin `520`–`525`/`530` use bounded retries; retryable rate limits honor `Retry-After`
- `digest.openai_timeout_seconds` can override the shared AI network timeout per attempt for digest only; when omitted or blank, it inherits `digest_ai.openai_timeout_seconds`. Analytics keeps the shared timeout. See the example config for units and bounds.
- failed OpenAI analysis for one channel is recorded with safe HTTP or network diagnostics, retry outcome, and a copyable channel selector for a manual rerun, then produces `status=partial` while the remaining channels continue
- transient delivery failures are recorded as per-channel errors and produce `status=partial`
- permanent delivery failures, such as invalid chat ids or revoked tokens, fail the run and write `status=failed`
- final error summary delivery is best-effort

## Storage

Analytics jobs, checkpoints, batches, evidence, and input references use the same
history database. Initialization adds their tables idempotently without requiring
FTS5 or changing stored messages. Keep the database when rolling back code; after
writes by an older runtime, use `index-history --rebuild` before returning to fast
search in the upgraded runtime.

Sync, digest, and message export use `open_telethon_session()` to serialize the
canonical session file. Lease waits reuse the connection timeout; uncertain
cleanup retains ownership until the worker exits.

Analytics history loading separates API pages from SQLite flushes. A pending page
survives restart; download allowance is reserved before the request and remains
charged if its outcome is unknown. User-mode analytics requires an already
authorized session and never starts an interactive phone/OTP login.
Closed UTC days reuse the local archive; today's edge is refreshed up to the
admitted cutoff. Retained successful empty ranges and unfinished cursors are
reused across jobs. An unknown empty requested window triggers bounded backfill;
surrounding archive dates or a previous reuse-only record do not prove it was fetched.
This pragmatic policy accepts smaller internal holes and does not routinely rescan
closed days for remote edits or deletions.

The bot's `/help` includes analytics syntax, options, examples and job controls;
unlike the history commands, `/topics`, `/search` and `/qa` require explicit channels.

Analytics selection persists whole-message IDs and SHA-256 digests in paged
select steps before extraction. The reader verifies current text and ordered
OCR/media metadata; changed or removed inputs interrupt the job. Resume repairs
existing manifest pages without adding late inputs or resetting AI budgets.
With `processing.ocr=true`, pending downloaded images in the explicit channel/window
scope are processed before selection. `ocr.pending_default_limit` caps OCR assets
across the job; `ocr.tesseract_timeout_seconds` bounds each recognition. Remaining
pending or failed OCR makes coverage partial. Resume preserves an existing manifest.

When FTS5 is available, initialization creates the optional `unicode61` index.
Message, OCR and deletion writers update it in the same transaction as the source.
Creating this table does not backfill old history or establish search readiness;
from the Connector directory, run `python3 telegram_history_client.py index-history`
to queue or resume its build through the existing bridge `listen` supervisor.
Fast analytics checks readiness before query expansion; `/status` explains index
errors and offers `--mode full`. Candidates prioritize original terms and include
bounded same-channel context; the result identifies this as a candidate selection.
Use `index-history --rebuild` after VACUUM, a legacy writer, or a readiness error
requiring rebuild. The command invalidates readiness immediately; the worker
clears and rebuilds the index in bounded pages under the shared TTL. Interrupted
or timed-out builds resume with `index-history`; ordinary job retention preserves
the service checkpoint. Indexing needs no LLM model or Telegram session.

Primary local runtime paths:

- `telegram_connector/data/telegram_history.sqlite3`
- `telegram_connector/data/media/`
- `telegram_connector/data/exports/`
- `telegram_connector/data/sessions/`
- `telegram_connector/data/launchd/`

## Dependencies

Required runtime dependencies:

- `Telethon`
- `tesseract`

Python setup:

```bash
python3 -m venv .venv-test-gap-detection
.venv-test-gap-detection/bin/python -m pip install -r telegram_connector/requirements.txt
```

## Tests

Fixture tests cover parsing/admission, history and OCR, FTS, bounded extraction and
multi-stage merge, source support, profile-specific generation, ACL, cancellation,
delivery receipts, cache retention and full-file recovery. Fault cases exercise
source edits after delivery preparation, cache eviction before report restoration,
pending OCR before selection, and hard-TTL cleanup of a hung evaluation process.
Worker diagnostics retain only bounded exception categories and application locations.
Fixtures prove these mechanics; model quality and real deployment are separate checks.

Preferred local regression run:

```bash
.venv-test-gap-detection/bin/python -m pytest telegram_connector/tests -q
```

When changing `telegram_shared`, run the shared suite as well:

```bash
.venv-test-gap-detection/bin/python -m pytest --import-mode=importlib common/tests telegram_shared/tests telegram_connector/tests telegram_agent_bot/tests -q
```

Fallback runner:

```bash
python3 telegram_connector/run_tests.py
```

### Resource and model checks

The three large `test_analysis_load.py` cases are opt-in (`RUN_TELEGRAM_ANALYSIS_LOAD=1`).
They measure bounded manifests, indexes, storage, RSS and explicit partial stops at
10k/100k/200k messages with a synthetic provider. They do not measure real model latency
or full semantic processing of 200k messages. Keep workload/interpreter/source hashes
with the measurements and freeze the RSS acceptance budget before optimization.

The local quality evaluator uses an annotated synthetic corpus, isolated SQLite and
local delivery capture; it never publishes to Telegram. From the repo root:

```bash
python3 telegram_connector/scripts/evaluate_analysis.py validate
python3 telegram_connector/scripts/evaluate_analysis.py --output scratch/analytics-eval-new prepare
```

Review the generated `scope.json` and obtain approval for its model, cases and total
budgets before executing `run`. Only then use the approved scope hash:

```bash
python3 telegram_connector/scripts/evaluate_analysis.py --output scratch/analytics-eval-new run --case CASE_ID --approved-scope APPROVED_SHA256
python3 telegram_connector/scripts/evaluate_analysis.py --output scratch/analytics-eval-new review-template --case CASE_ID
python3 telegram_connector/scripts/evaluate_analysis.py --output scratch/analytics-eval-new score --case CASE_ID
```

Complete the generated claim/source review before scoring; a mock response or successful
API call does not establish semantic quality. `--case all` stops at the first incomplete
case. The whole invocation uses the configured bridge worker TTL and shared outer
limiter; `evaluation.last_attempt.json` records a timeout. The private `--worker-inner`
route is only for that supervisor. An uncaptured interrupted case requires inspection,
not a blind replay. Captured results can be reviewed/scored without new model calls.
