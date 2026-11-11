"""Adaptive packing changes physical requests, never the candidate graph."""
import json
import copy
from datetime import datetime, timezone

import pytest

from telegram_connector.tests.analysis_fixtures import prompt_json
from telegram_connector import analysis_telemetry as telemetry
from telegram_connector.tests.test_analysis_manifest import manifest_job, change_setting
from telegram_connector.tests.test_analysis_relations import records_fixture, link, link_sender, candidates
from common import sqlite as common_sqlite
from telegram_connector.analysis_store import AnalysisStore, BudgetExceededError, canonical_json


def relation_case(store, job):
    records_fixture(store, job, [{'claim': f'Uno выпустил продукт {n}.', 'entities': ['Uno'],
                                 'topic_key': 'релиз'} for n in range(12)])
    assert len(candidates(store, job)) == 66


@pytest.mark.parametrize('ceiling,attempts', [(0, 1), (48, 2), (20, 4)])
def test_adaptive_and_fixed_keep_the_same_complete_pair_set(manifest_job, ceiling, attempts):
    store, job = manifest_job
    change_setting(store, job, 'analysis', 'link_pairs_per_request', ceiling)
    job = store.get_job(job['job_id'])
    relation_case(store, job)
    seen = []
    delegate = link_sender('complements')
    def provider(req, timeout):
        if not req.full_url.endswith('input_tokens'):
            data = prompt_json(json.loads(req.data)['input'], 'records')
            assert len({r['record_id'] for r in data['records']}) == len(data['records'])
            # Wire aliases are local to a request; compare immutable fact identities.
            labels = {r['record_id']: r['payload']['claim'] for r in data['records']}
            seen.extend(frozenset((labels[p['left_id']], labels[p['right_id']])) for p in data['pairs'])
        return delegate(req, timeout)
    assert not link(store, job, provider)['partial']
    assert len(seen) == len(set(seen)) == 66
    current = store.get_job(job['job_id'])
    assert current['llm_calls'] == attempts and current['provider_requests'] == 2 * attempts
    before = (current['llm_calls'], current['provider_requests'])
    link(store, job, lambda *a, **k: pytest.fail('Settled pairs cannot be replayed'))
    assert before == (store.get_job(job['job_id'])['llm_calls'], store.get_job(job['job_id'])['provider_requests'])
    report = telemetry.summarize_job(store, job['job_id'])
    assert report['relations']['accepted_pairs'] == 66
    assert report['relations']['packing']['mode'] == ('adaptive' if ceiling == 0 else 'fixed')


def test_saved_fixed_plan_is_not_repacked(manifest_job):
    store, job = manifest_job
    change_setting(store, job, 'analysis', 'link_pairs_per_request', 48)
    job = store.get_job(job['job_id'])
    relation_case(store, job)
    link(store, job, link_sender('unrelated'))
    saved = {r[0] for r in store.conn.execute("SELECT batch_id FROM analysis_batches WHERE kind='link'")}
    link(store, job, lambda *a, **k: pytest.fail('Saved receipt must remain settled'))
    assert saved == {r[0] for r in store.conn.execute("SELECT batch_id FROM analysis_batches WHERE kind='link'")}
    assert store.get_job(job['job_id'])['llm_calls'] == 2


def test_missing_physical_observation_keeps_total_packing_bytes_unknown(manifest_job):
    store, job = manifest_job
    relation_case(store, job)
    link(store, job, link_sender('unrelated'))
    batch = store.plan_batch(job['job_id'], generation=job['generation'], step_key='unknown-link', kind='link', payload={})
    store.reserve_provider_attempt(job['job_id'], batch['batch_id'], generation=job['generation'],
        attempt_id='unknown-link', request_kind='inference', input_tokens=100, output_tokens=8192)
    store.settle_provider_attempt(job['job_id'], batch['batch_id'], generation=job['generation'],
        attempt_id='unknown-link', outcome='unknown', usage=None)
    packing = telemetry.summarize_job(store, job['job_id'])['relations']['packing']
    assert packing['record_payload_bytes'] is None
    assert packing['known_record_payload_bytes'] > 0
    assert packing['unknown_inference_observations'] == 1


@pytest.mark.parametrize('ceiling', [0, 48])
@pytest.mark.parametrize('boundary', ['save_token_count', 'save_link_progress'])
def test_new_generation_resumes_saved_packing_without_repeating_receipts(manifest_job, monkeypatch, ceiling, boundary):
    store, job = manifest_job
    change_setting(store, job, 'analysis', 'link_pairs_per_request', ceiling)
    job = store.get_job(job['job_id'])
    relation_case(store, job)
    frozen = copy.deepcopy(job['request'])
    original = getattr(store, boundary)
    stopped = []
    def stop_after_save(*args, **kwargs):
        value = original(*args, **kwargs)
        if not stopped:
            stopped.append(True)
            raise InterruptedError('Stop after durable packing boundary')
        return value
    monkeypatch.setattr(store, boundary, stop_after_save)
    with pytest.raises(InterruptedError):
        link(store, job, link_sender('unrelated'))
    now = datetime.now(timezone.utc)
    store.finish_job(job['job_id'], generation=job['generation'], status='interrupted', now=now)
    queued = store.resume_job(job['job_id'], generation=job['generation'], phase='link', max_queued_jobs=10, now=now)
    resumed = store.claim_job(job['job_id'], max_running_jobs=1, now=now)
    assert resumed['generation'] == queued['generation'] + 1 > job['generation']
    assert not link(store, resumed, link_sender('unrelated'))['partial']
    current = store.get_job(job['job_id'])
    assert current['request'] == frozen
    assert current['llm_calls'] == (2 if ceiling else 1)
    assert current['provider_requests'] == 2 * current['llm_calls']
    assert len(list(store.iter_link_decisions(job['job_id']))) == 66


@pytest.mark.parametrize('delta', [-1, 0, 1])
def test_packing_observation_reserve_is_funded_before_any_provider_call(manifest_job, delta):
    store, job = manifest_job
    records = records_fixture(store, job, [{'claim': 'Uno A.'}, {'claim': 'Uno B.'}])
    batch = store.plan_batch(job['job_id'], generation=job['generation'], step_key='reserve-case', kind='link',
        payload={'pair_map': {'p001': [r['record_id'] for r in records]}, 'record_digests': {},
                 'packing': {'mode': 'adaptive', 'pairs': 1, 'unique_records': 2, 'reason': 'end'}})
    progress = {'accepted_decisions': {}, 'unresolved_pair_ids': ['p001'], 'diagnostics': [],
                'active_request': None, 'saturated': False}
    clone = common_sqlite.connect_sqlite(':memory:')
    try:
        store.conn.backup(clone)
        probe = AnalysisStore(clone)
        probe.begin_link_request(job['job_id'], batch['batch_id'], generation=job['generation'], expected=None, progress=progress)
        request = copy.deepcopy(store.get_job(job['job_id'])['request'])
        needed = probe.storage_usage(job['job_id'])['allocated_bytes']
        for _ in range(4):
            request['settings']['analysis']['max_intermediate_bytes'] = needed
            clone.execute('UPDATE analysis_jobs SET request_json=? WHERE job_id=?', (canonical_json(request), job['job_id']))
            next_needed = probe.storage_usage(job['job_id'])['allocated_bytes']
            if needed == next_needed:
                break
            needed = next_needed
        assert probe.storage_usage(job['job_id'])['progress_reserve'] > 0
        request['settings']['analysis']['max_intermediate_bytes'] = needed + delta
        store.conn.execute('UPDATE analysis_jobs SET request_json=? WHERE job_id=?', (canonical_json(request), job['job_id']))
        if delta < 0:
            with pytest.raises(BudgetExceededError, match='max_intermediate_bytes'):
                store.begin_link_request(job['job_id'], batch['batch_id'], generation=job['generation'], expected=None, progress=progress)
            assert 'link_progress' not in store.get_batch(batch['batch_id'])['payload']
        else:
            store.begin_link_request(job['job_id'], batch['batch_id'], generation=job['generation'], expected=None, progress=progress)
            assert store.storage_usage(job['job_id'])['allocated_bytes'] <= needed + delta
        assert store.get_job(job['job_id'])['provider_requests'] == 0
    finally:
        clone.close()
