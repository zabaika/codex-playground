# Skills

Local Codex skills that extend workspace-specific workflows.

## Available Skills

- [article-to-obsidian-kb](./article-to-obsidian-kb/SKILL.md)  
  Converts an article URL, transcript, or other long-form source into compact Russian-language Obsidian knowledge-base notes, routes the source through an engineering or general analysis pass, updates overlapping notes instead of creating duplicates, and maintains wikilink connections between source-derived notes and concept notes.
- [youtube-to-obsidian-kb](./youtube-to-obsidian-kb/SKILL.md)  
  Converts a YouTube URL into linked Obsidian knowledge-base notes by first extracting a local transcript through `youtube-transcribe-skill`, then applying the `article-to-obsidian-kb` vault workflow, and stopping honestly when transcript extraction fails.
- [youtube-transcribe-skill](./youtube-transcribe-skill/SKILL.md)  
  Local sanitized fork of a third-party YouTube transcript skill. Tries `youtube-transcript-api` first, falls back to a reviewed `yt-dlp` provider path, writes into project-local `scratch/`, removes browser automation, and requires explicit approval before any cookie-based retry.

## article-to-obsidian-kb

The skill is designed for a vault-backed knowledge workflow rather than for generic note generation. It now separates source analysis from vault writing: one routing step chooses the right extraction prompt, while one shared Obsidian workflow applies the same note rules to every source.

What it does:

- reads a source URL or provided long-form text
- routes the source through either an engineering analysis path or a general source-analysis path
- builds an internal extraction with reusable concepts, non-obvious insights, lessons, general high-signal summaries, and operating-model details when they are really present
- searches existing Obsidian note roots before drafting anything
- prefers updating matching notes over creating duplicates
- writes source-derived notes of type `lessons`, `general`, or `operating-model`, plus concept notes in Russian
- keeps technical terms in English only when they are the stable industry form
- normalizes tags into canonical English forms and treats brand-new tags as a last resort
- keeps one shared rule set for titles, tags, vault search, language cleanup, and update-vs-create regardless of the chosen analysis route
- reports the chosen route in the final response with a short reason, without writing that trace into the saved Obsidian notes
- preserves concrete operational detail instead of flattening everything into generic summaries

Local runtime behavior:

- loads local vault roots from [config/runtime.local.toml](./article-to-obsidian-kb/config/runtime.local.toml)
- keeps the repo copy of `config/runtime.local.toml` as the single editable local config, with the installed Codex skill expected to point at the same file
- uses separate note roots for source-derived notes and concept notes
- depends on local-only paths and therefore must not commit machine-specific roots or secrets

Supporting references:

- [local-config.md](./article-to-obsidian-kb/references/local-config.md)
- [vault-conventions.md](./article-to-obsidian-kb/references/vault-conventions.md)
- [language-normalization.md](./article-to-obsidian-kb/references/language-normalization.md)
- [update-patterns.md](./article-to-obsidian-kb/references/update-patterns.md)
- [source-analysis-engineering.md](./article-to-obsidian-kb/references/source-analysis-engineering.md)
- [source-analysis-general.md](./article-to-obsidian-kb/references/source-analysis-general.md)

## youtube-transcribe-skill

The skill is designed for a local, fail-closed YouTube subtitle workflow rather than for generic speech-to-text transcription.

What it does:

- accepts a YouTube URL
- tries `youtube-transcript-api` first for the safer low-complexity path
- still attempts the reviewed `yt-dlp` provider path when the first engine does not return subtitles
- chooses only one subtitle language based on configured priority
- saves subtitles as local `.srt` files with readable filenames
- reports which engine handled the request and which subtitle language was selected
- keeps browser-cookie access out of the default path

Local runtime behavior:

- loads local runtime settings from [config/runtime.local.toml](./youtube-transcribe-skill/config/runtime.local.toml)
- keeps the repo copy of `config/runtime.local.toml` as the single editable local config, with the installed Codex skill expected to point at the same file
- resolves project-local output and log paths through `[paths]`, usually into `scratch/`
- keeps one append-only log file for normal operation instead of per-run log fan-out
- uses `youtube-transcript-api` as the preferred engine and a reviewed `yt-dlp` provider as a second extraction attempt
- retries temporary DNS, connection, timeout, and rate-limit failures with bounded backoff
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

## youtube-to-obsidian-kb

The skill is designed as a fail-closed orchestration layer between transcript extraction and the existing vault-writing workflow.

What it does:

- accepts a YouTube URL
- reuses `youtube-transcribe-skill` to fetch subtitles or transcripts locally
- stages a cleaned markdown transcript in project-local `scratch/`
- reuses the `article-to-obsidian-kb` vault workflow for search, update-vs-create, tags, and note writing
- stops honestly when no transcript can be fetched or the cleaned transcript has no usable content

Local runtime behavior:

- prefers the existing sibling local configs instead of duplicating machine-specific settings
- can optionally load its own `config/runtime.local.toml` only for config pointers and staging/log path overrides
- keeps transcript staging and logs project-local through `[paths]`
- validates that the sibling article skill still has both required Obsidian note roots before note generation

Supporting references:

- [SKILL.md](./youtube-to-obsidian-kb/SKILL.md)
- [config/runtime.example.toml](./youtube-to-obsidian-kb/config/runtime.example.toml)
- [references/local-config.md](./youtube-to-obsidian-kb/references/local-config.md)

## Notes

- Skills in this folder are workspace extensions rather than standalone applications.
- Add new skill links here when new skill folders are added.
- Third-party skills should be reviewed and, when needed, narrowed before installation into `~/.codex/skills`.
