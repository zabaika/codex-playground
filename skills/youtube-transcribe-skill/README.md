# youtube-transcribe-skill

Local sanitized Codex skill for extracting YouTube subtitles through a fail-closed local workflow.

## Purpose

Use `youtube-transcribe-skill` when a YouTube transcript or subtitle file is needed locally with bounded permissions and explicit fallback behavior.

The skill:

- accepts a YouTube URL
- tries `youtube-transcript-api` first
- falls back to the reviewed `yt-dlp` provider path when needed
- chooses only one subtitle language from a configured priority list
- writes subtitle files and logs into project-local paths
- keeps browser-cookie access out of the default path and requires explicit approval before that fallback

## Local Runtime Behavior

- loads `config/runtime.local.toml` when present and otherwise falls back to `config/runtime.example.toml`
- treats the repository copy of `config/runtime.local.toml` as the single editable local config
- resolves output and log paths through `[paths]`, usually into `scratch/`
- prefers provider-based steady-state auth over browser-cookie access
- keeps one append-only log file instead of per-run log fan-out

## Installation

Install or refresh the local Codex copy with:

```bash
bash skills/youtube-transcribe-skill/install-local.sh
```

## Main Files

- `SKILL.md`: runtime workflow entrypoint
- `install-local.sh`: install or refresh helper
- `config/runtime.example.toml`: runtime config shape and defaults
- `scripts/run_youtube_transcribe.py`: main local runner
- `scripts/verify_provider_setup.sh`: provider verification helper

## Review Docs

- `SECURITY_REVIEW.md`: summarizes local security decisions, removed capabilities, and remaining risk boundaries
- `THIRD_PARTY_AUDIT.md`: documents the audit of the reviewed third-party material bundled into this skill
- `THIRD_PARTY_AUDIT_YOUTUBE_TRANSCRIPT_API.md`: documents the narrower audit of the `youtube-transcript-api` dependency path

## Notes

- Keep this README operator-facing and brief.
- Keep detailed guardrails, retries, auth fallback behavior, and output rules in `SKILL.md`.
