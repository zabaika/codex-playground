# telegram_shared

Shared infrastructure primitives for the Telegram projects in this repository.

This package is intentionally domain-light. It owns reusable runtime mechanics such as:

- Telegram Bot API transport helpers
- retry classification and retry execution for explicitly configured Bot API calls
- bridge environment and subprocess helpers
- runtime config parsing helpers
- Telegram text formatting
- metadata-to-message-link formatting shared with digest; analytics owns stricter provenance checks
- sensitive-text redaction
- secret resolution wrappers
- shared path resolution
- AI usage summary formatting
- OpenAI Responses API transport and retry classification
- optional physical-attempt identity/usage correlation for application-owned budget ledgers

Product behavior stays in the owning app. For example, retry limits are read from each app's runtime config, not from `telegram_shared`.

## AI Usage Metrics

The shared usage summary exposes prompt-cache signals and response diagnostics:

- `cached input tokens`: tokens read from an existing prompt cache
- `cache write tokens`: tokens written while creating or refreshing a prompt cache entry when the API reports the metric
- `prompt versions`: distinct locally computed hashes of the static prompt template used in the selected history window
- `reasoning tokens`, response status, incomplete-response reason, and visible output character count: diagnostics for separating model reasoning from delivered text

Older database rows and API responses that omit a diagnostic are stored as unavailable rather than being treated as zero. The connector and agent bot Telegram summaries omit unavailable fields and retain known zeroes. Normal logging stores usage telemetry and hashes, not full prompt text. These counters make cache behavior observable; they do not guarantee a future cache hit.

## Access Boundary

`is_user_allowed` fails closed: when both the allowed user-id and username lists are empty, no Telegram user is authorized. The owning app configures those allowlists.

## Maintenance Scripts

Clear persisted full OpenAI prompt text from the Telegram SQLite usage logs:

```bash
python3 telegram_shared/scripts/purge_openai_prompt_text.py
```

## Retry Boundary

`async_retry` provides bounded retries and sleep-excluding timeouts. The caller owns budgets, error classification, cleanup, and logging. Failed cleanup must stop retries; the outer process TTL remains required.

`telegram_shared.bot_api.call_bot_api_with_retry` accepts explicit `attempts` and `backoff_seconds` from the caller. It does not define operator-facing defaults.

Current retryable Telegram Bot API failures are:

- `Telegram API request failed while calling <method>...`
- `Telegram API request timed out while calling <method>...`
- `Telegram API HTTP 5xx while calling <method>.`

Permanent Telegram API errors such as bad chat ids, revoked tokens, and other non-retryable API responses should be allowed to surface to the owning app.

`TelegramApiError` also exposes `request_outcome`, `error_code`, and `retry_after`
without changing legacy error text. Only explicit, consistent Bot API 4xx error
responses establish refusal; transport and unverified responses remain unknown.
Durable delivery callers use one `api_call` per recorded intent and own their retry
policy rather than invoking the legacy retry wrapper.

## OpenAI Responses Retry Boundary

`telegram_shared.openai_api.post_json` owns bounded JSON POST retries for two allowlisted
endpoints: `responses` and `input_tokens`. The latter uses
[`POST /v1/responses/input_tokens`](https://developers.openai.com/api/docs/guides/token-counting),
not local token estimation. `post_responses` preserves the legacy interface and sender
behavior. Each caller supplies its timeout, attempt count and backoff from runtime config.

The default `post_json` sender rejects redirects. An explicit sender is an injection
boundary, not permission to redirect authenticated requests. Optional `before_attempt`
and `after_attempt` hooks bracket physical attempts; the admitted identity is returned
as `OpenAIResponse.attempt_id`. Hook failures propagate. The optional monotonic deadline
bounds request time and retry waits, while the application still owns the outer hard TTL.
Applications own reservations, cumulative budgets, unknown outcomes and idempotency;
counted callers use the hooks to admit and settle retries against that ledger.

`openai_usage.log_openai_usage` can correlate a usage row with
`(job_id, batch_id, attempt_id)` and retain measured failure latency without usage tokens.
The owning application initializes the corresponding schema. Missing usage is not zero
and does not release an uncertain reservation. Prompt-text persistence stays opt-in.

Retryable OpenAI failures are network errors, including OS-level connection and temporary DNS failures; client errors `408` and eligible `429`; standard transient server errors `500`/`502`/`503`/`504`; and edge/origin errors `520` through `525` plus `530`. HTTP `3xx` responses are never retried: a redirect that reaches the classifier needs an explicitly validated target and is not a transient API failure. Every other HTTP `4xx` response is also not retried: it indicates a request, authentication, permission, quota, policy, or resource-state problem that another identical POST cannot resolve. A `429` with a permanent quota type or code is also not retried. Explicit local OS failures such as invalid descriptors and permission errors, HTTP `501`, and edge certificate error `526` are not retried. The helper honors `Retry-After`; otherwise it uses exponential backoff with jitter.

The helper records only bounded, redacted error diagnostics: status, OpenAI type/code, request id, exception class, OS error code, message, completed attempts, and retry delays. Digest decides whether to continue other channels and how to summarize the error for Telegram; the agent worker decides how to end one interactive request.

## Tests

Run shared helper tests directly:

```bash
.venv-test-gap-detection/bin/python -m pytest telegram_shared/tests -q
```

Run them together with the Telegram app suites when changing shared behavior:

```bash
.venv-test-gap-detection/bin/python -m pytest --import-mode=importlib telegram_shared/tests telegram_connector/tests telegram_agent_bot/tests -q
```
