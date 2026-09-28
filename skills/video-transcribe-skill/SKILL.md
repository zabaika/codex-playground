---
name: video-transcribe-skill
description: Extract subtitles or transcripts from YouTube, Vimeo, LinkedIn Events recordings or individual LinkedIn Learning lessons. Use a local subtitle-only workflow, select one language, and require explicit approval for browser-cookie access to authenticated recordings.
allowed-tools: Read, Write, Bash(which:*), Bash(python3:*), Bash(yt-dlp:*)
---

# Video Transcribe Skill

## Overview

Extract subtitles from a supported video URL into a local subtitle file. This local fork intentionally removes browser automation, chooses only one subtitle language based on priority order, prefers a dedicated PO token provider over direct browser-cookie access for YouTube, and converts downloaded `vtt` subtitles to `srt` when direct `srt` output is unavailable. For LinkedIn, follow the LinkedIn Recordings section below.

Input video URL: `$ARGUMENTS`

## Local Runtime Config

1. Load `config/runtime.local.toml` when it exists.
2. If no local config exists, fall back to the defaults documented in `config/runtime.example.toml`.
3. Resolve relative runtime paths from the project root:
   - `CODEX_PLAYGROUND_PROJECT_ROOT` when set
   - otherwise `[paths].project_root`
   - otherwise repo-root inference from the skill location
4. Never resolve project-local relative paths from the shell cwd.
5. Prefer a provider-based steady-state setup.
6. Never commit `runtime.local.toml`.
7. Prefer writing subtitles and logs into a project-local `scratch/` directory via `[paths]`.
8. Bootstrap and run only from this skill's tracked audited runtime inputs and the runnable local runtime they deterministically produce.

## Guardrails

1. Accept standard YouTube and Vimeo video URLs, LinkedIn `/events/<event>/` (including `/comments/`), and single `/learning/<course>/<lesson>` URLs. Reject LinkedIn profile, feed and course landing pages; do not process an entire course as one recording.
2. Before running anything, check `which yt-dlp`.
3. If `yt-dlp` is missing, stop and tell the user that this skill requires local `yt-dlp`. Do not fall back to browser automation, remote transcription APIs, or custom scripts.
4. Never download video or audio media when subtitles are enough. Always prefer subtitle-only extraction with `--skip-download`.
5. Never read browser cookies by default when provider mode is available.
6. If subtitle extraction fails because the video is age-gated, region-gated, or otherwise requires browser authentication, prefer the configured provider mode. Use `--cookies-from-browser=<browser>` only as an explicit fallback.
7. Never print cookie values, never inspect cookie databases manually, and never use account passwords in command arguments.
8. Save files only in the configured subtitle output directory.
9. Do not request multiple subtitle languages in one download. First detect available languages, then download only the first matching language from the priority list.
10. Do not replace or repair vendored runtime dependencies from the internet during ordinary install, refresh, or maintenance flows. Use only this skill's tracked audited vendor set.

## Default Workflow

1. Validate the URL.
2. Confirm `yt-dlp` is available with `which yt-dlp`.
3. For real supported-host subtitle extraction, request to run the local runner outside the sandbox immediately instead of first waiting for an in-sandbox DNS or HTTPS failure.
   - Treat network access to supported hosts as the normal path for this skill, not as an exceptional fallback.
   - Do not burn a full failed attempt inside the sandbox just to rediscover that public video-host resolution is blocked there.
   - If the user declines the outside-sandbox run, stop honestly and report that the transcript pipeline cannot continue under the current network restrictions.
4. Prefer the local runner:

```bash
python3 scripts/run_video_transcribe.py --url "[VIDEO_URL]"
```

5. The runner should:
   - load `config/runtime.local.toml` when present
   - detect whether the URL is YouTube, Vimeo or LinkedIn
   - try `youtube-transcript-api` first only for YouTube when the vendored venv is installed
   - if the first engine does not return subtitles, still attempt the `yt-dlp` path before stopping
   - skip `youtube-transcript-api` entirely for Vimeo and LinkedIn and use `yt-dlp` directly
   - keep `yt-dlp` plugins disabled unless a provider mode is explicitly configured
   - resolve project-root-relative `[paths]`
   - write subtitles to `[paths].output_dir`
   - append diagnostics into `[paths].log_file`
   - retry temporary network, timeout, and rate-limit failures with bounded backoff
   - list available subtitles first
   - choose exactly one language by priority
   - download only that language in `srt`, then `vtt`, then best available subtitle format
   - if the downloaded subtitle file is `vtt`, convert it to sibling `srt` and report the final `srt` path
   - print the engine used

Recommended steady-state auth order:

1. `provider-script`
2. `provider-http` on `127.0.0.1` only
3. `browser-cookies`

Recommended engine order:

1. `youtube-transcript-api`
2. `yt-dlp` with the configured auth mode for YouTube
3. `yt-dlp` direct host support for Vimeo

6. If the command succeeds, report:
   - saved file path
   - engine used: `youtube-transcript-api` or `yt-dlp`
   - platform used: `youtube`, `vimeo` or `linkedin`
   - selected subtitle language
   - whether subtitles were uploaded or auto-generated when visible from the output
   - output format: final reported format after conversion, preferably `srt`
   - filename pattern:
     `<safe video title> [<video id>].<language>.srt` for `youtube-transcript-api`
     `%(title).180B [%(id)s].%(ext)s` plus yt-dlp's subtitle language suffix for the `yt-dlp` path
7. If the command fails because subtitles do not exist, say so plainly and stop.
8. Do not treat a `youtube-transcript-api` no-subtitles result as final. Attempt the `yt-dlp` path too, because the engines can disagree on subtitle availability.
9. If the command fails because authentication is required and the current config uses no auth or provider auth, ask whether to temporarily retry with browser cookies from a specific browser such as `chrome`, `firefox`, `safari`, or `edge`.
10. If the command fails because of a temporary DNS, timeout, connection, or rate-limit issue, retry automatically according to `[retry]` and then stop with a concise final error if all attempts fail.

## Cookie-Gated Retry

Use this only after the user explicitly approves local browser-cookie access for this task.

1. Pass `--cookies-from-browser <approved-browser>` for this run. Do not change persistent auth config for a one-task retry.
2. Re-run:

```bash
python3 scripts/run_video_transcribe.py --url "[VIDEO_URL]" --cookies-from-browser chrome
```

3. If the retry still fails with `HTTP Error 429`, explain that current yt-dlp guidance points to PO Token handling for YouTube subtitle requests and that browser cookies alone may not be sufficient.

## LinkedIn Recordings

- Use the installed `yt-dlp` LinkedIn Events or Learning extractor directly. Check `yt-dlp --ignore-config --list-extractors` if support is uncertain; do not upgrade audited dependencies automatically.
- Events and Learning generally require a logged-in session. An unauthenticated failure is not evidence that captions do not exist. After that failure, ask once for permission to use cookies from the specific browser where the user is logged in, unless already approved for this task.
- Run `python3 scripts/run_video_transcribe.py --url "[LINKEDIN_URL]" --cookies-from-browser chrome` only after that approval (substitute the approved browser).
- The LinkedIn route ignores ambient yt-dlp config, disables plugins, and ignores persistent YouTube auth settings. Browser cookies are opt-in per run and are not exported to a file.
- Extract only available subtitles/transcript data, one language at a time. Do not download media or invoke speech recognition when captions are absent. Distinguish authentication failure, extractor failure, and an accessible recording with no exposed captions.
- Learning requires a single lesson URL and account access to that lesson. Do not bypass subscription or access restrictions. A similarly named course is not automatically the same recording.
- Never output raw provider responses, cookie values, signed media URLs or cookie-store paths. Keep query strings out of LinkedIn provenance logs.
- If the request is transcript-only, finish with the extracted artifact or the precise blocker; do not start Obsidian note generation.

## Provider Mode

Prefer `provider-script` as the safest low-maintenance steady-state mode on this workstation:

- no browser-cookie access on the normal path
- no localhost listener required
- fresh PO tokens can be minted for each request

Supporting files:

- `config/runtime.example.toml`
- `scripts/run_video_transcribe.py`
- `scripts/verify_provider_setup.sh`

## Output

- Preferred output format: `srt`; when the host exposes only `vtt`, download `vtt` and convert it locally to `srt`
- Output location: `[paths].output_dir`
- Log location: `[paths].log_file`
- Preferred completion report:
  - absolute file path
  - engine used
  - platform used
  - subtitle language
  - source type: uploaded or auto-generated, when known
- Preferred failure reporting:
  - concise no-subtitles message when the video has no captions
  - both engines attempted before a final no-subtitles conclusion when the first engine fails to fetch subtitles
  - bounded automatic retries for temporary failures
  - compact log entries without raw stack traces or full yt-dlp dumps
