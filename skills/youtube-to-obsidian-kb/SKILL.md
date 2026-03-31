---
name: youtube-to-obsidian-kb
description: Convert a YouTube video URL into linked Obsidian knowledge-base notes by first fetching a local transcript through youtube-transcribe-skill and then applying the article-to-obsidian-kb note workflow. Stop honestly when no transcript can be fetched.
---

# YouTube To Obsidian KB

## Overview

Turn a YouTube URL into compact, Russian-language Obsidian knowledge-base notes with a fail-closed pipeline:

1. fetch the transcript through the local `youtube-transcribe-skill`
2. stage a cleaned markdown transcript locally
3. apply the same vault-search, update-vs-create, tag-normalization, and note-writing rules used by `article-to-obsidian-kb`

Input YouTube URL: `$ARGUMENTS`

Do not draft notes from the video title, thumbnail, or short description alone. If the transcript step fails, stop and report that the pipeline could not continue.

## Local Runtime Config

1. Load [references/local-config.md](references/local-config.md) before running the pipeline.
2. Prefer the existing local configs from sibling skills instead of duplicating machine-specific values:
   - in this skill's `config/runtime.local.toml`, point `youtube_transcribe_config` to `../../youtube-transcribe-skill/config/runtime.local.toml`
   - in this skill's `config/runtime.local.toml`, point `article_to_obsidian_config` to `../../article-to-obsidian-kb/config/runtime.local.toml`
3. Treat this skill's own `config/runtime.local.toml` as optional.
4. If this skill has a local config, use it only for:
   - pointing at the sibling skill configs
   - overriding the project-root-relative staging/log paths
5. Never copy note-root values or transcript-provider settings into tracked files.
6. If the sibling transcript config is missing, stop and tell the user that transcript extraction is not configured.
7. If the sibling article config is missing or lacks `note_roots.article` and `note_roots.concept`, stop and tell the user that the Obsidian roots are not configured.

## Default Workflow

1. Validate that the input is a standard YouTube URL.
2. Run the local helper:

```bash
python3 scripts/prepare_youtube_transcript.py --url "[VIDEO_URL]"
```

3. The helper must:
   - reuse `youtube-transcribe-skill/scripts/run_youtube_transcribe.py`
   - reuse the sibling local config for transcript settings unless this skill explicitly overrides the path
   - save or reuse the subtitle file produced by the transcript skill
   - create a cleaned markdown transcript under a project-local `scratch/` path
   - detect the `article-to-obsidian-kb` route for the prepared transcript
   - let `detect_source_route.py` print the chosen route immediately when it is detected
   - print the prepared transcript path, subtitle path, engine used, and selected subtitle language without repeating the route block
4. If transcript extraction fails or no subtitle file path is reported, stop.
5. Do not retry with browser cookies unless the user explicitly approves that path for this task.
6. After the helper succeeds, read the prepared markdown transcript as the source text.
7. Then load the sibling article workflow in this order:
   - `../article-to-obsidian-kb/SKILL.md`
   - `../article-to-obsidian-kb/references/local-config.md`
   - `../article-to-obsidian-kb/scripts/detect_source_route.py`
   - `../article-to-obsidian-kb/references/vault-conventions.md`
   - `../article-to-obsidian-kb/references/language-normalization.md`
   - `../article-to-obsidian-kb/references/update-patterns.md`
8. Apply the `article-to-obsidian-kb` workflow to the prepared transcript, not to the raw subtitle file.
   - inherit the sibling workflow completely, not partially
   - do not stop after note drafting or note updates if the sibling workflow still requires final validation passes
   - when `article-to-obsidian-kb` requires `note-compliance pass` and `regression-sweep pass`, execute both of them for every touched note in this wrapper flow too
   - do not treat the YouTube wrapper as a shortcut path that may skip final contract validation because the source came from a transcript
9. Search the configured vault roots before drafting anything and prefer updates over duplicate notes.
10. Reuse the sibling `article-to-obsidian-kb` final-output contract verbatim.
   - do not redefine the report block names, ordering, or inclusion rules locally
   - do not keep a second summary schema in this skill for created vs updated notes
   - if `article-to-obsidian-kb` changes its final output format later, follow that source of truth instead of preserving an older local copy here
11. Inherit the sibling workflow's final validation contract, not only its report shape.
   - if `article-to-obsidian-kb` adds or tightens full-note validation, regression sweeps, note-contract checks, or similar final quality gates later, this wrapper must follow those gates automatically
   - do not re-specify a narrower local validation subset here
   - if there is any conflict between this wrapper and the sibling note workflow, prefer the deeper sibling workflow as the source of truth for note validation

## Transcript-Specific Guidance

- Treat the prepared transcript as a noisy spoken source, not as polished prose.
- Ignore obvious intro fluff, sponsor reads, repeated captions, outro calls to action, and caption artifacts when building the concept map.
- Preserve concrete operational detail, named systems, metrics, process steps, org boundaries, rollout mechanics, and constraints when they appear in the transcript.
- If a transcript segment is ambiguous because of caption quality, keep the note conservative instead of inventing missing detail.
- If the video does not yield enough substance for reusable notes after transcript cleanup, say so briefly and stop instead of manufacturing concepts.

## Final Output

- Follow the `## Final Output` section from `../article-to-obsidian-kb/SKILL.md` as the canonical report format.
- After that inherited report, mention the prepared transcript file and subtitle file that were used as source artifacts.
- If no transcript was obtained, report that the pipeline stopped before note generation.
