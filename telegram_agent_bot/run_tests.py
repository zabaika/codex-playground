#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent


def main() -> int:
    return subprocess.call([sys.executable, "-m", "pytest", str(APP_DIR / "tests"), "-q"])


if __name__ == "__main__":
    raise SystemExit(main())
