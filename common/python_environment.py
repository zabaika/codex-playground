"""Private environment worker, supervised by python_runtime_prepare in shell."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import tempfile

COMMON_ROOT = Path(__file__).resolve().parent
if str(COMMON_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(COMMON_ROOT.parent))

from common import python_runtime as runtime
from common.json_io import write_json_atomic


@contextmanager
def recoverable_backup(parent: Path, *, prefix=".python-previous-"):
    directory = Path(tempfile.mkdtemp(prefix=prefix, dir=parent))
    previous = directory / "runtime"
    try:
        yield previous
    except BaseException:
        # Never discard the only previous environment after a failed rollback.
        if not previous.exists():
            shutil.rmtree(directory)
        raise
    else:
        shutil.rmtree(directory)


def _checked(command: list[str], description: str) -> None:
    result = runtime._run(command)
    if result.returncode or result.stderr.strip():
        raise runtime.RuntimeSelectionError(description)


def _health(
    binding: dict, requirements: Path | None, scratch: Path,
    imports: tuple[str, ...], constraints: Path | None,
) -> None:
    if requirements is not None:
        runtime.validate_requirements(
            binding, requirements, scratch, constraints=constraints,
            use_installed=True,
        )
    _checked([binding["executable"], "-I", "-m", "pip", "check"],
             "Prepared Python has broken requirements")
    runtime.check_installed_python(binding, scratch)
    if imports:
        _checked([
            binding["executable"], "-I", "-c",
            "import importlib,json,sys; "
            "[importlib.import_module(n) for n in json.loads(sys.argv[1])]",
            json.dumps(imports),
        ], "Prepared Python import check failed")


def _observation(base: dict, target: Path, requirements: Path | None) -> dict:
    current = runtime.probe_python(str(target / "bin/python"))
    if current["base_executable"] != base["base_executable"]:
        raise runtime.RuntimeSelectionError("Prepared venv base identity differs")
    current["source"] = base.get("source", "explicit")
    current["policy_digest"] = hashlib.sha256(
        runtime.POLICY_PATH.read_bytes(),
    ).hexdigest()
    current["dependency_inputs"] = {}
    if requirements is not None:
        current["dependency_inputs"][requirements.name] = hashlib.sha256(
            requirements.read_bytes(),
        ).hexdigest()
    return current


def _preflight_venv(
    base: dict, target: Path, *, requirements: Path | None = None,
    constraints: Path | None = None, wheels: Path | None = None,
    imports: tuple[str, ...] = (), scratch: Path | None = None,
) -> dict | None:
    """Check reuse or a fresh dependency plan without changing the target."""
    runtime.validate_descriptor(base, runtime.load_policy())
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        raise runtime.RuntimeSelectionError("Venv target is not an owned directory")
    scratch = scratch or target.parent / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    if target.exists():
        try:
            current = _observation(base, target, requirements)
            if any(current[key] != base[key] for key in
                   ("version", "version_text", "soabi", "architecture", "free_threaded")):
                raise runtime.RuntimeSelectionError("Venv identity differs")
            record = target / ".python-runtime.json"
            if record.exists():
                saved = json.loads(record.read_text())
                if any(saved.get(key) != current[key] for key in
                       ("base_executable", "version_text", "soabi", "architecture",
                        "free_threaded")):
                    raise runtime.RuntimeSelectionError("Recorded venv ABI differs")
            _health(current, requirements, scratch, imports, constraints)
        except (runtime.RuntimeSelectionError, OSError, ValueError):
            pass  # Rebuild only after a complete new dependency preflight.
        else:
            return current
    if requirements is not None:
        runtime.validate_requirements(
            base, requirements, scratch, constraints=constraints, wheels=wheels,
        )
    return None


def prepare_venv(
    base: dict, target: Path, *, requirements: Path | None = None,
    constraints: Path | None = None, wheels: Path | None = None,
    imports: tuple[str, ...] = (), scratch: Path | None = None,
) -> dict:
    scratch = scratch or target.parent / "scratch"
    current = _preflight_venv(
        base, target, requirements=requirements, constraints=constraints,
        wheels=wheels, imports=imports, scratch=scratch,
    )
    if current is not None:
        write_json_atomic(target / ".python-runtime.json", current)
        return current
    target.parent.mkdir(parents=True, exist_ok=True)
    with recoverable_backup(target.parent) as previous:
        had_previous = target.exists()
        try:
            if had_previous:
                target.rename(previous)
            _checked([base["base_executable"], "-I", "-m", "venv", str(target)],
                     "Could not create the new Python environment")
            current = _observation(base, target, requirements)
            if requirements is not None:
                command = [current["executable"], "-I", "-m", "pip", "install",
                           "--only-binary=:all:", "-r", str(requirements)]
                if constraints is not None:
                    command.extend(["-c", str(constraints)])
                if wheels is not None:
                    command.extend(["--find-links", str(wheels)])
                _checked(command, "Could not install the prepared requirements")
            _health(current, requirements, scratch, imports, constraints)
            write_json_atomic(target / ".python-runtime.json", current)
            return current
        except BaseException:
            if previous.exists():
                if target.exists():
                    shutil.rmtree(target)
                previous.rename(target)
            elif not had_previous and target.exists():
                shutil.rmtree(target)
            raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--venv", required=True, type=Path, action="append")
    parser.add_argument("--requirements", type=Path)
    parser.add_argument("--scratch", type=Path)
    parser.add_argument("--constraints", type=Path,
                        default=os.environ.get("PIP_CONSTRAINT") or None)
    parser.add_argument("--find-links", type=Path,
                        default=os.environ.get("PIP_FIND_LINKS") or None)
    parser.add_argument("--import", dest="imports", action="append", default=[])
    parser.add_argument("--record", type=Path)
    parser.add_argument("--path-only", action="store_true")
    args = parser.parse_args()
    try:
        preparation = dict(
            requirements=args.requirements, constraints=args.constraints,
            wheels=args.find_links, imports=tuple(args.imports),
            scratch=args.scratch,
        )

        def validate_candidate(binding: dict) -> None:
            for target in args.venv:
                _preflight_venv(binding, target, **preparation)

        base = runtime.resolve_python(
            explicit=args.python, runtime_config=args.config,
            validator=validate_candidate,
        )
        results = [prepare_venv(
            base, target, **preparation,
        ) for target in args.venv]
        result = results[0] if len(results) == 1 else results
        if args.record:
            args.record.parent.mkdir(parents=True, exist_ok=True)
            write_json_atomic(args.record, result)
        if args.path_only:
            print("\n".join(item["executable"] for item in results))
        else:
            print(json.dumps(result))
        return 0
    except (runtime.RuntimeSelectionError, OSError, ValueError) as exc:
        message = (str(exc) if isinstance(exc, runtime.RuntimeSelectionError)
                   else type(exc).__name__)
        print(f"Python environment: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    def interrupted(signum, frame):
        raise InterruptedError("Python environment preparation interrupted")

    signal.signal(signal.SIGTERM, interrupted)
    raise SystemExit(main())
