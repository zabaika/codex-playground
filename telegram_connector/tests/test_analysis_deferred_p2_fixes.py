"""Budget fallbacks and processed sources use durable, range-complete state."""
import json

import pytest

from telegram_connector import analysis_pipeline as pipeline
from telegram_connector import telegram_analysis as analysis
from telegram_connector.tests.test_analysis_manifest import manifest_job, change_setting
from telegram_connector.tests.test_analysis_relations import records_fixture
from telegram_connector.tests.test_analysis_split_recovery import (
    transport, split_after_commit, irrelevant_provider,
)
from telegram_connector.tests.test_analysis_extraction_accounting import accounting_batch
from telegram_connector.tests.test_analysis_transport import Response


def refusal(req, timeout):
    return Response({'input_tokens': 100} if req.full_url.endswith('input_tokens') else {
        'status': 'completed', 'output': [{'type': 'message', 'content': [
            {'type': 'refusal', 'refusal': 'Authored fixture refusal'}]}],
        'usage': {'input_tokens': 100, 'output_tokens': 1}})


def offline(*args, **kwargs):
    pytest.fail('Exhausted reservation or saved fallback must not dispatch')


@pytest.mark.parametrize('reason', ['max_provider_requests', 'max_llm_calls', 'max_total_tokens'])
def test_direct_budget_stop_persists_partial_and_delivers_saved_fallback(manifest_job, reason):
    store, job = manifest_job
    records_fixture(store, job, [{'claim': f'Подтверждённый факт {n}.'} for n in range(3)])
    change_setting(store, job, 'analysis', reason, 3 if reason == 'max_provider_requests' else
                   1 if reason == 'max_llm_calls' else 2)
    batch = store.plan_batch(job['job_id'], generation=job['generation'],
        step_key='prior-usage', kind='synthesize', payload={})
    prior_transport = transport(lambda *a, **k: Response({'input_tokens': 1} if
        reason == 'max_provider_requests' else {'status': 'completed', 'output': [],
        'usage': {'input_tokens': 1, 'output_tokens': 1}}))
    prior_transport.pop('capabilities')
    for _ in range(2 if reason == 'max_provider_requests' else 1):
        pipeline.request_analysis_json(store, job['job_id'], batch['batch_id'],
            generation=job['generation'], endpoint='input_tokens' if reason == 'max_provider_requests' else 'responses',
            payload={'model': 'fixture-model', 'input': 'Authored prior request', 'max_output_tokens': 1},
            counted_input_tokens=0 if reason == 'max_provider_requests' else 1, **prior_transport)
    checkpoint = store.get_job(job['job_id'])['checkpoint']
    checkpoint['relations'] = {'partial': True, 'reasons': ['relations_incomplete']}
    store.save_checkpoint(job['job_id'], checkpoint, generation=job['generation'],
        max_bytes=job['request']['settings']['analysis']['max_intermediate_bytes'])
    state = pipeline.merge_analysis_records(store, job['job_id'], generation=job['generation'], **transport(offline))
    saved = store.get_job(job['job_id'])
    assert state['state'] == 'partial'
    assert saved['checkpoint']['merge'] == state
    assert reason in state['reasons']
    assert saved['checkpoint']['relations'] == checkpoint['relations']
    assert saved['result']['kind'] == 'budget_stop' and saved['result']['reason'] == reason
    result = pipeline.synthesize_analysis_result(store, job['job_id'], generation=job['generation'], **transport(offline))
    assert result == saved['result']
    assert pipeline.merge_analysis_records(store, job['job_id'], generation=job['generation'], **transport(offline)) == state
    captured = []
    kwargs = dict(generation=job['generation'], token='fixture-token', deadline=100, monotonic_func=lambda: 0,
        sleep_func=lambda _: None,
        access_provider=lambda: {'chats': frozenset({'100'}), 'users': frozenset({'7'}), 'usernames': frozenset()},
        send_func=lambda *a, **k: captured.append(a[2]) or {'message_id': len(captured), 'chat': {'id': 100}})
    assert analysis.deliver_analysis_result(store, job['job_id'], **kwargs)['outcome'] == 'confirmed'
    sent = len(captured)
    assert sent and reason in '\n'.join(captured)
    assert analysis.deliver_analysis_result(store, job['job_id'], **kwargs)['outcome'] == 'confirmed'
    assert len(captured) == sent


def test_merge_byte_cap_persists_partial_using_existing_finalization_reserve(manifest_job):
    store, job = manifest_job
    records_fixture(store, job, [{'claim': 'Первый факт.'}, {'claim': 'Второй факт.'}])
    maximum = store.storage_usage(job['job_id'])['allocated_bytes']
    change_setting(store, job, 'analysis', 'max_intermediate_bytes', maximum)
    state = pipeline.merge_analysis_records(store, job['job_id'], generation=job['generation'], **transport(offline))
    assert state['state'] == 'partial' and state['reasons'] == ['max_intermediate_bytes']
    assert store.get_job(job['job_id'])['checkpoint']['merge'] == state
    assert store.storage_usage(job['job_id'])['allocated_bytes'] <= maximum
    fallback = pipeline.synthesize_analysis_result(store, job['job_id'], generation=job['generation'], **transport(offline))
    assert fallback['partial'] and 'max_intermediate_bytes' in fallback['reasons']


def test_partial_batch_keeps_only_its_fully_accounted_source(manifest_job):
    from telegram_connector.tests.test_analysis_extraction_accounting import envelope
    from telegram_connector.tests.test_analysis_evidence import model_response
    store, job = manifest_job
    batch = accounting_batch(store, job, ['Первый факт.', 'Второй факт.'])
    inference = []
    def provider(req, timeout):
        if req.full_url.endswith('input_tokens'):
            return Response({'input_tokens': 100})
        inference.append(json.loads(req.data))
        return Response(model_response(envelope(batch))) if len(inference) == 1 else refusal(req, timeout)
    result = pipeline.extract_analysis_batch(store, job['job_id'], batch,
        generation=job['generation'], **transport(provider))
    assert result['partial'] and len(inference) == 2
    assert store.processed_analysis_messages(job['job_id']) == 1
    assert [r['message_id'] for r in store.processed_source_refs(job['job_id'], limit=3)] == [1]


def test_refusal_has_no_processed_sources_in_presentation_or_budget_fallback(manifest_job):
    store, job = manifest_job
    batch = accounting_batch(store, job, ['Первый факт.', 'Второй факт.'])
    result = pipeline.extract_analysis_batch(store, job['job_id'], batch,
        generation=job['generation'], **transport(refusal))
    assert result['partial'] and store.get_batch(batch['batch_id'])['state'] == 'succeeded'
    assert store.processed_analysis_messages(job['job_id']) == 0
    assert store.processed_source_refs(job['job_id'], limit=3) == []
    document = {'command': 'topics', 'detail': 'normal', 'query': None,
        'summary': '', 'items': [], 'limitations': [], 'insufficient_information': True,
        'partial': True, 'reasons': ['extraction_coverage_incomplete'],
        'coverage': {'selection_mode': 'all_messages', 'selected_messages': 2,
            'processed_messages': 0, 'known_skipped': 0}}
    view = analysis.prepare_analysis_presentation(store, job['job_id'], document, generation=job['generation'])
    assert not view['result']['sources']
    assert 'Источники обработанных сообщений' not in '\n'.join(view['chunks'])
    fallback = store.record_budget_stop(job['job_id'], generation=job['generation'], reason='max_llm_calls')
    assert not fallback['source_refs']


def test_partial_split_has_no_processed_source_until_all_ranges_finish(manifest_job, monkeypatch):
    store, job = manifest_job
    _, parent = split_after_commit(store, job, monkeypatch)
    for index, identity in enumerate(parent['payload']['split_children']):
        pipeline.extract_analysis_batch(store, job['job_id'], pipeline._restore_extraction_batch(
            store, store.get_job(job['job_id']), identity), generation=job['generation'], **transport(irrelevant_provider))
        assert len(store.processed_source_refs(job['job_id'], limit=3)) == index
    assert store.processed_analysis_messages(job['job_id']) == 1


@pytest.mark.parametrize('change', ['invalidated', 'stale_digest', 'none'])
def test_processed_source_projection_preserves_legacy_and_rejects_invalid_inputs(manifest_job, change):
    store, job = manifest_job
    records_fixture(store, job, [{'claim': 'Первый факт.'}, {'claim': 'Второй факт.'}])
    if change == 'invalidated':
        identity = store.conn.execute("SELECT batch_id FROM analysis_batches WHERE job_id=? AND step_key='fixture-extract/0'",
            (job['job_id'],)).fetchone()[0]
        store.invalidate_batch_dependencies(job['job_id'], identity, generation=job['generation'])
    elif change == 'stale_digest':
        store.conn.execute("UPDATE messages SET text='Изменённый текст' WHERE channel_id=22 AND message_id=1")
    changes = store.conn.total_changes
    refs = store.processed_source_refs(job['job_id'], limit=1)
    assert [ref['message_id'] for ref in refs] == [1 if change == 'none' else 2]
    assert store.processed_source_refs(job['job_id'], limit=0) == []
    assert store.conn.total_changes == changes
