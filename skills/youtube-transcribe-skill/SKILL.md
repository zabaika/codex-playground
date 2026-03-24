---
name: youtube-transcribe-skill
description: Extract subtitles or transcripts from a YouTube video with a local, fail-closed workflow. Try `youtube-transcript-api` first, then fall back to reviewed `yt-dlp` provider mode. Select only the first available subtitle language from a priority list.
allowed-tools: Read, Write, Bash(which:*), Bash(python3:*), Bash(yt-dlp:*)
---

# YouTube Transcribe Skill

## Overview

Extract subtitles from a YouTube URL into a local subtitle file with the smallest practical permission set. This local fork intentionally removes browser automation, chooses only one subtitle language based on priority order, and prefers a dedicated PO token provider over direct browser-cookie access.

Input YouTube URL: `$ARGUMENTS`

## Local Runtime Config

1. Load `config/runtime.local.toml` when it exists.
2. If no local config exists, fall back to the defaults documented in `config/runtime.example.toml`.
3. Resolve relative runtime paths from the project root:
   - `CODEX_PLAYGROUND_PROJECT_ROOT` when set
   - otherwise `[paths].project_root`
   - otherwise the current working directory
4. Prefer a provider-based steady-state setup.
5. Never commit `runtime.local.toml`.
6. Prefer writing subtitles and logs into a project-local `scratch/` directory via `[paths]`.

## Guardrails

1. Accept only standard YouTube URLs such as `https://www.youtube.com/watch?v=...` and `https://youtu.be/...`.
2. Before running anything, check `which yt-dlp`.
3. If `yt-dlp` is missing, stop and tell the user that this skill requires local `yt-dlp`. Do not fall back to browser automation, remote transcription APIs, or custom scripts.
4. Never download video or audio media when subtitles are enough. Always prefer subtitle-only extraction with `--skip-download`.
5. Never read browser cookies by default when provider mode is available.
6. If subtitle extraction fails because the video is age-gated, region-gated, or otherwise requires browser authentication, prefer the configured provider mode. Use `--cookies-from-browser=<browser>` only as an explicit fallback.
7. Never print cookie values, never inspect cookie databases manually, and never use account passwords in command arguments.
8. Save files only in the configured subtitle output directory.
9. Do not request multiple subtitle languages in one download. First detect available languages, then download only the first matching language from the priority list.

## Default Workflow

1. Validate the URL.
2. Confirm `yt-dlp` is available with `which yt-dlp`.
3. Prefer the local runner:

```bash
python3 scripts/run_youtube_transcribe.py --url "[VIDEO_URL]"
```

4. The runner should:
   - load `config/runtime.local.toml` when present
   - try `youtube-transcript-api` first when the vendored venv is installed
   - keep `yt-dlp` plugins disabled unless a provider mode is explicitly configured
   - resolve project-root-relative `[paths]`
   - write subtitles to `[paths].output_dir`
   - append diagnostics into `[paths].log_file`
   - list available subtitles first
   - choose exactly one language by priority
   - download only that language in `srt`, then `vtt`, then best available subtitle format
   - print the engine used

Recommended steady-state auth order:

1. `provider-script`
2. `provider-http` on `127.0.0.1` only
3. `browser-cookies`

Recommended engine order:

1. `youtube-transcript-api`
2. `yt-dlp` with the configured auth mode

5. If the command succeeds, report:
   - saved file path
   - engine used: `youtube-transcript-api` or `yt-dlp`
   - selected subtitle language
   - whether subtitles were uploaded or auto-generated when visible from the output
   - output format: `srt`, `vtt`, or fallback subtitle format
   - filename pattern:
     `<safe video title> [<video id>].<language>.srt` for `youtube-transcript-api`
     `%(title).180B [%(id)s].%(ext)s` plus yt-dlp's subtitle language suffix for the `yt-dlp` path
6. If the command fails because subtitles do not exist, say so plainly and stop.
7. If the command fails because authentication is required and the current config uses no auth or provider auth, ask whether to temporarily retry with browser cookies from a specific browser such as `chrome`, `firefox`, `safari`, or `edge`.

## Cookie-Gated Retry

Use this only after the user explicitly approves local browser-cookie access for this task.

1. Update `config/runtime.local.toml` to `mode = "browser-cookies"` and set `browser = "<browser>"`, or pass an equivalent temporary config.
2. Re-run:

```bash
python3 scripts/run_youtube_transcribe.py --url "[VIDEO_URL]"
```

3. If the retry still fails with `HTTP Error 429`, explain that current yt-dlp guidance points to PO Token handling for YouTube subtitle requests and that browser cookies alone may not be sufficient.

## Provider Mode

Prefer `provider-script` as the safest low-maintenance steady-state mode on this workstation:

- no browser-cookie access on the normal path
- no localhost listener required
- fresh PO tokens can be minted for each request

Supporting files:

- `config/runtime.example.toml`
- `scripts/run_youtube_transcribe.py`
- `scripts/verify_provider_setup.sh`

## Output

- Preferred output format: `srt` when YouTube exposes it directly, otherwise `vtt`, otherwise the best available subtitle format
- Output location: `[paths].output_dir`
- Log location: `[paths].log_file`
- Preferred completion report:
  - absolute file path
  - engine used
  - subtitle language
  - source type: uploaded or auto-generated, when known
