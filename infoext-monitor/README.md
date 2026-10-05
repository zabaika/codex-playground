# InfoExt monitor

Local macOS monitor for an InfoExt expediente status. The launchd calendar is
defined only by the `[launchd]` section of the local TOML file. CAPTCHA is
recognized locally and notifications use the existing sibling
`../telegram_connector` project.

## Architecture

```text
launchd
  -> installed runtime in Application Support
     -> stable launcher wrapper
        -> launchd/run_monitor.py (whole-run limit and last-attempt audit)
           -> common/ttl_runner.py (process group and forced termination)
              -> main.py
                 -> infoext.py (Playwright Chromium and the official site)
                 -> captcha_solver.py (Pillow and macOS Apple Vision)
                 -> state.py (atomic state.json and history.jsonl)
                 -> notifier.py
                    -> configured telegram_connector/telegram_bridge.py
                       -> existing Keychain/config/token/chat id
                       -> Telegram
```

`notifier.py` imports the existing `telegram_connector` API. It does not
create a second bot token, copy connector source code, or load credentials
into this project's configuration. The recipient comes from the connector's
`[telegram].default_chat_id`; the token and `sendMessage` retry policy remain
owned by the connector.

## Observed InfoExt form

The form and a successful result were verified with Playwright Chromium. The flow
uses these selectors:

| Element | Selector | Observation |
|---|---|---|
| Form entry | `get_by_role("link", name="ENTRAR FORMULARIO")` | Needed when the direct URL returns the entry screen. |
| NIE | `#nie` | `name="nie"` |
| Submission date | `#fechaPresentacion` | `name="fechaPresentacion"` |
| Birth year, when displayed | `#anio` | `name="anio"`; the server rejects an empty value. |
| CAPTCHA image | `img[alt="captcha"]` | PNG is directly available as a `data:image/png;base64,...` URL. |
| CAPTCHA refresh | `get_by_role("link", name="Recargar Captcha")` | Calls `javascript: recargarCaptcha()`. |
| CAPTCHA input | `#captcha` | `name="txtCaptcha"`, placeholder `Introduce el texto aquí`. |
| Submit | `#btnConsulta` | `onclick="envioForm()"`. |

Refreshing CAPTCHA sends a POST to `consulta.html`, so the client waits for a
new document rather than an AJAX response. The `domcontentloaded` event wait
is registered before clicking refresh, then the client checks for URL rejection
and waits for the new document's CAPTCHA image. The result parser extracts labelled
pairs from tables, `dl`, and label/layout elements. `Estado` is required.
InfoExt's original status text is stored; comparison only normalizes whitespace
and case. Before every `CONSULTAR`, the client verifies that both the NIE and
submission-date fields are present and non-empty. A visible birth-year field
requires `infoext.ano_nacimiento`; an empty setting stops before submission.
Identity-field errors stop the check and are not retried as CAPTCHA failures.

A CAPTCHA rejection is identified by the explicit field error
`Los caracteres escritos no son correctos.` Returning the form alone does not
prove CAPTCHA rejection. Other validation errors are reported separately.

## Requirements

- macOS and Python 3.11 or later;
- Xcode Command Line Tools for the local Apple Vision helper
  (`xcode-select --install` if absent);
- an existing configured `../telegram_connector` project;
- access to the official InfoExt site.

## Installation

```bash
cd /path/to/infoext-monitor
cp config/runtime.example.toml config/runtime.local.toml
chmod 600 config/runtime.local.toml
# Fill infoext.nie, infoext.fecha_presentacion and infoext.ano_nacimiento when required by the portal.
bash install.sh
```

`install.sh` creates `.venv`, installs Python dependencies and Chromium,
compiles the Apple Vision helper, creates local runtime directories, renders a plist with
absolute runtime paths, and registers the LaunchAgent. It does not change
`telegram_connector`.

As in `telegram_connector`, scheduled executable code is deployed to
`~/Library/Application Support/infoext_monitor_service`. The installer syncs
application code, launchers, the compiled Vision helper and `common/`, and
creates a separate service venv. Both the launcher and `WorkingDirectory` use
this installed runtime rather than `Documents`. `INFOEXT_PROJECT_ROOT` points
to the source project, whose local config, state, history, debug data and logs
remain canonical. Local config and Telegram credentials are not copied into
the service bundle. Manual commands continue to use the source project's venv.

The repository is the editable source of truth. Do not edit installed Python
files, helpers, shared process config or the service venv as a repair path;
change the source and redeploy with the canonical installer. Changes to shared
`common/` code or config also require redeployment, because scheduled jobs use
the installed copy.

Before installation, the script verifies that `infoext.nie` is present and
that `infoext.fecha_presentacion` is a real calendar date in `DD/MM/YYYY`
format. Placeholder text is rejected.

All operator-controlled values live in `config/runtime.local.toml`: portal
minimum-interval protection, CAPTCHA attempts and delays, whole-run timeout,
health threshold, unchanged-status reports, OCR settings, and the launchd
calendar. Units, constraints, and the schedule formula are documented next to the matching keys in
`config/runtime.example.toml`. Shared termination grace, polling, signals, and
timeout exit code come from `../common/config/process.toml`, as used by
`telegram_connector`.

## Manual commands

```bash
.venv/bin/python main.py --check-now
.venv/bin/python main.py --check-now --notify
.venv/bin/python main.py --check-now --debug
.venv/bin/python main.py --test-telegram
```

`infoext.notify_on_unchanged_status` controls whether a successful unchanged
status creates a current-status report in the reliable Telegram queue. A status
change creates one transition notification. `--notify` forces a current-status
report when unchanged if the TOML setting disables it. `--debug` runs visible
Chromium, fills the form, captures its first CAPTCHA and evaluates it locally.
It does not invoke `CONSULTAR`, `Recargar Captcha`, obtain an expediente status,
update the known status or failure count, or call Telegram. It does persist the
portal-visit reservation used by the minimum-interval guard. It stores `filled-form.png`, `captcha-1.png`,
and `ocr-report.json` under `debug/<timestamp>/`. Every ordinary check,
including one launched by launchd, stores its CAPTCHA images under
`data/captcha/`, with timestamped filenames. Set `infoext.debug` to `"enable"`
to also retain the returned HTML, visible text, and OCR metadata after every
submitted CAPTCHA in `debug/captcha-responses/`; this is separate from the
non-submitting `--debug` command. Keep that setting disabled outside diagnosis,
because the server response can contain identity fields. In this mode, each
CAPTCHA image also has an adjacent JSON report retaining raw Vision candidates,
including candidates rejected by the length or alphabet filters. The report
measures OCR consensus and exact five-character coverage. Agreement and
confidence are not accuracy measurements: compare the saved image against a
manually verified transcription, or inspect the portal's explicit CAPTCHA response.

`infoext.portal_min_check_interval_seconds` applies to every run that opens the
InfoExt portal, including `--check-now` and `--debug`. The monitor writes the
reservation before opening Chromium, so a crash or concurrent invocation cannot
cause an earlier repeat visit. A run deferred by this guard exits successfully,
does not increase the failure count, and does not create a health alert.

`--test-telegram` sends this exact message through `telegram_connector`:

```text
InfoExt monitor: Telegram notifications configured successfully.
```

After confirmed delivery it prints `Telegram notification: OK`.

## CAPTCHA

OCR uses local Pillow and Apple Vision. `install.sh` compiles `vision_ocr.swift` into a
local helper and sends it the CAPTCHA through standard input. InfoExt CAPTCHA
codes contain five lowercase Latin letters or digits; OCR output is normalized
to lowercase, then visual Cyrillic lookalikes (`а е о р с х у`) are replaced
with their Latin equivalents (`a e o p c x y`) before whitelist filtering.
Other Cyrillic letters are not transliterated. Length and agreement checks
apply after normalization; raw Vision observations remain unchanged for
diagnostics. Preprocessing and optional
language correction are controlled by the OCR config; random codes normally
use language correction disabled. By default, every configured Vision
preprocessing variant must return the same five-character code. Confidence is
retained for diagnostics but does not decide submission. When
`ocr.vision_use_confidence` is enabled, the configured confidence thresholds
and fallback agreement apply instead. Preprocessing composites transparent
images onto white before grayscale, scaling, contrast, and optional median
filtering. The solver interface remains replaceable without changing the
browser client.

`infoext.captcha_max_attempts` is one shared budget for images evaluated during
a check, including OCR-withheld candidates and submitted codes rejected by the
portal. An OCR-withheld candidate triggers `Recargar Captcha` without
`CONSULTAR`. After an explicit CAPTCHA rejection, the client uses the new image
already returned by the portal instead of refreshing it again. The configured
retry delay precedes the next OCR pass in both cases; the final attempt does not
generate an unused CAPTCHA. Identity validation errors and a rejected URL stop
the run immediately. In particular, `The requested URL was rejected. Please
consult with your administrator.` is not retried as a CAPTCHA error.

## Reliability and local data

- `data/state.json`: current successful status, consecutive-failure count, and
  `pending_notifications`;
- `data/history.jsonl`: append-only record of every successful check;
- `logs/infoext.log`: diagnostics without a full NIE, token, or other secret;
- `data/launchd/com.infoext.monitor.last_attempt.json`: latest scheduled-run
  audit record.

For a status change, the updated state and notification event are committed
before Telegram is called. For an unchanged status, the current-status report
is first committed to the same queue. Undelivered events remain queued and are
retried at the start of a later check, before opening the portal; an event is
removed only after confirmed delivery. If the process crashes after Telegram
accepts a message but before that removal is persisted, a retry can deliver a
duplicate. The connector interface does not provide an atomic send-and-state
transaction, so delivery is at least once rather than exactly once.

When the configured failure threshold is reached, one health alert is created
and delivery is attempted in that same failing run. If Telegram is unavailable,
the alert remains pending. The first subsequent successful run creates a
single recovery notification containing the current status or status transition,
check time, and resolution date when available. It replaces the routine status
notification for that check, even when unchanged-status notifications are disabled.
An InfoExt failure never replaces a known status, and a
Telegram failure does not increment the InfoExt failure count.

`data/monitor.lock` prevents concurrent checks. Manual `--check-now`, its debug
variant, and `--test-telegram` all run under `common/ttl_runner.py`, using
`infoext.run_timeout_seconds` as the whole-worker hard TTL. The launchd runner
owns the same timeout for scheduled checks without nesting another supervisor.
Termination grace, polling and signals come from the shared process config.
Expiry terminates the whole process group, releases its locks and is recorded
in `logs/infoext.log` for manual commands or the launchd audit for scheduled
commands. `caffeinate -i` keeps the Mac awake while a worker runs; it does not
wake the Mac. Browser and network errors are reported with sanitized messages
and count as failed checks; the non-submitting debug mode only records diagnostics.

## LaunchAgent

Check the installed agent:

```bash
launchctl print "gui/$(id -u)/com.infoext.monitor"
```

After code or configuration changes, run:

```bash
bash restart.sh
```

It delegates to the canonical `install.sh`, which refreshes the service runtime,
performs `bootout → bootstrap`, renders the plist again, and verifies registration. It does not
create an unscheduled check; the next run follows the calendar from `[launchd]`.

`first_run_time`, `interval_hours`, `last_run_time`, and `weekdays` form the
calendar. Starts begin at `first_run_time`, repeat at `interval_hours` while not
later than `last_run_time`, and run only on `weekdays`. The final boundary is a
calendar slot only when an interval increment reaches it. `weekdays` uses launchd numbering:
`1` is Monday, `6` is Saturday, and `0` or `7` is Sunday. `RunAtLoad` is
intentionally absent, so login or reboot does not create an unscheduled check.

A missed slot during Mac sleep is not an InfoExt failure. If launchd reaches
the wrapper outside the configured time window or allowed weekday, it records
`skipped` in `data/launchd/com.infoext.monitor.last_attempt.json` and does not
start a check. The audit records start time, phase, terminal status
(`succeeded`, `failed`, or `timed_out`), skip reason, exit code, and duration.

The LaunchAgent uses an explicit wrapper, `.venv` interpreter, working
directory, and `PATH`. `telegram_connector.project_root` resolves the
connector without relying on an interactive shell environment.

For an immediate trial through the actual installed agent:

```bash
launchctl kickstart "gui/$(id -u)/com.infoext.monitor"
```

The trial still obeys the configured weekday, time window, and portal spacing.
Inspect the launchd audit and application log for its outcome; successful
registration alone does not prove that the worker or Telegram ran.

```bash
cat data/launchd/com.infoext.monitor.last_attempt.json
tail -n 40 logs/infoext.log
tail -n 20 logs/launchd.stdout.log
tail -n 20 logs/launchd.stderr.log
```

For a completed real check, correlate the audit's timestamps and exit code with
`CAPTCHA accepted`, a status record and `Telegram notification delivered` in
the application log. Confirm that `data/state.json` has a fresh
`last_successful_check`. An idle agent after a one-shot run is normal.
`succeeded` alone means the worker exited successfully: the portal-spacing
guard can also skip a visit with a successful exit. `skipped` reports a weekday
or time-window guard, and `failed` or `timed_out` requires diagnosis.

If Telegram failed, the InfoExt result may still be saved successfully; inspect
the pending queue and delivery warning rather than treating a successful
worker exit as proof of message delivery. The audit describes the latest
scheduled attempt; older log lines can belong to previous deployments.

## Uninstall

```bash
bash uninstall.sh
```

The script removes only the LaunchAgent. It preserves the service runtime,
local config, state, history, logs, debug data, and `telegram_connector`.
To stop scheduled checks while keeping these artifacts, use `bash uninstall.sh`;
to register the agent again, use `bash install.sh`.

## Verification

From the project directory:

```bash
.venv/bin/python -m pytest -q
```

From the Playground root:

```bash
infoext-monitor/.venv/bin/python -m pytest infoext-monitor/tests -q
```

`tests/conftest.py` adds the project root to the import path, so both commands
run the same test suite.
The suite includes forced-timeout and lock-release checks, transport-error
redaction, and isolated installer rendering, registration, restart and uninstall
checks. These lifecycle checks do not mutate the real LaunchAgent or send messages.

## Troubleshooting

- `Operation not permitted` or exit code `126` before any application log or
  audit appears: inspect `ProgramArguments` and `WorkingDirectory` in
  `launchctl print`. Both must point into the installed Application Support
  runtime. A legacy launcher or working directory under `Documents` can be
  denied before Python starts. Run `bash install.sh`, then repeat `kickstart`
  and verify a fresh audit; changing executable bits alone does not resolve
  macOS privacy restrictions. Old stderr entries remain after redeployment.
- An interactive check succeeds but launchd does not: compare the actual
  installed launcher, interpreter, working directory and project-root
  environment. Reinstall after source/shared-runtime changes; do not assume
  that Terminal permissions or environment are inherited by launchd.
- `InfoExt check failed`: inspect `logs/infoext.log`; the last successful
  status is retained.
- `CAPTCHA was not accepted`: inspect the saved images in `data/captcha/`.
  For a non-submitting diagnostic visit, use `--check-now --debug`. To retain
  a real submitted form's response, enable `infoext.debug` for an ordinary check.
- `InfoExt rejected required identity fields`: review the named local settings;
  changing OCR does not resolve missing or invalid identity data.
- `telegram_connector ... unavailable`: verify
  `telegram_connector.project_root` and the configured connector's
  `telegram_bridge.py`.
- `Telegram notification failed`: the event stays in `pending_notifications`
  and will be retried.
- After code or `config/runtime.local.toml` changes, run `bash restart.sh` to
  update the registered LaunchAgent.
