"""KB index project package."""

from pathlib import Path
import sys


def _ensure_common_repo_root_on_path() -> None:
    for parent in Path(__file__).resolve().parents:
        if (parent / "common").is_dir():
            parent_text = str(parent)
            if parent_text not in sys.path:
                sys.path.insert(0, parent_text)
            return


_ensure_common_repo_root_on_path()

__all__ = ["__version__"]
__version__ = "0.1.0"
