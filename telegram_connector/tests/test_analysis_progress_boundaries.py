"""Storage boundaries preserve accepted progress and physical receipts."""
import copy

import pytest

from common import sqlite as common_sqlite
from telegram_connector import analysis_evidence
from telegram_connector.analysis_store import AnalysisStore, BudgetExceededError, canonical_json
from telegram_connector.tests.test_analysis_manifest import manifest_job
from telegram_connector.tests.test_analysis_extraction_accounting import accounting_batch, envelope, classify


def observed(store, job, batch, progress, data, identity):
    store.reserve_provider_attempt(job['job_id'], batch['batch_id'], generation=job['generation'],
        attempt_id=identity, request_kind='inference', input_tokens=10, output_tokens=100)
    store.settle_provider_attempt(job['job_id'], batch['batch_id'], generation=job['generation'],
        attempt_id=identity, outcome='succeeded', usage={'input_tokens': 10, 'output_tokens': 20, 'cached_input_tokens': 0})
    result = classify(store, job, batch, data)
    result['record_slots'] = {record['record_id']: record for record in result['records']}
    return result


@pytest.mark.parametrize('delta', [-1, 0, 1])
def test_residual_progress_byte_boundary_preserves_original_record_and_usage(manifest_job, delta):
    store, job = manifest_job
    batch = accounting_batch(store, job, ['Первый факт ALPHA.', 'Второй факт BETA.'])
    scope = analysis_evidence.extraction_scope(batch['messages'])
    progress = store.begin_extraction_request(job['job_id'], batch['batch_id'],
        generation=job['generation'], expected=store.get_batch(batch['batch_id'])['payload'].get('extraction_progress'), scope=scope)
    first = observed(store, job, batch, progress, envelope(batch), 'first-receipt')
    progress = store.save_extraction_progress(job['job_id'], batch['batch_id'], generation=job['generation'],
        expected=progress, classified=first, attempt_id='first-receipt')
    progress = store.begin_extraction_request(job['job_id'], batch['batch_id'], generation=job['generation'],
        expected=progress, scope=scope)
    second = envelope({'messages': batch['messages'][1:]})
    second['input_dispositions']['i0001'] = second['input_dispositions'].pop('i0000')
    classified = observed(store, job, batch, progress, second, 'other-receipt')
    clone = common_sqlite.connect_sqlite(':memory:')
    try:
        store.conn.backup(clone)
        probe = AnalysisStore(clone)
        probe.save_extraction_progress(job['job_id'], batch['batch_id'], generation=job['generation'],
            expected=progress, classified=classified, attempt_id='other-receipt')
        request = copy.deepcopy(store.get_job(job['job_id'])['request'])
        needed = probe.storage_usage(job['job_id'])['allocated_bytes']
        for _ in range(4):
            request['settings']['analysis']['max_intermediate_bytes'] = needed
            clone.execute('UPDATE analysis_jobs SET request_json=? WHERE job_id=?', (canonical_json(request), job['job_id']))
            next_needed = probe.storage_usage(job['job_id'])['allocated_bytes']
            if next_needed == needed:
                break
            needed = next_needed
        request['settings']['analysis']['max_intermediate_bytes'] = needed + delta
        store.conn.execute('UPDATE analysis_jobs SET request_json=? WHERE job_id=?', (canonical_json(request), job['job_id']))
        if delta < 0:
            with pytest.raises(BudgetExceededError, match='max_intermediate_bytes'):
                store.save_extraction_progress(job['job_id'], batch['batch_id'], generation=job['generation'],
                    expected=progress, classified=classified, attempt_id='other-receipt')
            assert store.get_batch(batch['batch_id'])['payload']['extraction_progress'] == progress
        else:
            progress = store.save_extraction_progress(job['job_id'], batch['batch_id'], generation=job['generation'],
                expected=progress, classified=classified, attempt_id='other-receipt')
        store.finish_extraction_progress(job['job_id'], batch['batch_id'], generation=job['generation'], expected=progress)
        assert len(list(store.iter_leaf_evidence(job['job_id'], page_size=10))) == (1 if delta < 0 else 2)
        assert store.get_job(job['job_id'])['used_tokens'] == 60
        assert store.storage_usage(job['job_id'])['allocated_bytes'] <= needed + delta
    finally:
        clone.close()


@pytest.mark.parametrize('schema,algorithm', [(1, 9), (1, 10), (2, 9)])
def test_incompatible_execution_stops_before_secrets_or_provider(manifest_job, monkeypatch, schema, algorithm):
    from telegram_connector import telegram_analysis as analysis
    store, job = manifest_job
    request = copy.deepcopy(job['request'])
    request['settings'].update(schema_version=schema, algorithm_version=algorithm)
    store.conn.execute('UPDATE analysis_jobs SET request_json=? WHERE job_id=?', (canonical_json(request), job['job_id']))
    monkeypatch.setattr(analysis.bridge, 'resolve_secret_value', lambda *a: pytest.fail('No secrets for incompatible snapshots'))
    with pytest.raises(ValueError, match='analysis_policy_incompatible'):
        analysis.execute_analysis_job(store, store.get_job(job['job_id']),
            urlopen_func=lambda *a, **k: pytest.fail('No provider for incompatible snapshots'))
    assert store.get_job(job['job_id'])['provider_requests'] == 0
