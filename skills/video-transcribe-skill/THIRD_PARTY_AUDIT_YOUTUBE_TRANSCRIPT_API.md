# Third-party audit: youtube-transcript-api

Reviewed artifact:

- Package: `youtube-transcript-api`
- Version: `1.2.4`

## What was reviewed

- PyPI project metadata
- wheel metadata for `youtube-transcript-api==1.2.4`
- wheel metadata for direct dependency `requests==2.32.5`
- package source files:
  - `_api.py`
  - `_transcripts.py`
  - `_errors.py`
  - `formatters.py`

## Findings

1. The package is materially smaller and simpler than the bgutil provider path.
2. Direct dependencies are only `requests` and `defusedxml`.
3. It explicitly avoids headless browser requirements.
4. It works without authentication for common public videos.
5. Cookie auth is currently disabled upstream, so it is not a solution for age-restricted videos.
6. The package can still fail with `RequestBlocked` / `IpBlocked`, so it should be treated as the safe first path, not the only path.

## Chosen role in this skill

- first attempt for public videos
- safer low-complexity path
- fallback to the reviewed bgutil provider when it fails
