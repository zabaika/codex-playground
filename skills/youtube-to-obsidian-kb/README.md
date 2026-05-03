# youtube-to-obsidian-kb

Local Codex skill for turning a YouTube video into linked Obsidian knowledge-base notes through a fail-closed transcript-first pipeline.

## Purpose

Use `youtube-to-obsidian-kb` when a YouTube URL should become vault notes, but only after a real transcript is fetched locally and prepared for the shared note-writing workflow.

The skill:

- accepts a YouTube URL
- reuses `youtube-transcribe-skill` to fetch subtitles or transcripts locally
- stages a cleaned markdown transcript under project-local `scratch/`
- detects the shared `article-to-obsidian-kb` route before note generation
- reuses the sibling note workflow for search, update-vs-create, tags, validation, and final note writing
- stops honestly when no usable transcript can be fetched

## Source Of Truth

- Repository source of truth: `skills/youtube-to-obsidian-kb/`
- Installed Codex copy: `~/.codex/skills/youtube-to-obsidian-kb`

Edit the repository copy first. Reinstall into `~/.codex/skills` after changes.

## Installation

Install or refresh the local Codex copy with:

```bash
bash skills/youtube-to-obsidian-kb/install-local.sh
```

## Local Runtime Behavior

- prefers sibling local configs over duplicating machine-specific settings
- can use its own optional `config/runtime.local.toml` only for config pointers and staging overrides
- resolves sibling config pointers relative to this skill's config file so the same pointers work in both repo and installed copies
- treats sibling-skill outputs and logs as the canonical source for transcript engine and subtitle-selection metadata

## Main Files

- `SKILL.md`: runtime workflow entrypoint
- `config/runtime.example.toml`: optional local config shape
- `scripts/prepare_youtube_transcript.py`: transcript preparation helper

## References

- `references/local-config.md`: explains how this wrapper points at sibling skill configs and how local staging overrides work

## Notes

- Keep this README operator-facing and brief.
- Keep transcript, routing, and final validation rules in `SKILL.md` and inherited sibling references.
