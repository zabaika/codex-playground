# Local Config

This skill is an orchestration layer and should reuse sibling skill configs by default instead of duplicating the same machine-specific values.

## Files

- Commit `config/runtime.example.toml`.
- Keep real machine-specific overrides in `config/runtime.local.toml` only when needed.
- Keep `runtime.local.toml` untracked.

## Recommended Default Sources

- Transcript settings: `../youtube-transcribe-skill/config/runtime.local.toml`
- Obsidian note roots: `../article-to-obsidian-kb/config/runtime.local.toml`

## Supported Local Keys

```toml
[skills]
youtube_transcribe_config = "../youtube-transcribe-skill/config/runtime.local.toml"
article_to_obsidian_config = "../article-to-obsidian-kb/config/runtime.local.toml"

[paths]
project_root = "/absolute/path/to/codex-playground"
prepared_transcripts_dir = "scratch/youtube-to-obsidian-kb"
log_file = "scratch/youtube-to-obsidian-kb.log"
```

## Usage Rules

- Resolve sibling config pointers relative to this skill config file.
- Resolve `prepared_transcripts_dir` and `log_file` from:
  1. `CODEX_PLAYGROUND_PROJECT_ROOT`
  2. `[paths].project_root`
  3. current working directory
- Use this skill config only for orchestration paths.
- Do not duplicate the actual transcript-engine settings or Obsidian note-root values here unless there is a strong reason and the user explicitly wants a separate override.
