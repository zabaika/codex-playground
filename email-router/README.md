# email-router

Email Worker that filters all mail arriving at `zabaev.pro` and `zabaev.dev`.

| Verdict | Score | Action |
| --- | --- | --- |
| clean | < 25 | forward to `zabaevu+pro@gmail.com` (zabaev.pro) / `zabaevu+dev@gmail.com` (zabaev.dev) |
| suspicious | 25–59 | forward to `zabaevu+suspicious@gmail.com` |
| spam | >= 60 | stored in KV (`q:` prefix), **not forwarded** |

## Signals

- `Authentication-Results` from `mx.cloudflare.net` (SPF / DKIM / DMARC)
- Reply-To domain mismatch, display-name spoofing
- Sender domain on a suspicious TLD
- Missing `Message-ID`
- Phishing phrases in subject and body
- Dangerous attachment extensions (`.exe`, `.js`, `.iso`, `.docm`, …)
- Excessive number of distinct link hosts

The visible sender is parsed from the `From` header, including encoded display
names. The SMTP envelope sender remains separate in quarantine metadata.
`Reply-To` checks parsed mailbox addresses, not display names; multiple
addresses contribute at most one mismatch signal. RFC 2047 subjects are decoded
before phishing checks, including when body scanning is skipped.

Authentication scoring reads only the first `Authentication-Results` field
and requires the exact `mx.cloudflare.net` service identity. Subsequent fields
cannot fill missing results. Comments and quoted parameters cannot inject
authentication methods; malformed first fields provide no authentication score.

Text and HTML MIME parts are decoded before scanning, including base64,
quoted-printable and declared character sets. Attachment checks use decoded
MIME filenames rather than raw header text.

Body scanning is skipped for messages over 512 KiB. This bounds parser input;
it does not establish that every message fits a particular Workers CPU plan.
The MIME parser additionally limits nesting to 20, total headers to 64 KiB,
parts to 100, inline forwarded-message depth to 3 and forwarded addresses to
100. Limits are owned by `MIME_LIMITS` in `src/worker.js`. A parser failure
stores the full original message with verdict `scan-failed` instead of
forwarding an unscanned message.

## Upload without a build system

Upload **`worker.js`** as one ES module. It already includes the MIME parser;
the receiving deployment tool needs neither npm nor esbuild. The module has
a default export with `email` and `fetch` handlers and no external imports.
Do not upload `src/worker.js` directly: it is the editable source and imports
an npm package.

Deployment contract:

- Worker name: `email-router`; updating that name replaces the existing Worker.
- KV namespace: `email-quarantine`, bound as `QUARANTINE`.
- Secret: `QUARANTINE_TOKEN`, used by the quarantine HTTP API.
- Compatibility date: the value in `wrangler.jsonc`.
- Compatibility flags: none; `nodejs_compat` is not needed.
- No R2, D1, `send_email` binding or `vars` are required.

`src/worker.js` is canonical source. The tracked root `worker.js` is its
generated, upload-ready bundle. Maintain both through the local build below;
do not edit the generated bundle by hand.

## Setup

1. Create a KV namespace named `email-quarantine` and bind it as `QUARANTINE`.
   For Wrangler, run `wrangler kv namespace create email-quarantine` and put
   the returned id into `wrangler.jsonc`.
2. `wrangler secret put QUARANTINE_TOKEN` — any long random string.
3. `wrangler deploy`
4. In the dashboard, verify the three destination addresses under
   **Compute > Email Service > Email Routing > Destination addresses**.
5. For each domain, set the **catch-all** routing rule action to *Send to a Worker* → `email-router`.

No local listener, daemon or scheduler is required. Cloudflare invokes the
handlers. Worker exceptions are available through Cloudflare observability
(enabled in `wrangler.jsonc`) or `wrangler tail`; raw email content is not logged.
`observability.redact_query_string` is enabled to remove query strings,
including query-token credentials, from platform request logs and traces.
When uploading only `worker.js`, apply this observability setting separately
in the deployment tool or Cloudflare API; the module cannot configure platform
logging. Prefer Bearer authorization to avoid credentials in URLs altogether.

Each email invocation emits one structured `email_decision` console log with
`score`, `verdict`, `action` and `reason`. Filter Workers Logs by
`event = email_decision` to inspect filtering outcomes alongside invocation logs.
`verdict` is `clean` (below 25), `suspicious` (25–59), or `spam` (60 and above).
`action` is `forwarded`, `quarantined`, or `rejected`; it records the delivery
outcome separately from classification. Successful actions are recorded only
after forwarding or storage completes. Fixed reason codes identify spam,
missing routes, forwarding failures and MIME scan failures.

An incomplete classification uses `verdict: unclassified`; its score is
partial after a MIME parser failure, or `null` when scoring did not complete.
Custom decision logs omit subjects, addresses, filenames, raw content, tokens
and error details. Cloudflare's invocation records remain separate and can
contain their own platform metadata. Upload the regenerated `worker.js` to
enable these logs; earlier invocation records cannot recover missing scores.

## Delivery and storage failures

If forwarding fails, the Worker stores the complete RFC822 message in KV with
verdict `forward-failed`. An unknown clean-mail route uses verdict `no-route`.
Spam is accepted only after quarantine storage succeeds.

If storage is unavailable, the binding is missing, or the raw message exceeds
the KV value limit of 25 MiB, the Worker calls `setReject` with a generic reason
and propagates the error. Other unhandled processing failures use the same
boundary. This is an explicit permanent SMTP rejection, not an automatic retry
or a successful delivery. It prevents successful completion with neither a
delivered message nor a retained copy. SMTP behavior still requires a live
Cloudflare integration check.

Metadata previews (`from`, `to`, `subject`, `reasons`) may be shortened so the
entire serialized JSON fits KV's 1024-byte limit, including UTF-8 and JSON
escaping. Score, verdict, size and date remain intact. The raw message is not
truncated or replaced with an empty value.

## Quarantine API

```
GET    /list?token=...            # first page, up to 100 messages, oldest keys first
GET    /list?token=...&cursor=... # next page; URL-encode the returned cursor
GET    /raw?id=<key>&token=...    # raw RFC822 message
DELETE /?id=<key>&token=...       # delete one message
```

`/list` returns an object rather than the original bare array:

```json
{
  "messages": [{ "id": "q:...", "subject": "..." }],
  "list_complete": false,
  "cursor": "opaque-continuation-token"
}
```

Continue until `list_complete` is `true`, even if a page has no messages.
The final page has `cursor: null`. KV lists keys lexicographically; with the
existing timestamp keys, pages run from older to newer messages. This API
does not claim to return the latest 100 first. KV consistency applies, so
concurrent writes may not appear immediately and pagination is not a snapshot.

All endpoints also accept `Authorization: Bearer <token>`, avoiding the token
in the URL. The examples retain the existing query-token interface.

## Local development and verification

Use Node.js matching `package.json`. From this directory:

```sh
npm ci --ignore-scripts
npm run build
npm test
```

Dependency versions and integrity hashes are owned by `package.json` and
`package-lock.json`. The bundle includes `postal-mime` (MIT-0); esbuild is a
local build dependency only. Rebuild after editing `src/worker.js`, and include
the regenerated `worker.js` in the same change.

Tests exercise the shipped bundle: routing, combined forwarding/storage
failures, metadata byte limits, visible-sender parsing, MIME encodings and
attachments, parser limits, pagination, authorization and standalone module
loading without dependency resolution. They use local fixtures and do not
send real email or access a Cloudflare account.

## Tuning

`SPAM_THRESHOLD` (60) and `SUSPICIOUS_THRESHOLD` (25) at the top of `src/worker.js`.
Lower the suspicious threshold if too much spam reaches the normal inbox.
