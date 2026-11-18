"""Exercise /digest admission and the real supervisor with isolated workers."""
import json
import os
from pathlib import Path
import signal
import subprocess
import time

import pytest

from telegram_connector import telegram_bridge as bridge
from telegram_connector import digest_telemetry


UPDATE = {'message': {'text': '/digest @fixture since=2026-11-16 until=2026-11-16 user',
    'chat': {'id': 42}, 'from': {'id': 7}}}


def test_bridge_digest_uses_supervisor_and_preserves_worker_arguments(tmp_path, monkeypatch):
    config = {'bridge': {'allowed_chat_ids': '42', 'allowed_user_ids': '7',
                        'worker_process_timeout_seconds': '1'},
              'digest': {'run_total_timeout_seconds': '37', 'termination_grace_seconds': '4'}}
    monkeypatch.setattr(bridge, 'load_runtime_config', lambda: config)
    monkeypatch.setattr(bridge, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(bridge, 'resolve_worker_process_timeout_seconds',
                        lambda *a: pytest.fail('Supervised digest must not resolve bridge timeout'))
    calls = []
    audit = tmp_path / 'data/launchd/digest.last_attempt.json'
    audit.parent.mkdir(parents=True)
    previous = {'run_id': 'previous', 'status': 'sent', 'elapsed_seconds': 1800,
                'ttl_elapsed_seconds': 1650, 'sleep_seconds': 150}
    audit.write_text(json.dumps(previous))
    monkeypatch.setattr(bridge.subprocess, 'run', lambda argv, **kw:
        calls.append((argv, kw, json.loads(audit.read_text()))) or
        subprocess.CompletedProcess(argv, 0, '{"status":"sent"}', ''))
    monkeypatch.setattr(bridge, 'send_text_chunks', lambda *a, **kw: pytest.fail('unexpected reply'))
    bridge.handle_history_command('fixture', config, UPDATE,
                                  secret_env={'TELEGRAM_BOT_TOKEN': 'fixture-secret'})
    argv, options, prepared = calls[0]
    assert prepared['previous_supervisor'] == previous
    assert prepared['run_id'] != previous['run_id']
    assert prepared['status'] == 'started'
    assert Path(argv[1]).name == 'ttl_runner.py'
    assert argv[argv.index('--timeout-seconds') + 1] == '37'
    assert argv[argv.index('--grace-seconds') + 1] == '4'
    assert argv[argv.index('--sleep-policy') + 1] == 'pause'
    assert '--use-caffeinate' in argv
    assert argv[argv.index('--audit-file') + 1] == str(tmp_path / 'data/launchd/digest.last_attempt.json')
    assert argv[argv.index('--') + 1:] == [bridge.sys.executable, str(bridge.DIGEST_FILE), 'run',
        '--channel', '@fixture', '--since', '2026-11-16', '--until', '2026-11-16', '--auth-mode', 'user',
        '--audit-run-id', prepared['run_id']]
    assert options['timeout'] is None
    assert options['env']['TELEGRAM_BOT_TOKEN'] == 'fixture-secret'


def test_invalid_digest_supervisor_config_does_not_stop_bridge(tmp_path, monkeypatch):
    config = {'bridge': {'allowed_chat_ids': '42', 'allowed_user_ids': '7'},
              'digest': {'run_total_timeout_seconds': 'invalid'}}
    monkeypatch.setattr(bridge, 'load_runtime_config', lambda: config)
    monkeypatch.setattr(bridge.subprocess, 'run', lambda *a, **kw: pytest.fail('unexpected process'))
    replies = []
    monkeypatch.setattr(bridge, 'send_text_message', lambda token, chat, text: replies.append(text))
    bridge.handle_history_command('fixture', config, UPDATE, secret_env={})
    assert len(replies) == 1
    assert 'digest.run_total_timeout_seconds' in replies[0]


def test_audit_preparation_failure_does_not_launch_supervisor(tmp_path, monkeypatch):
    config = {'bridge': {'allowed_chat_ids': '42', 'allowed_user_ids': '7'}}
    monkeypatch.setattr(bridge, 'load_runtime_config', lambda: config)
    monkeypatch.setattr(bridge, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(bridge.subprocess, 'run', lambda *a, **kw: pytest.fail('unexpected process'))

    def fail(*args):
        raise OSError('fixture audit write failed')

    monkeypatch.setattr(digest_telemetry, 'prepare_supervised_audit', fail)
    replies = []
    monkeypatch.setattr(bridge, 'send_text_message', lambda token, chat, text: replies.append(text))
    bridge.handle_history_command('fixture', config, UPDATE, secret_env={})
    assert replies == ['fixture audit write failed']


def test_pre_supervisor_timeout_preserves_both_run_identities(tmp_path):
    from common import ttl_runner
    import sys

    audit = tmp_path / 'audit.json'
    previous = {'run_id': 'previous', 'status': 'sent', 'elapsed_seconds': 1800,
                'ttl_elapsed_seconds': 1650, 'sleep_seconds': 150}
    audit.write_text(json.dumps(previous))
    run_id = digest_telemetry.build_digest_run_id()
    digest_telemetry.prepare_supervised_audit(audit, run_id)
    # No worker audit is published: even import/startup hangs keep the old receipt intact.
    result = ttl_runner.run_with_ttl(
        [sys.executable, '-c', 'import time;time.sleep(30)'],
        timeout_seconds=.1, grace_seconds=.1, poll_interval_seconds=.02,
        timeout_exit_code=124, term_signal='TERM', kill_signal='KILL',
        audit_file=audit, timeout_reason='fixture_startup_timeout', use_caffeinate=False)
    assert result == 124
    receipt = json.loads(audit.read_text())
    assert receipt['run_id'] == run_id
    assert receipt['status'] == 'timed_out'
    assert receipt['previous_supervisor'] == previous
    assert receipt['elapsed_seconds'] < 10


@pytest.mark.parametrize('mode', ['success', 'timeout', 'SIGTERM', 'SIGINT'])
def test_bridge_digest_supervisor_records_outcome_and_cleans_process_group(tmp_path, monkeypatch, mode):
    audit = tmp_path / 'data/launchd/digest.last_attempt.json'
    marker = tmp_path / 'worker.json'
    worker = tmp_path / 'isolated_digest.py'
    worker.write_text('''import json, os, signal, subprocess, sys, time
from pathlib import Path
root = Path(os.environ['TELEGRAM_CONNECTOR_PROJECT_ROOT'])
audit = root / 'data/launchd/digest.last_attempt.json'
audit.parent.mkdir(parents=True, exist_ok=True)
mode = os.environ['FIXTURE_MODE']
audit.write_text(json.dumps({'run_id': 'isolated', 'status': 'running', 'phase': 'fixture'}))
if mode == 'success':
    audit.write_text(json.dumps({'run_id': 'isolated', 'status': 'sent', 'phase': 'completed'}))
    print('{"status":"sent"}')
else:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    child = subprocess.Popen([sys.executable, '-c', 'import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)'])
    (root / 'worker.json').write_text(json.dumps({'worker': os.getpid(), 'child': child.pid}))
    time.sleep(30)
''')
    config = {'bridge': {'allowed_chat_ids': '42', 'allowed_user_ids': '7',
                        'worker_process_timeout_seconds': '1'},
              'digest': {'run_total_timeout_seconds': '1' if mode == 'timeout' else '10',
                         'termination_grace_seconds': '1'}}
    monkeypatch.setattr(bridge, 'load_runtime_config', lambda: config)
    monkeypatch.setattr(bridge, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(bridge, 'BASE_DIR', tmp_path)
    monkeypatch.setattr(bridge, 'DIGEST_FILE', worker)
    monkeypatch.setattr(bridge, 'build_history_client_subprocess_env', lambda secrets:
                        {'TELEGRAM_CONNECTOR_PROJECT_ROOT': str(tmp_path), 'FIXTURE_MODE': mode})
    replies = []
    monkeypatch.setattr(bridge, 'send_text_chunks', lambda *a, **kw: replies.append(a[2]))
    if mode.startswith('SIG'):
        def send_signal(argv, **kwargs):
            proc = subprocess.Popen(argv, cwd=kwargs['cwd'], env=kwargs['env'],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 5
                while not marker.exists() and proc.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                assert marker.exists(), proc.communicate(timeout=2)
                proc.send_signal(getattr(signal, mode))
                stdout, stderr = proc.communicate(timeout=8)
                return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate(timeout=2)
        monkeypatch.setattr(bridge.subprocess, 'run', send_signal)
    try:
        bridge.handle_history_command('fixture', config, UPDATE, secret_env={})
        receipt = json.loads(audit.read_text())
        expected = 'sent' if mode == 'success' else 'timed_out' if mode == 'timeout' else 'cancelled'
        assert receipt['status'] == expected
        assert receipt['run_id'] == 'isolated'
        assert receipt['sleep_policy'] == 'pause'
        assert all(receipt[key] >= 0 for key in ('elapsed_seconds', 'ttl_elapsed_seconds', 'sleep_seconds'))
        assert 'caffeinate' in receipt
        if mode == 'success':
            assert replies == []
        elif mode == 'timeout':
            assert receipt['timeout_reason'] == 'process_ttl_expired'
            assert replies
        else:
            assert receipt['reason'] == 'external_signal'
            assert receipt['cancel_signal'] == mode
            assert replies
        if marker.exists():
            for pid in json.loads(marker.read_text()).values():
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(0.02)
                else:
                    pytest.fail(f'Fixture process survived supervisor cleanup: {pid}')
    finally:
        if marker.exists():
            try:
                os.killpg(json.loads(marker.read_text())['worker'], signal.SIGKILL)
            except ProcessLookupError:
                pass
