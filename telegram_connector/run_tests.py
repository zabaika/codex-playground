#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path


def main() -> int:
    app_dir = Path(__file__).resolve().parent
    repo_root = app_dir.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    tests_dir = app_dir / "tests"
    suite = unittest.defaultTestLoader.discover(str(tests_dir))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
