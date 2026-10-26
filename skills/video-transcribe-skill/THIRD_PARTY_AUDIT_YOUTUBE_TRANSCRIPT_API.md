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
- installed only from the audited local wheel-set archive declared in `config/vendor-manifest.toml`
- governed by the canonical reviewed lockfile in `third_party/youtube-transcript-api/requirements.lock`

## Python 3.15 wheel-set revision (2026-10-25)

The upstream Python sources remain unchanged. For `charset-normalizer==3.4.6`,
the CPython 3.14 universal2 wheel is replaced by
the official `charset_normalizer-3.4.6-py3-none-any.whl` from PyPI.

- SHA256: `947cf925bc916d90adba35a64c82aace04fa39b46b52d4630ece166655905a69`.
- Wheel metadata: `Requires-Python: >=3.7`, tag `py3-none-any`.
- The replacement has no native extension modules. Optional compiled
  acceleration is removed; performance equivalence is not claimed.
- Shared Python source files were compared with the previously reviewed
  CPython 3.14 wheel; their contents are unchanged.
- Five other dependency wheels retain their original bytes and versions.
- The manifest archive digest and locked distribution hash identify the
  replacement. Normal installation remains offline with required hashes.

Sources: [PyPI release files](https://pypi.org/project/charset-normalizer/3.4.6/#files)
and [wheel compatibility tags](https://packaging.python.org/en/latest/specifications/platform-compatibility-tags/).
Installation and runtime compatibility are checked separately by the local
install-contract and runner regression suites.

### Locally identified youtube-transcript-api metadata patch

The latest official `youtube-transcript-api==1.2.4` declares
`Requires-Python: >=3.8,<3.15`. The user explicitly approved a local compatibility
build for Python 3.15 rather than retaining Python 3.14.

- Local version: `1.2.4+py315`; this is not an upstream release.
- Original wheel SHA256:
  `03878759356da5caf5edac77431780b91448fb3d8c21d4496015bdc8a7bc43ff`.
- Local wheel SHA256:
  `fbd1ddc05a91283f842064163ab4ecacf24d26a089036a21b699570a0e343ce8`.
- Only distribution metadata and RECORD change: the version, dist-info path,
  and `Requires-Python: >=3.15,<3.16`. RECORD hashes are regenerated.
- Every Python source file is byte-identical to the official wheel. Network,
  parsing, retry and authentication behavior are not modified by the patch.
- Compatibility is limited to the local tested runtime. Upstream support for
  Python 3.15 is not claimed. Offline parser and runner tests remain required.
