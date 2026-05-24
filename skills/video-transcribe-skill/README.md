# video-transcribe-skill

Local sanitized Codex skill for extracting YouTube or Vimeo subtitles through a fail-closed local workflow.

## Purpose

Use `video-transcribe-skill` when a YouTube or Vimeo transcript or subtitle file is needed locally with bounded permissions and explicit fallback behavior.

The skill:

- accepts a YouTube or Vimeo URL
- tries `youtube-transcript-api` first for YouTube
- falls back to the reviewed `yt-dlp` path when needed
- chooses only one subtitle language from a configured priority list
- converts downloaded `vtt` subtitles to `srt` when direct `srt` output is unavailable
- writes subtitle files and logs into project-local paths
- keeps browser-cookie access out of the default path and requires explicit approval before that fallback
- bootstraps the local `youtube-transcript-api` venv from audited archives stored under the configured local artifacts root
- keeps audit metadata and lockfiles in `third_party/` while unpacking runtime assets only during install

## Local Runtime Behavior

- loads `config/runtime.local.toml` when present and otherwise falls back to `config/runtime.example.toml`
- reads `[artifacts].root_dir` as the local source of audited vendor archives for install/bootstrap
- resolves output and log paths through `[paths]`, usually into `scratch/`
- never resolves project-local relative paths from the shell cwd
- prefers provider-based steady-state auth over browser-cookie access
- keeps one append-only log file instead of per-run log fan-out

## Installation

Install or refresh the local Codex copy with:

```bash
bash skills/video-transcribe-skill/install-local.sh
```

The installer:

- copies the skill into `~/.codex/skills/video-transcribe-skill`
- reads the audited archive root from `CODEX_AUDITED_ARTIFACTS_ROOT` or `[artifacts].root_dir`
- requires `python3.14` so the bootstrapped runtime matches the audited local wheels
- verifies archive checksums and required audit metadata from `config/vendor-manifest.toml`
- unpacks `bgutil-plugin`, `bgutil-provider`, and the `youtube-transcript-api` wheel set from local audited archives
- creates `vendor/youtube-transcript-api/venv`
- installs `youtube-transcript-api` and its dependencies from the unpacked audited local wheels only
- does not fetch packages from the internet during install

## Main Files

- `SKILL.md`: runtime workflow entrypoint
- `install-local.sh`: install or refresh helper
- `config/runtime.example.toml`: runtime config shape and defaults
- `config/vendor-manifest.toml`: audited archive provenance, install targets, and checksums
- `third_party/`: audit metadata and lockfiles for the reviewed dependency set
- `scripts/run_video_transcribe.py`: main local runner
- `scripts/verify_provider_setup.sh`: provider verification helper

## Review Docs

- `SECURITY_REVIEW.md`: summarizes local security decisions, removed capabilities, and remaining risk boundaries
- `THIRD_PARTY_AUDIT.md`: documents the audit of the reviewed third-party material bundled into this skill
- `THIRD_PARTY_AUDIT_YOUTUBE_TRANSCRIPT_API.md`: documents the narrower audit of the `youtube-transcript-api` dependency path

## Notes

- Keep this README operator-facing and brief.
- Keep detailed guardrails, retries, auth fallback behavior, and output rules in `SKILL.md`.
