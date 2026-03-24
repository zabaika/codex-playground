# Third-party audit: bgutil-ytdlp-pot-provider

Reviewed artifact:

- Repo: `Brainicism/bgutil-ytdlp-pot-provider`
- Tag: `1.3.1`
- Commit: `7608dd51ee813b48cf9a6d68c6e42cb197ce10e0`

## What was reviewed

- `plugin/pyproject.toml`
- `plugin/yt_dlp_plugins/extractor/getpot_bgutil.py`
- `plugin/yt_dlp_plugins/extractor/getpot_bgutil_http.py`
- `plugin/yt_dlp_plugins/extractor/getpot_bgutil_script.py`
- `server/package.json`
- `server/package-lock.json`
- `server/deno.lock`
- `server/src/generate_once.ts`
- `server/src/main.ts`
- `server/src/session_manager.ts`
- `server/src/utils.ts`

## Findings

1. The Python yt-dlp plugin is lightweight and declares no Python package dependencies.
2. The server/script side has a much larger Node dependency surface, including `bgutils-js`, `youtubei.js`, `jsdom`, `axios`, `express`, `proxy-agent`, and `canvas`.
3. `npm audit --package-lock-only --omit=dev` on the pinned lockfile reported zero known vulnerabilities at review time.
4. `server/package-lock.json` still reports root version `1.2.2` while `package.json` is `1.3.1`. This looks like release hygiene drift rather than a direct exploit, but it lowers confidence.
5. HTTP server mode is not a safe default here because upstream binds to `::` and then `0.0.0.0` on fallback, not localhost-only.
6. The provider generates PO tokens by fetching challenge code from YouTube and executing it with `new Function(...)`. This is expected for the tool's purpose, but it is the dominant residual risk.
7. Two npm packages declare install scripts in the lockfile: `canvas` and `@swc/core`. The chosen install path avoids `@swc/core` entirely and does not install dev dependencies.

## Chosen mitigations

- Use `provider-script`, not `provider-http`
- Use a dedicated vendor directory under the installed skill
- Vendor the reviewed Python plugin files manually instead of global `pip` install
- Keep yt-dlp plugins disabled outside explicit provider modes
- Prefer Deno script mode so no TypeScript build toolchain is needed
- Install only runtime dependencies for the provider path
- Keep browser cookies out of the steady-state path

## Residual risk

The provider still executes challenge-related JavaScript obtained from YouTube. This is inherent to the current PO token mechanism and cannot be eliminated without abandoning this provider class entirely.
