"""Locked runtime identity and test evidence, isolated from operator records."""
import copy
import json
from pathlib import Path
import sys

import pytest

from common import python_environment as environment
from common import python_runtime as runtime


@pytest.fixture
def snapshot(tmp_path):
    binding = runtime.probe_python(sys.executable)
    record = {**binding, 'packages': runtime.installed_packages(sys.executable),
              'lock_sha256': 'a' * 64,
              'verification': {'status': 'succeeded', 'lock_sha256': 'a' * 64,
                               'commands': [[sys.executable, '-c', 'pass']]}}
    path = tmp_path / '.python-runtime.json'
    path.write_text(json.dumps(record))
    return binding, record, path


@pytest.mark.parametrize('change', ['upgrade', 'remove', 'extra', 'python'])
def test_snapshot_rejects_drift_without_rewriting_record(snapshot, monkeypatch, change):
    binding, record, path = snapshot
    observed = copy.deepcopy(record['packages'])
    if change == 'upgrade':
        observed[next(iter(observed))] = '999.0'
    elif change == 'remove':
        observed.pop(next(iter(observed)))
    elif change == 'extra':
        observed['fixture-extra'] = '1'
    else:
        binding = {**binding, 'soabi': 'different-abi'}
    monkeypatch.setattr(runtime, 'installed_packages', lambda _: observed)
    before = path.read_bytes()
    with pytest.raises(runtime.RuntimeSelectionError):
        environment.verify_snapshot(binding, record)
    assert path.read_bytes() == before


def test_equivalent_environment_paths_do_not_require_same_venv(snapshot):
    binding, record, _ = snapshot
    record = {**record, 'executable': '/fixture/another/bin/python',
              'prefix': '/fixture/another'}
    environment.verify_snapshot(binding, record)


def test_failed_validation_invalidates_previous_success(snapshot):
    _, _, path = snapshot
    with pytest.raises(runtime.RuntimeSelectionError):
        environment.record_verification(path, [[sys.executable, '-c', 'raise SystemExit(3)']])
    assert 'verification' not in json.loads(path.read_text())


def test_successful_validation_records_current_lock_and_commands(snapshot):
    _, record, path = snapshot
    command = [sys.executable, '-c', 'pass']
    environment.record_verification(path, [command])
    verified = json.loads(path.read_text())['verification']
    assert verified['status'] == 'succeeded'
    assert verified['lock_sha256'] == record['lock_sha256']
    assert verified['commands'] == [command]


def test_validation_receives_external_wheel_directory_without_unrelated_env(
        snapshot, tmp_path, monkeypatch):
    _, _, path = snapshot
    wheels = str(tmp_path / 'external wheels')
    lock = str(tmp_path / 'selected candidate.lock')
    monkeypatch.setenv('PIP_FIND_LINKS', wheels)
    monkeypatch.setenv('PYTHON_RUNTIME_TEST_LOCK', lock)
    monkeypatch.setenv('UNRELATED_TEST_SECRET', 'must-not-be-forwarded')
    command = [sys.executable, '-c',
        'import os, sys; '
        'assert os.environ.get("PIP_FIND_LINKS") == sys.argv[1]; '
        'assert os.environ.get("PYTHON_RUNTIME_TEST_LOCK") == sys.argv[2]; '
        'assert "UNRELATED_TEST_SECRET" not in os.environ', wheels, lock]
    environment.record_verification(path, [command])
    assert json.loads(path.read_text())['verification']['status'] == 'succeeded'


def test_changed_packages_during_validation_cannot_be_attested(snapshot, monkeypatch):
    _, record, path = snapshot
    packages = record['packages']
    calls = []

    def observe(_):
        calls.append(None)
        return packages if len(calls) == 1 else {**packages, 'late-package': '1'}

    monkeypatch.setattr(runtime, 'installed_packages', observe)
    with pytest.raises(runtime.RuntimeSelectionError):
        environment.record_verification(path, [[sys.executable, '-c', 'pass']])
    assert 'verification' not in json.loads(path.read_text())


def test_reference_requires_success_and_exact_lock_bytes(snapshot, tmp_path):
    binding, record, path = snapshot
    lock = tmp_path / 'candidate.lock'
    lock.write_text('# fixture lock\n')
    with pytest.raises(runtime.RuntimeSelectionError):
        environment.tested_reference(binding, lock, path)
    import hashlib
    record['lock_sha256'] = hashlib.sha256(lock.read_bytes()).hexdigest()
    record['verification']['lock_sha256'] = record['lock_sha256']
    path.write_text(json.dumps(record))
    environment.tested_reference(binding, lock, path)
    promoted = tmp_path / 'requirements.lock'
    promoted.write_bytes(lock.read_bytes())
    environment.tested_reference(binding, promoted, path)
    constraints = tmp_path / 'requirements.txt'
    constraints.write_text('# first declaration\n')
    record['dependency_inputs'] = {'constraints:requirements.txt': hashlib.sha256(
        constraints.read_bytes()).hexdigest()}
    path.write_text(json.dumps(record))
    environment.tested_reference(binding, promoted, path, constraints=constraints)
    constraints.write_text('# changed declaration\n')
    with pytest.raises(runtime.RuntimeSelectionError):
        environment.tested_reference(binding, promoted, path, constraints=constraints)
    del record['verification']
    path.write_text(json.dumps(record))
    with pytest.raises(runtime.RuntimeSelectionError):
        environment.tested_reference(binding, lock, path)


def test_current_guard_fails_closed_for_missing_required_record(tmp_path):
    with pytest.raises(runtime.RuntimeSelectionError):
        environment.verify_current_environment(record_path=tmp_path / 'missing', required=True)


@pytest.mark.parametrize('recorded', [True, False])
def test_explicit_interpreter_check_does_not_assume_prefix_bin_python(snapshot, tmp_path, monkeypatch, recorded):
    import hashlib
    binding, saved, path = snapshot
    lock = tmp_path / 'requirements.lock'
    lock.write_text('# fixture\n')
    saved['lock_sha256'] = hashlib.sha256(lock.read_bytes()).hexdigest()
    saved['verification']['lock_sha256'] = saved['lock_sha256']
    path.write_text(json.dumps(saved))
    binding['prefix'] = str(tmp_path / 'base-without-python-alias')
    if recorded:
        Path(binding['prefix']).mkdir()
        (Path(binding['prefix']) / '.python-runtime.json').write_text(json.dumps(saved))
    monkeypatch.setattr(runtime, 'probe_python', lambda _: binding)
    monkeypatch.setattr(runtime, 'validate_requirements', lambda *a, **kw: saved['packages'])
    output = tmp_path / 'installed.json'
    monkeypatch.setattr(sys, 'argv', ['fixture', '--check', '--require-hashes',
        '--requirements', str(lock), '--reference-record', str(path), '--record', str(output)])
    assert environment.main() == (0 if recorded else 1)
    if recorded:
        assert json.loads(output.read_text())['executable'] == binding['executable']
    else:
        assert not output.exists()


def test_unmanaged_runtime_is_not_forced_into_connector_contract(tmp_path):
    environment.verify_current_environment(record_path=tmp_path / 'missing')


@pytest.fixture
def locked_wheels(tmp_path):
    import hashlib
    import zipfile
    import ensurepip
    seed = next((Path(ensurepip.__file__).parent / '_bundled').glob('pip-*.whl'))
    locks = []
    for version in ('1', '2'):
        wheel = tmp_path / f'fixture_dependency-{version}-py3-none-any.whl'
        info = f'fixture_dependency-{version}.dist-info'
        with zipfile.ZipFile(wheel, 'w') as archive:
            archive.writestr('fixture_dependency/__init__.py', f'version = {version}\n')
            archive.writestr(info + '/METADATA',
                f'Metadata-Version: 2.1\nName: fixture-dependency\nVersion: {version}\n')
            archive.writestr(info + '/WHEEL',
                'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
            members = archive.namelist() + [info + '/RECORD']
            archive.writestr(info + '/RECORD', ''.join(name + ',,\n' for name in members))
        lock = tmp_path / f'{version}.lock'
        lock.write_text(f'fixture-dependency @ {wheel.as_uri()} '
                        f'--hash=sha256:{hashlib.sha256(wheel.read_bytes()).hexdigest()}\n'
                        f'pip @ {seed.as_uri()} '
                        f'--hash=sha256:{hashlib.sha256(seed.read_bytes()).hexdigest()}\n')
        locks.append(lock)
    return locks


def test_available_wheel_cannot_turn_pending_install_into_successful_reuse(tmp_path, locked_wheels):
    base = runtime.probe_python(sys.executable)
    target = tmp_path / 'environment'
    environment.prepare_venv(base, target, requirements=locked_wheels[1], require_hashes=True)
    marker = target / 'old-runtime'
    marker.touch()
    result = environment.prepare_venv(base, target, requirements=locked_wheels[0], require_hashes=True)
    assert result['packages']['fixture-dependency'] == '1'
    assert not marker.exists()
    assert runtime.installed_packages(result['executable']) == result['packages']
    path = target / '.python-runtime.json'
    environment.record_verification(path, [[result['executable'], '-c', 'pass']])
    promoted = tmp_path / 'canonical.lock'
    promoted.write_bytes(locked_wheels[0].read_bytes())
    reused = environment.prepare_venv(base, target, requirements=promoted, require_hashes=True)
    assert reused['verification']['lock_sha256'] == result['lock_sha256']
    site = next(target.glob('lib/python*/site-packages'))
    extra = site / 'fixture_extra-1.dist-info'
    extra.mkdir()
    (extra / 'METADATA').write_text('Name: fixture-extra\nVersion: 1\n')
    cleaned = environment.prepare_venv(base, target, requirements=promoted, require_hashes=True)
    assert 'fixture-extra' not in cleaned['packages']
    assert 'verification' not in cleaned


def test_wrong_hash_preserves_existing_environment(tmp_path, locked_wheels):
    target = tmp_path / 'environment'
    base = runtime.probe_python(sys.executable)
    environment.prepare_venv(base, target, requirements=locked_wheels[0], require_hashes=True)
    record = (target / '.python-runtime.json').read_bytes()
    text = locked_wheels[1].read_text()
    locked_wheels[1].write_text(text[:text.index('--hash=')] + '--hash=sha256:' + '0' * 64 + '\n')
    with pytest.raises(runtime.RuntimeSelectionError):
        environment.prepare_venv(base, target, requirements=locked_wheels[1], require_hashes=True)
    assert (target / '.python-runtime.json').read_bytes() == record
    assert runtime.installed_packages(str(target / 'bin/python'))['fixture-dependency'] == '1'


@pytest.mark.parametrize('change', ['hash', 'missing-record', 'legacy-record', 'reference'])
def test_locked_reuse_requires_recorded_artifact_identity(tmp_path, locked_wheels, change):
    import hashlib
    import subprocess
    import zipfile
    base = runtime.probe_python(sys.executable)
    target = tmp_path / 'environment'
    wheel = tmp_path / 'fixture_dependency-1-py3-none-any.whl'
    pip_line = locked_wheels[0].read_text().splitlines()[1]
    lock = tmp_path / 'selected.lock'
    lock.write_text('fixture-dependency==1 --hash=sha256:'
        + hashlib.sha256(wheel.read_bytes()).hexdigest() + '\n' + pip_line + '\n')
    result = environment.prepare_venv(
        base, target, requirements=lock, wheels=tmp_path, require_hashes=True)
    marker = target / 'old-environment'
    marker.touch()
    path = target / '.python-runtime.json'
    saved = json.loads(path.read_text())
    wheels = tmp_path
    reference = None
    if change == 'hash':
        wheels = tmp_path / 'rebuilt wheels'
        wheels.mkdir()
        replacement = wheels / wheel.name
        with zipfile.ZipFile(wheel) as old, zipfile.ZipFile(replacement, 'w') as new:
            for name in old.namelist():
                new.writestr(name, 'version = 999\n' if name.endswith('__init__.py')
                             else old.read(name))
        lock.write_text('fixture-dependency==1 --hash=sha256:'
            + hashlib.sha256(replacement.read_bytes()).hexdigest() + '\n' + pip_line + '\n')
    elif change == 'legacy-record':
        saved.pop('lock_sha256')
        path.write_text(json.dumps(saved))
    else:
        if change == 'reference':
            reference = tmp_path / 'tested.json'
            saved['verification'] = {'status': 'succeeded', 'lock_sha256': saved['lock_sha256'],
                                     'commands': [['fixture', 'verified']]}
            reference.write_text(json.dumps(saved))
        path.unlink()
    rebuilt = environment.prepare_venv(
        base, target, requirements=lock, wheels=wheels, require_hashes=True,
        reference_record=reference)
    assert not marker.exists()
    assert rebuilt['packages'] == result['packages']
    observed = subprocess.run([rebuilt['executable'], '-c',
        'import fixture_dependency; print(fixture_dependency.version)'],
        check=True, capture_output=True, text=True)
    assert observed.stdout.strip() == ('999' if change == 'hash' else '1')


@pytest.mark.parametrize('locked', [False, True])
def test_new_environment_removes_only_unlocked_seed_packages(
        tmp_path, locked_wheels, monkeypatch, locked):
    base = runtime.probe_python(sys.executable)
    target = tmp_path / 'environment'
    checked = environment._checked

    def with_additional_seed(command, description):
        checked(command, description)
        if command[2:4] == ['-m', 'venv']:
            # Model the extra bootstrap distribution provided by older venvs.
            site = next(target.glob('lib/python*/site-packages'))
            info = site / 'fixture_seed-1.dist-info'
            info.mkdir()
            (info / 'METADATA').write_text('Name: fixture-seed\nVersion: 1\n')
            (info / 'RECORD').write_text(
                'fixture_seed-1.dist-info/METADATA,,\n'
                'fixture_seed-1.dist-info/RECORD,,\n')

    monkeypatch.setattr(environment, '_checked', with_additional_seed)
    result = environment.prepare_venv(
        base, target, requirements=locked_wheels[0] if locked else None,
        require_hashes=locked)
    assert ('fixture-seed' in result['packages']) is not locked
    if locked:
        assert result['packages']['fixture-dependency'] == '1'
    assert runtime.installed_packages(result['executable']) == result['packages']


def test_package_added_after_bootstrap_is_rejected_and_previous_venv_restored(
        tmp_path, locked_wheels, monkeypatch):
    base = runtime.probe_python(sys.executable)
    target = tmp_path / 'environment'
    environment.prepare_venv(base, target)
    record = (target / '.python-runtime.json').read_bytes()
    marker = target / 'previous-runtime'
    marker.touch()
    checked = environment._checked

    def with_unexpected_package(command, description):
        checked(command, description)
        if command[2:5] == ['-m', 'pip', 'install']:
            site = next(target.glob('lib/python*/site-packages'))
            info = site / 'unexpected_package-1.dist-info'
            info.mkdir()
            (info / 'METADATA').write_text(
                'Name: unexpected-package\nVersion: 1\n')

    monkeypatch.setattr(environment, '_checked', with_unexpected_package)
    with pytest.raises(runtime.RuntimeSelectionError, match='differs from the lock'):
        environment.prepare_venv(
            base, target, requirements=locked_wheels[0], require_hashes=True)
    assert marker.exists()
    assert (target / '.python-runtime.json').read_bytes() == record
    assert 'unexpected-package' not in runtime.installed_packages(
        str(target / 'bin/python'))


def test_startup_detects_direct_install_in_real_isolated_environment(tmp_path):
    import subprocess
    target = tmp_path / 'production fixture'
    saved = environment.prepare_venv(runtime.probe_python(sys.executable), target)
    saved.update(lock_sha256='b' * 64,
        verification={'status': 'succeeded', 'lock_sha256': 'b' * 64})
    record = target / '.python-runtime.json'
    record.write_text(json.dumps(saved))
    site = next(target.glob('lib/python*/site-packages'))
    injected = site / 'fixture_extra-1.dist-info'
    injected.mkdir()
    (injected / 'METADATA').write_text('Name: fixture-extra\nVersion: 1\n')
    before = record.read_bytes()
    result = subprocess.run([str(target / 'bin/python'), '-I', str(Path(environment.__file__)),
                             '--check'], capture_output=True, text=True, timeout=15)
    assert result.returncode == 1
    assert record.read_bytes() == before


@pytest.mark.parametrize('payload', [[], None, 'bad', {'verification': None},
                                    {'packages': []}, {'lock_sha256': 42}])
def test_corrupt_record_refuses_startup_with_safe_error(tmp_path, payload):
    path = tmp_path / 'record.json'
    path.write_text(json.dumps(payload))
    with pytest.raises(runtime.RuntimeSelectionError, match='Runtime drift'):
        environment.verify_current_environment(record_path=path, required=True)


def test_installed_binding_is_checked_instead_of_mutable_venv_record(snapshot, tmp_path):
    import hashlib
    _, saved, path = snapshot
    app = tmp_path / 'bundle'
    app.mkdir()
    lock = app / 'requirements.lock'
    lock.write_text('# approved fixture\n')
    saved['lock_sha256'] = hashlib.sha256(lock.read_bytes()).hexdigest()
    saved['verification']['lock_sha256'] = saved['lock_sha256']
    path.write_text(json.dumps(saved))
    (app / '.python-runtime.json').symlink_to(path)
    environment.verify_application_environment(app)
    saved['packages']['telethon'] = '999'
    path.write_text(json.dumps(saved))
    with pytest.raises(runtime.RuntimeSelectionError):
        environment.verify_application_environment(app)
    path.unlink()
    with pytest.raises(runtime.RuntimeSelectionError):
        environment.verify_application_environment(app)
