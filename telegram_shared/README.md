# telegram_shared

Shared infrastructure primitives for the Telegram projects in this repository.

This package is intentionally domain-light. It owns reusable runtime mechanics such as:

- Telegram Bot API transport helpers
- retry classification and retry execution for explicitly configured Bot API calls
- bridge environment and subprocess helpers
- runtime config parsing helpers
- Telegram text formatting
- sensitive-text redaction
- secret resolution wrappers
- shared path resolution
- AI usage summary formatting

Product behavior stays in the owning app. For example, retry limits are read from each app's runtime config, not from `telegram_shared`.

## Retry Boundary

`telegram_shared.bot_api.call_bot_api_with_retry` accepts explicit `attempts` and `backoff_seconds` from the caller. It does not define operator-facing defaults.

Current retryable Telegram Bot API failures are:

- `Telegram API request failed while calling <method>...`
- `Telegram API request timed out while calling <method>...`
- `Telegram API HTTP 5xx while calling <method>.`

Permanent Telegram API errors such as bad chat ids, revoked tokens, and other non-retryable API responses should be allowed to surface to the owning app.

## Tests

Run shared helper tests directly:

```bash
.venv-test-gap-detection/bin/python -m pytest telegram_shared/tests -q
```

Run them together with the Telegram app suites when changing shared behavior:

```bash
.venv-test-gap-detection/bin/python -m pytest telegram_shared/tests telegram_connector/tests telegram_agent_bot/tests -q
```
