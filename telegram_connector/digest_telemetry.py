"""Buffered digest observations; durable writes only at external-call boundaries."""
from contextvars import ContextVar
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import hashlib
import sqlite3
import time
import uuid

from common import sqlite as common_sqlite
from common.json_io import write_json_atomic
from common.ttl_runner import load_audit_payload
from telegram_shared import openai_usage
from telegram_shared import bot_api

if __package__:
    from . import history_store
else:
    import history_store


CURRENT = ContextVar('digest_recorder', default=None)
SUPERVISOR_FAILURE_STATUSES = frozenset({'failed', 'timed_out', 'interrupted', 'cancelled'})
SUPERVISOR_RECEIPT_FIELDS = (
    'run_id', 'status', 'finished_at', 'elapsed_seconds', 'ttl_elapsed_seconds',
    'sleep_seconds', 'timeout_reason', 'reason', 'cancel_signal',
)


class TelemetryStorageError(RuntimeError):
    """Storage failed; retrying the external operation would be unsafe."""


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def build_digest_run_id():
    return f"digest-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex}"


def usage_operation_key(row):
    """Digest owns its logical operation identity, including the run boundary."""
    fields = ('run_id', 'channel', 'stage', 'request_index')
    return tuple(row[key] for key in fields) if all(
        row.get(key) is not None for key in fields) else None


class DigestRecorder:
    def __init__(self, conn, run_id, started_at, *,
                 monotonic_func=time.monotonic, now=utc_now, initial_clock=None):
        self.conn, self.run_id = conn, run_id
        self.clock, self.now = monotonic_func, now
        self.started_at, self.finished_at = started_at, None
        self.status = 'running'
        self.payload = {'version': 1, 'phases': [], 'delivery': []}
        self.dirty = True
        self.phase = None
        self.backoffs = {}
        self.last_ai = None
        self.enter('startup')
        if initial_clock is not None:
            self.phase_started = initial_clock
            self.phase['started_at'] = started_at
        self.worker_started = self.phase_started

    def enter(self, phase, channel=None):
        self.close_phase('succeeded')
        self.phase = {'sequence': len(self.payload['phases']), 'phase': phase,
            'channel': channel, 'started_at': self.now(), 'finished_at': None,
            'elapsed_ms': None, 'duration_source': 'monotonic', 'outcome': 'running'}
        self.payload['phases'].append(self.phase)
        self.phase_started = self.clock()
        self.dirty = True

    def close_phase(self, outcome):
        if self.phase is not None:
            self.phase.update(finished_at=self.now(), outcome=outcome,
                elapsed_ms=max(0, round((self.clock() - self.phase_started) * 1000)))
            self.phase = None
            self.dirty = True

    def update(self, **fields):
        self.payload.update(fields)
        self.dirty = True

    def flush(self, mutation=None):
        if not self.dirty and not self.backoffs and mutation is None:
            return
        snapshot = json.dumps(self.payload, ensure_ascii=False, separators=(',', ':'))
        try:
            with common_sqlite.write_tx(self.conn):
                for identity, values in self.backoffs.items():
                    openai_usage.write_openai_backoff(self.conn, run_id=self.run_id,
                        attempt_id=identity, elapsed_ms=values[0], requested_ms=values[1])
                result = mutation() if mutation is not None else None
                history_store.write_digest_run(self.conn, run_id=self.run_id,
                    started_at=self.started_at, updated_at=self.now(),
                    finished_at=self.finished_at, status=self.status,
                    payload_json=snapshot)
        except sqlite3.Error as exc:
            raise TelemetryStorageError('Digest telemetry storage failed') from exc
        self.backoffs.clear()
        self.dirty = False
        return result

    def begin_ai(self, index, fields):
        identity = uuid.uuid4().hex
        stamp = self.now()
        row_id = self.flush(lambda: openai_usage.begin_openai_attempt(self.conn,
            run_id=self.run_id, attempt_id=identity, attempt_index=index,
            created_at=stamp, feature='digest', **fields))
        self.last_ai = (identity, row_id)
        return identity, row_id

    def end_ai(self, identity, response, exc, latency, *, output_chars=None):
        status = ('succeeded' if exc is None else 'unknown'
            if exc.error_type in {'network_error', 'invalid_response'} else 'failed')
        error = (f'http_{exc.status_code}' if exc.status_code is not None
                 else exc.error_type) if exc is not None else None
        self.flush(lambda: openai_usage.settle_openai_attempt(self.conn, identity[1],
            response=response, status=status, error=error, latency_ms=latency,
            output_chars=output_chars))

    def measured_sleep(self, sleep_func, seconds):
        started = self.clock()
        try:
            sleep_func(seconds)
        finally:
            if self.last_ai is not None:
                self.backoffs[self.last_ai[0]] = (
                    max(0, round((self.clock() - started) * 1000)), round(seconds * 1000))

    @contextmanager
    def observe_delivery(self, channel, *, role='channel'):
        last_part = None
        last_attempt = None

        def before(method, payload):
            nonlocal last_part, last_attempt
            if method != 'sendMessage':
                raise ValueError('Digest observer only accepts sendMessage')
            digest = hashlib.sha256(payload['text'].encode()).hexdigest()[:16]
            if (last_part is None or last_part['text_hash'] != digest
                    or last_part['attempts'][-1]['outcome'] == 'confirmed'):
                last_part = {'channel': channel, 'role': role,
                    'part_index': sum(item['channel'] == channel and item['role'] == role
                                      for item in self.payload['delivery']),
                    'text_hash': digest, 'attempts': []}
                self.payload['delivery'].append(last_part)
            last_attempt = {'attempt_id': uuid.uuid4().hex,
                'attempt_index': len(last_part['attempts']) + 1,
                'started_at': self.now(), 'finished_at': None, 'elapsed_ms': None,
                'outcome': 'intent', 'message_id': None, 'error_code': None,
                'retry_after': None, 'backoff_ms': 0, 'backoff_requested_ms': 0}
            last_part['attempts'].append(last_attempt)
            self.dirty = True
            self.flush()
            return last_attempt

        def after(attempt, result, exc, latency):
            message_id = result.get('message_id') if isinstance(result, dict) else None
            confirmed = exc is None and type(message_id) is int and message_id > 0
            outcome = ('confirmed' if confirmed else 'refused'
                if getattr(exc, 'request_outcome', None) == 'refused' else 'unknown')
            attempt.update(finished_at=self.now(), elapsed_ms=latency, outcome=outcome,
                message_id=message_id if confirmed else None,
                error_code=getattr(exc, 'error_code', None),
                retry_after=getattr(exc, 'retry_after', None))
            self.dirty = True
            self.flush()

        def sleep(sleep_func, seconds):
            started = self.clock()
            try:
                sleep_func(seconds)
            finally:
                if last_attempt is not None:
                    last_attempt.update(backoff_ms=max(0, round(
                        (self.clock() - started) * 1000)), backoff_requested_ms=round(seconds * 1000))
                    self.dirty = True

        with bot_api.observe_calls(before=before, after=after, sleep=sleep):
            yield

    def finish(self, status, **fields):
        self.close_phase('succeeded' if status in {'sent', 'partial'} else status)
        self.status, self.finished_at = status, self.now()
        self.update(worker_elapsed_ms=max(0, round(
            (self.clock() - self.worker_started) * 1000)), **fields)
        self.flush()


def supervisor_receipt(audit):
    """Keep only a terminal receipt, without copying worker payload or history."""
    if not isinstance(audit, dict) or not audit.get('run_id') or audit.get('status') not in (
            SUPERVISOR_FAILURE_STATUSES | {'sent', 'partial'}):
        return None
    return {key: audit[key] for key in SUPERVISOR_RECEIPT_FIELDS if key in audit}


def prepare_supervised_audit(audit_file, run_id):
    """Save the old receipt before a new supervisor can publish diagnostics."""
    previous = load_audit_payload(audit_file)
    receipt = (supervisor_receipt(previous.get('previous_supervisor'))
               or supervisor_receipt(previous))
    started = utc_now()
    payload = {'run_id': run_id, 'started_at': started, 'updated_at': started,
               'finished_at': None, 'status': 'started', 'phase': 'starting'}
    if receipt is not None:
        payload['previous_supervisor'] = receipt
    write_json_atomic(audit_file, payload)


def retain_supervisor_outcome(conn, audit):
    """Preserve a matching terminal supervisor receipt before audit replacement."""
    receipt = supervisor_receipt(audit)
    if receipt is None:
        return
    row = conn.execute('SELECT * FROM digest_runs WHERE run_id=?',
                       (receipt['run_id'],)).fetchone()
    if row is None:
        return
    row = dict(row)
    payload = json.loads(row['payload_json'])
    payload['supervisor'] = receipt
    serialized = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    with common_sqlite.write_tx(conn):
        history_store.write_digest_run(conn, run_id=row['run_id'],
            started_at=row['started_at'], updated_at=utc_now(),
            finished_at=row['finished_at'], status=row['status'], payload_json=serialized)


def summarize_run(conn, run_id=None, audit=None):
    """Read retained telemetry; never initialize, repair or replay a run."""
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if 'digest_runs' not in tables:
        return {'available': False, 'reason': 'digest_telemetry_unavailable'}
    row = (conn.execute('SELECT * FROM digest_runs WHERE run_id=?', (run_id,)).fetchone()
        if run_id is not None else conn.execute(
            'SELECT * FROM digest_runs ORDER BY started_at DESC,run_id DESC LIMIT 1').fetchone())
    if row is None:
        return {'available': False, 'reason': 'digest_run_not_found'}
    run = dict(row)
    payload = json.loads(run.pop('payload_json'))
    if audit and audit.get('run_id') != run['run_id']:
        previous = audit.get('previous_supervisor')
        if isinstance(previous, dict) and previous.get('run_id') == run['run_id']:
            audit = previous
    if not audit or audit.get('run_id') != run['run_id']:
        retained = payload.get('supervisor')
        audit = {'run_id': run['run_id'], **retained} if retained else None
    if audit and audit.get('run_id') == run['run_id']:
        payload['supervisor'] = {key: audit[key] for key in SUPERVISOR_RECEIPT_FIELDS if key in audit}
    effective = run['status']
    source = 'worker'
    if effective in {'started', 'running'}:
        effective, source = 'unknown', 'unfinished_worker'
    if audit and audit.get('run_id') == run['run_id']:
        if audit.get('status') in SUPERVISOR_FAILURE_STATUSES:
            effective, source = audit['status'], 'supervisor'
    columns = {item[1] for item in conn.execute('PRAGMA table_info(ai_usage_log)')}
    if 'run_id' not in columns:
        raise ValueError('Digest telemetry schema is incomplete: missing ai_usage_log.run_id')
    rows = [dict(item) for item in conn.execute(
        'SELECT * FROM ai_usage_log WHERE run_id=? ORDER BY id', (run['run_id'],))]
    usage = {'physical_attempts': len(rows), 'unknown_usage_attempts': sum(
        item['input_tokens'] is None or item['output_tokens'] is None for item in rows)}
    for key in ('input_tokens', 'output_tokens', 'cached_input_tokens',
                'latency_ms', 'backoff_ms'):
        values = [item[key] for item in rows if item[key] is not None]
        usage[key] = sum(values) if values else None
    operation_keys = {usage_operation_key(item) for item in rows} - {None}
    usage['logical_operations'] = len(operation_keys) if operation_keys or not rows else None
    usage['pending_attempts'] = sum(item['status'] == 'pending' for item in rows)
    usage['unknown_outcomes'] = sum(item['status'] == 'unknown' for item in rows)
    warnings = []
    if usage['pending_attempts']:
        warnings.append('unfinished_attempts')
    if usage['unknown_usage_attempts']:
        warnings.append('missing_usage')
    phases = payload.get('phases', [])
    complete_phases = bool(phases) and all(item.get('elapsed_ms') is not None for item in phases)
    phase_ms = sum(item['elapsed_ms'] for item in phases) if complete_phases else None
    if not complete_phases:
        warnings.append('unfinished_phase')
    worker_ms = payload.get('worker_elapsed_ms')
    unaccounted = worker_ms - phase_ms if worker_ms is not None and phase_ms is not None else None
    if unaccounted is not None and unaccounted < 0:
        warnings.append('inconsistent_phase_timing')
    delivery = [attempt for part in payload.get('delivery', []) for attempt in part['attempts']]
    if any(item['outcome'] in {'intent', 'unknown'} for item in delivery):
        warnings.append('unknown_delivery_outcome')
    if payload.get('sent_channel_messages') and not delivery:
        warnings.append('delivery_observation_missing')
    accounting = {'worker_elapsed_ms': worker_ms, 'accounted_phase_ms': phase_ms,
        'transition_and_unmeasured_ms': unaccounted,
        'delivery_attempts': len(delivery),
        'delivered_parts': sum(item['outcome'] == 'confirmed' for item in delivery)}
    api_complete = not usage['pending_attempts'] and all(
        item['latency_ms'] is not None for item in rows)
    delivery_complete = all(item.get('elapsed_ms') is not None for item in delivery)
    provider_ms = sum(item['latency_ms'] for item in rows) if api_complete else None
    delivery_ms = sum(item['elapsed_ms'] for item in delivery) if delivery_complete else None
    backoff_ms = (sum(item['backoff_ms'] for item in rows)
        + sum(item['backoff_ms'] for item in delivery)) if api_complete else None
    other_ms = (worker_ms - provider_ms - delivery_ms - backoff_ms
        if all(value is not None for value in (worker_ms, provider_ms, delivery_ms, backoff_ms))
        else None)
    if other_ms is not None and other_ms < 0:
        warnings.append('inconsistent_external_timing')
    accounting.update(provider_ms=provider_ms, delivery_ms=delivery_ms,
                      backoff_ms=backoff_ms, other_worker_ms=other_ms)
    attempt_fields = ('attempt_id', 'attempt_index', 'channel', 'stage',
        'request_index', 'started_at', 'latency_ms', 'backoff_ms',
        'backoff_requested_ms', 'status', 'error', 'input_tokens', 'output_tokens',
        'cached_input_tokens', 'cache_write_tokens', 'reasoning_tokens',
        'actual_model', 'actual_service_tier', 'response_id')
    return {'available': True, 'run_id': run['run_id'],
        'persisted_status': run['status'], 'effective_status': effective,
        'status_source': source, 'started_at': run['started_at'],
        'finished_at': run['finished_at'], 'usage': usage,
        'timing': accounting, 'warnings': warnings,
        'cost': {'usd': None, 'reason': 'pricing_not_supplied'},
        'telemetry_complete': not warnings,
        'attempts': [{key: item[key] for key in attempt_fields} for item in rows],
        **payload}
