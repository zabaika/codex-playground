# Skills

Local Codex skills that extend workspace-specific workflows.

## Available Skills

- [article-to-obsidian-kb](./article-to-obsidian-kb/SKILL.md)  
  Converts an engineering article URL into compact Russian-language Obsidian knowledge-base notes, updates overlapping notes instead of creating duplicates, and maintains wikilink connections between article notes and concept notes.
- [youtube-transcribe-skill](./youtube-transcribe-skill/SKILL.md)  
  Local sanitized fork of a third-party YouTube transcript skill. Tries `youtube-transcript-api` first, falls back to a reviewed `yt-dlp` provider path, writes into project-local `scratch/`, removes browser automation, and requires explicit approval before any cookie-based retry.

## article-to-obsidian-kb

The skill is designed for a vault-backed knowledge workflow rather than for generic note generation.

What it does:

- reads a source article URL
- builds an internal concept map with engineering concepts, non-obvious insights, operating-model details, and reusable lessons
- searches existing Obsidian note roots before drafting anything
- prefers updating matching notes over creating duplicates
- writes article notes and concept notes in Russian
- keeps technical terms in English only when they are the stable industry form
- normalizes tags into canonical English forms
- preserves concrete operational detail instead of flattening everything into generic summaries

Local runtime behavior:

- loads local vault roots from [config/runtime.local.toml](./article-to-obsidian-kb/config/runtime.local.toml)
- keeps the repo copy of `config/runtime.local.toml` as the single editable local config, with the installed Codex skill expected to point at the same file
- uses separate note roots for article-derived notes and concept notes
- depends on local-only paths and therefore must not commit machine-specific roots or secrets

Supporting references:

- [local-config.md](./article-to-obsidian-kb/references/local-config.md)
- [vault-conventions.md](./article-to-obsidian-kb/references/vault-conventions.md)
- [language-normalization.md](./article-to-obsidian-kb/references/language-normalization.md)
- [update-patterns.md](./article-to-obsidian-kb/references/update-patterns.md)

## youtube-transcribe-skill

The skill is designed for a local, fail-closed YouTube subtitle workflow rather than for generic speech-to-text transcription.

What it does:

- accepts a YouTube URL
- tries `youtube-transcript-api` first for the safer low-complexity path
- falls back to the reviewed `yt-dlp` provider path when the first path cannot fetch subtitles
- chooses only one subtitle language based on configured priority
- saves subtitles as local `.srt` files with readable filenames
- reports which engine handled the request and which subtitle language was selected
- keeps browser-cookie access out of the default path

Local runtime behavior:

- loads local runtime settings from [config/runtime.local.toml](./youtube-transcribe-skill/config/runtime.local.toml)
- keeps the repo copy of `config/runtime.local.toml` as the single editable local config, with the installed Codex skill expected to point at the same file
- resolves project-local output and log paths through `[paths]`, usually into `scratch/`
- keeps one append-only log file for normal operation instead of per-run log fan-out
- uses `youtube-transcript-api` as the preferred engine and a reviewed `yt-dlp` provider as fallback
- treats `browser-cookies` as an explicit fallback mode, not as the normal setup

Provider setup notes:

- the reviewed bgutil plugin and provider are meant to stay in the installed Codex skill under `~/.codex/skills/youtube-transcribe-skill/vendor/`
- the recommended steady-state mode is `provider-script`
- Deno script mode is preferred over Node build mode
- the provider path stays isolated from the normal `youtube-transcript-api` first path and is used only as fallback

Supporting references:

- [SKILL.md](./youtube-transcribe-skill/SKILL.md)
- [config/runtime.example.toml](./youtube-transcribe-skill/config/runtime.example.toml)
- [SECURITY_REVIEW.md](./youtube-transcribe-skill/SECURITY_REVIEW.md)
- [THIRD_PARTY_AUDIT.md](./youtube-transcribe-skill/THIRD_PARTY_AUDIT.md)
- [THIRD_PARTY_AUDIT_YOUTUBE_TRANSCRIPT_API.md](./youtube-transcribe-skill/THIRD_PARTY_AUDIT_YOUTUBE_TRANSCRIPT_API.md)

## Notes

- Skills in this folder are workspace extensions rather than standalone applications.
- Add new skill links here when new skill folders are added.
- Third-party skills should be reviewed and, when needed, narrowed before installation into `~/.codex/skills`.
