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

Use the example config for the complete schema, defaults and validated ranges.
Common setup areas are:

- `telethon`, `auth` and `channels` for account access and default channels
- `bridge.allowed_chat_ids`, `bridge.allowed_user_ids` and `bridge.allowed_usernames` for command access control
- `digest`, `digest_ai` and `digest_limits` for the scheduled digest
- `analysis` for separate analytics budgets, AI settings and output profiles
- `digest_prompts` and `analysis_prompts` for local prompt bundles
- `sync` and `ocr` for history ingestion and image recognition

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

The installer prepares a service-local venv through the shared
[Python runtime owner](../common/README.md#python-runtime-selection).

Reload only the already-installed bridge LaunchAgent:

```bash
bash telegram_connector/scripts/restart_launch_agent.sh
```

Use the installer after code changes, `telegram_shared` changes, runtime config changes, prompt-bundle changes, or scheduled digest config changes. Use restart only when installed code and config are already current.

The installer preserves existing logs and the latest digest audit. Installed
upgrades initialize the database automatically; no manual migration is required.

Launchd logs:

- `telegram_connector/data/launchd/bridge.startup.log`
- `telegram_connector/data/launchd/bridge.stdout.log`
- `telegram_connector/data/launchd/bridge.stderr.log`
- `telegram_connector/data/launchd/digest.startup.log`
- `telegram_connector/data/launchd/digest.stdout.log`
- `telegram_connector/data/launchd/digest.stderr.log`
- `telegram_connector/data/launchd/digest.last_attempt.json`

Ошибки запуска команд bridge возвращает в Telegram кратким сообщением.
Полная диагностика, включая traceback, сохраняется в `data/launchd/bridge.stderr.log`
с удалением секретов и приватных путей. Этот журнал принимает ошибки раннего
запуска digest, даже если digest ещё не смог открыть собственные журналы.

## Commands

Bot command quick reference:

- `/help`: command syntax, options and examples
- `/infoext [NIE] [submission-date] [birth-year]`: check now and notify the monitor's configured recipient.
  Date: `DD/MM/YYYY`; birth year: `YYYY`. Omitted fields use config; supplied fields
  select a one-time query without changing the monitored state. Requires the installed
  InfoExt monitor; `infoext` without `/` also works.
  See [setup and query behavior](../infoext-monitor/README.md#check-from-telegram).
- `/agent-stats`: recent local Digest AI usage and prompt-cache summary; see [shared metric definitions](../telegram_shared/README.md#ai-usage-metrics)
- `/top-models [limit]`: configured external free-model ranking
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

Analytics run over explicitly selected channel history with bounded AI budgets.
Missing requested history is downloaded before analysis. Comments in linked
discussion groups are excluded. Analytics use their own `analysis.ai` settings;
digest AI settings do not change new analytic jobs.

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
Conditions and exceptions are included when composing each item's text, without
separate blocks or automatically appended detail lists.

The bridge queues requests durably and remains responsive. One separate progress
message per job is updated at the configured interval; `/status <job_id>` shows stage,
coverage and cumulative usage. Controls require the original owner and current ACL.
`/cancel` requests a bounded worker stop; `/resume` keeps checkpoints and spent budgets
for supported frozen policies. Retired policies remain readable as history and cannot
start a new analysis automatically. Checkpoint expiry and cache cleanup preserve saved
results, prepared texts, usage receipts, delivery records and phase telemetry.
Failed jobs with retained checkpoints may resume after repair. Cancelled jobs remain
terminal, and an unknown send outcome requires inspection rather than automatic resend.

If source data changes, the job stops and requires inspection. Resume preserves
the original scope and spent budgets. Incompatible saved jobs require a fresh
request. A supported job's missing full report can be restored from retained data without
repeating a confirmed Telegram send; user-edited files are preserved.

Topics omit digest-only items. An additional digest does not block an item when
standalone posts support all its published content. QA and search can use unique information from
digests and prefer supporting standalone posts when available. QA omits answers
without supporting sources. Partial results keep usable content and explain
coverage, source or budget limitations before the main content.

Topics keep an event's characteristics together and separate independent
announcements. Headlines name the event; the following text adds details.
A shared digest alone does not create a relation candidate. Shared entities,
topics, ordinary posts, explicit replies and exact duplicates remain valid hints.
Resumed jobs retain their frozen relation policy.

For a read-only timing and usage report, without new API requests:

```bash
python3 telegram_connector/scripts/report_analysis.py --db telegram_connector/data/telegram_history.sqlite3 --job-id JOB_ID
```

The report shows phase timings, retries, usage and input accounting without
changing the database. Missing historical measurements remain unknown. Input
accounting does not establish that every relevant fact was extracted.
Selection compares model choices with the saved synthesis input, independently
of later filtering. Unresolved model references are listed separately and leave
the count of omitted records unknown.

Optional `--pricing` accepts a reviewed JSON snapshot of USD rates per million
tokens (`input`, `cached_input`, `cache_write_30m`, `cache_write_12h`, `output`),
plus `model` and `service_tier`. Incomplete usage leaves cost unknown or produces
a conditional range; this is not an invoice. See the
[shared usage metric definitions](../telegram_shared/README.md#ai-usage-metrics).

## Digest Behavior

The installed LaunchAgent schedules digest runs; `/digest` and the CLI can also
start a one-shot run. Each channel is synchronized, optionally processed with OCR,
analyzed and delivered to `telegram.default_chat_id`.

- Host sleep pauses the scheduled run's lifetime budget; time spent waiting and
  retrying while awake still counts.
- Channels with too few messages send a loaded-without-analysis note. Large
  windows use bounded batches and a final summary.
- Exhausted history coverage produces a warning, including for empty local results.
- Transient AI and Telegram failures use bounded retries. A failed channel makes
  the run partial while other channels continue; invalid delivery credentials or
  destinations fail the run. Inspect the latest digest audit before a manual rerun.

## Storage

History, analytics state and AI usage share the configured SQLite database.
Preserve it across code upgrades and rollbacks. Analytics in user auth mode require
an already authorized session; background jobs do not prompt for phone/OTP login.

Closed UTC days reuse local history and are not routinely refreshed for remote
edits or deletions. The current day's edge is refreshed up to the job cutoff.
With OCR enabled, pending downloaded images in the selected channel/window are
processed before analysis. Unprocessed or failed images make coverage partial.

Fast search needs an FTS index. With the bridge listener running, queue or resume
its build from the repository root:

```bash
python3 telegram_connector/telegram_history_client.py index-history
```

Use `index-history --rebuild` after VACUUM, writes from an older runtime, or a
readiness error requiring rebuild. Indexing needs no AI model or Telegram session.
If FTS is unavailable or not ready, `/status` explains the problem; use search
`--mode full` to scan the available window without lexical preselection.

Primary local runtime paths:

- `telegram_connector/data/telegram_history.sqlite3`
- `telegram_connector/data/media/`
- `telegram_connector/data/exports/`
- `telegram_connector/data/sessions/`
- `telegram_connector/data/launchd/`

## Dependencies

Use Python 3 with the dependencies in [requirements.txt](./requirements.txt).
Image OCR additionally needs Tesseract. For Python environment setup, use the
[shared setup instructions](../common/README.md#python-runtime-selection).
Developer checks belong to [AGENTS.md](./AGENTS.md#testing).

## Local Quality Evaluation

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

Complete the generated claim/source review before scoring; a mock response or
successful API call does not establish semantic quality. Inspect an interrupted
case before rerunning it. Captured results can be reviewed and scored without
new model calls.

### Prompt semantic regressions

Use [analysis_prompt_semantics_gold.json](tests/fixtures/analysis_prompt_semantics_gold.json)
before accepting prompt changes affecting attribution, dates, planned/completed
actions or global ranking. This optional corpus supplements the default one;
the application and ordinary pytest runs do not execute inference from it.

Follow the workflow above, adding `--gold` to **every** command:

```bash
EVAL_GOLD=telegram_connector/tests/fixtures/analysis_prompt_semantics_gold.json
python3 telegram_connector/scripts/evaluate_analysis.py --gold "$EVAL_GOLD" validate
python3 telegram_connector/scripts/evaluate_analysis.py --gold "$EVAL_GOLD" --output scratch/analytics-prompt-eval-candidate prepare
```

`validate` and `prepare` do not call a model. Obtain scope/budget approval before
`run`; keep the same `--gold` and `--output` for review and scoring. Fill the review
using `review_rubric`, accepting supported paraphrases. Require
`structural_pass=true`, `critical_recall=1` and `unsupported_rate=0`.

Compare prompt versions using the same corpus/model/settings and separate output
directories and approved scopes. Semantic quality remains unverified until fresh
runs and reviews finish; pytest or re-scoring old outputs cannot establish it.
