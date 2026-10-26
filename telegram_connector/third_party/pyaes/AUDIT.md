# Reviewed PyAES wheel

This is a packaging input for Telethon's source-only PyAES dependency. Normal
installation consumes the repository wheel; it never builds PyAES from source.
The library version remains the one already used by the existing environments.

## Owners and provenance

- The dependency pin belongs to `../../requirements.txt`.
- The wheel filename and authoritative SHA-256 belong to [index.html](./index.html).
  Pip verifies its link hash during a cold dependency plan and installation.
- [origin.json](./origin.json) records the official source URL and SHA-256.
  The matching [PyPI release](https://pypi.org/project/pyaes/1.6.1/#files)
  supplies only an sdist.
- The reviewed wheel was already built and checked during the Python migration.
  Its `WHEEL` metadata records `setuptools (82.0.1)`, pure Python and
  `py3-none-any`. Its Python files match the official sdist byte-for-byte.
  The MIT license is embedded in the wheel.

Verification on 2026-10-26 compared the previously reviewed wheel hash with the
migration record, verified the downloaded official sdist hash and compared every
`pyaes/*.py` file. No Python source, wheel metadata or dependency version changed.

## Reproduction from a checkout

Keep this directory with `requirements.txt`. A complete checkout already
contains the required wheel, index and hash; pip resolves the relative index
from the requirements file even when called from another working directory.
No pip cache, previously installed venv or `PIP_FIND_LINKS` is required for PyAES.

From the repository root, use `bash common/setup_python.sh tests` or the normal
`bash telegram_connector/scripts/install_launch_agent.sh`. Other dependencies
still use the existing requirements and operator constraints. This directory
does not claim to be a complete offline wheelhouse for every dependency.

The release source can be obtained again from the URL in `origin.json` and
verified against its recorded hash. A new build produces a new reviewed input;
ZIP metadata can differ even with identical Python sources. Replacing the wheel,
source, pin or index hash requires a separate review. Do not rebuild or fetch a
replacement automatically during normal installation.
