# video-to-obsidian-kb

Local Codex skill for turning a YouTube or Vimeo video into linked Obsidian knowledge-base notes through a fail-closed transcript-first pipeline.

## Purpose

Use `video-to-obsidian-kb` when a YouTube or Vimeo URL should become vault notes, but only after a real transcript is fetched locally and prepared for the shared note-writing workflow.

The skill:

- accepts a YouTube or Vimeo URL
- reuses `video-transcribe-skill` to fetch subtitles or transcripts locally
- stages a cleaned markdown transcript under project-local `scratch/`
- leaves source understanding, vault search, and final route/output-shape selection to `article-to-obsidian-kb`
- reuses the sibling note workflow for search, update-vs-create, tags, validation, and final note writing
- stops honestly when no usable transcript can be fetched

## Source Of Truth

- Repository source of truth: `skills/video-to-obsidian-kb/`
- Installed Codex copy: `~/.codex/skills/video-to-obsidian-kb`

Edit the repository copy first. Reinstall into `~/.codex/skills` after changes.

## Installation

Install or refresh the local Codex copy with:

```bash
bash skills/video-to-obsidian-kb/install-local.sh
```

Treat this skill's `config/runtime.local.toml` as optional and wrapper-only. When it exists, use it for sibling-skill config pointers and staging/log overrides, not for duplicating transcript-engine settings or Obsidian note roots.

## Local Runtime Behavior

- prefers sibling local configs over duplicating machine-specific settings
- can use its own optional `config/runtime.local.toml` only for config pointers and staging overrides
- resolves sibling config pointers relative to this skill's config file so the same pointers work in both repo and installed copies
- uses `config/runtime.example.toml` as the canonical operator-facing reference for wrapper config keys and defaults
- treats sibling-skill outputs and logs as the canonical source for transcript engine and subtitle-selection metadata

## Main Files

- `SKILL.md`: runtime workflow entrypoint
- `config/runtime.example.toml`: optional local config shape
- `scripts/prepare_video_transcript.py`: transcript preparation helper

## Notes

- Keep this README operator-facing and brief.
- Keep transcript-preparation rules in this skill, and keep source understanding, routing, note writing, and final validation rules in the inherited `article-to-obsidian-kb` workflow.
