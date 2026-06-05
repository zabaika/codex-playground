from __future__ import annotations

import json
from pathlib import Path


class SchemaContractService:
    def __init__(self, *, schemas_dir: Path) -> None:
        self._schemas_dir = schemas_dir

    def manifest(self) -> dict[str, object]:
        manifest_path = self._schemas_dir / "schema_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        schema_names = set()
        for item in manifest.get("schemas", []):
            if not isinstance(item, dict):
                raise ValueError("schema_manifest schemas entries must be objects")
            name = str(item.get("name") or "")
            relative_path = str(item.get("path") or "")
            version = str(item.get("version") or "")
            if not name or not relative_path or not version:
                raise ValueError("schema_manifest entries require name, path and version")
            if name in schema_names:
                raise ValueError(f"Duplicate schema manifest entry: {name}")
            schema_names.add(name)
            schema_path = self._schemas_dir / relative_path
            if not schema_path.is_file():
                raise ValueError(f"Schema manifest references missing schema: {relative_path}")
        return manifest
