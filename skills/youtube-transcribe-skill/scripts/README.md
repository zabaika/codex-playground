# Provider Setup Notes

Recommended steady-state setup on this workstation:

1. Vendor the reviewed yt-dlp PO token plugin into a dedicated local directory instead of installing it globally with `pip`.
2. Vendor the reviewed bgutil provider source into:

```text
~/.codex/skills/youtube-transcribe-skill/vendor/bgutil-provider
```
3. Prefer Deno script mode over Node build mode when possible.
4. Build or prepare the provider script:

```bash
cd ~/.codex/skills/youtube-transcribe-skill/vendor/bgutil-provider/server
deno install --allow-scripts=npm:canvas --frozen
```

5. Keep the skill config in `provider-script` mode and point `plugin_dir` and `script_path` at:

```text
~/.codex/skills/youtube-transcribe-skill/vendor/bgutil-plugin
```

For Deno mode the config should point to:

```text
~/.codex/skills/youtube-transcribe-skill/vendor/bgutil-provider/server/src/generate_once.ts
```

Use Node mode only as a fallback. Its config path would be:

```text
~/.codex/skills/youtube-transcribe-skill/vendor/bgutil-provider/server/build/generate_once.js
```

Why this is the safer default here:

- no direct browser-cookie access on the steady-state path
- no local HTTP listener required
- fresh PO tokens can be minted per request
- yt-dlp plugins are disabled outside explicit provider modes
- the plugin can stay isolated in a dedicated vendor folder instead of global Python paths

Tradeoff:

- slower than an always-on localhost provider
- still depends on reviewed third-party provider code
