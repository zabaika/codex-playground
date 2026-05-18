# video-transcribe-skill security review

This folder contains a local, sanitized fork of the third-party `youtube-transcribe-skill`.

## Upstream behavior reviewed

Reviewed upstream variant:

- Repository: `feiskyer/claude-code-settings`
- Path: `plugins/youtube-transcribe-skill/skills/youtube-transcribe-skill/SKILL.md`

## Main findings

1. The upstream package appears to contain a single `SKILL.md`, so the supply-chain surface is small.
2. The main risk is not embedded malware, but the operational behavior encouraged by the instructions.
3. Upstream recommends `yt-dlp --cookies-from-browser=chrome` by default.
4. Upstream also allows broad browser automation through a Chrome MCP plugin and injected DOM-reading JavaScript.
5. In the current Codex environment, that browser MCP is not available, and local `yt-dlp` is also not installed, so the upstream skill would not work here as-is.
6. Current yt-dlp documentation says YouTube is enforcing PO Tokens for some subtitle requests on the `web` client, so cookies alone are not a durable answer to subtitle-related `HTTP 429` failures.
7. A dedicated PO token provider is the lowest-friction long-term path, but it should be isolated from the main yt-dlp workflow as much as possible.

## Why the local fork is safer

The local fork keeps only the narrowly useful part of the workflow:

- minimal tool scope: `Read`, `Write`, `Bash(which:*)`, `Bash(yt-dlp:*)`
- no browser automation fallback
- no default cookie access
- fail-closed behavior when `yt-dlp` is missing
- explicit user approval required before cookie-based retry
- subtitle-only extraction with `--skip-download`
- config-driven local runner with a safer `youtube-transcript-api` first path
- a second `yt-dlp` extraction attempt before concluding that subtitles are unavailable
- project-root-relative output and log paths
- one append-only log file instead of per-run log fan-out
- bounded retries only for temporary network-style failures
- explicit plugin disablement outside dedicated provider modes

## Residual risks

1. `yt-dlp` is still a powerful external binary and should be installed from a trusted source.
2. If the user approves `--cookies-from-browser`, local browser cookie access still expands the trust boundary.
3. PO Tokens are often video-bound and short-lived, so a durable `yt-dlp` path still needs a provider plugin.
4. YouTube UI and subtitle availability can change, so the skill should be treated as a convenience wrapper, not a guaranteed ingestion pipeline.

## Local environment status at initial review time

- `video-transcribe-skill` is not installed under `~/.codex/skills`
- `yt-dlp` is not present in `PATH`
- no compatible Chrome MCP tool is available in this Codex session

## Recommended activation path

1. Install `yt-dlp` from a trusted package source.
2. Copy this sanitized skill into `~/.codex/skills/video-transcribe-skill`.
   - A helper script is included at `install-local.sh`.
3. Prefer configuring `config/runtime.local.toml` with `provider-script` mode.
4. Restart Codex so the new skill is discovered.
5. Use cookie-based retry only for videos that truly require authentication or when the provider path is unavailable.
