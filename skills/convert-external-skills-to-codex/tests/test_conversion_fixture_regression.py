#!/usr/bin/env python3
"""Regression tests for converted output fixtures."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from check_conversion_fixtures import check_all


class ConversionFixtureRegressionTest(unittest.TestCase):
    def test_all_fixtures_pass(self) -> None:
        violations = check_all()
        if violations:
            rendered = "\n".join(f"{v.code}: {v.message}" for v in violations)
            self.fail(rendered)


if __name__ == "__main__":
    unittest.main()
