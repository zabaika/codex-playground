"""Digest telemetry uses isolated storage and never contacts providers."""
import json
import io
import subprocess
import sys
import os
from pathlib import Path
from types import SimpleNamespace
from urllib import error

from common import sqlite as common_sqlite
from common import ttl_runner
from telegram_connector import history_store
from telegram_connector import digest_telemetry
from telegram_connector import digest_pipeline
from telegram_shared import openai_api
from telegram_shared import bot_api
from telegram_shared.ai_usage_stats import fetch_ai_usage_summary, format_ai_usage_summary
import pytest
from telegram_shared import openai_usage


def test_digest_schema_is_additive_and_snapshot_joins_attempt_transaction(tmp_path):
    conn = common_sqlite.connect_sqlite(tmp_path / 'history.db')
    history_store.init_db(conn)
    before = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    history_store.init_db(conn)
    assert 'digest_runs' in before
    columns = {row[1] for row in conn.execute('PRAGMA table_info(ai_usage_log)')}
    assert {'run_id', 'attempt_index'} <= columns
    assert not {'digest_phase_runs', 'http_transport_events'} & before
    snapshot = json.dumps({'version': 1, 'phases': [], 'delivery': []})
    with common_sqlite.write_tx(conn):
        history_store.write_digest_run(conn, run_id='fixture-run',
            started_at='2026-11-17T10:00:00+00:00',
            updated_at='2026-11-17T10:00:00+00:00', finished_at=None,
            status='running', payload_json=snapshot)
        assert conn.in_transaction
    row = conn.execute('SELECT * FROM digest_runs').fetchone()
    assert row['run_id'] == 'fixture-run'
    assert json.loads(row['payload_json'])['version'] == 1
    conn.close()


def test_recorder_flushes_buffer_with_attempt_and_report_is_read_only(tmp_path):
    conn = common_sqlite.connect_sqlite(tmp_path / 'history.db')
    history_store.init_db(conn)
    clock = [0.0]
    recorder = digest_telemetry.DigestRecorder(conn, 'fixture',
        '2026-11-17T10:00:00+00:00', monotonic_func=lambda: clock[0])
    recorder.flush()
    commits = []
    conn.set_trace_callback(lambda sql: commits.append(sql) if sql == 'COMMIT' else None)
    clock[0] = 1
    recorder.enter('prepare', '@fixture')
    clock[0] = 2
    recorder.enter('ai', '@fixture')
    assert commits == []
    fields = dict(stage='single', channel='@fixture', since=None, until=None,
        model='fixture', request_index=1, message_count=1, prompt_text='data',
        cache_info=openai_usage.build_prompt_cache_info(cache_key='fixture',
            system_instructions='', prompt_text='data', shared_prefix=''))
    identity = recorder.begin_ai(1, fields)
    assert len(commits) == 1
    assert not conn.in_transaction
    clock[0] = 3
    recorder.end_ai(identity, {'usage': {'input_tokens': 2, 'output_tokens': 1}}, None, 1000)
    assert len(commits) == 2
    recorder.finish('sent')
    before = conn.total_changes
    report = digest_telemetry.summarize_run(conn, 'fixture')
    assert conn.total_changes == before
    assert report['effective_status'] == 'sent'
    assert report['usage']['physical_attempts'] == 1
    assert report['usage']['input_tokens'] == 2
    assert report['phases'][0]['elapsed_ms'] == 1000
    recorder.flush()
    assert len(commits) == 3
    conn.close()


def test_attempt_intent_and_receipt_are_caller_owned_and_unknown_usage_stays_null(tmp_path):
    conn = common_sqlite.connect_sqlite(tmp_path / 'history.db')
    history_store.init_db(conn)
    fields = dict(feature='digest', created_at='2026-11-17T10:00:00+00:00',
        stage='single', channel='@fixture', since=None, until=None,
        model='fixture', request_index=1, message_count=1, prompt_text='data',
        cache_info=openai_usage.build_prompt_cache_info(cache_key='fixture',
            system_instructions='', prompt_text='data', shared_prefix=''))
    with common_sqlite.write_tx(conn):
        row_id = openai_usage.begin_openai_attempt(conn, run_id='run',
            attempt_id='attempt', attempt_index=1, **fields)
        assert conn.in_transaction
    with common_sqlite.write_tx(conn):
        openai_usage.settle_openai_attempt(conn, row_id, response={'id': 'response'},
            status='succeeded', error=None, latency_ms=12)
        assert conn.in_transaction
    row = conn.execute('SELECT * FROM ai_usage_log').fetchone()
    assert row['run_id'] == 'run'
    assert row['started_at'] == fields['created_at']
    assert row['input_tokens'] is None
    assert row['output_tokens'] is None
    assert row['latency_ms'] == 12
    assert row['status'] == 'succeeded'
    conn.close()


def test_report_cli_preserves_database_and_handles_missing_telemetry(tmp_path):
    path = tmp_path / 'legacy.db'
    conn = common_sqlite.connect_sqlite(path)
    conn.execute('CREATE TABLE legacy(value TEXT)')
    conn.close()
    before = path.read_bytes()
    script = Path(__file__).resolve().parents[1] / 'scripts/report_digest.py'
    result = subprocess.run([sys.executable, str(script), '--db', str(path)],
                            capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)['reason'] == 'digest_telemetry_unavailable'
    assert path.read_bytes() == before


def test_real_retry_loop_records_each_attempt_and_buffers_backoff(tmp_path, monkeypatch):
    path = tmp_path / 'history.db'
    conn = common_sqlite.connect_sqlite(path)
    history_store.init_db(conn)
    clock = [0.0]
    recorder = digest_telemetry.DigestRecorder(conn, 'retry',
        '2026-11-17T10:00:00+00:00', monotonic_func=lambda: clock[0])
    recorder.flush()
    calls, pauses = [], []

    def send(req, **kwargs):
        assert not conn.in_transaction
        witness = common_sqlite.connect_sqlite(path)
        try:
            rows = witness.execute('SELECT status FROM ai_usage_log ORDER BY id').fetchall()
            assert rows[-1]['status'] == 'pending'
        finally:
            witness.close()
        calls.append(req.data)
        if len(calls) == 1:
            raise error.HTTPError(req.full_url, 429, 'limit', {}, io.BytesIO(b'{}'))
        return io.BytesIO(json.dumps({'id': 'fixture', 'model': 'actual-fixture',
            'service_tier': 'default', 'status': 'completed', 'output_text': 'answer',
            'usage': {'input_tokens': 12, 'output_tokens': 3,
                      'input_tokens_details': {'cached_tokens': 4}}}).encode())

    def sleep(seconds):
        assert not conn.in_transaction
        pauses.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(openai_api._JSON_OPENER, 'open', send)
    monkeypatch.setattr(digest_pipeline.time, 'sleep', sleep)
    config = SimpleNamespace(model='fixture', store_prompt_text=False,
        system_instructions='instructions', cache_breakpoint_marker='boundary',
        openai_reasoning_effort='none', openai_reasoning_summary='none',
        openai_max_output_tokens=100, openai_timeout_seconds=10,
        openai_retry_attempts=2, openai_retry_backoff_seconds=1)
    cache = openai_usage.build_prompt_cache_info(cache_key='fixture',
        system_instructions='instructions', prompt_text='data', shared_prefix='')
    token = digest_telemetry.CURRENT.set(recorder)
    try:
        result = digest_pipeline.request_digest_with_usage(conn, api_key='fixture',
            config=config, channel='@fixture',
            since='2026-11-16', until='2026-11-16', stage='single',
            request_index=1, message_count=1, cache_info=cache, prompt='data')
        recorder.finish('sent')
    finally:
        digest_telemetry.CURRENT.reset(token)
    assert result.text == 'answer'
    rows = list(conn.execute('SELECT * FROM ai_usage_log ORDER BY id'))
    assert len(rows) == len(calls) == 2
    assert calls[0] == calls[1]
    assert [row['status'] for row in rows] == ['failed', 'succeeded']
    assert [row['attempt_index'] for row in rows] == [1, 2]
    assert rows[0]['input_tokens'] is None
    assert rows[0]['backoff_ms'] == round(pauses[0] * 1000)
    assert rows[1]['actual_model'] == 'actual-fixture'
    assert rows[1]['actual_service_tier'] == 'default'
    conn.close()


def test_delivery_retry_records_intent_unknown_and_receipt_without_text(tmp_path):
    conn = common_sqlite.connect_sqlite(tmp_path / 'history.db')
    history_store.init_db(conn)
    recorder = digest_telemetry.DigestRecorder(conn, 'delivery',
        '2026-11-17T10:00:00+00:00')
    calls = []

    def send(req, **kwargs):
        assert not conn.in_transaction
        snapshot = json.loads(conn.execute('SELECT payload_json FROM digest_runs').fetchone()[0])
        assert snapshot['delivery'][0]['attempts'][-1]['outcome'] == 'intent'
        calls.append(req.data)
        if len(calls) == 1:
            raise error.URLError('secret-token')
        return io.BytesIO(b'{"ok":true,"result":{"message_id":17}}')

    with recorder.observe_delivery('@fixture'):
        result = bot_api.call_bot_api_with_retry(lambda: bot_api.api_call(
            'secret-token', 'sendMessage', {'chat_id': 1, 'text': 'private text'},
            urlopen_func=send), method='sendMessage', attempts=2, backoff_seconds=0)
    recorder.finish('sent')
    assert result['message_id'] == 17
    snapshot = json.loads(conn.execute('SELECT payload_json FROM digest_runs').fetchone()[0])
    assert len(snapshot['delivery']) == 1
    attempts = snapshot['delivery'][0]['attempts']
    assert [item['outcome'] for item in attempts] == ['unknown', 'confirmed']
    assert attempts[-1]['message_id'] == 17
    assert 'private text' not in json.dumps(snapshot)
    assert 'secret-token' not in json.dumps(snapshot)
    conn.close()


def test_stats_distinguish_physical_attempts_operations_and_missing_usage(tmp_path):
    path = tmp_path / 'history.db'
    conn = common_sqlite.connect_sqlite(path)
    history_store.init_db(conn)
    for number, status in enumerate(('unknown', 'pending', 'succeeded'), 1):
        conn.execute("""INSERT INTO ai_usage_log
            (created_at,feature,stage,channel,model,request_index,status,run_id,attempt_id)
            VALUES ('2026-11-17','digest','single','@fixture','fixture',1,?,'run',?)""",
            (status, str(number)))
    conn.close()
    from telegram_connector.telegram_bridge import fetch_digest_usage_summary
    summary = fetch_digest_usage_summary({'paths': {'history_db': str(path)}}, row_limit=100)
    assert summary['global']['physical_attempts'] == 3
    assert summary['global']['observed_logical_operations'] == 1
    assert summary['global']['unknown_requests'] == 1
    assert summary['global']['pending_requests'] == 1
    assert summary['global']['unknown_usage_requests'] == 3
    rendered = format_ai_usage_summary(summary, title='Fixture')
    assert 'unknown usage' in rendered


def test_receipt_storage_failure_does_not_repeat_accepted_send(tmp_path):
    path = tmp_path / 'history.db'
    conn = common_sqlite.connect_sqlite(path)
    history_store.init_db(conn)
    recorder = digest_telemetry.DigestRecorder(conn, 'storage-failure',
        '2026-11-17T10:00:00+00:00')
    calls = []

    def accepted(req, **kwargs):
        calls.append(req.data)
        conn.close()
        return io.BytesIO(b'{"ok":true,"result":{"message_id":19}}')

    with pytest.raises(digest_telemetry.TelemetryStorageError), recorder.observe_delivery('@fixture'):
        bot_api.call_bot_api_with_retry(lambda: bot_api.api_call('fixture', 'sendMessage',
            {'chat_id': 1, 'text': 'fixture'}, urlopen_func=accepted),
            method='sendMessage', attempts=3, backoff_seconds=0)
    assert len(calls) == 1
    check = common_sqlite.connect_sqlite(path)
    report = digest_telemetry.summarize_run(check, 'storage-failure')
    assert report['delivery'][0]['attempts'][0]['outcome'] == 'intent'
    assert report['effective_status'] == 'unknown'
    check.close()


@pytest.mark.parametrize('status,signal', [
    ('timed_out', None), ('cancelled', 'SIGTERM'), ('cancelled', 'SIGINT'),
])
def test_supervisor_receipt_matches_run_and_survives_audit_replacement(tmp_path, status, signal):
    conn = common_sqlite.connect_sqlite(tmp_path / 'history.db')
    history_store.init_db(conn)
    recorder = digest_telemetry.DigestRecorder(conn, 'interrupted',
        '2026-11-17T10:00:00+00:00')
    recorder.flush()
    mismatch = {'run_id': 'other', 'status': 'timed_out'}
    assert digest_telemetry.summarize_run(conn, 'interrupted', mismatch)['effective_status'] == 'unknown'
    audit = {'run_id': 'interrupted', 'status': status, 'sleep_seconds': 0,
             'finished_at': '2026-11-17T10:01:00+00:00'}
    if signal:
        audit.update(reason='external_signal', cancel_signal=signal)
    live = digest_telemetry.summarize_run(conn, 'interrupted', audit)
    assert live['effective_status'] == status
    assert live['status_source'] == 'supervisor'
    digest_telemetry.retain_supervisor_outcome(conn, audit)
    report = digest_telemetry.summarize_run(conn, 'interrupted', mismatch)
    assert report['persisted_status'] == 'running'
    assert report['effective_status'] == status
    assert report['status_source'] == 'supervisor'
    assert report['supervisor']['sleep_seconds'] == 0
    if signal:
        assert report['supervisor']['reason'] == 'external_signal'
        assert report['supervisor']['cancel_signal'] == signal
    conn.close()


@pytest.mark.parametrize('stage', ['runtime', 'database'])
def test_early_ttl_identifies_new_run_and_preserves_previous_receipt(tmp_path, monkeypatch, stage):
    path, audit = tmp_path / 'history.db', tmp_path / 'audit.json'
    conn = common_sqlite.connect_sqlite(path)
    history_store.init_db(conn)
    recorder = digest_telemetry.DigestRecorder(conn, 'previous', digest_telemetry.utc_now())
    recorder.flush()
    previous = {'run_id': 'previous', 'status': 'cancelled',
                'reason': 'external_signal', 'cancel_signal': 'SIGTERM',
                'elapsed_seconds': 1800, 'ttl_elapsed_seconds': 1650,
                'sleep_seconds': 150}
    audit.write_text(json.dumps(previous))
    conn.close()
    gate = tmp_path / 'supervisor-published'
    original_write = ttl_runner.write_audit_payload

    def publish(path, payload):
        original_write(path, payload)
        gate.touch()

    monkeypatch.setattr(ttl_runner, 'write_audit_payload', publish)
    code = '''
import json, os, socket, sys, time
from pathlib import Path
from types import SimpleNamespace
while not Path(os.environ['FIXTURE_SUPERVISOR_READY']).exists():
    time.sleep(.005)
sys.path.insert(0, sys.argv[1])
import telegram_digest as digest
from common import sqlite as db
path, audit, stage = Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]
digest.DIGEST_LAST_ATTEMPT_LOG = audit
def forbid_network(*args, **kwargs):
    raise AssertionError('unexpected network access')
socket.socket.connect = forbid_network
def block():
    time.sleep(30)
digest.history_client.resolve_runtime = block if stage == 'runtime' else lambda: SimpleNamespace(default_auth_mode='user')
digest.history_client.load_runtime_config = lambda: {}
digest.digest_settings.resolve_digest_config = lambda config: SimpleNamespace(sync_mode='update', sync_total_timeout_seconds=1)
digest.digest_settings.resolve_digest_window = lambda config: ('2026-11-17', '2026-11-17')
digest.digest_settings.normalize_digest_window_values = lambda since, until: (since, until)
digest.digest_settings.resolve_digest_limits = lambda *args: SimpleNamespace()
digest.history_client.resolve_channels_argument = lambda *args: []
digest.history_client.connect_db = lambda runtime: db.connect_sqlite(path)
if stage == 'database':
    digest.history_client.init_db = lambda conn: block()
if stage == 'recover':
    def stop_before_external_work(*args, **kwargs):
        raise SystemExit(0)
    digest.digest_pipeline.digest_telemetry.DigestRecorder = stop_before_external_work
args = digest.build_parser().parse_args(['run'])
args.audit_run_id = sys.argv[5] if len(sys.argv) > 5 else stage
digest.cmd_run(args)
'''
    command = [sys.executable, '-c', code, str(history_store.REPO_ROOT / 'telegram_connector'),
               str(path), str(audit), stage]
    env = {key: value for key, value in os.environ.items()
           if key in {'PATH', 'LANG', 'LC_ALL', 'TMPDIR'}}
    env['FIXTURE_SUPERVISOR_READY'] = str(gate)
    digest_telemetry.prepare_supervised_audit(audit, stage)
    result = ttl_runner.run_with_ttl(command, timeout_seconds=2, grace_seconds=0.1,
        poll_interval_seconds=0.02, timeout_exit_code=124, term_signal='TERM',
        kill_signal='KILL', audit_file=audit, timeout_reason='early_fixture_timeout',
        use_caffeinate=False, env=env)
    assert result == 124
    receipt = json.loads(audit.read_text())
    assert receipt['run_id'] == stage
    assert receipt['status'] == 'timed_out'
    assert receipt['phase'] == 'starting'
    assert {key: receipt['previous_supervisor'][key] for key in previous} == previous
    assert 'previous_supervisor' not in receipt['previous_supervisor']
    # Another pre-database timeout must not replace the still-pending receipt.
    repeated = command + ['repeated-' + stage]
    gate.unlink()
    digest_telemetry.prepare_supervised_audit(audit, 'repeated-' + stage)
    result = ttl_runner.run_with_ttl(repeated, timeout_seconds=2, grace_seconds=0.1,
        poll_interval_seconds=0.02, timeout_exit_code=124, term_signal='TERM',
        kill_signal='KILL', audit_file=audit, timeout_reason='early_fixture_timeout',
        use_caffeinate=False, env=env)
    assert result == 124
    repeated_receipt = json.loads(audit.read_text())
    assert repeated_receipt['run_id'] == 'repeated-' + stage
    assert repeated_receipt['previous_supervisor'] == receipt['previous_supervisor']
    conn = common_sqlite.connect_sqlite(path)
    old = digest_telemetry.summarize_run(conn, 'previous', receipt)
    assert old['effective_status'] == 'cancelled'
    assert old['supervisor']['cancel_signal'] == 'SIGTERM'
    command[-1] = 'recover'
    digest_telemetry.prepare_supervised_audit(audit, 'recover')
    subprocess.run(command, env=env, check=True, timeout=10, capture_output=True)
    retained = digest_telemetry.summarize_run(conn, 'previous')
    assert retained['effective_status'] == 'cancelled'
    assert retained['supervisor']['cancel_signal'] == 'SIGTERM'
    assert {key: retained['supervisor'][key] for key in previous} == previous
    assert 'previous_supervisor' not in json.loads(audit.read_text())
    conn.close()


def test_utc_jump_does_not_change_monotonic_phase_measurement(tmp_path):
    conn = common_sqlite.connect_sqlite(tmp_path / 'history.db')
    history_store.init_db(conn)
    clock = [0.0]
    wall = ['2026-11-17T10:00:00+00:00']
    recorder = digest_telemetry.DigestRecorder(conn, 'clock', wall[0],
        monotonic_func=lambda: clock[0], now=lambda: wall[0])
    clock[0] = 3.0
    wall[0] = '2026-11-16T10:00:00+00:00'
    recorder.enter('cleanup')
    clock[0] = 4.0
    recorder.finish('sent')
    report = digest_telemetry.summarize_run(conn, 'clock')
    assert report['timing']['worker_elapsed_ms'] == 4000
    assert report['timing']['accounted_phase_ms'] == 4000
    assert report['timing']['other_worker_ms'] == 4000
    conn.close()


def test_outer_ttl_preserves_pending_intent_and_releases_storage(tmp_path):
    path, audit = tmp_path / 'history.db', tmp_path / 'audit.json'
    code = '''
import json, sys, time
from pathlib import Path
from common import sqlite as db
from telegram_connector import history_store, digest_telemetry
from telegram_shared import openai_usage
conn = db.connect_sqlite(Path(sys.argv[1]))
history_store.init_db(conn)
recorder = digest_telemetry.DigestRecorder(conn, 'ttl-fixture', digest_telemetry.utc_now())
cache = openai_usage.build_prompt_cache_info(cache_key='fixture', system_instructions='',
    prompt_text='fixture', shared_prefix='')
recorder.begin_ai(1, dict(stage='single', channel='@fixture', since=None, until=None,
    model='fixture', request_index=1, message_count=1, cache_info=cache, prompt_text='fixture'))
Path(sys.argv[2]).write_text(json.dumps(dict(run_id='ttl-fixture', status='running')))
time.sleep(30)
'''
    result = ttl_runner.run_with_ttl([sys.executable, '-c', code, str(path), str(audit)],
        timeout_seconds=2, grace_seconds=0.1, poll_interval_seconds=0.02,
        timeout_exit_code=124, term_signal='TERM', kill_signal='KILL',
        audit_file=audit, timeout_reason='fixture_timeout', use_caffeinate=False,
        env={key: value for key, value in os.environ.items()
             if key in {'PATH', 'LANG', 'LC_ALL', 'TMPDIR'}})
    assert result == 124
    receipt = json.loads(audit.read_text())
    assert receipt['run_id'] == 'ttl-fixture'
    with pytest.raises(ProcessLookupError):
        os.kill(receipt['timed_out_pid'], 0)
    conn = common_sqlite.connect_sqlite(path)
    report = digest_telemetry.summarize_run(conn, 'ttl-fixture', receipt)
    assert report['effective_status'] == 'timed_out'
    assert report['attempts'][0]['status'] == 'pending'
    assert report['attempts'][0]['input_tokens'] is None
    digest_telemetry.retain_supervisor_outcome(conn, receipt)
    replacement = digest_telemetry.DigestRecorder(conn, 'next-run', digest_telemetry.utc_now())
    replacement.finish('sent')
    assert digest_telemetry.summarize_run(conn, 'ttl-fixture')['effective_status'] == 'timed_out'
    conn.close()


@pytest.mark.parametrize('failure,expected,count', [
    ('unauthorized', 'failed', 1), ('network', 'unknown', 2), ('json', 'unknown', 1),
])
def test_failed_ai_attempts_preserve_unknown_usage_and_bounded_retry(tmp_path, failure, expected, count):
    conn = common_sqlite.connect_sqlite(tmp_path / 'history.db')
    history_store.init_db(conn)
    recorder = digest_telemetry.DigestRecorder(conn, 'failed', digest_telemetry.utc_now())
    cache = openai_usage.build_prompt_cache_info(cache_key='fixture',
        system_instructions='', prompt_text='fixture', shared_prefix='')
    fields = dict(stage='single', channel='@fixture', since=None, until=None,
        model='fixture', request_index=1, message_count=1, cache_info=cache, prompt_text='fixture')
    sent = []

    def send(req, **kwargs):
        assert not conn.in_transaction
        sent.append(1)
        if failure == 'unauthorized':
            raise error.HTTPError(req.full_url, 401, 'unauthorized', {}, io.BytesIO(b'{}'))
        if failure == 'network':
            raise error.URLError('fixture')
        return io.BytesIO(b'not json')

    with pytest.raises(openai_api.OpenAIRequestError):
        openai_api.post_responses({'model': 'fixture', 'input': 'fixture'}, 'fixture',
            timeout_seconds=1, retry_attempts=2, retry_backoff_seconds=0,
            sleep_func=lambda seconds: None, urlopen_func=send,
            before_attempt=lambda index: recorder.begin_ai(index, fields),
            after_attempt=recorder.end_ai)
    recorder.finish('failed')
    rows = list(conn.execute('SELECT status,input_tokens,output_tokens FROM ai_usage_log'))
    assert len(sent) == len(rows) == count
    assert all(tuple(row) == (expected, None, None) for row in rows)
    conn.close()


def test_intent_storage_failure_prevents_dispatch(tmp_path):
    conn = common_sqlite.connect_sqlite(tmp_path / 'history.db')
    history_store.init_db(conn)
    recorder = digest_telemetry.DigestRecorder(conn, 'no-dispatch', digest_telemetry.utc_now())
    conn.close()
    sent = []
    with pytest.raises(digest_telemetry.TelemetryStorageError), recorder.observe_delivery('@fixture'):
        bot_api.api_call('fixture', 'sendMessage', {'chat_id': 1, 'text': 'fixture'},
                         urlopen_func=lambda *args, **kwargs: sent.append(1))
    assert sent == []


def test_history_initializer_preserves_current_usage_records(tmp_path):
    conn = common_sqlite.connect_sqlite(tmp_path / 'history.db')
    history_store.init_db(conn)
    conn.execute("""INSERT INTO ai_usage_log
        (created_at,feature,stage,model,status,input_tokens,response_status)
        VALUES ('fixture','digest','single','fixture','succeeded',17,'completed')""")
    history_store.init_db(conn)
    row = conn.execute('SELECT status,input_tokens,response_status FROM ai_usage_log').fetchone()
    assert tuple(row) == ('succeeded', 17, 'completed')
    history_store.init_db(conn)
    assert tuple(conn.execute('SELECT status,input_tokens,response_status FROM ai_usage_log').fetchone()) == tuple(row)
    conn.close()


def test_digest_request_requires_recorder_before_provider_dispatch(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError('Provider must not run without a recorder')
    monkeypatch.setattr(digest_pipeline, 'run_openai_digest', unexpected)
    with pytest.raises(RuntimeError, match='recorder'):
        digest_pipeline.request_digest_with_usage(None, api_key='fixture',
            config=SimpleNamespace(model='fixture', store_prompt_text=False),
            stage='single', channel='@fixture', since='2026-11-17', until='2026-11-17',
            request_index=1, message_count=1, cache_info=None, prompt='fixture')


def test_report_rejects_missing_attempt_schema_instead_of_zero_attempts(tmp_path):
    conn = common_sqlite.connect_sqlite(tmp_path / 'history.db')
    history_store.init_db(conn)
    recorder = digest_telemetry.DigestRecorder(conn, 'schema', digest_telemetry.utc_now())
    recorder.finish('sent')
    conn.execute('DROP TABLE ai_usage_log')
    conn.execute('CREATE TABLE ai_usage_log(id INTEGER)')
    with pytest.raises(ValueError, match='schema'):
        digest_telemetry.summarize_run(conn, 'schema')
    conn.close()
