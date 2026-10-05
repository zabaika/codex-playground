"""Durable, atomic JSON replacement for project-owned state and audit files."""

from __future__ import annotations

from contextlib import suppress
import json
import os
from pathlib import Path
import tempfile


def write_json_atomic(path: Path, payload: object, *, sort_keys: bool = False) -> None:
    """Replace a JSON file using a private temporary file on the same filesystem.

    Propagate serialization and IO errors. If directory fsync fails after
    replacement, the new file is already visible; this is not a rollback API.
    """
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=sort_keys) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temporary = Path(name)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
