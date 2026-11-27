import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import worker from "./worker.js";

const PHISH = "verify your account; confirm your identity; account will be suspended";

function message(options = {}) {
  const headers = new Headers({
    from: "Alice <alice@example.org>",
    to: "user@zabaev.pro",
    subject: "Hello",
    "message-id": "<test@example.org>",
    ...options.headers
  });
  const raw = options.raw ?? [...headers].map(([k, v]) => k + ": " + v).join("\r\n") +
    "\r\n\r\n" + (options.body ?? "Hello there");
  const bytes = new TextEncoder().encode(raw);
  return {
    from: options.from ?? "alice@example.org",
    to: options.to ?? "user@zabaev.pro",
    headers,
    rawSize: options.rawSize ?? bytes.length,
    rawBytes: bytes,
    raw: new Blob([bytes]).stream(),
    forwarded: [],
    rejected: [],
    async forward(target) {
      if (options.forwardError) throw new Error("destination unavailable");
      this.forwarded.push(target);
    },
    setReject(reason) { this.rejected.push(reason); }
  };
}

function environment(options = {}) {
  const entries = [];
  return {
    entries,
    QUARANTINE_TOKEN: "local-test-token",
    QUARANTINE: {
      async put(id, body, { metadata }) {
        if (options.storageError) throw new Error("storage unavailable");
        assert.ok(new TextEncoder().encode(JSON.stringify(metadata)).length <= 1024,
          "metadata exceeds KV byte limit");
        assert.ok(body.byteLength > 0, "quarantine must retain the raw message");
        entries.push({ id, body, metadata });
      }
    }
  };
}

test("clean messages keep their domain-specific destinations", async () => {
  for (const suffix of ["pro", "dev"]) {
    const m = message({ to: "user@zabaev." + suffix });
    const env = environment();
    await worker.email(m, env);
    assert.deepEqual(m.forwarded, ["zabaevu+" + suffix + "@gmail.com"]);
    assert.equal(env.entries.length, 0);
    assert.deepEqual(m.rejected, []);
  }
});

test("failed forwarding falls back to a complete raw quarantine record", async () => {
  const m = message({ forwardError: true });
  const env = environment();
  await worker.email(m, env);
  assert.equal(env.entries.length, 1);
  assert.equal(env.entries[0].metadata.verdict, "forward-failed");
  assert.equal(env.entries[0].body.byteLength, m.rawSize);
  assert.deepEqual(new Uint8Array(env.entries[0].body), m.rawBytes);
  assert.deepEqual(m.rejected, []);
});

test("failed forwarding and failed storage explicitly reject and propagate failure", async () => {
  const m = message({ forwardError: true });
  await assert.rejects(worker.email(m, environment({ storageError: true })));
  assert.equal(m.rejected.length, 1);
  assert.ok(!m.rejected[0].includes("storage unavailable"));
  assert.deepEqual(m.forwarded, []);
});

test("spam storage failures and missing bindings cannot silently accept mail", async () => {
  for (const env of [environment({ storageError: true }), {}]) {
    const m = message({ headers: {
      "authentication-results": "mx.cloudflare.net; dmarc=fail; spf=fail; dkim=fail"
    } });
    await assert.rejects(worker.email(m, env));
    assert.equal(m.rejected.length, 1);
    assert.deepEqual(m.forwarded, []);
  }
});

test("no-route storage failure explicitly rejects", async () => {
  const m = message({ to: "user@unknown.example" });
  await assert.rejects(worker.email(m, environment({ storageError: true })));
  assert.equal(m.rejected.length, 1);
});

test("oversized mail cannot be accepted as an empty quarantine record", async () => {
  const m = message({ rawSize: 25 * 1024 * 1024 + 1, forwardError: true });
  const env = environment();
  await assert.rejects(worker.email(m, env));
  assert.equal(env.entries.length, 0);
  assert.equal(m.rejected.length, 1);
});

for (const subject of ["a".repeat(200), "é".repeat(200), '\\"\t'.repeat(200)]) {
  test("serialized metadata fits KV even with escaping or multibyte text: " + subject.slice(0, 8), async () => {
    const from = "a".repeat(64) + "@" + "b".repeat(63) + "." + "c".repeat(63) + ".org";
    const m = message({
      from, to: from, headers: { subject, "content-type": 'multipart/mixed; boundary="part"' },
      body: Array.from({ length: 8 }, (_, i) => [
        "--part", "Content-Type: application/octet-stream",
        "Content-Disposition: attachment; filename*=utf-8''" + encodeURIComponent("😀漢字".repeat(12) + i + ".exe"),
        "", "data"
      ].join("\r\n")).join("\r\n") + "\r\n--part--",
      forwardError: true
    });
    const env = environment();
    await worker.email(m, env);
    assert.equal(env.entries.length, 1);
    const meta = env.entries[0].metadata;
    assert.equal(typeof meta.score, "number");
    assert.ok(meta.date);
    assert.ok(meta.verdict);
    assert.equal(meta.size, m.rawSize);
  });
}

test("visible From identity is checked independently of the envelope", async () => {
  for (const name of ['"support@trusted.org"', '=?UTF-8?B?c3VwcG9ydEB0cnVzdGVkLm9yZw==?=']) {
    const m = message({ headers: { from: name + " <sender@other.org>" }, forwardError: true });
    const env = environment();
    await worker.email(m, env);
    assert.ok(env.entries[0].metadata.reasons.includes("display-name spoof"));
    assert.equal(env.entries[0].metadata.from, "alice@example.org");
  }
});

test("reply-to compares with the visible sender address rather than the envelope", async () => {
  const m = message({ headers: {
    from: '"Google Help" <support@google-help.org>',
    "reply-to": "support@google-help.org"
  }, forwardError: true });
  const env = environment();
  await worker.email(m, env);
  assert.equal(m.forwarded.length, 0);
  assert.equal(env.entries[0].metadata.score, 0);
  assert.ok(!env.entries[0].metadata.reasons.includes("reply-to mismatch"));
});

test("brand substrings alone do not penalize official or other sender domains", async () => {
  for (const domain of ["mail.ru", "telegram.org", "yandex.ru", "vtb.ru", "news.telegram.org", "google-help.org"]) {
    const m = message({ from: "alice@" + domain, headers: {
      from: "Alice <alice@" + domain + ">",
      "authentication-results": "mx.cloudflare.net; spf=none; dkim=pass; dmarc=pass"
    } });
    const env = environment();
    await worker.email(m, env);
    assert.deepEqual(m.forwarded, ["zabaevu+pro@gmail.com"], domain);
    assert.equal(env.entries.length, 0);
  }
});

test("removing brand matching preserves suspicious TLD and phishing signals", async () => {
  const tld = message({ headers: { from: "Alice <alice@paypal.xyz>" }, forwardError: true });
  const env = environment();
  await worker.email(tld, env);
  assert.equal(env.entries[0].metadata.score, 15);
  assert.ok(env.entries[0].metadata.reasons.includes("suspicious TLD .xyz"));

  const phish = message({ headers: { from: "Alice <alice@mail.ru>",
    "authentication-results": "mx.cloudflare.net; dmarc=fail"
  }, body: PHISH });
  await worker.email(phish, env);
  assert.deepEqual(phish.forwarded, []);
  assert.equal(env.entries[1].metadata.score, 64);
  assert.equal(env.entries[1].metadata.verdict, "spam");
});

for (const encoding of ["7bit", "base64", "quoted-printable"]) {
  test("phishing body is detected after " + encoding + " decoding", async () => {
    const body = encoding === "base64" ? Buffer.from(PHISH).toString("base64") :
      encoding === "quoted-printable" ? PHISH.replaceAll(" ", "=20") : PHISH;
    const m = message({ body, headers: {
      "content-type": "text/plain; charset=utf-8",
      "content-transfer-encoding": encoding,
      "authentication-results": "mx.cloudflare.net; dmarc=fail"
    } });
    const env = environment();
    await worker.email(m, env);
    assert.deepEqual(m.forwarded, []);
    assert.equal(env.entries[0].metadata.score, 64);
    assert.equal(env.entries[0].metadata.verdict, "spam");
  });
}

test("multipart HTML is decoded and encoded dangerous filenames are inspected", async () => {
  const m = message({ headers: { "content-type": 'multipart/mixed; boundary="part"' }, body: [
    "--part", "Content-Type: text/html; charset=utf-8", "Content-Transfer-Encoding: base64", "",
    Buffer.from("<p>" + PHISH + "</p>").toString("base64"),
    "--part", "Content-Type: application/octet-stream",
    "Content-Disposition: attachment; filename*=utf-8''payload%2Eexe", "", "data", "--part--"
  ].join("\r\n") });
  const env = environment();
  await worker.email(m, env);
  assert.deepEqual(m.forwarded, ["zabaevu+suspicious@gmail.com"]);
  // Force forwarding failure in a second message so metadata is observable.
  const retry = message({ headers: Object.fromEntries(m.headers), forwardError: true,
    body: ["--part", "Content-Type: application/octet-stream",
      "Content-Disposition: attachment; filename*=utf-8''payload%2Eexe", "", "data", "--part--"].join("\r\n") });
  await worker.email(retry, env);
  assert.ok(env.entries[0].metadata.reasons.includes("dangerous attachment payload.exe"));
});

test("MIME resource limits quarantine the complete raw message instead of bypassing scanning", async () => {
  const m = message({ headers: { "content-type": 'multipart/mixed; boundary="part"' },
    body: Array.from({ length: 101 }, () => "--part\r\nContent-Type: text/plain\r\n\r\nHi\r\n").join("") + "--part--"
  });
  const env = environment();
  await worker.email(m, env);
  assert.deepEqual(m.forwarded, []);
  assert.equal(env.entries[0].metadata.verdict, "scan-failed");
  assert.equal(env.entries[0].body.byteLength, m.rawSize);
  assert.deepEqual(new Uint8Array(env.entries[0].body), m.rawBytes);
});

test("large clean mail retains the documented scan cutoff and forwarding", async () => {
  const m = message({ rawSize: 524289 });
  await worker.email(m, environment());
  assert.deepEqual(m.forwarded, ["zabaevu+pro@gmail.com"]);
});

test("quarantine pagination reaches new mail beyond the first 100 entries", async () => {
  const all = Array.from({ length: 205 }, (_, i) => ({ name: "q:" + (1000 + i), metadata: { subject: "mail " + i } }));
  const env = environment();
  const seen = [];
  env.QUARANTINE.list = async ({ prefix, limit, cursor }) => {
    assert.equal(prefix, "q:");
    assert.equal(limit, 100);
    const offset = Number(cursor ?? 0);
    const end = Math.min(offset + limit, all.length);
    return { keys: all.slice(offset, end), list_complete: end === all.length, cursor: String(end) };
  };
  let cursor;
  do {
    const response = await worker.fetch(new Request("https://worker.test/list" +
      (cursor ? "?cursor=" + encodeURIComponent(cursor) : ""), {
      headers: { authorization: "Bearer " + env.QUARANTINE_TOKEN }
    }), env);
    const page = await response.json();
    assert.equal(response.status, 200);
    assert.ok(Array.isArray(page.messages));
    seen.push(...page.messages.map(m => m.id));
    assert.equal(page.list_complete, page.cursor === null);
    cursor = page.cursor;
  } while (cursor);
  assert.deepEqual(seen, all.map(m => m.name));
});

test("empty nonfinal KV pages retain their continuation cursor", async () => {
  const env = environment();
  env.QUARANTINE.list = async () => ({ keys: [], list_complete: false, cursor: "next/page+=?" });
  const response = await worker.fetch(new Request("https://worker.test/list?token=" + env.QUARANTINE_TOKEN), env);
  assert.deepEqual(await response.json(), { messages: [], list_complete: false, cursor: "next/page+=?" });
});

test("quarantine API still requires authorization", async () => {
  const env = environment();
  const response = await worker.fetch(new Request("https://worker.test/list"), env);
  assert.equal(response.status, 401);
});

test("the shipped worker imports as a standalone ES module without resolving dependencies", async () => {
  const source = await readFile(new URL("./worker.js", import.meta.url), "utf8");
  const { default: standalone } = await import("data:text/javascript;base64," + Buffer.from(source).toString("base64"));
  assert.equal(typeof standalone.email, "function");
  assert.equal(typeof standalone.fetch, "function");
  const m = message({ headers: {
    "content-transfer-encoding": "base64", "content-type": "text/plain",
    "authentication-results": "mx.cloudflare.net; dmarc=fail"
  }, body: Buffer.from(PHISH).toString("base64") });
  const env = environment();
  await standalone.email(m, env);
  assert.deepEqual(m.forwarded, []);
  assert.equal(env.entries[0].metadata.verdict, "spam");
});

test("normal encoded mail still forwards and encoded links are scanned", async () => {
  const clean = message({ headers: { "content-transfer-encoding": "base64", "content-type": "text/plain" },
    body: Buffer.from("Hello, see you tomorrow.").toString("base64") });
  await worker.email(clean, environment());
  assert.deepEqual(clean.forwarded, ["zabaevu+pro@gmail.com"]);

  const links = Array.from({ length: 16 }, (_, i) => "https://host" + i + ".example.org/page").join(" ");
  const m = message({ headers: { "content-transfer-encoding": "base64", "content-type": "text/plain" },
    body: Buffer.from(links).toString("base64"), forwardError: true });
  const env = environment();
  await worker.email(m, env);
  assert.ok(env.entries[0].metadata.reasons.includes("many link hosts 16"));
});

test("a MIME limit failure plus unavailable storage explicitly rejects mail", async () => {
  const m = message({ headers: { "content-type": 'multipart/mixed; boundary="part"' },
    body: Array.from({ length: 101 }, () => "--part\r\n\r\nx\r\n").join("") + "--part--" });
  await assert.rejects(worker.email(m, environment({ storageError: true })));
  assert.equal(m.rejected.length, 1);
  assert.deepEqual(m.forwarded, []);
});

test("unexpected stream errors explicitly reject instead of accepting unprocessed mail", async () => {
  const m = message();
  m.raw = new ReadableStream({ start(controller) { controller.error(new Error("read failed")); } });
  await assert.rejects(worker.email(m, environment()));
  assert.equal(m.rejected.length, 1);
});

test("sender-supplied authentication results cannot fill gaps in Cloudflare results", async () => {
  for (const extra of ["", ", attacker.example; dkim=pass", ", mx.cloudflare.net; dkim=pass"]) {
    const m = message({ headers: {
      "authentication-results": "mx.cloudflare.net; spf=pass; dmarc=pass" + extra,
      "content-type": 'multipart/mixed; boundary="part"'
    }, body: ["--part", "Content-Type: application/octet-stream",
      'Content-Disposition: attachment; filename="one.exe"', "", "data",
      "--part", "Content-Type: application/octet-stream",
      'Content-Disposition: attachment; filename="two.exe"', "", "data", "--part--"].join("\r\n") });
    const env = environment();
    await worker.email(m, env);
    assert.deepEqual(m.forwarded, []);
    assert.equal(env.entries[0].metadata.score, 70);
    assert.equal(env.entries[0].metadata.verdict, "spam");
  }
});

test("authentication service identity must match the first header exactly", async () => {
  for (const auth of [
    "mx.cloudflare.net.attacker.example; spf=pass; dkim=pass; dmarc=pass",
    "attacker.example; spf=pass; dkim=pass; dmarc=pass, mx.cloudflare.net; spf=pass; dkim=pass; dmarc=pass",
    "attacker.example (mx.cloudflare.net); spf=pass; dkim=pass; dmarc=pass"
  ]) {
    const m = message({ headers: { "authentication-results": auth }, forwardError: true });
    const env = environment();
    await worker.email(m, env);
    assert.equal(env.entries[0].metadata.score, 0);
  }
});

test("authentication comments and quoted parameters cannot inject method results", async () => {
  const cases = [
    ["mx.cloudflare.net; spf=pass (nested (comment, dkim=pass)); dmarc=pass", 0],
    ['mx.cloudflare.net; spf=pass smtp.mailfrom="a,;dkim=pass@example.org"; dmarc=pass', 0],
    ['mx.cloudflare.net; spf=pass (dkim=pass); dmarc=pass, attacker.example; dkim=pass', 0],
    ["mx.cloudflare.net; x-dkim=pass; spf=pass; dmarc=pass", 0],
    ["mx.cloudflare.net; spf=pass; dkim=pass; dmarc=pass", -25],
    ["mx.cloudflare.net 1 (trusted); spf = pass; dkim = pass; dmarc = pass", -25],
    ["mx.cloudflare.net; spf=pass; dkim=pass; dmarc=pass (unterminated", 0]
  ];
  for (const [auth, score] of cases) {
    const m = message({ headers: { "authentication-results": auth }, forwardError: true });
    const env = environment();
    await worker.email(m, env);
    assert.equal(env.entries[0].metadata.score, score, auth);
  }
});

test("subject encodings do not change the phishing verdict, even above the body cutoff", async () => {
  const phrase = "verify your account";
  const subjects = [phrase, "=?UTF-8?B?" + Buffer.from(phrase).toString("base64") + "?=",
    "=?UTF-8?Q?verify_your_account?="];
  for (const rawSize of [undefined, 524289]) {
    for (const subject of subjects) {
      const m = message({ rawSize, headers: { subject,
        "authentication-results": "mx.cloudflare.net; spf=fail; dmarc=fail"
      } });
      const env = environment();
      await worker.email(m, env);
      assert.deepEqual(m.forwarded, []);
      assert.equal(env.entries[0].metadata.verdict, "spam");
      assert.equal(env.entries[0].metadata.score, rawSize ? 70 : 65);
    }
  }
});

test("Reply-To checks actual mailbox domains and counts a mismatch once", async () => {
  for (const replyTo of [
    '"Support <support@example.org>" <attacker@evil.org>',
    '=?UTF-8?B?c3VwcG9ydEBleGFtcGxlLm9yZw==?= <attacker@evil.org>',
    'alice@example.org, attacker@evil.org',
    'attacker@evil.org, second@other.org'
  ]) {
    const m = message({ headers: { "reply-to": replyTo,
      "authentication-results": "mx.cloudflare.net; spf=fail; dmarc=fail"
    } });
    const env = environment();
    await worker.email(m, env);
    assert.deepEqual(m.forwarded, []);
    assert.equal(env.entries[0].metadata.score, 67);
  }
  const m = message({ headers: { "reply-to": '"attacker@evil.org" <alice@example.org>' } });
  await worker.email(m, environment());
  assert.deepEqual(m.forwarded, ["zabaevu+pro@gmail.com"]);
});

test("quoted local-part separators do not change sender or Reply-To domains", async () => {
  for (const from of ['"alice@work"@example.org', '"alice\\\"@work"@example.org']) {
    const m = message({ from, headers: { from,
      "reply-to": '"bob@office"@example.org',
      "authentication-results": "mx.cloudflare.net; spf=fail; dmarc=fail"
    } });
    const env = environment();
    await worker.email(m, env);
    assert.deepEqual(m.forwarded, ["zabaevu+suspicious@gmail.com"]);
    assert.equal(env.entries.length, 0);
  }
});

test("quoted local parts preserve recipient routing and genuine Reply-To mismatches", async () => {
  for (const suffix of ["pro", "dev"]) {
    const m = message({ to: '"alice@work"@zabaev.' + suffix });
    await worker.email(m, environment());
    assert.deepEqual(m.forwarded, ["zabaevu+" + suffix + "@gmail.com"]);
  }
  const m = message({ headers: { from: '"alice@work"@example.org',
    "reply-to": '"bob@example.org"@evil.org',
    "authentication-results": "mx.cloudflare.net; spf=fail; dmarc=fail"
  } });
  const env = environment();
  await worker.email(m, env);
  assert.deepEqual(m.forwarded, []);
  assert.equal(env.entries[0].metadata.score, 67);
  assert.ok(env.entries[0].metadata.reasons.includes("reply-to mismatch"));
});

test("Cloudflare observability configuration redacts query strings", async () => {
  const config = JSON.parse(await readFile(new URL("./wrangler.jsonc", import.meta.url), "utf8"));
  assert.equal(config.observability.redact_query_string, true);
});

test("each email logs its score, classification and completed action without message content", async (t) => {
  const logs = [];
  t.mock.method(console, "log", record => logs.push(structuredClone(record)));
  const cases = [
    [{}, { score: 0, verdict: "clean", action: "forwarded", reason: null }],
    [{ headers: { "authentication-results": "mx.cloudflare.net; dmarc=fail" } },
      { score: 40, verdict: "suspicious", action: "forwarded", reason: null }],
    [{ headers: { "authentication-results": "mx.cloudflare.net; spf=fail; dkim=fail; dmarc=fail" } },
      { score: 70, verdict: "spam", action: "quarantined", reason: "spam" }],
    [{ forwardError: true }, { score: 0, verdict: "clean", action: "quarantined", reason: "forward-failed" }],
    [{ to: "user@unknown.example" }, { score: 0, verdict: "clean", action: "quarantined", reason: "no-route" }]
  ];
  for (const [options, expected] of cases) {
    const m = message({ ...options, headers: { ...options.headers, subject: "private subject marker" },
      body: "private body marker" });
    await worker.email(m, environment());
    assert.equal(logs.length, 1);
    assert.deepEqual(logs.pop(), { event: "email_decision", ...expected });
  }
});

test("failed storage logs rejection instead of a successful quarantine", async (t) => {
  const logs = [];
  t.mock.method(console, "log", record => logs.push(structuredClone(record)));
  const m = message({ forwardError: true });
  await assert.rejects(worker.email(m, environment({ storageError: true })));
  assert.deepEqual(logs, [{ event: "email_decision", score: 0, verdict: "clean",
    action: "rejected", reason: "forward-failed" }]);
});

test("incomplete scans are logged as unclassified and unexpected errors have no invented score", async (t) => {
  const logs = [];
  t.mock.method(console, "log", record => logs.push(structuredClone(record)));
  const m = message({ headers: { "content-type": 'multipart/mixed; boundary="part"' },
    body: Array.from({ length: 101 }, () => "--part\r\n\r\nx\r\n").join("") + "--part--" });
  await worker.email(m, environment());
  assert.deepEqual(logs.pop(), { event: "email_decision", score: 0, verdict: "unclassified",
    action: "quarantined", reason: "scan-failed" });
  const broken = message();
  broken.raw = new ReadableStream({ start(controller) { controller.error(new Error("private error marker")); } });
  await assert.rejects(worker.email(broken, environment()));
  assert.deepEqual(logs, [{ event: "email_decision", score: null, verdict: "unclassified",
    action: "rejected", reason: null }]);
});
