#!/usr/bin/env python3

import argparse
import json
from pathlib import Path

import render_council_report


def main():
    parser = argparse.ArgumentParser(
        description="Normalize and save a canonical llm-council payload JSON."
    )
    parser.add_argument("input_json", help="Path to the raw council payload JSON")
    parser.add_argument("output_json", help="Path to the canonical output JSON")
    parser.add_argument(
        "--config-path",
        type=Path,
        help="Optional path to runtime.local.toml used to resolve `paths.temp_root`.",
    )
    args = parser.parse_args()

    input_path = Path(args.input_json)
    output_path = Path(args.output_json)
    raw = json.loads(input_path.read_text(encoding="utf-8"))
    render_council_report.write_canonical_payload(
        raw,
        output_path,
        config_path=args.config_path,
    )


if __name__ == "__main__":
    main()
