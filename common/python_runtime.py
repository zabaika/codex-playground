"""Resolve installed Python; project compatibility remains with its owner."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
from typing import Callable
import zipfile

COMMON_ROOT = Path(__file__).resolve().parent
if str(COMMON_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(COMMON_ROOT.parent))

from common import process, ttl_runner
from common.json_io import write_json_atomic

POLICY_PATH = COMMON_ROOT / "config/python.toml"
PROBE = """import importlib.util, json, os, platform, sys, sysconfig
virtual = sys.prefix != sys.base_prefix
base = getattr(sys, '_base_executable', sys.executable)
print(json.dumps(dict(
    executable=sys.executable if virtual else os.path.realpath(sys.executable),
    base_executable=os.path.realpath(base),
    prefix=sys.prefix, base_prefix=sys.base_prefix,
    version=list(sys.version_info[:3]), implementation=sys.implementation.name,
    version_text=sys.version, soabi=sysconfig.get_config_var('SOABI'),
    architecture=platform.machine(), platform=sysconfig.get_platform(),
    free_threaded=bool(sysconfig.get_config_var('Py_GIL_DISABLED')),
    capabilities=dict(tomllib=importlib.util.find_spec('tomllib') is not None)
)))
"""


class RuntimeSelectionError(ValueError):
    """A requested or discovered interpreter cannot satisfy its contract."""


def _load(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, ValueError) as exc:
        raise RuntimeSelectionError(
            "Cannot read runtime configuration",
        ) from exc


def load_policy(path: Path = POLICY_PATH) -> dict:
    section = _load(path).get("python")
    required = {"minimum_version", "implementation", "architecture",
                "free_threaded"}
    if not isinstance(section, dict) or set(section) != required:
        raise RuntimeSelectionError("Invalid [python] policy fields")
    version = section["minimum_version"]
    if (not isinstance(version, list) or len(version) != 2
            or any(type(n) is not int or n < 0 for n in version)
            or version[0] == 0):
        raise RuntimeSelectionError("minimum_version must contain major/minor")
    for key in ("implementation", "architecture"):
        if not isinstance(section[key], str) or not section[key].strip():
            raise RuntimeSelectionError(f"Invalid policy {key}")
    if type(section["free_threaded"]) is not bool:
        raise RuntimeSelectionError("Invalid policy free_threaded")
    return section


def _run(command: list[str]) -> subprocess.CompletedProcess:
    # CLI callers additionally have one outer TTL for the complete operation.
    config = process.load_process_config()
    env = {key: os.environ[key] for key in
           ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL") if key in os.environ}
    env.update(PYTHONDONTWRITEBYTECODE="1", PIP_CONFIG_FILE=os.devnull,
               PIP_DISABLE_PIP_VERSION_CHECK="1")
    try:
        return subprocess.run(
            command, capture_output=True, text=True, env=env,
            timeout=config.default_run_total_timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeSelectionError(
            "Python runtime probe failed or timed out",
        ) from exc


def probe_python(executable: str) -> dict:
    candidate = os.path.expanduser(executable)
    if not os.path.isabs(candidate):
        candidate = shutil.which(candidate) or candidate
    result = _run([candidate, "-I", "-c", PROBE])
    if result.returncode:
        raise RuntimeSelectionError("Python interpreter probe failed")
    try:
        return json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise RuntimeSelectionError("Invalid Python probe result") from exc


def validate_descriptor(binding: dict, policy: dict) -> None:
    version = binding.get("version")
    if (not isinstance(version, list) or len(version) != 3
            or any(type(n) is not int for n in version)):
        raise RuntimeSelectionError("Invalid Python version descriptor")
    minimum = policy["minimum_version"]
    if version[0] != minimum[0]:
        raise RuntimeSelectionError("Python major does not match policy")
    if version[:2] < minimum:
        raise RuntimeSelectionError("Python is below the configured minimum")
    for key in ("implementation", "architecture", "free_threaded"):
        if binding.get(key) != policy[key]:
            raise RuntimeSelectionError(f"Python {key} does not match policy")
    if binding.get("capabilities", {}).get("tomllib") is False:
        raise RuntimeSelectionError("Python lacks required stdlib tomllib")


def discover_candidates() -> list[tuple[str, str]]:
    candidates = []
    # The native macOS prefix is a platform convention, not a version pin.
    brews = [Path("/opt/homebrew/bin/brew")]
    on_path = shutil.which("brew")
    if on_path:
        brews.append(Path(on_path))
    for brew in dict.fromkeys(brews):
        if not brew.is_file():
            continue
        result = _run([str(brew), "--prefix"])
        if result.returncode == 0 and result.stdout.strip():
            candidates.append(("homebrew", str(
                Path(result.stdout.strip()) / "bin/python3",
            )))
    on_path = shutil.which("python3")
    if on_path:
        candidates.append(("path", on_path))
    return candidates


def resolve_python(
    *, explicit: str | None = None, runtime_config: Path | None = None,
    policy_path: Path | None = None,
    validator: Callable[[dict], None] | None = None,
) -> dict:
    path = policy_path or POLICY_PATH
    policy = load_policy(path)
    requested = (explicit if explicit is not None
                 else os.environ.get("PYTHON_BIN", "")).strip()
    local = ""
    if runtime_config is not None:
        section = _load(runtime_config).get("python", {})
        if not isinstance(section, dict):
            raise RuntimeSelectionError("Invalid local [python] config")
        local = section.get("executable", "")
        if not isinstance(local, str):
            raise RuntimeSelectionError("python.executable must be a string")
        local = local.strip()
    fixed = bool(requested or local)
    candidates = ([("explicit" if requested else "local", requested or local)]
                  if fixed else discover_candidates())
    seen = set()
    failures = []
    for source, executable in candidates:
        try:
            binding = probe_python(executable)
            identity = (str(Path(binding["executable"]).resolve()),
                        binding["prefix"])
            if identity in seen:
                continue
            seen.add(identity)
            validate_descriptor(binding, policy)
            if validator is not None:
                validator(binding)
            return binding | {
                "source": source,
                "policy_digest": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        except RuntimeSelectionError as exc:
            if fixed:
                raise
            failures.append(f"{source}: {exc}")
    raise RuntimeSelectionError(
        "No compatible installed Python: " + "; ".join(failures),
    )


def check_requires_python(binding: dict, specifier: str, scratch: Path) -> None:
    """Let public pip validate project metadata without installing a package."""
    if (not isinstance(specifier, str) or not specifier.strip()
            or "\n" in specifier or "\r" in specifier):
        raise RuntimeSelectionError("Invalid project Requires-Python")
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch) as directory:
        root = Path(directory)
        wheel = root / "runtime_policy_probe-0-py3-none-any.whl"
        info = "runtime_policy_probe-0.dist-info"
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr(f"{info}/METADATA", (
                "Metadata-Version: 2.1\nName: runtime-policy-probe\n"
                f"Version: 0\nRequires-Python: {specifier}\n"
            ))
            archive.writestr(f"{info}/WHEEL", (
                "Wheel-Version: 1.0\nRoot-Is-Purelib: true\n"
                "Tag: py3-none-any\n"
            ))
            archive.writestr(f"{info}/RECORD", "")
        result = _run([
            binding["executable"], "-I", "-m", "pip", "install", "--dry-run",
            "--ignore-installed", "--no-deps", "--no-index", "--report",
            str(root / "report.json"), str(wheel),
        ])
        # pip may ignore invalid Requires-Python after emitting a warning.
        # A warning is not positive compatibility evidence.
        if result.returncode or result.stderr.strip():
            raise RuntimeSelectionError(
                "Project Requires-Python validation failed: incompatible "
                "interpreter, invalid metadata, or unavailable public pip",
            )


def validate_requirements(
    binding: dict, requirements: Path, scratch: Path, *,
    wheels: Path | None = None, require_hashes: bool = False,
    constraints: Path | None = None, use_installed: bool = False,
) -> None:
    """Resolve the real dependency plan before touching a working runtime."""
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch) as directory:
        command = [
            binding["executable"], "-I", "-m", "pip", "install", "--dry-run",
            "--only-binary=:all:", "--report",
            str(Path(directory) / "report.json"), "-r", str(requirements),
        ]
        if not use_installed:
            command.append("--ignore-installed")
        if wheels is not None:
            command.extend(["--find-links", str(wheels)])
        if use_installed or require_hashes:
            command.append("--no-index")
        if require_hashes:
            command.append("--require-hashes")
        if constraints is not None:
            command.extend(["-c", str(constraints)])
        result = _run(command)
        if result.returncode or result.stderr.strip():
            raise RuntimeSelectionError(
                "Python requirements preflight failed; working runtime "
                "has not been replaced",
            )
    inputs = binding.setdefault("dependency_inputs", {})
    inputs[requirements.name] = hashlib.sha256(
        requirements.read_bytes(),
    ).hexdigest()


def check_installed_python(binding: dict, scratch: Path) -> None:
    """Check installed metadata too: pip may reuse an already installed wheel."""
    result = _run([binding["executable"], "-I", "-m", "pip", "inspect", "--local"])
    if result.returncode or result.stderr.strip():
        raise RuntimeSelectionError("Installed metadata inspection failed")
    try:
        metadata = [entry["metadata"] for entry in json.loads(result.stdout)["installed"]]
        specifiers = [entry["requires_python"] for entry in metadata
                      if entry.get("requires_python")]
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeSelectionError("Invalid installed metadata report") from exc
    if specifiers:
        # Requires-Python specifier lists are conjunctions, so joining preserves
        # their meaning without implementing a private version-specifier parser.
        check_requires_python(binding, ",".join(specifiers), scratch)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--policy", type=Path, default=POLICY_PATH)
    parser.add_argument("--pyproject", type=Path)
    parser.add_argument("--requirements", type=Path)
    parser.add_argument("--constraints", type=Path,
                        default=os.environ.get("PIP_CONSTRAINT") or None)
    parser.add_argument("--scratch", type=Path)
    parser.add_argument("--record", type=Path)
    parser.add_argument("--path-only", action="store_true")
    parser.add_argument(
        "--_worker", action="store_true", help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)
    if not args._worker:
        config = process.load_process_config()
        return ttl_runner.run_with_ttl(
            [sys.executable, "-I", str(Path(__file__)), "--_worker",
             *(sys.argv[1:] if argv is None else argv)],
            timeout_seconds=config.default_run_total_timeout_seconds,
            grace_seconds=config.default_termination_grace_seconds,
            poll_interval_seconds=config.poll_interval_seconds,
            timeout_exit_code=config.timeout_exit_code,
            term_signal=config.term_signal, kill_signal=config.kill_signal,
            audit_file=None, timeout_reason="python_selection_ttl_expired",
            use_caffeinate=False,
        )
    try:
        requirement = ""
        if args.pyproject:
            metadata = _load(args.pyproject).get("project", {})
            if not isinstance(metadata, dict):
                raise RuntimeSelectionError("Invalid project metadata")
            requirement = metadata.get("requires-python", "")
            if not isinstance(requirement, str):
                raise RuntimeSelectionError("Invalid project Requires-Python")
        scratch = args.scratch
        if scratch is None:
            owner = args.pyproject or args.requirements or args.config
            scratch = (owner.parent if owner else COMMON_ROOT.parent) / "scratch"

        def validator(binding: dict) -> None:
            if requirement:
                check_requires_python(binding, requirement, scratch)
            if args.requirements:
                validate_requirements(
                    binding, args.requirements, scratch,
                    constraints=args.constraints, use_installed=True,
                )
                check_installed_python(binding, scratch)
        binding = resolve_python(
            explicit=args.python, runtime_config=args.config,
            policy_path=args.policy, validator=validator,
        )
        if args.pyproject:
            binding.setdefault("dependency_inputs", {}).update({
                "pyproject": hashlib.sha256(
                    args.pyproject.read_bytes(),
                ).hexdigest(),
            })
        if args.record:
            args.record.parent.mkdir(parents=True, exist_ok=True)
            write_json_atomic(args.record, binding)
        print(binding["executable"] if args.path_only else json.dumps(binding))
        return 0
    except RuntimeSelectionError as exc:
        print(f"Python runtime: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
