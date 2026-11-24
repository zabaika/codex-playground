# LinkedIn MCP skill and plugin security review

Historical static review performed on 2026-11-24 before installation.
This document records that review and the subsequently applied mitigations;
adding it is not a new audit of the installed runtime.

## Reviewed upstream artifact

- Repository: `stickerdaniel/linkedin-mcp-server`.
- Commit: `0e12514ce3ed408e0b5def05bab42361875852c1`.
- Declared plugin version: `4.31.0`.
- Target: `plugins/linkedin-mcp-server`, six regular files including hidden
  Codex/Claude/Cursor manifests, `.mcp.json`, the skill and SVG asset.
- Workflow: `codex-security:check-skills`; all target-bundle files were read.
- Exact upstream paths and SHA-256 values: [provenance.json](provenance.json).

The target was outside active skill-discovery paths, and the review session
did not start inside its checkout. The upstream instructions were treated as
evidence, not activated. The user separately authorized downloading the snapshot.
No target code, installers, dependencies, browser or LinkedIn tool ran during
the static review. Six upstream repository-maintenance skills in `.agents/skills`
were discovered but excluded: they are not part of this plugin bundle.

## Verdicts

- **PASS — upstream SKILL.md text only.** Its 63 lines restrict activation to
  explicit LinkedIn requests, require narrow reads, gate writes, stop on
  authentication challenges and forbid unsafe write retries. No instruction
  hijacking, secret disclosure or hidden execution was found in that text.
- **REVIEW — connecting the plugin package.** Runtime selection, browser-session
  access and client-dependent confirmation require an explicit trust decision.
- **INCOMPLETE — full runtime/dependency assessment.** Selected source paths were
  inspected, not the entire Python server, Chromium or dependency graph.

PASS is scoped to the reviewed upstream text; it does not certify the current
local fork, PyPI artifacts, dependencies or real host behavior.

## Findings and evidence chains

1. **High — mutable executable dependency.** At the reviewed commit,
   `.codex-plugin/plugin.json:41` selects `.mcp.json`, whose lines 4–9 launch
   `uvx mcp-server-linkedin@latest`. Once installed and launched, resolution can
   execute a different distribution from the reviewed checkout with the server
   process's privileges. The local config now pins `4.31.0`; PyPI artifact
   identity and transitive dependency locking remain unverified.
2. **Medium — default browser-session import.** Upstream SKILL.md:34–38 describes
   import during a first request. `bootstrap.py:4154–4210` enables the default
   under desktop conditions; lines 4403–4409 start the import task, and
   4253–4310 call the browser-import orchestrator. Its introductory flow and
   staging function describe discovery, cookie extraction and local persistence.
   This may access browser credentials/Keychain when no server session exists.
   It is not evidence of exfiltration to a third party. The complete extractor
   was not audited. The local config disables auto-import; the skill prohibits
   automatic import and requires permission for separate login. The user later
   authorized a one-time explicit import from an agreed browser/profile selection.
3. **Medium — write confirmation depends on the client.** Upstream SKILL.md:21–30
   requires authorization. `tools/messaging.py:275–278` accepts model-supplied
   `confirm_send`. `write_confirmation.py:111–117` enumerates protected writes;
   `server.py:290–292` registers the middleware. Without client form support,
   middleware lines 159–160 delegate to host approvals. With form support,
   lines 162–193 request confirmation and handle refusal/errors. The local
   skill requires confirmation of each exact operation and disallows blanket
   approvals. No live Codex write-confirmation test has been performed.

Source locations above refer to the recorded upstream commit, not line numbers
in the subsequently modified local package.

## Local adaptation and maintenance

The user explicitly retained all LinkedIn functions, including writes, while
requiring per-write confirmation, disabled session auto-import and a fixed
server version. The local skill also treats LinkedIn text as untrusted data,
reports incomplete extraction, and gates login, updates and dependency repair.
The original exception for already-supplied message text was removed.
No server Python source was modified.

Subsequent user decisions retain sessions for repeated local use, allow explicit
one-time browser import, and require only the server-version pin rather than a
transitive dependency lock. Automatic re-import and automatic logout remain
forbidden. A later scoped static audit found conditional inherited-macOS-ACL
exposure of plaintext cookies in shared directories. The user accepted that
conditional risk for this local deployment; no ACL fix was made and actual
workstation ACLs were not inspected. This acceptance does not change the finding
or establish that the runtime is fully audited.

The later Codex-only packaging cleanup removed the Claude and Cursor compatibility
manifests. This does not change the historical six-file upstream review scope or
remove a LinkedIn tool. Their upstream hashes remain recorded in provenance;
the Codex icon is retained because the active manifest references it.

These are policy mitigations, not a new technical authorization layer. The
canonical requirements and update gates live in
[Local update contract](README.md#local-update-contract). AGENTS.md routes
maintainers there; the contract checker covers pinning, auto-import and optional
source/install parity, not natural-language compliance.

## Residual risk and activation boundary

Browser automation can break after LinkedIn changes. Upstream warns of account
restrictions from automated access; local execution does not eliminate that risk.
Retrieved content can contain adversarial instructions. The skill's instruction
boundary reduces intended exposure but does not prove model resistance.

Installation and a CLI help smoke check happened only after the static review
and explicit user authorization. The initial checks used no login, cookies,
Keychain operation or live LinkedIn write. Later user-authorized login and
read-only MCP checks are recorded separately in
[THIRD_PARTY_AUDIT.md](THIRD_PARTY_AUDIT.md) for the runtime coverage gap.
