# InfoExt monitor maintenance

## Scope and reading order

This project is a local macOS scheduled monitor for an InfoExt expediente. Its
operator contract and observed DOM selectors are in [README.md](./README.md).
For every change, read this file, then the repository [RULEBOOK.md](../RULEBOOK.md),
then the relevant tests in [tests](./tests).

## Configuration and secrets

- `config/runtime.example.toml` is the tracked schema, examples, units and
  validation guidance. `config/runtime.local.toml` is the only machine-local
  configuration and must remain untracked with mode `0600`.
- Keep operator-tunable timings, retry limits, OCR settings, notification
  policy and calendar schedule in the TOML schema. Do not duplicate their
  effective values in Python, shell scripts, plist templates or README prose.
- README may name a config key and describe its formula or effect. It must not
  restate concrete values from local configuration.
- Never add NIE, submission date, Telegram token, chat ID, Keychain material,
  or absolute workstation paths to tracked files, fixtures, logs or docs.

## Telegram boundary

- Use `notifier.py` and the configured existing `telegram_connector` project.
  Do not add a Telegram Bot API client, token, chat ID or copied connector
  source to this project.
- Preserve `pending_notifications`: commit a notification event before an
  external send, clear it only after confirmed delivery, and retry it on a
  later run.
- Use `common.json_io.write_json_atomic` for state and launchd audit replacement.
  Keep state schemas, the process lock, notification ordering and JSONL history
  in this project; the shared helper owns only file publication.
- A Telegram failure must not overwrite a known InfoExt status or turn a
  successful InfoExt check into an unknown status.

## Scheduled runtime

- Treat `install.sh` as the canonical deploy/redeploy path. It renders the
  plist from TOML and performs `bootout` then `bootstrap`.
- Scheduled code, venv, Vision helper and shared process runtime are installed
  in `~/Library/Application Support/infoext_monitor_service`, matching the
  connector's deployment shape. Keep launchers and WorkingDirectory there.
- `INFOEXT_PROJECT_ROOT` selects project-owned config, data and logs; runtime
  modules and helper executables resolve from `RUNTIME_ROOT`. Do not copy local
  config or connector credentials into this service runtime.
- Treat installed runtime files as derived artifacts; edit the source and
  redeploy after application or shared `common/` changes. Never patch the
  Application Support copy directly.
- `restart.sh` delegates to that same installer, so it is also a redeploy
  operation. Do not manually edit the installed plist.
- Keep `ProgramArguments[0]` as the stable launcher wrapper. Keep shell
  runners thin and delegate application behavior to `main.py`.
- Keep one outer supervisor from `common/ttl_runner.py` for manual and scheduled
  modes. The scheduled runner invokes the worker directly; keep the hidden worker
  argument out of operator examples.
- Preserve `data/launchd/com.infoext.monitor.last_attempt.json` as the scheduled
  audit. Host-sleep interruptions must preserve status and failure counters.
- Calendar entries must be generated only from `[launchd]`; do not duplicate
  schedule values in the plist template or launcher scripts.

## Verification

- Evaluate OCR changes against saved `data/captcha/` images before considering
  another live portal visit. Keep experiment scripts and generated samples out
  of the runtime source and tests; use synthetic fixtures for policy regressions.
- Apple Vision is the only supported OCR engine. Preserve the replaceable
  solver interface and its configurable consensus and confidence policies.
- Update code, config schema, tests and README together for behavior changes.
- Run the test suite both from this directory and from the Playground root:
  `.venv/bin/python -m pytest -q` and
  `infoext-monitor/.venv/bin/python -m pytest infoext-monitor/tests -q`.
- For launchd changes, validate the rendered plist with `plutil -lint`, run the
  canonical installer, and inspect the loaded service with `launchctl print`.
- Verify launcher migrations through an actual `kickstart` during an eligible
  configured window. Correlate a fresh audit with worker and delivery logs;
  bootstrap success or an idle service alone does not prove a completed check.
  A portal-spacing skip can exit successfully without contacting InfoExt.
- Before committing, scan tracked candidates for secrets and absolute local
  paths, and keep generated data, logs, debug artifacts, virtual environments
  and local config out of Git.
