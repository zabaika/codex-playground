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

## charset-normalizer native wheel revision (2026-11-03)

Replaces the previous pure-Python `charset-normalizer==3.4.6` wheel with the
unmodified official `charset-normalizer==3.5.2` wheel. This revision was verified
in an isolated installation, then installed into the working Codex runtime
through the canonical installer on 2026-11-03. Actual-runtime checks confirm the
version, native imports, decoding and fixture transcript parsing. Configuration,
existing data and source/install parity were verified after installation.

- Wheel: `charset_normalizer-3.5.2-cp315-cp315-macosx_10_15_universal2.whl`.
- Wheel SHA256:
  `87475fabc8d9996fd9c27debb395e642e8c838d78a00b6e932227a0e06b81e26`.
- Official source archive SHA256:
  `39de2a259fc954455c57274dc94c79d5842774e1247a016aff30bc0efed0f4ef`.
- PyPI metadata permits Python `>=3.7`; the selected wheel specifically requires
  CPython 3.15, its regular ABI, and macOS 10.15+ universal2. It is not an
  arbitrary-platform or free-threaded wheel.
- Verified every wheel RECORD hash and size, and byte identity of all packaged
  Python sources against the official source archive.
- Reviewed release notes, public import surface, codec loading and source-diff
  imports. Version 3.5 changes detection heuristics, caches and optional native
  compilation from mypyc to Cython; it is not merely a packaging substitution.
- Both `md` and `cd` native modules contain ARM64 and x86_64 slices. Their Mach-O
  dependencies reference only the system `libSystem.B.dylib`. Native binary
  correctness is assessed through provenance and execution, not a binary audit.
- Six other wheels retain their exact previous bytes, including the locally
  identified transcript API metadata patch below.
- The new archive is named separately; the previous audited archive remains
  available. Manifest and lockfile identify the new archive and wheel hashes.
  Normal installation remains offline and requires hashes.

Verification on ARM64 CPython 3.15.2:

- 300 upstream tests passed against the extracted official native wheel.
- 24 skill regression tests passed, including offline bootstrap, dependency
  preflight, rollback, fixture transcript parsing, runner and path behavior.
- 8 video-to-Obsidian wrapper tests passed; skill frontmatter validation passed.
- A separate canonical installer run prepared an isolated Codex installation.
  Both compiled modules load from that installation. UTF-8, UTF-16 and CP1251
  round-trip detection and Requests decoding pass. Explicit CP1252 decoding
  passes; unknown-encoding detection remains heuristic.
- A short Spanish CP1252 probe is misdetected as CP1250 by both 3.4.6 and 3.5.2,
  changing `ñ` to `ń`. This is a reproduced baseline limitation, not evidence of
  a new regression. Perfect encoding detection and performance parity are not
  claimed. No real video-host or browser-cookie request was used.

Sources: [official release files](https://pypi.org/project/charset-normalizer/3.5.2/#files)
and the `CHANGELOG.md` included in the official source archive.

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
