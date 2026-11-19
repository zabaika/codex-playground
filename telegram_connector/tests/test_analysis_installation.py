"""Exercise the canonical installer without touching user services or HOME."""

import ast
import json
import io
import hashlib
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULES = (
    "telegram_bridge.py", "telegram_history_client.py", "telegram_digest.py",
    "history_settings.py",
    "history_messages.py",
    "history_store.py",
    "history_session.py",
    "analysis_history.py",
    "digest_settings.py",
    "digest_rendering.py",
    "digest_pipeline.py",
    "digest_telemetry.py",
    "telegram_analysis.py", "analysis_store.py", "analysis_evidence.py",
    "analysis_settings.py", "analysis_rendering.py", "analysis_pipeline.py",
    "analysis_telemetry.py",
)


@pytest.fixture
def installation(tmp_path):
    installer = ROOT / "telegram_connector/scripts/install_launch_agent.sh"
    project = tmp_path / "project's source/telegram_connector"
    (project / "scripts").mkdir(parents=True)
    (project / "config").mkdir()
    (project / "data/sessions").mkdir(parents=True)
    for name in MODULES:
        shutil.copy2(ROOT / "telegram_connector" / name, project / name)
    shutil.copy2(installer, project / "scripts/install_launch_agent.sh")
    shutil.copy2(ROOT / "telegram_connector/scripts/report_analysis.py", project / "scripts/report_analysis.py")
    shutil.copy2(ROOT / "telegram_connector/scripts/report_digest.py", project / "scripts/report_digest.py")
    shutil.copy2(ROOT / "telegram_connector/requirements.txt", project / "requirements.txt")
    selected_lock = Path(os.environ.get('PYTHON_RUNTIME_TEST_LOCK') or
                         ROOT / 'telegram_connector/requirements.lock')
    shutil.copy2(selected_lock, project / 'requirements.lock')
    from common import python_runtime
    tested = {**python_runtime.probe_python(sys.executable),
              'packages': python_runtime.installed_packages(sys.executable),
              'dependency_inputs': {'constraints:requirements.txt': hashlib.sha256(
                  (project / 'requirements.txt').read_bytes()).hexdigest()},
              'lock_sha256': hashlib.sha256((project / 'requirements.lock').read_bytes()).hexdigest()}
    tested['verification'] = {'status': 'succeeded', 'lock_sha256': tested['lock_sha256'],
                              'commands': [['fixture', 'successful-regression']]}
    reference = project.parent / '.venv-test-gap-detection/.python-runtime.json'
    reference.parent.mkdir()
    reference.write_text(json.dumps(tested))
    assets = Path(os.environ.get('PIP_FIND_LINKS') or
                  ROOT / 'telegram_connector/third_party').expanduser()
    shutil.copytree(assets, project / 'third_party')
    for name in ("runtime.example.toml", "digest_prompts.toml", "analysis_prompts.toml"):
        shutil.copy2(ROOT / "telegram_connector/config" / name, project / "config" / name)
    shutil.copy2(project / "config/runtime.example.toml", project / "config/runtime.local.toml")
    for name in ("common", "telegram_shared"):
        shutil.copytree(ROOT / name, project.parent / name, ignore=shutil.ignore_patterns("__pycache__", "tests"))
    (project / "data/telegram_history.sqlite3").write_text("source database fixture")
    (project / "data/sessions/fixture.session").write_text("source session fixture")
    staging = tmp_path / "isolated staging"
    commands = tmp_path / "commands"
    commands.mkdir()
    marker = tmp_path / "launchctl-called"
    fake = commands / "launchctl"
    fake.write_text('#!/bin/sh\nprintf called > "$LAUNCHCTL_TEST_MARKER"\nexit 99\n')
    fake.chmod(0o755)
    env = {**os.environ, "PYTHON_BIN": sys.executable, "PATH": str(commands) + os.pathsep + os.environ["PATH"],
        "PYTHONDONTWRITEBYTECODE": "1", "LAUNCHCTL_TEST_MARKER": str(marker)}
    return project, staging, env, marker


def install(installation):
    project, staging, env, _ = installation
    # Until staging exists, fail before executing an installer that targets HOME.
    assert "--staging-root" in (project / "scripts/install_launch_agent.sh").read_text(), "Installer needs an isolated fixture root with launchctl disabled"
    return subprocess.run(["bash", str(project / "scripts/install_launch_agent.sh"), "--staging-root", str(staging)],
        env=env, text=True, capture_output=True, timeout=30)


def test_s37_runtime_bundle_staging_parity_imports_and_reinstall(installation):
    project, staging, env, marker = installation
    outcome = install(installation)
    assert outcome.returncode == 0, outcome.stderr
    assert f"Python interpreter: {sys.executable}" in outcome.stdout
    assert not marker.exists()
    service = staging / "telegram_connector_service"
    reporter = subprocess.run([sys.executable, str(service / "scripts/report_analysis.py"), "--help"],
                              env=env, text=True, capture_output=True, timeout=10)
    assert reporter.returncode == 0, reporter.stderr
    assert "--job-id" in reporter.stdout
    binding = json.loads((service / ".python-runtime.json").read_text())
    assert binding["policy_digest"]
    assert binding["architecture"] == "arm64"
    assert binding["dependency_inputs"]["requirements.lock"]
    for name in MODULES:
        assert (service / name).read_bytes() == (project / name).read_bytes()
    for name in ("digest_prompts.toml", "analysis_prompts.toml", "runtime.local.toml"):
        assert (service / "config" / name).read_bytes() == (project / "config" / name).read_bytes()
    for command in ("bridge", "digest"):
        plist = plistlib.loads((staging / "LaunchAgents" / f"com.zabaika.telegram-connector-{command}.plist").read_bytes())
        assert plist["EnvironmentVariables"]["TELEGRAM_CONNECTOR_PROJECT_ROOT"] == str(project)
        assert plist["WorkingDirectory"] == str(service)
        assert Path(plist["ProgramArguments"][0]).exists()
        runner = service / "scripts" / f"run_telegram_{command}.sh"
        assert runner.read_text().startswith("#!/bin/bash\n")
        subprocess.run(["bash", "-n", str(runner)], check=True, capture_output=True)
        assignment = next(line for line in runner.read_text().splitlines() if line.startswith("ROOT="))
        resolved = subprocess.run(["bash", "-c", assignment + '\nprintf "%s" "$ROOT"'],
            text=True, capture_output=True, check=True)
        assert resolved.stdout == str(service)
    probe = """
import json, sys, tomllib
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import telegram_analysis as analysis
import telegram_history_client as history
from common import sqlite
root = Path(sys.argv[1])
config = tomllib.loads((root / 'config/runtime.local.toml').read_text())
settings = analysis.resolve_analysis_settings(config, config_dir=root / 'config')
conn = sqlite.connect_sqlite(root / 'probe.sqlite3')
history.init_db(conn)
print(json.dumps({'prompts': settings['analysis_prompts']['hash'], 'fts': conn.execute("SELECT count(*) FROM sqlite_master WHERE name='messages_fts'").fetchone()[0]}))
conn.close()
"""
    observed = subprocess.run([sys.executable, "-I", "-c", probe, str(service)], cwd=service, env=env,
        text=True, capture_output=True, timeout=30)
    assert observed.returncode == 0, observed.stderr
    assert json.loads(observed.stdout)["fts"] == 1
    assert len(json.loads(observed.stdout)["prompts"]) == 64
    # Each module is the first import in a fresh interpreter. Check both
    # supported import layouts without borrowing modules from the checkout.
    cold_probe = """
import importlib, sys
from pathlib import Path
root, module, package = Path(sys.argv[1]), sys.argv[2], sys.argv[3] == 'package'
sys.path.insert(0, str(root.parent if package else root))
prefix = 'telegram_connector.' if package else ''
loaded = importlib.import_module(prefix + module)
assert Path(loaded.__file__).resolve() == (root / (module + '.py')).resolve()
for name, value in list(sys.modules.items()):
    if name.removeprefix(prefix) in sys.argv[4:]:
        assert Path(value.__file__).resolve() == (root / (name.removeprefix(prefix) + '.py')).resolve()
    shared = name.split('.')[0]
    if shared in {'common', 'telegram_shared'} and getattr(value, '__file__', None):
        assert Path(value.__file__).resolve().is_relative_to(root.resolve() / shared)
"""
    for mode in ("script", "package"):
        root = service
        if mode == "package":
            # The service bundle is intentionally flat. A fixture package
            # alias exercises package imports against these same staged bytes.
            root = staging / "telegram_connector"
            root.symlink_to(service, target_is_directory=True)
            for shared in ("common", "telegram_shared"):
                (staging / shared).symlink_to(service / shared, target_is_directory=True)
        for name in MODULES:
            # Digest's existing entrypoint uses script-only sibling imports.
            if mode == "package" and name == "telegram_digest.py":
                continue
            module = Path(name).stem
            cold = subprocess.run([sys.executable, "-I", "-c", cold_probe,
                str(root), module, mode, *(Path(value).stem for value in MODULES)],
                cwd=staging, env=env, text=True, capture_output=True, timeout=30)
            assert cold.returncode == 0, cold.stderr
    for mode in ("script", "package"):
        arguments = [str(service / "telegram_analysis.py")] if mode == "script" else ["-m", "telegram_connector.telegram_analysis"]
        help_result = subprocess.run([sys.executable, *arguments, "--help"],
            cwd=staging, env={**env, "PYTHONPATH": str(staging)},
            text=True, capture_output=True, timeout=30)
        assert help_result.returncode == 0, help_result.stderr
        assert "--job-id" in help_result.stdout and "--worker-inner" not in help_result.stdout
    (service / "data/telegram_history.sqlite3").write_text("keep installed database")
    (service / "data/sessions/fixture.session").write_text("keep installed session")
    repeated = install(installation)
    assert repeated.returncode == 0, repeated.stderr
    assert (service / "data/telegram_history.sqlite3").read_text() == "keep installed database"
    assert (service / "data/sessions/fixture.session").read_text() == "keep installed session"
    assert not marker.exists()


def test_analytics_modules_preserve_ownership_boundaries():
    allowed = {
        "history_settings": set(),
        "history_messages": set(),
        "history_store": {"history_settings", "history_messages"},
        "history_session": {"history_settings", "history_messages"},
        "analysis_history": {"history_settings", "history_messages", "history_store", "history_session", "analysis_store"},
        "digest_settings": {"history_settings"},
        "digest_rendering": set(),
        "digest_pipeline": {"digest_settings", "digest_rendering", "history_messages", "history_store", "digest_telemetry"},
        "digest_telemetry": {"history_store"},
        "analysis_settings": set(),
        "analysis_evidence": {"analysis_settings", "analysis_store"},
        "analysis_rendering": set(),
        "analysis_pipeline": {"analysis_settings", "analysis_evidence", "analysis_store", "analysis_telemetry", "telegram_history_client"},
        "analysis_telemetry": {"analysis_store"},
    }
    connector = {Path(name).stem for name in MODULES}
    for module, dependencies in allowed.items():
        tree = ast.parse((ROOT / "telegram_connector" / (module + ".py")).read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.update([node.module] if node.module else [alias.name for alias in node.names])
                assert all(alias.name != "*" for alias in node.names)
        assert imported & connector <= dependencies
        if module == "analysis_settings":
            assert imported <= {"copy", "hashlib", "pathlib", "re", "string", "tomllib", "typing"}
        if module == "analysis_rendering":
            assert imported <= {"html", "pathlib", "re", "sys", "telegram_shared.bot_api"}


@pytest.mark.parametrize("filename", ["analysis_prompts.toml", "digest_prompts.toml"])
def test_s37_runtime_bundle_configured_relative_prompt_is_copied(installation, filename):
    project, staging, _, _ = installation
    config = project / "config/runtime.local.toml"
    config.write_text(config.read_text().replace(f'file = "{filename}"', 'file = "custom/analytics.toml"'))
    custom = project / "config/custom/analytics.toml"
    custom.parent.mkdir()
    shutil.copy2(project / "config" / filename, custom)
    outcome = install(installation)
    assert outcome.returncode == 0, outcome.stderr
    assert (staging / "telegram_connector_service/config/custom/analytics.toml").read_bytes() == custom.read_bytes()


def test_s37_runtime_bundle_missing_asset_does_not_launch(installation):
    project, staging, _, marker = installation
    (project / "analysis_evidence.py").unlink()
    outcome = install(installation)
    assert outcome.returncode != 0
    assert not marker.exists()


def test_s37_runtime_bundle_without_fts_keeps_history_and_rejects_fast(installation):
    _, staging, env, marker = installation
    outcome = install(installation)
    assert outcome.returncode == 0, outcome.stderr
    service = staging / "telegram_connector_service"
    probe = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import telegram_history_client as history
from common import sqlite
history.history_store.fts5_available = lambda conn: False
conn = sqlite.connect_sqlite(Path(sys.argv[1]) / 'without-fts.sqlite3')
history.init_db(conn)
assert conn.execute('SELECT count(*) FROM messages').fetchone()[0] == 0
try:
    history.require_fts_ready(conn)
except ValueError as error:
    assert str(error) == 'fts_unavailable'
else:
    raise AssertionError('Fast search needs unavailable FTS')
conn.close()
"""
    observed = subprocess.run([sys.executable, "-c", probe, str(service)], cwd=service, env=env,
        text=True, capture_output=True, timeout=30)
    assert observed.returncode == 0, observed.stderr
    assert not marker.exists()


def test_s37_runtime_bundle_historical_writer_keeps_schema_and_rebuilds_fts(installation, tmp_path):
    baseline = "6d97423"
    available = subprocess.run(["git", "cat-file", "-e", f"{baseline}:telegram_connector/telegram_history_client.py"],
        cwd=ROOT, capture_output=True)
    if available.returncode:
        pytest.skip("Historical writer fixture needs the recorded baseline git object")
    archive = subprocess.run(["git", "archive", baseline, "telegram_connector", "common", "telegram_shared"],
        cwd=ROOT, capture_output=True, check=True).stdout
    legacy = tmp_path / "historical-runtime"
    with tarfile.open(fileobj=io.BytesIO(archive)) as snapshot:
        snapshot.extractall(legacy, filter="data")
    project, staging, env, marker = installation
    outcome = install(installation)
    assert outcome.returncode == 0, outcome.stderr
    service = staging / "telegram_connector_service"
    db_path = service / "rollback.sqlite3"
    probe = """
import json, sys, tomllib
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
import telegram_history_client as history
from common import sqlite
root, db_path, phase = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
conn = sqlite.connect_sqlite(db_path)
history.init_db(conn)
class Channel(SimpleNamespace): pass
entity = Channel(id=123, username=None, title='Rollback fixture')
if phase != 'after':
    identity = 2 if phase == 'legacy' else 1
    history.upsert_channel(conn, entity)
    history.upsert_message(conn, entity, SimpleNamespace(id=identity,
        date=datetime(2026,10,10,tzinfo=timezone.utc), message=f'fixture {identity}', media=None), None, None, None, None)
if phase != 'legacy':
    import telegram_analysis as analysis
    from analysis_store import AnalysisStore
    config = tomllib.loads((root / 'config/runtime.local.toml').read_text())
    config['paths']['history_db'] = str(db_path)
    store = AnalysisStore(conn)
    if phase == 'after':
        assert conn.execute('SELECT count(*) FROM messages').fetchone()[0] == 2
        assert conn.execute('SELECT count(*) FROM messages_fts').fetchone()[0] == 1
    now = datetime.now(timezone.utc)
    queued = analysis.enqueue_fts_backfill(store, config, rebuild=phase=='after', now=now)
    job = store.claim_job(queued['job_id'], max_running_jobs=1, now=now)
    while history.backfill_fts_page(store, job['job_id'], generation=job['generation'])['state'] != 'ready': pass
    history.require_fts_ready(conn)
    assert conn.execute('SELECT count(*) FROM messages_fts').fetchone()[0] == (2 if phase=='after' else 1)
assert conn.execute('SELECT text FROM messages WHERE message_id=1').fetchone()[0] == 'fixture 1'
print(json.dumps({'phase':phase,'messages':conn.execute('SELECT count(*) FROM messages').fetchone()[0]}))
conn.close()
"""
    for root, phase in ((service, "before"), (legacy / "telegram_connector", "legacy"), (service, "after")):
        observed = subprocess.run([sys.executable, "-c", probe, str(root), str(db_path), phase], cwd=root,
            env={**env, "TELEGRAM_CONNECTOR_PROJECT_ROOT": str(project)}, text=True, capture_output=True, timeout=30)
        assert observed.returncode == 0, observed.stderr
        assert json.loads(observed.stdout)["messages"] == (1 if phase == "before" else 2)
    assert not marker.exists()


def test_scheduled_digest_uses_canonical_supervisor_in_installed_runtime(installation):
    project, staging, env, marker = installation
    outcome = install(installation)
    assert outcome.returncode == 0, outcome.stderr
    service = staging / 'telegram_connector_service'
    # Replace only the isolated fixture worker, never an operator runtime.
    (service / 'telegram_digest.py').write_text('''import json, os, sys
from pathlib import Path
path = Path(os.environ['TELEGRAM_CONNECTOR_PROJECT_ROOT']) / 'data/launchd/digest.last_attempt.json'
payload = json.loads(path.read_text())
assert payload['run_id'] == sys.argv[sys.argv.index('--audit-run-id') + 1]
payload.update(status='sent', phase='completed', worker_args=sys.argv[1:])
path.write_text(json.dumps(payload))
print('{"status":"sent"}')
''')
    audit_path = project / 'data/launchd/digest.last_attempt.json'
    previous = {'run_id': 'previous', 'status': 'sent', 'elapsed_seconds': 1800,
                'ttl_elapsed_seconds': 1650, 'sleep_seconds': 150}
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(previous))
    completed = subprocess.run(['bash', str(service / 'scripts/run_telegram_digest.sh')],
        env={**env, 'TELEGRAM_CONNECTOR_PROJECT_ROOT': str(project)},
        text=True, capture_output=True, timeout=10)
    assert completed.returncode == 0, completed.stderr
    audit = json.loads((project / 'data/launchd/digest.last_attempt.json').read_text())
    assert audit['run_id'] != previous['run_id']
    assert audit['previous_supervisor'] == previous
    assert audit['status'] == 'sent'
    assert audit['worker_args'] == ['run', '--audit-run-id', audit['run_id']]
    assert audit['sleep_policy'] == 'pause'
    assert all(audit[key] >= 0 for key in ('elapsed_seconds', 'ttl_elapsed_seconds', 'sleep_seconds'))
    assert 'caffeinate' in audit
    assert not marker.exists()
    assert (project / 'data/telegram_history.sqlite3').read_text() == 'source database fixture'


def test_unverified_lock_cannot_replace_runtime_or_binding(installation):
    project, staging, _, marker = installation
    reference = project.parent / '.venv-test-gap-detection/.python-runtime.json'
    saved = json.loads(reference.read_text())
    saved.pop('verification')
    reference.write_text(json.dumps(saved))
    binding = project / 'data/runtime/python.json'
    binding.parent.mkdir()
    binding.write_text('{"previous":true}')
    outcome = install(installation)
    assert outcome.returncode != 0
    assert binding.read_text() == '{"previous":true}'
    assert not (staging / 'telegram_connector_service').exists()
    assert not marker.exists()


def test_scheduled_runtime_drift_fails_new_audit_preserving_previous(installation):
    project, staging, env, marker = installation
    outcome = install(installation)
    assert outcome.returncode == 0, outcome.stderr
    service = staging / 'telegram_connector_service'
    binding = service / '.python-runtime.json'
    saved = json.loads(binding.read_text())
    saved['packages']['telethon'] = '999.0'
    binding.write_text(json.dumps(saved))
    audit = project / 'data/launchd/digest.last_attempt.json'
    previous = {'run_id': 'previous', 'status': 'sent', 'elapsed_seconds': 1800,
                'ttl_elapsed_seconds': 1650, 'sleep_seconds': 150}
    audit.write_text(json.dumps(previous))
    called = project / 'worker-called'
    (service / 'telegram_digest.py').write_text(
        'from pathlib import Path\nPath(' + repr(str(called)) + ').touch()\n')
    result = subprocess.run(['bash', str(service / 'scripts/run_telegram_digest.sh')],
        env={**env, 'TELEGRAM_CONNECTOR_PROJECT_ROOT': str(project)},
        text=True, capture_output=True, timeout=10)
    assert result.returncode != 0
    payload = json.loads(audit.read_text())
    assert payload['run_id'] != previous['run_id']
    assert payload['previous_supervisor'] == previous
    assert payload['status'] == 'failed'
    assert payload['reason'] == 'runtime_drift'
    assert payload['finished_at']
    assert not called.exists()
    assert not marker.exists()


def test_installer_creates_service_venv_from_verified_lock_offline(installation):
    project, staging, env, marker = installation
    env.pop('PYTHON_BIN', None)
    env.pop('BASE_PYTHON_BIN', None)
    outcome = install(installation)
    assert outcome.returncode == 0, outcome.stderr
    service = staging / 'telegram_connector_service'
    binding = json.loads((service / '.python-runtime.json').read_text())
    assert Path(binding['executable']).resolve() == (service / '.venv/bin/python').resolve()
    assert binding['lock_sha256'] == hashlib.sha256((service / 'requirements.lock').read_bytes()).hexdigest()
    assert binding['verification']['status'] == 'succeeded'
    assert not (service / '.python-runtime.json').is_symlink()
    result = subprocess.run([binding['executable'], '-I', str(service / 'common/python_environment.py'),
        '--check', '--requirements', str(service / 'requirements.lock'),
        '--record', str(service / '.python-runtime.json')],
        capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert not marker.exists()
    assert (project / 'data/telegram_history.sqlite3').read_text() == 'source database fixture'


@pytest.mark.parametrize('explicit_python', [True, False])
def test_installer_uses_external_wheels_without_project_cache(
        installation, tmp_path, explicit_python):
    project, staging, env, marker = installation
    wheels = tmp_path / 'external audited wheels'
    (project / 'third_party').rename(wheels)
    env['PIP_FIND_LINKS'] = str(wheels)
    if not explicit_python:
        env.pop('PYTHON_BIN', None)
        env.pop('BASE_PYTHON_BIN', None)
    outcome = install(installation)
    assert outcome.returncode == 0, outcome.stderr
    assert (staging / 'telegram_connector_service/.python-runtime.json').exists()
    assert not (project / 'third_party').exists()
    assert not marker.exists()


@pytest.mark.parametrize('explicit_python', [True, False])
@pytest.mark.parametrize('failure', ['schedule', 'bundle', 'publication'])
def test_failed_redeploy_preserves_installed_lock_and_runtime_binding(
        installation, explicit_python, failure):
    project, staging, env, marker = installation
    if not explicit_python:
        env.pop('PYTHON_BIN', None)
        env.pop('BASE_PYTHON_BIN', None)
    outcome = install(installation)
    assert outcome.returncode == 0, outcome.stderr
    service = staging / 'telegram_connector_service'
    binding = service / '.python-runtime.json'
    old_binding = binding.read_bytes()
    old_lock = (service / 'requirements.lock').read_bytes()
    lock = project / 'requirements.lock'
    lock.write_bytes(old_lock + b'\n# reviewed candidate\n')
    reference = project.parent / '.venv-test-gap-detection/.python-runtime.json'
    tested = json.loads(reference.read_text())
    tested['lock_sha256'] = hashlib.sha256(lock.read_bytes()).hexdigest()
    tested['verification']['lock_sha256'] = tested['lock_sha256']
    reference.write_text(json.dumps(tested))
    if explicit_python and failure != 'schedule':
        from common import python_environment, python_runtime
        prepared = python_environment.prepare_venv(
            python_runtime.probe_python(sys.executable), project.parent / 'candidate environment',
            requirements=lock, constraints=project / 'requirements.txt',
            wheels=project / 'third_party', require_hashes=True)
        env['PYTHON_BIN'] = prepared['executable']
    if failure == 'schedule':
        config = project / 'config/runtime.local.toml'
        config.write_text(config.read_text().replace('time = "08:00"', 'time = "invalid"'))
    elif failure == 'bundle':
        (project / 'config/analysis_prompts.toml').unlink()
    else:
        io_module = project.parent / 'common/json_io.py'
        io_module.write_text(io_module.read_text() + '\n'
            '_original_write = write_text_atomic\n'
            'def write_text_atomic(path, text):\n'
            '    result = _original_write(path, text)\n'
            f'    if str(path) == {str(binding)!r} and {tested["lock_sha256"]!r} in text:\n'
            '        raise OSError("fixture publication failure")\n'
            '    return result\n')
    outcome = install(installation)
    assert outcome.returncode != 0
    assert binding.read_bytes() == old_binding
    assert (service / 'requirements.lock').read_bytes() == old_lock
    check = subprocess.run([sys.executable if explicit_python else
        str(service / '.venv/bin/python'), '-I', str(service / 'common/python_environment.py'),
        '--check', '--requirements', str(service / 'requirements.lock'),
        '--record', str(binding)], capture_output=True, text=True, timeout=15)
    assert check.returncode == 0, check.stderr
    assert not marker.exists()


def test_installation_uses_selected_candidate_lock(tmp_path, monkeypatch, request):
    source = Path(os.environ.get('PYTHON_RUNTIME_TEST_LOCK') or
                  ROOT / 'telegram_connector/requirements.lock')
    candidate = tmp_path / 'selected candidate.lock'
    candidate.write_bytes(source.read_bytes() + b'\n# selected candidate fixture\n')
    monkeypatch.setenv('PYTHON_RUNTIME_TEST_LOCK', str(candidate))
    project, _, _, _ = request.getfixturevalue('installation')
    assert (project / 'requirements.lock').read_bytes() == candidate.read_bytes()


def test_non_directory_venv_is_preserved_on_rejected_install(installation):
    _, staging, env, marker = installation
    env.pop('PYTHON_BIN', None)
    env.pop('BASE_PYTHON_BIN', None)
    service = staging / 'telegram_connector_service'
    service.mkdir(parents=True)
    path = service / '.venv'
    path.write_text('not an owned environment')
    outcome = install(installation)
    assert outcome.returncode != 0
    assert path.read_text() == 'not an owned environment'
    assert not marker.exists()


@pytest.mark.parametrize('managed_venv', [False, True])
def test_failed_legacy_upgrade_restores_startup_bundle(installation, tmp_path, managed_venv):
    project, staging, env, marker = installation
    baseline = '22dbfcd'  # Last recorded installer before lock-based startup.
    available = subprocess.run(['git', 'cat-file', '-e', baseline + ':telegram_connector'],
                               cwd=ROOT, capture_output=True)
    if available.returncode:
        pytest.skip('Legacy installer fixture needs its recorded git object')
    current = tmp_path / 'current source'
    for path in (project, project.parent / 'common', project.parent / 'telegram_shared'):
        shutil.copytree(path, current / path.name)
        shutil.rmtree(path)
    archive = subprocess.run(['git', 'archive', baseline, 'telegram_connector', 'common',
                              'telegram_shared'], cwd=ROOT, capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as snapshot:
        snapshot.extractall(project.parent, filter='data')
    shutil.copy2(current / project.name / 'config/runtime.local.toml',
                 project / 'config/runtime.local.toml')
    shutil.copytree(current / project.name / 'data', project / 'data', dirs_exist_ok=True)
    outcome = install(installation)
    assert outcome.returncode == 0, outcome.stderr
    service = staging / 'telegram_connector_service'
    binding = project / 'data/runtime/python.json'
    old_binding = binding.read_bytes()
    assert 'lock_sha256' not in json.loads(old_binding)
    assert not (service / 'requirements.lock').exists()
    started = tmp_path / 'legacy-started'
    (service / 'telegram_bridge.py').write_text(
        'from pathlib import Path\nPath(' + repr(str(started)) + ').touch()\n')

    def startup_contents():
        result = {}
        for path in service.rglob('*'):
            relative = path.relative_to(service)
            if relative.parts[0] == 'data':
                continue
            result[str(relative)] = (('link', os.readlink(path)) if path.is_symlink()
                else ('dir',) if path.is_dir() else ('file', path.read_bytes()))
        return result

    old_startup = startup_contents()
    for name in (project.name, 'common', 'telegram_shared'):
        target = project.parent / name
        if name != project.name:
            shutil.rmtree(target)
        shutil.copytree(current / name, target, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('data') if name == project.name else None)
    retained = service / 'data/retained.txt'
    retained.write_text('before attempt')
    io_module = project.parent / 'common/json_io.py'
    io_module.write_text(io_module.read_text() + '\n'
        '_original_write = write_text_atomic\n'
        'def write_text_atomic(path, text):\n'
        '    result = _original_write(path, text)\n'
        f'    if str(path) == {str(service / ".python-runtime.json")!r} and "lock_sha256" in text:\n'
        f'        Path({str(retained)!r}).write_text("retained during attempt")\n'
        '        raise OSError("fixture legacy publication failure")\n'
        '    return result\n')
    if managed_venv:
        env.pop('PYTHON_BIN', None)
        env.pop('BASE_PYTHON_BIN', None)
    outcome = install(installation)
    assert outcome.returncode != 0
    assert binding.read_bytes() == old_binding
    restored = startup_contents()
    assert restored.keys() == old_startup.keys()
    assert [name for name in old_startup if restored[name] != old_startup[name]] == []
    assert retained.read_text() == 'retained during attempt'
    result = subprocess.run(['bash', str(service / 'scripts/run_telegram_bridge.sh')],
        env={'PATH': os.defpath, 'HOME': str(tmp_path), 'PYTHONDONTWRITEBYTECODE': '1',
             'TELEGRAM_CONNECTOR_PROJECT_ROOT': str(project)},
        capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert started.exists()
    assert not marker.exists()


@pytest.mark.parametrize('second_fails', [False, True])
def test_staging_roots_keep_independent_runtime_bindings(installation, tmp_path, second_fails):
    project, staging, env, marker = installation
    env.pop('PYTHON_BIN', None)
    env.pop('BASE_PYTHON_BIN', None)
    outcome = install(installation)
    assert outcome.returncode == 0, outcome.stderr
    first = staging / 'telegram_connector_service'
    record = first / '.python-runtime.json'
    first_binding = record.read_bytes()
    first_lock = (first / 'requirements.lock').read_bytes()
    shared = project / 'data/runtime/python.json'
    shared_before = shared.read_bytes() if shared.exists() else None
    lock = project / 'requirements.lock'
    lock.write_bytes(first_lock + b'\n# second installation\n')
    reference = project.parent / '.venv-test-gap-detection/.python-runtime.json'
    tested = json.loads(reference.read_text())
    tested['lock_sha256'] = hashlib.sha256(lock.read_bytes()).hexdigest()
    tested['verification']['lock_sha256'] = tested['lock_sha256']
    reference.write_text(json.dumps(tested))
    second_stage = tmp_path / 'second staging'
    second = second_stage / 'telegram_connector_service'
    if second_fails:
        io_module = project.parent / 'common/json_io.py'
        io_module.write_text(io_module.read_text() + '\n'
            '_original_write = write_text_atomic\n'
            'def write_text_atomic(path, text):\n'
            '    result = _original_write(path, text)\n'
            f'    if str(path) == {str(second / ".python-runtime.json")!r}:\n'
            '        raise OSError("fixture second installation failure")\n'
            '    return result\n')
    outcome = install((project, second_stage, env, marker))
    assert (outcome.returncode != 0) == second_fails, outcome.stderr
    assert record.read_bytes() == first_binding
    assert (first / 'requirements.lock').read_bytes() == first_lock
    assert (shared.read_bytes() if shared.exists() else None) == shared_before
    assert not record.is_symlink()
    if not second_fails:
        second_record = second / '.python-runtime.json'
        assert not second_record.is_symlink()
        saved = json.loads(second_record.read_text())
        checked = subprocess.run([saved['executable'], '-I',
            str(second / 'common/python_environment.py'), '--check',
            '--requirements', str(second / 'requirements.lock'), '--record', str(second_record)],
            capture_output=True, text=True, timeout=15)
        assert checked.returncode == 0, checked.stderr
    runner_env = {**env, 'TELEGRAM_CONNECTOR_PROJECT_ROOT': str(project)}
    for name, module in [('bridge', 'telegram_bridge.py'), ('digest', 'telegram_digest.py')]:
        called = tmp_path / (name + '-started')
        (first / module).write_text('from pathlib import Path\nPath(' + repr(str(called)) + ').touch()\n')
        checked = subprocess.run(['/bin/bash', str(first / 'scripts' / ('run_telegram_' + name + '.sh'))],
            env=runner_env, capture_output=True, text=True, timeout=15)
        assert checked.returncode == 0, checked.stderr
        assert called.exists()
    assert not marker.exists()


@pytest.mark.parametrize('publication_fails', [False, True])
def test_existing_binding_symlink_preserves_shared_record(installation, publication_fails):
    project, staging, env, marker = installation
    env.pop('PYTHON_BIN', None)
    env.pop('BASE_PYTHON_BIN', None)
    outcome = install(installation)
    assert outcome.returncode == 0, outcome.stderr
    service = staging / 'telegram_connector_service'
    record = service / '.python-runtime.json'
    old_binding = record.read_bytes()
    shared = project / 'data/runtime/python.json'
    shared.parent.mkdir(parents=True, exist_ok=True)
    shared.write_bytes(old_binding)
    record.unlink()
    record.symlink_to(shared)
    old_lock = (service / 'requirements.lock').read_bytes()
    lock = project / 'requirements.lock'
    lock.write_bytes(old_lock + b'\n# migrate former shared binding\n')
    reference = project.parent / '.venv-test-gap-detection/.python-runtime.json'
    tested = json.loads(reference.read_text())
    tested['lock_sha256'] = hashlib.sha256(lock.read_bytes()).hexdigest()
    tested['verification']['lock_sha256'] = tested['lock_sha256']
    reference.write_text(json.dumps(tested))
    if publication_fails:
        io_module = project.parent / 'common/json_io.py'
        io_module.write_text(io_module.read_text() + '\n'
            '_original_write = write_text_atomic\n'
            'def write_text_atomic(path, text):\n'
            '    result = _original_write(path, text)\n'
            f'    if str(path) == {str(record)!r}:\n'
            '        raise OSError("fixture symlink publication failure")\n'
            '    return result\n')
    outcome = install(installation)
    assert (outcome.returncode != 0) == publication_fails, outcome.stderr
    assert shared.read_bytes() == old_binding
    assert record.is_symlink() is publication_fails
    if publication_fails:
        assert record.read_bytes() == old_binding
        assert os.readlink(record) == str(shared)
        assert (service / 'requirements.lock').read_bytes() == old_lock
    else:
        assert json.loads(record.read_text())['lock_sha256'] == tested['lock_sha256']
    saved = json.loads(record.read_text())
    checked = subprocess.run([saved['executable'], '-I',
        str(service / 'common/python_environment.py'), '--check',
        '--requirements', str(service / 'requirements.lock'), '--record', str(record)],
        capture_output=True, text=True, timeout=15)
    assert checked.returncode == 0, checked.stderr
    assert not marker.exists()
