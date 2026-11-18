# Telegram Connector

Local Telegram history, OCR, CSV export, scheduled digests and bounded channel
analytics, controlled through a Bot API bridge or CLI.

## Sources Of Truth

- [RULEBOOK.md](../RULEBOOK.md): shared engineering and runtime rules
- [AGENTS.md](./AGENTS.md): architecture, maintenance and developer checks
- [runtime.example.toml](config/runtime.example.toml): complete config schema, defaults and ranges
- [digest_prompts.toml](config/digest_prompts.toml) and [analysis_prompts.toml](config/analysis_prompts.toml): task instructions
- [telegram_shared](../telegram_shared/README.md): shared helpers, retry behavior and usage metrics
- `/help` and entrypoint `--help`: exact command syntax

## Runtime Config

Copy `config/runtime.example.toml` to `config/runtime.local.toml`. Keep local
secrets and machine-specific paths out of git. Common setup groups are:

- `telethon`, `auth`, `channels`: account access and default channels
- `bridge`: allowed chats and users
- `digest`, `digest_ai`, `digest_limits`: scheduled digest settings
- `analysis`: independent analytics AI settings, budgets and output profiles
- `sync`, `ocr`: ingestion and image recognition

`telegram.default_chat_id` may stay empty until a message to the bot reveals the
chat ID through `listen`.

### Secrets

Prefer Keychain references in local config:

```toml
[secrets]
bot_token = "keychain://telegram-connector/bot_token"
api_hash = "keychain://telegram-connector/api_hash"
```

## Operation

From the repository root:

```bash
# Install or refresh bridge and scheduled digest
bash telegram_connector/scripts/install_launch_agent.sh
# Reload the already-installed bridge
bash telegram_connector/scripts/restart_launch_agent.sh
# Run the bridge manually
python3 telegram_connector/telegram_bridge.py listen --run-commands
```

Reinstall after code, shared helper, config or prompt changes. Restart only when
the installed copy is already current. The installer preserves runtime data and
logs and prepares a service-local venv through the
[shared Python runtime owner](../common/README.md#python-runtime-selection).

Logs live under `data/launchd/`: `bridge.*.log`, `digest.*.log` and
`digest.last_attempt.json`. Command failures return a short Telegram message;
`bridge.stderr.log` retains redacted tracebacks, including early digest failures.
Inspect the latest audit before manually rerunning a failed digest.

Read-only timing, API-attempt, delivery and usage reports need no API calls:

```bash
python3 telegram_connector/scripts/report_digest.py --db telegram_connector/data/telegram_history.sqlite3
python3 telegram_connector/scripts/report_analysis.py --db telegram_connector/data/telegram_history.sqlite3 --job-id JOB_ID
```

For digest, `--run-id ID` selects a retained run. Missing measurements and costs
without pricing remain unknown; see report `--help` and the
[shared usage definitions](../telegram_shared/README.md#ai-usage-metrics).

## Commands

Use `/help` for options and examples.

| Bot command | Purpose |
| --- | --- |
| `/backfill`, `/tail`, `/update` | Historical load, latest-window sync, newer-message import |
| `/ocrhistory`, `/ocr` | Sync with OCR, or OCR pending downloaded images |
| `/exportcsv` | Export stored history |
| `/digest` | Sync, AI digest and per-channel Telegram delivery |
| `/topics`, `/search`, `/qa` | Channel analytics; see below |
| `/status`, `/cancel`, `/resume` | Analytics job controls |
| `/agent-stats` | Local digest usage and prompt-cache summary |
| `/top-models` | Configured external free-model ranking |
| `/infoext` | [Check the installed InfoExt monitor](../infoext-monitor/README.md#check-from-telegram) |

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

History and digest can use `channels.default_list`; analytics require explicit
channels. Private channels/groups may use Bot API IDs such as `-1001449711572`.
Date-only `since`/`until` bounds mean start/end of day UTC; supported aliases
include `today`, `yesterday`, `week`, `month` and `-Nd`.

Bot commands default to `user` auth. `media` downloads media; `ocr` also runs image
recognition. `read` only affects user-authenticated sync.

## Analytics Commands

Analytics use their own AI settings and budgets. Requested missing history is
loaded before analysis; linked discussion comments are excluded.

```text
/topics @channel1,@channel2 since=week --detail short
/search @channel1 since=month "Which banks were discussed?" --mode fast
/qa @channel1 since=2026-09-01 until=2026-09-10 --detail full
```

Channels are mandatory. Omitted dates inherit `digest.since`/`digest.until`;
admission freezes the current-time cutoff.

- `--detail short|normal|full`: short/normal return Telegram text; full saves
  Markdown and returns its relative path. Default: normal.
- `--limit N`, `--sources N`: cap result items and sources within profile budgets.
- `/search --mode fast|full`: fast uses FTS candidates and bounded context; full
  scans the available window within budgets. Zero fast matches do not prove absence
  from the archive.

Results include source references and disclose partial coverage or budget limits.
`/status JOB_ID` shows stage, coverage and cumulative usage; progress updates use
one separate message per job. Controls require the original owner and current ACL.

`/cancel` requests a bounded worker stop; cancelled jobs are terminal.
`/resume` uses retained checkpoints, original scope and spent budgets; incompatible
jobs require a fresh request. Inspect source changes or unknown send outcomes
before retrying. Restoring a missing full report preserves user edits and does not
repeat confirmed Telegram delivery.

## Digest Behavior

The LaunchAgent schedules digest runs; `/digest` and the CLI start one-shot runs.
Each channel is synchronized, optionally processed with OCR, analyzed and delivered
to `telegram.default_chat_id`.

- Scheduled runs and `/digest` share the outer supervisor and audit metrics.
  Host sleep pauses their lifetime budget; awake retry waits count.
- After host sleep, ordinary history sessions send one diagnostic Ping to prompt
  SDK transport recovery. A Ping timeout logs the problem without forcing reconnect.
- Channels below the AI threshold send a loaded-without-analysis note; large
  windows use bounded batches and a final summary.
- Coverage limits produce warnings, including for empty local results.
- Transient failures use bounded retries. A failed channel makes the run partial;
  invalid delivery credentials or destinations fail the run.

## Storage

Preserve the configured SQLite history across upgrades and rollbacks. Runtime
files live under `data/`: the database, `media/`, `exports/`, `sessions/` and
`launchd/`. Background user-authenticated analytics need an already authorized
session; they do not prompt for phone/OTP login.

Closed UTC days reuse local history without routine remote edit/deletion refresh.
The current-day edge refreshes to the job cutoff. Enabled OCR runs before analysis;
unprocessed or failed images make coverage partial.

Fast search requires FTS. With the bridge running, queue or resume indexing:

```bash
python3 telegram_connector/telegram_history_client.py index-history
```

Use `index-history --rebuild` after VACUUM, older-runtime writes or a readiness
error requiring rebuild. Indexing needs no AI or Telegram session. If FTS is
unavailable, inspect `/status` or use search `--mode full`.

## Dependencies

Use Python 3 with [requirements.txt](requirements.txt); image OCR needs Tesseract.
See shared runtime setup above and [developer checks](AGENTS.md#testing).

## Local Quality Evaluation

The evaluator uses annotated synthetic data, isolated SQLite and local delivery
capture; it never publishes to Telegram. Start with:

```bash
python3 telegram_connector/scripts/evaluate_analysis.py validate
python3 telegram_connector/scripts/evaluate_analysis.py --output scratch/analytics-eval-new prepare
```

Review `scope.json` and obtain approval for its model, cases and budgets before
`run` with `--approved-scope`. Generate `review-template` and fill the claim/source
review before `score`; see evaluator `--help` for syntax. Inspect interrupted
cases before rerunning. Saved captures need no new model calls for review/scoring.

### Prompt semantic regressions

For attribution, dates, planned/completed actions and ranking, use
[analysis_prompt_semantics_gold.json](tests/fixtures/analysis_prompt_semantics_gold.json).
Pass the same `--gold` and `--output` through every evaluation step. `validate`
and `prepare` make no model calls. Require `structural_pass=true`,
`critical_recall=1` and `unsupported_rate=0` after claim/source review.

Compare prompt versions with the same corpus/model/settings and separate output
directories and approved scopes. Fresh model runs and reviews establish semantic
quality; pytest or re-scoring old captures do not.
