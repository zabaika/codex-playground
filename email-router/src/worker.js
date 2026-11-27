import PostalMime, { addressParser, decodeWords } from "postal-mime";

// Email router for zabaev.pro / zabaev.dev
// clean      -> domain mailbox (zabaevu+pro@gmail.com / zabaevu+dev@gmail.com)
// suspicious -> zabaevu+suspicious@gmail.com
// spam       -> KV quarantine, dropped (never forwarded)

const ROUTES = {
  "zabaev.pro": "zabaevu+pro@gmail.com",
  "zabaev.dev": "zabaevu+dev@gmail.com"
};
const SUSPICIOUS_INBOX = "zabaevu+suspicious@gmail.com";

const SPAM_THRESHOLD = 60;
const SUSPICIOUS_THRESHOLD = 25;
const MAX_BODY_SCAN = 524288; // 512 KiB
const MAX_KV_VALUE = 25 * 1024 * 1024; // KV value limit
const MAX_KV_METADATA = 1024; // KV serialized metadata limit, in UTF-8 bytes
const MIME_LIMITS = {
  maxNestingDepth: 20,
  maxHeadersSize: 65536,
  maxPartCount: 100,
  maxRfc822NestingDepth: 3,
  maxForwardedAddresses: 100
};

const SUSPICIOUS_TLDS = new Set([
  "zip", "mov", "top", "xyz", "click", "link", "loan", "work", "gq", "tk", "ml", "cf",
  "buzz", "rest", "cam", "surf", "icu", "cyou", "sbs", "quest", "monster", "lol", "bond",
  "cfd", "beauty", "makeup", "skin", "fit", "best", "life", "today", "support"
]);

const DANGEROUS_EXT = /\.(exe|scr|js|jse|vbs|vbe|bat|cmd|com|pif|jar|iso|img|lnk|hta|ps1|msi|reg|docm|xlsm|pptm|svg)$/i;

const PHISH_WORDS = [
  "verify your account", "confirm your identity", "account will be suspended",
  "account has been suspended", "unusual sign-in", "unusual activity",
  "password will expire", "reset your password", "update your payment",
  "wire transfer", "gift card", "bitcoin", "crypto wallet", "you have won",
  "claim your prize", "act now", "urgent action", "final notice",
  "invoice attached", "payment failed", "click here to verify",
  "confirm your wallet", "seed phrase", "earn money fast", "work from home",
  "limited time offer"
];

function domainOf(addr) {
  if (!addr) return "";
  // Mailboxes are already parsed. The final @ separates a DNS domain from
  // the local part, which may itself contain quoted @ characters.
  const m = String(addr).match(/@([^@>\s,;"]+)>?\s*$/);
  return m ? m[1].toLowerCase().replace(/[.>]+$/, "") : "";
}

// Cloudflare prepends a trusted Authentication-Results header (authserv-id mx.cloudflare.net).
// Anything else in the header chain was supplied by the sender and must not be trusted.
function authResults(headers) {
  const result = { spf: null, dkim: null, dmarc: null };
  const raw = headers.get("authentication-results") || "";
  const fields = [];
  let field = "";
  let commentDepth = 0;
  let quoted = false;
  let escaped = false;
  // Headers.get() can join duplicate fields with commas. Stop at the first
  // field boundary, but not at separators inside comments or quoted values.
  for (const c of raw) {
    if (escaped) {
      if (!commentDepth) field += c;
      escaped = false;
      continue;
    }
    if ((quoted || commentDepth) && c === "\\") {
      if (!commentDepth) field += c;
      escaped = true;
      continue;
    }
    if (commentDepth) {
      if (c === "(") commentDepth++;
      if (c === ")") commentDepth--;
      continue;
    }
    if (c === '"') { quoted = !quoted; field += c; continue; }
    if (!quoted && c === "(") { commentDepth = 1; field += " "; continue; }
    if (!quoted && c === ")") return result;
    if (!quoted && c === ",") break;
    if (!quoted && c === ";") { fields.push(field.trim()); field = ""; continue; }
    field += c;
  }
  if (quoted || commentDepth || escaped) return result;
  fields.push(field.trim());
  if (!/^mx\.cloudflare\.net(?:\s+\d+)?$/i.test(fields.shift())) return result;
  for (const value of fields) {
    const match = value.match(/^(spf|dkim|dmarc)(?:\/\d+)?\s*=\s*([a-z]+)(?=\s|$)/i);
    if (match && result[match[1].toLowerCase()] === null) {
      result[match[1].toLowerCase()] = match[2].toLowerCase();
    }
  }
  return result;
}

function scanBody(text, attachments) {
  const reasons = [];
  let score = 0;
  const lower = text.toLowerCase();

  let hits = 0;
  for (const w of PHISH_WORDS) {
    if (lower.indexOf(w) !== -1) hits++;
  }
  if (hits > 0) {
    score += Math.min(hits * 8, 24);
    reasons.push("phishing phrases x" + hits);
  }

  for (const attachment of attachments) {
    const name = attachment.filename || "";
    if (DANGEROUS_EXT.test(name)) {
      score += 35;
      reasons.push("dangerous attachment " + name.slice(0, 40));
    }
  }

  const links = (text.match(/https?:\/\/[^\s"'<>)]+/gi) || []).slice(0, 60);
  const hosts = new Set();
  for (const l of links) {
    try { hosts.add(new URL(l).hostname.toLowerCase()); } catch (e) {}
  }
  if (hosts.size > 15) {
    score += 10;
    reasons.push("many link hosts " + hosts.size);
  }

  return { score: score, reasons: reasons };
}

function fitMetadata(meta) {
  const encoder = new TextEncoder();
  const fields = ["from", "to", "subject", "reasons"];
  // Measure the entire JSON, including escaping and multibyte characters.
  while (encoder.encode(JSON.stringify(meta)).byteLength > MAX_KV_METADATA) {
    const field = fields.reduce((a, b) => meta[a].length >= meta[b].length ? a : b);
    if (!meta[field].length) throw new Error("Quarantine metadata cannot fit KV");
    meta[field] = meta[field].slice(0, -1);
  }
  return meta;
}

async function quarantine(env, message, rawBuf, score, reasons, verdict) {
  if (!env.QUARANTINE) throw new Error("Quarantine binding is missing");
  if (message.rawSize > MAX_KV_VALUE) throw new Error("Message exceeds KV value limit");
  const id = "q:" + Date.now() + ":" + crypto.randomUUID();
  const meta = fitMetadata({
    from: String(message.from || "").slice(0, 200),
    to: String(message.to || "").slice(0, 200),
    subject: String(message.headers.get("subject") || "").slice(0, 200),
    score: score,
    verdict: verdict,
    reasons: reasons.slice(0, 8).join("; ").slice(0, 500),
    size: message.rawSize,
    date: new Date().toISOString()
  });
  const body = rawBuf || (await new Response(message.raw).arrayBuffer());
  await env.QUARANTINE.put(id, body, { metadata: meta });
}

async function routeEmail(message, env, decision) {
  const reasons = [];
  let score = 0;

  const headers = message.headers;
  const auth = authResults(headers);

  if (auth.dmarc === "fail") { score += 40; reasons.push("dmarc=fail"); }
  if (auth.spf === "fail") { score += 15; reasons.push("spf=fail"); }
  if (auth.dkim === "fail") { score += 15; reasons.push("dkim=fail"); }
  if (auth.spf === "pass" && auth.dkim === "pass" && auth.dmarc === "pass") score -= 25;

  const sender = addressParser(headers.get("from") || message.from, { flatten: true })[0];
  const fromDomain = domainOf(sender?.address);
  const toDomain = domainOf(message.to);
  const replyTo = addressParser(headers.get("reply-to") || "", { flatten: true });
  const subject = decodeWords(headers.get("subject") || "");
  const name = decodeWords(sender?.name || "");

  if (replyTo.some(mailbox => domainOf(mailbox.address) && domainOf(mailbox.address) !== fromDomain)) {
    score += 12; reasons.push("reply-to mismatch");
  }
  if (name && name.indexOf("@") !== -1 && domainOf(name) && domainOf(name) !== fromDomain) {
    score += 15; reasons.push("display-name spoof");
  }

  const tld = fromDomain.split(".").pop();
  if (SUSPICIOUS_TLDS.has(tld)) { score += 15; reasons.push("suspicious TLD ." + tld); }

  if (!headers.get("message-id")) { score += 5; reasons.push("no message-id"); }

  const subjLower = subject.toLowerCase();
  for (const w of PHISH_WORDS) {
    if (subjLower.indexOf(w) !== -1) { score += 10; reasons.push("subject: " + w); break; }
  }

  let rawBuf = null;
  if (message.rawSize <= MAX_BODY_SCAN) {
    rawBuf = await new Response(message.raw).arrayBuffer();
    let parsed;
    try {
      parsed = await PostalMime.parse(rawBuf, MIME_LIMITS);
    } catch (e) {
      reasons.push("MIME scan failed");
      decision.score = score;
      decision.reason = "scan-failed";
      await quarantine(env, message, rawBuf, score, reasons, "scan-failed");
      decision.action = "quarantined";
      return;
    }
    const s = scanBody((parsed.text || "") + "\n" + (parsed.html || ""), parsed.attachments || []);
    score += s.score;
    for (const r of s.reasons) reasons.push(r);
  } else {
    score += 5; reasons.push("large message, body not scanned");
  }

  const verdict = score >= SPAM_THRESHOLD ? "spam" : (score >= SUSPICIOUS_THRESHOLD ? "suspicious" : "clean");
  decision.score = score;
  decision.verdict = verdict;

  if (verdict === "spam") {
    decision.reason = "spam";
    await quarantine(env, message, rawBuf, score, reasons, verdict);
    decision.action = "quarantined";
    return; // dropped, never forwarded
  }

  const target = verdict === "suspicious" ? SUSPICIOUS_INBOX : ROUTES[toDomain];
  if (!target) {
    decision.reason = "no-route";
    await quarantine(env, message, rawBuf, score, reasons, "no-route");
    decision.action = "quarantined";
    return;
  }

  try {
    await message.forward(target);
    decision.action = "forwarded";
  } catch (e) {
    // destination not verified, size limit, etc.
    decision.reason = "forward-failed";
    await quarantine(env, message, rawBuf, score, reasons, "forward-failed");
    decision.action = "quarantined";
  }
}

export default {
  async email(message, env, ctx) {
    const decision = {
      event: "email_decision",
      score: null,
      verdict: "unclassified",
      action: "rejected",
      reason: null
    };
    try {
      await routeEmail(message, env, decision);
    } catch (e) {
      // Use an explicit SMTP action, without exposing internal errors.
      message.setReject("Unable to deliver or store this message");
      throw e;
    } finally {
      // Log only bounded operational fields, never message content or errors.
      console.log(decision);
    }
  },

  async fetch(request, env) {
    const url = new URL(request.url);
    const header = request.headers.get("authorization") || "";
    const token = url.searchParams.get("token") || header.replace(/^Bearer\s+/i, "");
    if (!env.QUARANTINE_TOKEN || token !== env.QUARANTINE_TOKEN) {
      return new Response("Unauthorized", { status: 401 });
    }
    if (url.pathname === "/list") {
      const options = { prefix: "q:", limit: 100 };
      const cursor = url.searchParams.get("cursor");
      if (cursor) options.cursor = cursor;
      const list = await env.QUARANTINE.list(options);
      const messages = list.keys.map(function (k) {
        const m = k.metadata || {};
        return {
          id: k.name, date: m.date, from: m.from, to: m.to,
          subject: m.subject, score: m.score, verdict: m.verdict,
          reasons: m.reasons, size: m.size
        };
      });
      return Response.json({
        messages: messages,
        list_complete: list.list_complete,
        cursor: list.list_complete ? null : list.cursor
      });
    }
    if (url.pathname === "/raw") {
      const id = url.searchParams.get("id");
      if (!id) return new Response("missing id", { status: 400 });
      const v = await env.QUARANTINE.get(id, "arrayBuffer");
      if (!v) return new Response("not found", { status: 404 });
      return new Response(v, { headers: { "content-type": "message/rfc822" } });
    }
    if (request.method === "DELETE") {
      const id = url.searchParams.get("id");
      if (!id) return new Response("missing id", { status: 400 });
      await env.QUARANTINE.delete(id);
      return new Response("deleted");
    }
    return new Response("email-router ok\nGET /list?token=...&cursor=...\nGET /raw?id=...&token=...\nDELETE /?id=...&token=...");
  }
};
