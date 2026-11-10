"""Retention expires reuse, never recorded results, receipts or deliveries."""
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from telegram_connector import analysis_telemetry
from telegram_connector.tests.test_analysis_manifest import manifest_job
from telegram_connector.tests.test_analysis_relations import records_fixture


NOW = datetime(2026, 11, 10, tzinfo=timezone.utc)


def archived_job(manifest_job, outcome, tmp_path):
    store, job = manifest_job
    records = records_fixture(store, job, [{'claim': 'ALPHA требует 8 GB.',
        'entities': ['ALPHA'], 'topic_key': 'ALPHA'}])
    batch_id = store.conn.execute('SELECT batch_id FROM analysis_evidence WHERE record_id=?',
        (records[0]['record_id'],)).fetchone()[0]
    result = {'command': 'topics', 'items': [{'title': 'ALPHA', 'text': 'Требует 8 GB.',
        'evidence_ids': [records[0]['record_id']]}], 'partial': False, 'reasons': []}
    store.save_analysis_result(job['job_id'], result, generation=job['generation'])
    artifact = tmp_path / 'saved.md'
    artifact.write_text('Исторический текст без повторного анализа.')
    finished = (NOW - timedelta(days=8)).isoformat()
    delivery = {'outcome': outcome, 'parts': [{'outcome': outcome, 'text': '<b>ALPHA</b>\nТребует 8 GB.'}]}
    store.conn.execute('UPDATE analysis_jobs SET status=?,finished_at=?,updated_at=?,delivery_json=?,full_path=?,full_hash=?,llm_calls=1,provider_requests=1,used_tokens=15 WHERE job_id=?',
        ('completed', finished, finished, json.dumps(delivery), str(artifact),
         hashlib.sha256(artifact.read_bytes()).hexdigest(), job['job_id']))
    attempt = {'attempt_id': 'archived-attempt', 'request_kind': 'inference',
        'state': 'succeeded', 'usage': {'input_tokens': 10, 'output_tokens': 5}}
    store.conn.execute('UPDATE analysis_batches SET cache_key=?,attempts_json=? WHERE batch_id=?',
        ('archived-cache', json.dumps([attempt]), batch_id))
    sequence = store.conn.execute('SELECT COALESCE(MAX(sequence),-1)+1 FROM analysis_phase_runs WHERE job_id=? AND generation=?',
        (job['job_id'], job['generation'])).fetchone()[0]
    store.conn.execute('INSERT INTO analysis_phase_runs(job_id,generation,sequence,phase,process_run_id,implementation_sha256,started_at,finished_at,elapsed_ms,duration_source,outcome) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
        (job['job_id'], job['generation'], sequence, 'extract', 'archived-worker', 'archived-code',
         finished, finished, 0, 'monotonic', 'succeeded'))
    store.conn.execute('INSERT INTO ai_usage_log(created_at,feature,stage,model,status,input_tokens,output_tokens,total_tokens,cached_input_tokens,cache_write_tokens,latency_ms,job_id,batch_id,attempt_id,generation,phase_sequence,actual_model,actual_service_tier) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (finished, 'analysis', 'extract:inference', job['request']['settings']['processing']['model'],
         'succeeded', 10, 5, 15, 0, 0, 0, job['job_id'], batch_id,
         'archived-attempt', job['generation'], sequence, job['request']['settings']['processing']['model'], 'default'))
    return store, job, artifact


def history_rows(store):
    values = {}
    for table in ('analysis_jobs', 'analysis_batches', 'analysis_evidence', 'analysis_inputs',
                  'analysis_phase_runs', 'ai_usage_log'):
        rows = [dict(row) for row in store.conn.execute(f'SELECT * FROM {table}')]
        for row in rows:
            row.pop('cache_key', None)  # Cross-job reuse is explicitly evictable.
            if table == 'analysis_jobs':
                row.pop('intermediate_bytes')  # Actual storage size changes with cache keys.
        values[table] = rows
    return values


@pytest.mark.parametrize('outcome', ['confirmed', 'unknown'])
@pytest.mark.parametrize('cleanup', ['retention', 'explicit_cutoff', 'cache_cap'])
def test_cleanup_preserves_archive_and_read_only_report(manifest_job, tmp_path, outcome, cleanup):
    store, job, artifact = archived_job(manifest_job, outcome, tmp_path)
    if cleanup == 'cache_cap':
        store.conn.execute('UPDATE analysis_jobs SET finished_at=?,updated_at=? WHERE job_id=?',
            (NOW.isoformat(), NOW.isoformat(), job['job_id']))
    before = history_rows(store)
    report = analysis_telemetry.summarize_job(store, job['job_id'])
    if cleanup == 'explicit_cutoff':
        assert store.prune_expired_analysis_jobs(before=NOW - timedelta(days=7)) == 0
    else:
        result = store.maintain_analysis_storage(now=NOW,
            max_cache_bytes=1 if cleanup == 'cache_cap' else 67108864)
        assert result['pruned_jobs'] == 0
    assert history_rows(store) == before
    assert analysis_telemetry.summarize_job(store, job['job_id']) == report
    assert store.conn.execute('SELECT count(*) FROM analysis_batches WHERE cache_key IS NOT NULL').fetchone()[0] == 0
    assert artifact.read_text() == 'Исторический текст без повторного анализа.'


def test_cache_cap_keeps_cancelled_job_evidence_and_attempt_history(manifest_job, tmp_path):
    store, job, _ = archived_job(manifest_job, 'unknown', tmp_path)
    store.conn.execute('UPDATE analysis_jobs SET status=?,result_json=NULL,full_path=NULL,full_hash=NULL,finished_at=? WHERE job_id=?',
        ('cancelled', NOW.isoformat(), job['job_id']))
    before = history_rows(store)
    assert store.maintain_analysis_storage(now=NOW, max_cache_bytes=1)['cache_bytes'] <= 1
    assert history_rows(store) == before
