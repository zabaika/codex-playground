"""Exercise the canonical installer without touching user services or HOME."""

import ast
import json
import io
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
    "telegram_analysis.py", "analysis_store.py", "analysis_evidence.py",
    "analysis_settings.py", "analysis_rendering.py", "analysis_pipeline.py",
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
    shutil.copy2(ROOT / "telegram_connector/requirements.txt", project / "requirements.txt")
    shutil.copytree(ROOT / "telegram_connector/third_party", project / "third_party")
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
    binding = json.loads((project / "data/runtime/python.json").read_text())
    assert binding["policy_digest"]
    assert binding["architecture"] == "arm64"
    assert binding["dependency_inputs"]["requirements.txt"]
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
        "digest_pipeline": {"digest_settings", "digest_rendering", "history_messages", "history_store"},
        "analysis_settings": set(),
        "analysis_evidence": {"analysis_settings", "analysis_store"},
        "analysis_rendering": set(),
        "analysis_pipeline": {"analysis_settings", "analysis_evidence", "analysis_store", "telegram_history_client"},
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
