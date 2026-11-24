# Third-party audit: mcp-server-linkedin

## Reviewed artifact

- Repository: `stickerdaniel/linkedin-mcp-server`.
- Commit: `0e12514ce3ed408e0b5def05bab42361875852c1`.
- Declared version and configured package release: `4.31.0`.
- Initial static review date: `2026-11-24`.
- Assessment: **INCOMPLETE for the full runtime and dependencies**; the plugin
  package connection decision was **REVIEW**, not an unrestricted security PASS.

## What was reviewed

All six files of the upstream plugin wrapper were inspected, including hidden
configuration. [provenance.json](provenance.json) records their SHA-256 values.

Targeted runtime inspection covered these paths:

- `linkedin_mcp_server/tools/person.py`: profile reads and connection action.
- `linkedin_mcp_server/tools/messaging.py`: `send_message` definition and dispatch.
- `linkedin_mcp_server/write_confirmation.py`: complete confirmation middleware.
- `linkedin_mcp_server/server.py`: registration of the middleware.
- `linkedin_mcp_server/bootstrap.py`: default auto-import gate and task dispatch.
- `linkedin_mcp_server/config/loaders.py`: auto-import CLI/config mapping.
- `linkedin_mcp_server/browser_import/orchestrate.py`: introductory flow and
  initial discovery/extraction staging functions.

Only the described fragments were reviewed unless explicitly marked complete.
Runtime findings and their source-line evidence are owned by
[SECURITY_REVIEW.md](SECURITY_REVIEW.md), not duplicated here.

## Findings and chosen role

The server supplies local browser-backed LinkedIn access over MCP stdio. It is
an optional data/action provider, not an implemented JSS adapter. It retains
profiles, companies, jobs, content, messages and explicitly confirmed writes.
The local wrapper pins one package release and disables browser-cookie
auto-import. If no usable session exists, separate sign-in or an explicitly
authorized one-time browser import is allowed. The saved session is retained.

No external Python runtime code was vendored or modified in this plugin folder.
The package is resolved through `uvx`, unlike the reviewed offline wheel/archive
install paths used by some other local skills. Do not infer equivalent offline,
hash-pinned or supply-chain guarantees merely from this audit document layout.

## Subsequent installation evidence

After user approval, Homebrew installed native ARM `uv` 0.13.9. A separate
installation smoke check ran the pinned package with `--no-auto-import --help`
and completed successfully; its output included the expected authentication,
stdio and auto-import flags. The uv environment reported installing 74 packages.
This establishes CLI startup, not MCP tool discovery or LinkedIn functionality.

The adapted plugin was installed through the local Codex marketplace as
`linkedin-mcp-server@codex-playground-local`. Codex reported it installed and
enabled. Subsequent maintenance checks passed eight local regression tests,
configuration validation and full source/install byte parity. Those tests cover
the local checker, not upstream server behavior.

These are recorded historical results; verify parity again after any update.
Installation receipts remain temporary workstation evidence in scratch and are
not treated as a portable third-party source/lockfile.

## Subsequent cookie-lifecycle review

A separate offline source review covered browser-cookie extraction and storage,
including an independent baseline. It confirmed full Cookies SQLite/WAL/SHM
temporary copying, access to the browser-wide macOS Safe Storage key, plaintext
portable cookies.json with POSIX modes0600/0700, export after separate login,
and retention of retired sessions until explicit logout. No real credentials,
Keychain or runtime filesystem ACLs were read. No application tests were executed
as part of that source review.

The review reported conditional cross-user exposure when an auth-root inherits
a permissive macOS ACL: common_utils.py:82–94 applies chmod without clearing ACL,
while browser_import/orchestrate.py:184–186 writes decrypted cookies through it.
Private home deployment without that ACL was not shown vulnerable. The user
accepted this conditional finding for local use without requesting a fix.

A separate direct MCP client also completed handshake, listed 26 tools and read
local get_session_status. No saved session existed at that time. That check did
not establish LinkedIn data access or native Codex tool availability.

The user chose persistent reuse, one-time explicit import and a server-only pin.
No transitive dependency lock or recurring logout requirement is imposed.

## Explicit Chrome import attempt

The user authorized one explicit import with upstream selection among Chrome
profiles. Browser provisioning completed, but the import exited with no usable
session found. Follow-up metadata-only inspection found one unexpired li_at in
the main Chrome profile. Reproducing the importer's metadata reader failed with
a SQLite UTF-8 decoding error on encrypted_value. The broad import discovery
error handling therefore hid a data-reading compatibility problem as an absent
session. No successful cookie decryption or import was observed; the dependency
pin and server code were not changed. Fixing the runtime requires a separate
decision, or a separately authorized login can establish a persistent session.
Do not treat this attempt as successful LinkedIn read verification.

## Separate login and live read verification

The user subsequently authorized a separate visible login. The pinned CLI
completed successfully with auto-import disabled and retained the session.
A new direct MCP client, launched using the installed plugin configuration,
listed 26 tools and read the main personal profile plus Microsoft's company
about page without tool errors or section errors. After that client exited,
another fresh MCP process read the same personal profile without another login.
The saved session therefore survived process/browser shutdown in this test.

No write tool, logout or credential deletion was requested. Personal profile
content and credentials are not included in this repository record. This tests
the installed MCP configuration through a direct client; native Codex tool
discovery and write-confirmation UI remain unverified. Successful reads do not
resolve the separate Chrome-import compatibility problem described above.

## Unreviewed material and residual risk

- No complete Python-server, Chromium/Patchright or dependency-source audit.
- No verification that PyPI release bytes match the reviewed Git checkout.
- No recorded wheel hashes, reproducible dependency lock or offline bootstrap.
- No full inspection of network destinations, subprocesses, cookie extraction,
  profile lifecycle, browser provisioning or update mechanisms.
- No job-search, message-send or native host confirmation test. Live login and
  limited profile/company reads are recorded above; broader extraction is untested.

An exact server version still permits transitive dependency resolution.
Autoloaded browser assets and service behavior remain separate trust surfaces.
Do not label the package or its dependencies fully audited. A new release or
dependency change needs a review at the scope appropriate to the intended use,
while preserving the user's [update contract](README.md#local-update-contract).
