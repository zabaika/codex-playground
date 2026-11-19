"""Private environment worker, supervised by python_runtime_prepare in shell."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
import platform
import re
from pathlib import Path
import shutil
import signal
import sys
import sysconfig
import tempfile
from datetime import datetime, timezone

COMMON_ROOT = Path(__file__).resolve().parent
if str(COMMON_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(COMMON_ROOT.parent))

from common import python_runtime as runtime
from common.json_io import write_json_atomic


IDENTITY_FIELDS = ('version_text', 'implementation', 'soabi', 'architecture', 'free_threaded')


def _read_record(path):
    saved = json.loads(path.read_text())
    if not isinstance(saved, dict) or any(
            key in saved and not isinstance(saved[key], dict)
            for key in ('packages', 'verification', 'dependency_inputs')):
        raise runtime.RuntimeSelectionError('Invalid runtime record')
    if 'lock_sha256' in saved and (not isinstance(saved['lock_sha256'], str)
            or not re.fullmatch(r'[0-9a-f]{64}', saved['lock_sha256'])):
        raise runtime.RuntimeSelectionError('Invalid lock identity')
    return saved


def verify_snapshot(binding, saved, *, packages=True):
    """Paths may differ; the tested interpreter and dependency set may not."""
    if any(binding.get(key) != saved.get(key) for key in IDENTITY_FIELDS):
        raise runtime.RuntimeSelectionError('Python differs from the tested runtime')
    if packages and runtime.installed_packages(binding['executable']) != saved.get('packages'):
        raise runtime.RuntimeSelectionError('Installed packages differ from the recorded runtime')


def tested_reference(binding, requirements, path, *, packages=True, constraints=None):
    try:
        saved = _read_record(path)
        digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
        verified = saved.get('verification', {})
        if (saved.get('lock_sha256') != digest or verified.get('status') != 'succeeded'
                or verified.get('lock_sha256') != digest or not verified.get('commands')):
            raise ValueError
        if constraints is not None and saved.get('dependency_inputs', {}).get(
                'constraints:' + constraints.name) != hashlib.sha256(
                    constraints.read_bytes()).hexdigest():
            raise ValueError
        verify_snapshot(binding, saved, packages=packages)
        return saved
    except (OSError, ValueError) as exc:
        raise runtime.RuntimeSelectionError('A matching successfully tested lock is required') from exc


def verify_current_environment(*, requirements=None, record_path=None, required=False):
    """Read-only startup guard; unmanaged callers retain their existing policy."""
    path = record_path or Path(sys.prefix) / '.python-runtime.json'
    if not path.exists() and not required:
        return
    try:
        saved = _read_record(path)
        if not saved.get('lock_sha256'):
            if not required:
                return
            raise ValueError
        if required and (saved.get('verification', {}).get('status') != 'succeeded'
                or saved['verification'].get('lock_sha256') != saved['lock_sha256']):
            raise ValueError
        if requirements is not None and saved['lock_sha256'] != hashlib.sha256(
                requirements.read_bytes()).hexdigest():
            raise ValueError
        binding = {'executable': sys.executable, 'version_text': sys.version,
                   'implementation': sys.implementation.name,
                   'soabi': sysconfig.get_config_var('SOABI'),
                   'architecture': platform.machine(),
                   'free_threaded': bool(sysconfig.get_config_var('Py_GIL_DISABLED'))}
        verify_snapshot(binding, saved)
    except (OSError, ValueError) as exc:
        raise runtime.RuntimeSelectionError('Runtime drift detected; redeploy the approved lock') from exc


def verify_application_environment(app_root):
    """Installed bundles point to their approved binding, not a mutable venv record."""
    record = app_root / '.python-runtime.json'
    if record.exists() or record.is_symlink():
        verify_current_environment(record_path=record,
            requirements=app_root / 'requirements.lock', required=True)
    else:
        verify_current_environment()


def record_verification(path, commands):
    saved = _read_record(path)
    saved.pop('verification', None)
    write_json_atomic(path, saved)
    if not commands or not saved.get('lock_sha256'):
        raise runtime.RuntimeSelectionError('Locked requirements and validation commands are required')
    binding = runtime.probe_python(saved['executable'])
    verify_snapshot(binding, saved)
    log = path.parent / 'verification.log'
    for command in commands:
        result = runtime._run(command, pass_env=(
            'PIP_FIND_LINKS', 'PYTHON_RUNTIME_TEST_LOCK'))
        with log.open('a') as handle:
            handle.write(result.stdout + result.stderr)
        if result.returncode:
            raise runtime.RuntimeSelectionError('Validation failed; inspect verification.log')
    verify_snapshot(runtime.probe_python(saved['executable']), saved)
    saved['verification'] = {'status': 'succeeded', 'lock_sha256': saved['lock_sha256'],
        'commands': commands, 'finished_at': datetime.now(timezone.utc).isoformat()}
    write_json_atomic(path, saved)


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
    imports: tuple[str, ...], constraints: Path | None, require_hashes=False,
) -> None:
    if requirements is not None:
        runtime.validate_requirements(
            binding, requirements, scratch, constraints=constraints,
            use_installed=True, require_hashes=require_hashes,
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


def _observation(base: dict, target: Path, requirements: Path | None,
                 constraints=None, require_hashes=False) -> dict:
    current = runtime.probe_python(str(target / "bin/python"))
    if current["base_executable"] != base["base_executable"]:
        raise runtime.RuntimeSelectionError("Prepared venv base identity differs")
    current["source"] = base.get("source", "explicit")
    return _environment_record(current, requirements, constraints, require_hashes)


def _environment_record(binding, requirements, constraints=None, require_hashes=False):
    current = dict(binding)
    current.setdefault('source', 'explicit')
    current["policy_digest"] = hashlib.sha256(
        runtime.POLICY_PATH.read_bytes(),
    ).hexdigest()
    current["dependency_inputs"] = {}
    if requirements is not None:
        current["dependency_inputs"][requirements.name] = hashlib.sha256(
            requirements.read_bytes(),
        ).hexdigest()
    current['packages'] = runtime.installed_packages(current['executable'])
    if constraints is not None:
        current['dependency_inputs']['constraints:' + constraints.name] = hashlib.sha256(
            constraints.read_bytes()).hexdigest()
    if require_hashes:
        current['lock_sha256'] = hashlib.sha256(requirements.read_bytes()).hexdigest()
    return current


def _preflight_venv(
    base: dict, target: Path, *, requirements: Path | None = None,
    constraints: Path | None = None, wheels: Path | None = None,
    imports: tuple[str, ...] = (), scratch: Path | None = None,
    require_hashes=False, reference_record=None,
) -> dict | None:
    """Check reuse or a fresh dependency plan without changing the target."""
    runtime.validate_descriptor(base, runtime.load_policy())
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        raise runtime.RuntimeSelectionError("Venv target is not an owned directory")
    scratch = scratch or target.parent / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    reference = (tested_reference(base, requirements, reference_record,
                 packages=False, constraints=constraints) if reference_record else None)
    locked_packages = runtime.validate_requirements(
        base, requirements, scratch, constraints=constraints, wheels=wheels,
        require_hashes=True) if require_hashes else None
    if target.exists():
        try:
            current = _observation(base, target, requirements, constraints, require_hashes)
            if any(current[key] != base[key] for key in
                   ("version", "version_text", "soabi", "architecture", "free_threaded")):
                raise runtime.RuntimeSelectionError("Venv identity differs")
            record = target / ".python-runtime.json"
            saved = {}
            if record.exists():
                saved = _read_record(record)
                if any(saved.get(key) != current[key] for key in
                       ("base_executable", "version_text", "soabi", "architecture",
                        "free_threaded")):
                    raise runtime.RuntimeSelectionError("Recorded venv ABI differs")
            _health(current, requirements, scratch, imports, constraints, require_hashes)
            if require_hashes:
                if (saved.get('lock_sha256') != current['lock_sha256']
                        or saved.get('packages') != current['packages']):
                    raise runtime.RuntimeSelectionError('Recorded artifact identity differs from the lock')
                if current['packages'] != locked_packages:
                    raise runtime.RuntimeSelectionError('Installed package set differs from the lock')
            if reference:
                verify_snapshot(current, reference)
                current['verification'] = reference['verification']
            elif (record.exists() and saved.get('lock_sha256') == current.get('lock_sha256')
                  and saved.get('packages') == current['packages']
                  and {k: v for k, v in saved.get('dependency_inputs', {}).items()
                       if k.startswith('constraints:')} == {
                           k: v for k, v in current['dependency_inputs'].items()
                           if k.startswith('constraints:')}):
                if 'verification' in saved:
                    current['verification'] = saved['verification']
        except (runtime.RuntimeSelectionError, OSError, ValueError):
            pass  # Rebuild only after a complete new dependency preflight.
        else:
            return current
    if requirements is not None and not require_hashes:
        runtime.validate_requirements(
            base, requirements, scratch, constraints=constraints, wheels=wheels,
            require_hashes=require_hashes,
        )
    return None


def prepare_venv(
    base: dict, target: Path, *, requirements: Path | None = None,
    constraints: Path | None = None, wheels: Path | None = None,
    imports: tuple[str, ...] = (), scratch: Path | None = None,
    require_hashes=False, reference_record=None,
) -> dict:
    scratch = scratch or target.parent / "scratch"
    current = _preflight_venv(
        base, target, requirements=requirements, constraints=constraints,
        wheels=wheels, imports=imports, scratch=scratch,
        require_hashes=require_hashes, reference_record=reference_record,
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
            current = _observation(base, target, requirements, constraints, require_hashes)
            if requirements is not None:
                command = [current["executable"], "-I", "-m", "pip", "install",
                           "--only-binary=:all:", "-r", str(requirements)]
                if constraints is not None:
                    command.extend(["-c", str(constraints)])
                if wheels is not None:
                    command.extend(["--find-links", str(wheels)])
                if require_hashes:
                    command.extend(['--require-hashes', '--no-index'])
                _checked(command, "Could not install the prepared requirements")
            if require_hashes:
                planned = runtime.validate_requirements(
                    base, requirements, scratch, constraints=constraints,
                    wheels=wheels, require_hashes=True)
                # Only the new venv's bootstrap packages may be discarded.
                unused_seeds = sorted(current['packages'].keys() - planned.keys())
                if unused_seeds:
                    _checked([current['executable'], '-I', '-m', 'pip',
                              'uninstall', '--yes', *unused_seeds],
                             'Could not normalize the new environment seeds')
            _health(current, requirements, scratch, imports, constraints, require_hashes)
            current = _observation(base, target, requirements, constraints, require_hashes)
            if require_hashes and current['packages'] != planned:
                raise runtime.RuntimeSelectionError('Installed package set differs from the lock')
            if reference_record:
                reference = tested_reference(current, requirements, reference_record,
                                             constraints=constraints)
                current['verification'] = reference['verification']
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
    parser.add_argument("--venv", type=Path, action="append")
    parser.add_argument("--requirements", type=Path)
    parser.add_argument("--scratch", type=Path)
    parser.add_argument("--constraints", type=Path,
                        default=os.environ.get("PIP_CONSTRAINT") or None)
    parser.add_argument("--find-links", type=Path,
                        default=os.environ.get("PIP_FIND_LINKS") or None)
    parser.add_argument("--import", dest="imports", action="append", default=[])
    parser.add_argument("--record", type=Path)
    parser.add_argument("--path-only", action="store_true")
    parser.add_argument('--require-hashes', action='store_true')
    parser.add_argument('--reference-record', type=Path)
    parser.add_argument('--test-suite', type=Path, action='append', default=[])
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    try:
        if args.require_hashes and args.requirements is None:
            raise runtime.RuntimeSelectionError('Hash-locked preparation requires a lock file')
        if args.reference_record and not args.require_hashes:
            raise runtime.RuntimeSelectionError('A tested reference requires hash-locked requirements')
        if args.check:
            if args.reference_record:
                binding = runtime.probe_python(sys.executable)
                reference = tested_reference(binding, args.requirements, args.reference_record,
                                             constraints=args.constraints)
                result = _environment_record(binding, args.requirements,
                                             args.constraints, True)
                try:
                    own_record = _read_record(Path(binding['prefix']) / '.python-runtime.json')
                    if own_record.get('lock_sha256') != result['lock_sha256']:
                        raise ValueError
                    verify_snapshot(binding, own_record)
                except (OSError, ValueError) as exc:
                    raise runtime.RuntimeSelectionError(
                        'Explicit runtime needs preparation for the selected lock') from exc
                planned = runtime.validate_requirements(binding, args.requirements,
                    args.scratch or Path(sys.prefix).parent / 'scratch',
                    wheels=args.find_links, constraints=args.constraints,
                    require_hashes=True)
                if result['packages'] != planned:
                    raise runtime.RuntimeSelectionError('Installed package set differs from the lock')
                result['verification'] = reference['verification']
                if args.record:
                    write_json_atomic(args.record, result)
            else:
                verify_current_environment(requirements=args.requirements,
                    record_path=args.record, required=True)
            return 0
        if not args.venv:
            raise runtime.RuntimeSelectionError('Preparation requires --venv')
        preparation = dict(
            requirements=args.requirements, constraints=args.constraints,
            wheels=args.find_links, imports=tuple(args.imports),
            scratch=args.scratch,
            require_hashes=args.require_hashes, reference_record=args.reference_record,
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
        if args.test_suite:
            for target, item in zip(args.venv, results):
                record_verification(target / '.python-runtime.json', [
                    [item['executable'], '-m', 'pytest', str(suite), '-q']
                    for suite in args.test_suite])
            results = [json.loads((target / '.python-runtime.json').read_text())
                       for target in args.venv]
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
