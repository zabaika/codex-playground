# InfoExt monitor

Local macOS expediente monitor using Playwright Chromium and local Apple Vision
OCR. Checks run through CLI, Telegram or launchd; notifications use the existing
`../telegram_connector` configuration and recipient.

## Requirements

- macOS, Python 3.11+ and access to the official InfoExt site.
- Xcode Command Line Tools (`xcode-select --install`).
- A configured sibling `telegram_connector` project.

## Installation and configuration

```bash
cd /path/to/infoext-monitor
cp config/runtime.example.toml config/runtime.local.toml
chmod 600 config/runtime.local.toml
# Fill the local config before installing.
bash install.sh
```

Set `infoext.nie` and `infoext.fecha_presentacion` (`DD/MM/YYYY`); add
`infoext.ano_nacimiento` (`YYYY`) when the portal requires it. Configure the
existing connector through `telegram_connector.project_root`; keep its
credentials in the connector.

[config/runtime.example.toml](./config/runtime.example.toml) documents all
settings, units and limits: schedule, portal spacing, retries, notifications,
debug and OCR. Shared shutdown settings come from
[common/config/process.toml](../common/config/process.toml).

The installer creates venvs, installs Chromium, compiles Vision and registers
launchd. Scheduled code runs from `~/Library/Application Support/infoext_monitor_service`;
config, data and logs stay in the source project selected by `INFOEXT_PROJECT_ROOT`.
Edit the source and rerun the installer after code, config or shared-runtime changes.

## Manual commands

```bash
.venv/bin/python main.py --check-now
.venv/bin/python main.py --check-now --notify
.venv/bin/python main.py --check-now --debug
.venv/bin/python main.py --test-telegram
```

- `--check-now`: check and apply `infoext.notify_on_unchanged_status`; real status changes notify regardless.
- `--notify`: also report an unchanged status.
- `--debug`: visible browser, first CAPTCHA and local OCR only; no submit, refresh or Telegram message.
- `--test-telegram`: send a connector test and print `Telegram notification: OK` on success.

All portal visits obey `infoext.portal_min_check_interval_seconds` and the
process lock. Manual checks can run outside the launchd calendar.

### Check from Telegram

Redeploy the existing connector bridge to enable the command:

```bash
bash ../telegram_connector/scripts/install_launch_agent.sh
```

```text
/infoext
/infoext <NIE> <submission-date> <birth-year>
```

Submission date uses `DD/MM/YYYY`; birth year accepts only four digits (`YYYY`).
Trailing arguments may be omitted and use config values. `infoext` without `/`
also works. Any supplied identity field selects a one-time query that preserves
the primary status, history and failure counters.

Existing bridge allowlists apply. Paths come from the installed InfoExt LaunchAgent;
no extra connector path settings are needed. Results go to the monitor's configured
recipient, with no extra reply after confirmed delivery. Failure, unconfirmed
delivery or a skipped check produces a diagnostic; failed deliveries remain queued.
Status messages include the queried NIE, retained in the private pending event
for retries; application logs use the masked NIE only.
Personal arguments are excluded from local command records but remain in Telegram history.

## CAPTCHA and debug artifacts

Apple Vision validates five lowercase Latin letters or digits and configured
variant agreement. `ocr.vision_use_confidence` controls confidence gating;
agreement and confidence are not measurements of actual accuracy.
`infoext.captcha_max_attempts` is shared by OCR-withheld and server-rejected attempts.

Ordinary checks save timestamped images in `data/captcha/`. CLI `--debug` saves
its image, filled-form screenshot and OCR report in `debug/<timestamp>/`.
Setting `infoext.debug = "enable"` instead retains submitted-form responses in
`debug/captcha-responses/` and adjacent OCR JSON reports in `data/captcha/`.
Response artifacts can contain personal data; keep this setting disabled outside
diagnosis. One-time queries do not save returned-page artifacts.

## State and reliability

| Artifact | Purpose |
|---|---|
| `data/state.json` | Last status, failures and pending notifications |
| `data/history.jsonl` | Successful monitored checks |
| `logs/infoext.log` | Application diagnostics |
| `data/launchd/com.infoext.monitor.last_attempt.json` | Latest scheduled attempt |

State and notification events are written atomically before sending. Failed
notifications remain queued for retry; a crash after delivery but before queue
removal can cause a duplicate. Temporary check failures preserve the last status.
The configured failure threshold creates one alert; recovery includes the current
status in a single message. Telegram failures do not increase check-failure counters.

The shared TTL runner enforces `infoext.run_timeout_seconds` and terminates the
whole process group on expiry. Locking prevents concurrent checks.

## LaunchAgent

```bash
bash restart.sh
launchctl print "gui/$(id -u)/com.infoext.monitor"
launchctl kickstart "gui/$(id -u)/com.infoext.monitor"
```

`restart.sh` redeploys through `install.sh`; it does not trigger a check.
The `[launchd]` config defines weekdays and slots from `first_run_time` through
`last_run_time` at `interval_hours` increments. Login loads the calendar without
an immediate check. Sleep does not count as failure and the monitor does not wake
the Mac; delayed triggers outside allowed days or the time window are skipped.
`kickstart` still obeys those guards and portal spacing.

```bash
cat data/launchd/com.infoext.monitor.last_attempt.json
tail -n 40 logs/infoext.log
tail -n 20 logs/launchd.stderr.log
```

Confirm a fresh successful-check timestamp, `CAPTCHA accepted`, a status and
Telegram delivery in the logs. Exit zero can also mean a portal-spacing skip;
an idle agent between checks is normal. Delivery failure leaves a pending event.

Stop or remove scheduling with `bash uninstall.sh`; resume with `bash install.sh`.
Uninstall preserves the runtime, config, state, history and logs.

## Observed InfoExt form

| Element | Selector |
|---|---|
| Entry | `get_by_role("link", name="ENTRAR FORMULARIO")` |
| NIE / submission date / birth year | `#nie` / `#fechaPresentacion` / `#anio` |
| CAPTCHA image | `img[alt="captcha"]` |
| Refresh | `get_by_role("link", name="Recargar Captcha")` |
| CAPTCHA input / submit | `#captcha` / `#btnConsulta` |

CAPTCHA is a PNG data URL. Refresh sends a POST and requires waiting for the new
document; subscribe to `domcontentloaded` before clicking. Results use labelled
fields and require `Estado`; comparison normalizes whitespace and case only.
Submit clicks `#btnConsulta`, waits for `domcontentloaded` and the configured
settle delay, then checks whether the form returned. Before submitting, NIE and
submission date must be nonempty; visible `#anio` must also be filled.
Result parsing supports table rows, `dt`/`dd` pairs and labelled values, extracting
Estado, expediente number, authorization type, submission date and resolution
date when present. Missing Estado is a parsing failure, never a new status.
Explicit CAPTCHA rejection is `Los caracteres escritos no son correctos.`
Identity validation errors and `The requested URL was rejected` stop the run.

## Tests and troubleshooting

```bash
# From this project:
.venv/bin/python -m pytest -q
# From the Playground root:
infoext-monitor/.venv/bin/python -m pytest infoext-monitor/tests -q
```

- CAPTCHA failures: inspect saved images; use CLI `--debug` without submitting,
  or `infoext.debug` for submitted-response diagnosis.
- Identity errors: check the local NIE, submission date and required birth year.
- Connector errors: check `telegram_connector.project_root` and its own configuration;
  undelivered notifications remain pending.
- `Operation not permitted` or exit `126`: reinstall; launcher and working directory
  must point to the Application Support runtime. Old stderr entries may remain.
- Missing InfoExt LaunchAgent in Telegram: run `bash install.sh`.
- Terminal works but launchd fails: reinstall and inspect its audit and stderr;
  launchd does not inherit the interactive shell environment.
