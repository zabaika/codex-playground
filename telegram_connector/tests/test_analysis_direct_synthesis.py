from telegram_connector.tests.analysis_fixtures import CURRENT_EVIDENCE_FORMAT, CURRENT_SYNTHESIS_FORMAT
"""A complete exact-fit frontier goes straight to one global synthesis."""
import copy
import json

import pytest

from telegram_connector.tests.analysis_fixtures import prompt_json
from telegram_connector import analysis_pipeline
from telegram_connector.tests.test_analysis_manifest import manifest_job
from telegram_connector.tests.test_analysis_relations import records_fixture
from telegram_connector.tests.test_analysis_merge import merge, merge_sender
from telegram_connector.tests.test_analysis_synthesis import synthesize
from telegram_connector.tests.test_analysis_evidence import model_response
from telegram_connector.tests.test_analysis_transport import Response
from telegram_connector.tests.analysis_fixtures import expand_wire_quotes, semantic_fact_payload


def direct_job(store, job, count=12):
    records = records_fixture(store, job, [
        {'claim': f'Выпущен продукт {index}.', 'details': [f'Условие {index}']}
        for index in range(count)])
    return records


@pytest.mark.parametrize('command', ['topics', 'qa', 'search'])
def test_direct_route_is_shared_and_resume_uses_the_frozen_count(manifest_job, command):
    store, job = manifest_job
    direct_job(store, job, count=3)
    store.conn.execute('UPDATE analysis_jobs SET command=? WHERE job_id=?', (command, job['job_id']))
    request = copy.deepcopy(store.get_job(job['job_id'])['request'])
    request['request']['query'] = 'Выпущен продукт'
    store.conn.execute('UPDATE analysis_jobs SET request_json=? WHERE job_id=?',
                       (analysis_pipeline.canonical_json(request), job['job_id']))
    counts = []
    def provider(req, timeout):
        assert req.full_url.endswith('input_tokens')
        counts.append(1)
        return Response({'input_tokens': 100})
    result = merge(store, job, provider)
    assert result['route'] == 'direct'
    again = merge(store, job, lambda *a, **k: pytest.fail('Resume must not recount'))
    assert again == result and len(counts) == 1
    assert store.get_job(job['job_id'])['llm_calls'] == 0


def test_direct_preflight_unknown_count_is_not_a_false_fit_or_new_time_window(manifest_job):
    store, job = manifest_job
    direct_job(store, job, count=3)
    with pytest.raises(ValueError, match='input_token_count_unavailable'):
        merge(store, job, lambda *a, **k: Response({'input_tokens': None}))
    state = store.get_job(job['job_id'])['checkpoint']['merge']
    assert state['state'] == 'running' and 'route' not in state
    assert store.get_job(job['job_id'])['llm_calls'] == 0


def test_direct_resume_checks_changed_source_before_synthesis(manifest_job):
    store, job = manifest_job
    direct_job(store, job, count=3)
    merge(store, job, lambda *a, **k: Response({'input_tokens': 100}))
    store.conn.execute("UPDATE messages SET text='Источник изменён' WHERE channel_id=22 AND message_id=1")
    with pytest.raises(ValueError, match='input_changed'):
        synthesize(store, job, lambda *a, **k: pytest.fail('Source edit must stop before send'))
    assert store.get_job(job['job_id'])['status'] == 'interrupted'


def test_direct_resume_rejects_changed_frozen_settings_before_provider(manifest_job):
    from telegram_connector.tests.test_analysis_manifest import change_setting
    store, job = manifest_job
    direct_job(store, job, count=3)
    merge(store, job, lambda *a, **k: Response({'input_tokens': 100}))
    change_setting(store, job, 'analysis.ai', 'reasoning_effort', 'high')
    with pytest.raises(ValueError, match='analysis_policy_incompatible'):
        synthesize(store, job, lambda *a, **k: pytest.fail('Changed settings require fresh route'))


@pytest.mark.parametrize('delta', [-1, 0, 1])
@pytest.mark.parametrize('boundary', ['input', 'context'])
def test_exact_direct_preflight_boundaries_use_hierarchical_only_above_limit(manifest_job, delta, boundary):
    from telegram_connector import analysis_settings
    from telegram_connector.tests.test_analysis_manifest import change_setting
    store, job = manifest_job
    direct_job(store, job, count=3)
    output = analysis_settings.stage_output_limit(store.get_job(job['job_id'])['request']['settings'],
                                                   'synthesize', detail='normal')
    capability = {'model': 'fixture-model', 'context_window': 128000, 'max_output_tokens': 128000, 'source': 'fixture'}
    if boundary == 'input':
        change_setting(store, job, 'analysis', 'max_input_tokens', 100)
    else:
        capability['context_window'] = output + 100
    delegate = merge_sender()
    def provider(req, timeout):
        payload = json.loads(req.data)
        if payload['text']['format']['name'] == CURRENT_SYNTHESIS_FORMAT:
            assert req.full_url.endswith('input_tokens')
            return Response({'input_tokens': 100 + delta})
        if req.full_url.endswith('input_tokens'):
            return Response({'input_tokens': 1})
        return delegate(req, timeout)
    state = analysis_pipeline.merge_analysis_records(store, job['job_id'], generation=job['generation'],
        api_key='fixture-key', capabilities=capability, deadline=100, monotonic_func=lambda: 0,
        urlopen_func=provider, sleep_func=lambda _: None)
    assert state['route'] == ('hierarchical' if delta > 0 else 'direct')
    if delta <= 0:
        assert store.get_job(job['job_id'])['llm_calls'] == 0
    else:
        assert state['direct_skip_reason'] in {'max_input_tokens', 'model_context_limit'}


def test_complete_frontier_skips_merge_and_reuses_exact_synthesis_count(manifest_job):
    store, job = manifest_job
    records = direct_job(store, job)
    calls = []
    def provider(req, timeout):
        payload = json.loads(req.data)
        calls.append((req.full_url, payload))
        evidence = expand_wire_quotes(prompt_json(payload['input'], 'evidence'))
        assert evidence['group_order'] == 'unranked'
        assert {analysis_pipeline.canonical_json(semantic_fact_payload(r['payload'], synthesis=True)) for r in evidence['records']} == {
            analysis_pipeline.canonical_json(semantic_fact_payload(r['payload'], synthesis=True)) for r in records}
        if req.full_url.endswith('input_tokens'):
            return Response({'input_tokens': 100})
        return Response(model_response({'summary': 'События периода.', 'items': [{'title': r['payload']['claim'], 'text': ' '.join([r['payload']['claim'], *r['payload']['details']]), 'contradictions': [], 'evidence_ids': [r['record_id']]} for r in sorted(evidence['records'], key=lambda r: r['payload']['source_refs'][0]['message_id'], reverse=True)], 'limitations': [], 'insufficient_information': False, 'saturated': False}))
    state = merge(store, job, provider)
    assert state['route'] == 'direct'
    assert state['reduction_outcome'] == 'not_required'
    assert store.get_job(job['job_id'])['llm_calls'] == 0
    result = synthesize(store, job, provider)
    assert len(calls) == 2
    count, inference = calls[0][1], calls[1][1]
    assert count == {key: inference[key] for key in ('model', 'input', 'instructions', 'text', 'reasoning')}
    assert result['items'][0]['evidence_ids'] == [records[-1]['record_id']]
    assert all(item['text'] and not {'conditions', 'exceptions'} & item.keys() for item in result['items'])
    assert store.get_job(job['job_id'])['llm_calls'] == 1
    assert merge(store, job, lambda *a, **k: pytest.fail('Unexpected resumed provider request'))['route'] == 'direct'


def test_oversized_complete_frontier_uses_bounded_hierarchical_fallback(manifest_job):
    store, job = manifest_job
    direct_job(store, job, 3)
    fallback = merge_sender()
    def provider(req, timeout):
        payload = json.loads(req.data)
        if payload['text']['format']['name'] == CURRENT_SYNTHESIS_FORMAT:
            assert req.full_url.endswith('input_tokens')
            return Response({'input_tokens': 1000000})
        return fallback(req, timeout)
    state = merge(store, job, provider)
    assert state['route'] == 'hierarchical'
    assert state['direct_skip_reason'] == 'max_input_tokens'
    assert state['state'] == 'ready'
    assert store.get_job(job['job_id'])['llm_calls'] == 1
