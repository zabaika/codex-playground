"""Addressed extraction counts inputs without claiming semantic completeness."""
import copy
import json

import pytest

from telegram_connector import analysis_evidence
from telegram_connector.tests.test_analysis_manifest import manifest_job, seed, build
from telegram_connector.tests.test_analysis_batching import batches



def accounting_batch(store, job, texts):
    for number, text in enumerate(texts, 1):
        seed(store, 22, [number], text=text)
    build(store, job)
    batch = next(batches(store, job))
    return batch


def envelope(batch, *, dispositions=None):
    """Author the current owned wire; absent decisions intentionally stay absent."""
    fragment = batch['messages'][0]
    record = dict(record_type='claim', claim=fragment['message']['text'], details=[],
        entities=[], topic_key='событие', assertion_kind='reported', support_status='reported',
        supporting_input_ids=[], related_input_ids=[])
    return dict(saturated=False, input_dispositions=(
        {'i0000': dict(status='represented', records=[record], retained_record_ids=[], reason='')}
        if dispositions is None else copy.deepcopy(dispositions)))


def classify(store, job, batch, value):
    return analysis_evidence.classify_extraction_document(
        store, job['job_id'], batch['batch_id'], generation=job['generation'],
        document=value, fragments=batch['messages'] + batch['context'], selected=batch['messages'])


def test_saturated_output_splits_only_residual_and_keeps_parent_record(manifest_job):
    from telegram_connector import analysis_pipeline
    from telegram_connector.tests.test_analysis_evidence import model_response
    from telegram_connector.tests.test_analysis_transport import Response

    store, job = manifest_job
    batch = accounting_batch(store, job, ['Событие ALPHA.', 'Событие BETA.', 'Событие GAMMA.'])
    request = copy.deepcopy(store.get_job(job['job_id'])['request'])
    store.conn.execute('UPDATE analysis_jobs SET request_json=? WHERE job_id=?',
        (analysis_pipeline.canonical_json(request), job['job_id']))
    sent = []
    first = envelope(batch)
    first['saturated'] = True
    def send(req, timeout):
        if req.full_url.endswith('input_tokens'):
            return Response({'input_tokens': 100})
        payload = json.loads(req.data)
        sent.append(payload)
        if len(sent) == 1:
            return Response(model_response(first))
        assert 'ALPHA' not in payload['input']
        active = next(row for row in store.conn.execute(
            "SELECT batch_id FROM analysis_batches WHERE job_id=? AND state='running' ORDER BY rowid DESC",
            (job['job_id'],)))
        child = analysis_pipeline._restore_extraction_batch(store, store.get_job(job['job_id']), active[0])
        return Response(model_response(envelope(child)))
    kwargs = dict(generation=job['generation'], api_key='fixture-key',
        capabilities={'model': 'fixture-model', 'context_window': 128000,
                      'max_output_tokens': 128000, 'source': 'fixture'},
        deadline=100, monotonic_func=lambda: 0, urlopen_func=send, sleep_func=lambda _: None)
    result = analysis_pipeline.extract_analysis_batch(store, job['job_id'], batch, **kwargs)
    assert result['partial'] is False
    assert len(sent) == 3
    records = list(store.iter_leaf_evidence(job['job_id'], page_size=10))
    assert len(records) == 3
    parent = store.get_batch(batch['batch_id'])
    assert parent['state'] == 'succeeded'
    assert parent['payload']['extraction_progress']['accepted_records']
    assert not store.incomplete_evidence_reasons(job['job_id'])
    again = analysis_pipeline.extract_analysis_batch(store, job['job_id'], batch, **kwargs)
    assert again['partial'] is False and len(sent) == 3


def test_cache_preserves_addressed_coverage_and_literal_date(manifest_job):
    from datetime import datetime, timezone
    from telegram_connector import analysis_pipeline
    from telegram_connector.tests.test_analysis_cache import new_target, reuse
    from telegram_connector.tests.test_analysis_evidence import model_response
    from telegram_connector.tests.test_analysis_transport import Response

    store, job = manifest_job
    batch = accounting_batch(store, job, ['Склад откроется 4 июля.'])
    value = envelope(batch)
    def send(req, timeout):
        return Response({'input_tokens': 100} if req.full_url.endswith('input_tokens') else
                        {**model_response(value), 'model': 'fixture-model'})
    result = analysis_pipeline.extract_analysis_batch(store, job['job_id'], batch,
        generation=job['generation'], api_key='fixture-key',
        capabilities={'model': 'fixture-model', 'context_window': 128000,
                      'max_output_tokens': 128000, 'source': 'fixture'},
        deadline=100, monotonic_func=lambda: 0, urlopen_func=send, sleep_func=lambda _: None)
    assert not result['partial']
    origin = next(store.iter_leaf_evidence(job['job_id'], page_size=10))
    store.finish_job(job['job_id'], generation=job['generation'], status='completed', now=datetime.now(timezone.utc))
    target, ready = new_target(store, job)
    assert reuse(store, target, ready)
    copied = next(store.iter_leaf_evidence(target['job_id'], page_size=10))
    assert origin['record_id'] != copied['record_id']
    assert copied['payload'] == origin['payload']
    progress = store.get_batch(ready['batch_id'])['payload']['extraction_progress']
    assert progress['dispositions']['i0000']['record_ids'] == [copied['record_id']]
    assert progress['closed'] and not progress['unresolved_input_ids']
    assert store.get_job(target['job_id'])['llm_calls'] == 0


def test_saturated_all_addressed_inputs_do_not_duplicate_accepted_parent_facts(manifest_job):
    from telegram_connector import analysis_pipeline
    from telegram_connector.tests.analysis_fixtures import extraction_inputs
    from telegram_connector.tests.test_analysis_evidence import model_response
    from telegram_connector.tests.test_analysis_transport import Response

    store, job = manifest_job
    batch = accounting_batch(store, job, ['Продукт ALPHA.', 'Продукт BETA.'])
    requests = []
    def send(req, timeout):
        if req.full_url.endswith('input_tokens'):
            return Response({'input_tokens': 100})
        payload = json.loads(req.data)
        requests.append(payload)
        fragments = extraction_inputs(payload)
        decisions = {}
        for fragment in fragments:
            decision = envelope({'messages': [fragment]})['input_dispositions']['i0000']
            decisions[fragment['input_id']] = decision
        return Response(model_response({'input_dispositions': decisions, 'saturated': len(requests) == 1}))
    result = analysis_pipeline.extract_analysis_batch(store, job['job_id'], batch,
        generation=job['generation'], api_key='fixture-key',
        capabilities={'model': 'fixture-model', 'context_window': 128000,
                      'max_output_tokens': 128000, 'source': 'fixture'},
        deadline=100, monotonic_func=lambda: 0, urlopen_func=send, sleep_func=lambda _: None)
    assert not result['partial'] and len(requests) == 3
    assert len(list(store.iter_leaf_evidence(job['job_id'], page_size=10))) == 2


def test_missing_input_disposition_preserves_valid_record_and_reports_unaccounted(manifest_job):
    store, job = manifest_job
    batch = accounting_batch(store, job, ['Первое событие.', 'Другое событие.'])
    value = envelope(batch)
    before = copy.deepcopy(value)
    result = classify(store, job, batch, value)
    scope = analysis_evidence.extraction_scope(batch['messages'])
    assert len(result['records']) == 1
    assert result['unresolved_input_ids'] == [list(scope['inputs'])[1]]
    assert result['input_accounting_complete'] is False
    assert result['semantic_coverage'] == 'unknown'
    assert 'extraction_coverage_incomplete' in result['reasons']
    assert value == before


def test_local_empty_text_closes_only_empty_input_without_an_inference_disposition(manifest_job):
    store, job = manifest_job
    batch = accounting_batch(store, job, ['Первое событие.', ''])
    result = classify(store, job, batch, envelope(batch))
    assert result['input_accounting_complete'] is True
    assert result['unresolved_input_ids'] == []
    assert sorted(d['status'] for d in result['dispositions'].values()) == ['empty_text', 'represented']
    assert result['semantic_coverage'] == 'unknown'


def test_empty_scope_is_local_before_any_provider_dispatch(manifest_job):
    from telegram_connector import analysis_pipeline
    store, job = manifest_job
    seed(store, 22, [1], text='')
    build(store, job)
    transport = {'generation': job['generation'], 'api_key': 'fixture-key',
        'capabilities': {'model': 'fixture-model', 'context_window': 128000,
                         'max_output_tokens': 128000, 'source': 'fixture'},
        'deadline': 100, 'monotonic_func': lambda: 0,
        'urlopen_func': lambda *a, **k: pytest.fail('Empty selected scope requires no provider count/inference')}
    planned = list(analysis_pipeline.iter_analysis_batches(store, job['job_id'],
        build_payload=lambda selected, context: analysis_pipeline.build_extraction_payload(store.get_job(job['job_id']), selected, context), **transport))
    assert len(planned) == 1
    progress = store.get_batch(planned[0]['batch_id'])['payload']['extraction_progress']
    assert progress['dispositions']['i0000']['status'] == 'empty_text'
    assert not analysis_pipeline.extract_analysis_batch(store, job['job_id'], planned[0], **transport)['partial']
    assert store.get_job(job['job_id'])['provider_requests'] == 0




def test_extraction_progress_requires_receipt_and_keeps_accepted_slots_immutable(manifest_job):
    import pytest
    from telegram_connector.analysis_store import StaleGenerationError

    store, job = manifest_job
    batch = accounting_batch(store, job, ['Первое событие.', 'Другое событие.'])
    scope = analysis_evidence.extraction_scope(batch['messages'])
    progress = store.begin_extraction_request(job['job_id'], batch['batch_id'],
        generation=job['generation'], expected=store.get_batch(batch['batch_id'])['payload'].get('extraction_progress'), scope=scope)
    classified = classify(store, job, batch, envelope(batch))
    with pytest.raises(ValueError, match='settled'):
        store.save_extraction_progress(job['job_id'], batch['batch_id'],
            generation=job['generation'], expected=progress,
            classified=classified, attempt_id='missing')
    store.reserve_provider_attempt(job['job_id'], batch['batch_id'],
        generation=job['generation'], attempt_id='fixture', request_kind='inference',
        input_tokens=10, output_tokens=100)
    store.settle_provider_attempt(job['job_id'], batch['batch_id'],
        generation=job['generation'], attempt_id='fixture', outcome='succeeded',
        usage={'input_tokens': 10, 'output_tokens': 20, 'cached_input_tokens': 0})
    saved = store.save_extraction_progress(job['job_id'], batch['batch_id'],
        generation=job['generation'], expected=progress,
        classified=classified, attempt_id='fixture')
    assert len(saved['accepted_records']) == 1
    with pytest.raises(StaleGenerationError):
        store.save_extraction_progress(job['job_id'], batch['batch_id'],
            generation=job['generation'], expected=progress,
            classified=classified, attempt_id='fixture')
    closed = store.finish_extraction_progress(job['job_id'], batch['batch_id'],
        generation=job['generation'], expected=saved)
    assert closed['state'] == 'succeeded'
    assert store.incomplete_evidence_reasons(job['job_id']) == ['extraction_coverage_incomplete']
    assert len(list(store.iter_leaf_evidence(job['job_id'], page_size=10))) == 1
    assert store.get_job(job['job_id'])['used_tokens'] == 30


def test_worker_surfaces_missing_input_in_content_status_and_read_only_report(manifest_job, monkeypatch):
    import json
    from types import SimpleNamespace
    from telegram_connector import telegram_analysis as analysis
    from telegram_connector import analysis_telemetry
    from telegram_connector.tests.test_analysis_transport import Response

    store, job = manifest_job
    batch = accounting_batch(store, job, ['Первое событие.', 'Другое событие.'])
    value = envelope(batch)
    request = copy.deepcopy(store.get_job(job['job_id'])['request'])
    request['settings']['analysis']['max_repair_attempts'] = 0
    store.conn.execute('UPDATE analysis_jobs SET request_json=? WHERE job_id=?',
                       (analysis.canonical_json(request), job['job_id']))

    async def history_ready(*args, **kwargs):
        return None

    monkeypatch.setattr(analysis.history_client, 'ensure_analysis_history', history_ready)
    sent = []
    def provider(req, timeout):
        payload = json.loads(req.data)
        if req.full_url.endswith('input_tokens'):
            return Response({'input_tokens': 100})
        if payload['text']['format']['name'] == analysis_evidence.ADDRESSED_EVIDENCE_FORMATS[job['request']['settings']['schema_version']]:
            result = value
        else:
            evidence = json.loads(payload['input'].split('Опорные записи: ', 1)[1])
            result = {'summary': 'За период произошло событие.', 'items': [
                {'title': 'Первое событие', 'text': 'Первое событие.',
                 'contradictions': [],
                 'evidence_ids': [evidence['records'][0]['record_id']]}],
                'limitations': [], 'insufficient_information': False, 'saturated': False}
        return Response({'status': 'completed', 'output': [{'type': 'message', 'content': [
            {'type': 'output_text', 'text': json.dumps(result)}]}],
            'usage': {'input_tokens': 100, 'output_tokens': 100}})

    result = analysis.execute_analysis_job(store, store.get_job(job['job_id']),
        runtime=SimpleNamespace(), client=object(), api_key='fixture-key', token='fixture-token',
        capabilities={'model': 'fixture-model', 'context_window': 128000,
                      'max_output_tokens': 128000, 'source': 'fixture'},
        access_provider=lambda: {'chats': frozenset({'100'}), 'users': frozenset({'7'}), 'usernames': frozenset()},
        urlopen_func=provider, send_func=lambda *args, **kwargs: (
            sent.append(args[2]) or {'message_id': len(sent), 'chat': {'id': 100}}),
        deadline=100, monotonic_func=lambda: 0, sleep_func=lambda _: None)
    assert result == 'partial'
    assert 'extraction_coverage_incomplete' in '\n'.join(sent)
    assert 'Первое событие.' in '\n'.join(sent)
    before = store.conn.total_changes
    report = analysis_telemetry.summarize_job(store, job['job_id'])
    assert report['extraction']['input_accounting_complete'] is False
    assert report['extraction']['unaccounted_fragments'] == 1
    assert report['extraction']['semantic_coverage'] == 'unknown'
    assert store.conn.total_changes == before












@pytest.mark.parametrize('defect', ['missing_decision', 'invalid_record'])
def test_residual_repair_preserves_first_record_and_sends_only_unresolved_input(manifest_job, defect):
    from telegram_connector import analysis_pipeline
    from telegram_connector.tests.test_analysis_evidence import model_response
    from telegram_connector.tests.test_analysis_transport import Response

    store, job = manifest_job
    batch = accounting_batch(store, job, ['Выпущен продукт ALPHA.', 'Выпущен продукт BETA.'])
    initial = envelope(batch)
    fixed = envelope(batch)
    fixed['input_dispositions']['i0001'] = fixed['input_dispositions'].pop('i0000')
    fixed['input_dispositions']['i0001']['records'][0]['claim'] = 'Выпущен продукт BETA.'
    if defect == 'invalid_record':
        initial['input_dispositions']['i0001'] = copy.deepcopy(fixed['input_dispositions']['i0001'])
        initial['input_dispositions']['i0001']['records'][0]['details'] = 'bad type'
    request = copy.deepcopy(store.get_job(job['job_id'])['request'])
    store.conn.execute('UPDATE analysis_jobs SET request_json=? WHERE job_id=?',
                       (analysis_pipeline.canonical_json(request), job['job_id']))
    requests = []
    def send(req, timeout):
        if req.full_url.endswith('input_tokens'):
            return Response({'input_tokens': 100})
        requests.append(json.loads(req.data))
        return Response(model_response(initial if len(requests) == 1 else fixed))
    result = analysis_pipeline.extract_analysis_batch(store, job['job_id'], batch,
        generation=job['generation'], api_key='fixture-key',
        capabilities={'model': 'fixture-model', 'context_window': 128000,
                      'max_output_tokens': 128000, 'source': 'fixture'},
        deadline=100, monotonic_func=lambda: 0, urlopen_func=send, sleep_func=lambda _: None)
    diagnostic = store.get_batch(batch['batch_id'])['payload']['extraction_progress']
    assert result['partial'] is False, json.dumps({k: diagnostic[k] for k in (
        'record_errors', 'dispositions', 'unresolved_input_ids')}, ensure_ascii=False)
    assert len(requests) == 2
    assert 'original_task' in requests[1]['input'] and 'event_at' not in requests[1]['input']
    assert 'BETA' in requests[1]['input'] and 'ALPHA' not in requests[1]['input']
    saved = store.get_batch(batch['batch_id'])['payload']['extraction_progress']
    origins = list(saved['accepted_records'].values())
    assert len(origins) == 2 and origins[0]['attempt_id'] != origins[1]['attempt_id']
    assert [r['payload']['claim'] for r in store.iter_leaf_evidence(job['job_id'], page_size=10)] != []
    assert store.get_job(job['job_id'])['llm_calls'] == 2




def test_repair_reordering_cannot_reinsert_an_already_accepted_fact(manifest_job):
    from telegram_connector import analysis_pipeline
    from telegram_connector.tests.test_analysis_evidence import model_response
    from telegram_connector.tests.test_analysis_transport import Response
    store, job = manifest_job
    batch = accounting_batch(store, job, ['Вышли ALPHA и BETA.'])
    value = envelope(batch)
    alpha = value['input_dispositions']['i0000']['records'][0]
    alpha['claim'] = 'Вышла ALPHA.'
    beta = {**copy.deepcopy(alpha), 'claim': 'Вышла BETA.'}
    initial = copy.deepcopy(value)
    initial['input_dispositions']['i0000']['records'] = [{**copy.deepcopy(beta), 'details': 'invalid'}, alpha]
    fixed = copy.deepcopy(value)
    fixed['input_dispositions']['i0000']['records'] = [alpha, beta]  # Accepted ALPHA moved from slot 1 to slot 0.
    calls = []
    def send(req, timeout):
        if req.full_url.endswith('input_tokens'):
            return Response({'input_tokens': 100})
        calls.append(1)
        return Response(model_response(initial if len(calls) == 1 else fixed))
    result = analysis_pipeline.extract_analysis_batch(store, job['job_id'], batch,
        generation=job['generation'], api_key='fixture-key',
        capabilities={'model': 'fixture-model', 'context_window': 128000,
                      'max_output_tokens': 128000, 'source': 'fixture'},
        deadline=100, monotonic_func=lambda: 0, urlopen_func=send, sleep_func=lambda _: None)
    assert not result['partial']
    records = list(store.iter_leaf_evidence(job['job_id'], page_size=10))
    assert len(records) == 2
    progress = store.get_batch(batch['batch_id'])['payload']['extraction_progress']
    alpha_origin = next(value for value in progress['accepted_records'].values()
                        if value['record']['payload']['claim'] == 'Вышла ALPHA.')
    assert alpha_origin['attempt_id'] == progress['diagnostics'][0]['attempt_id']


def test_saturated_accounting_does_not_become_complete_after_all_input_ids_are_present(manifest_job):
    store, job = manifest_job
    batch = accounting_batch(store, job, ['Первое событие.'])
    value = envelope(batch)
    value['saturated'] = True
    result = classify(store, job, batch, value)
    assert result['input_accounting_complete'] is False
    assert result['saturated'] is True


def test_malformed_record_does_not_discard_neighboring_supported_record(manifest_job):
    store, job = manifest_job
    batch = accounting_batch(store, job, ['Первое событие.'])
    value = envelope(batch)
    value['input_dispositions']['i0000']['records'].append('not a record')
    result = classify(store, job, batch, value)
    assert len(result['records']) == 1
    assert result['record_errors'] == {'1': 'extraction_owned_record_schema'}






def test_one_invalid_record_keeps_its_source_input_unresolved_even_with_a_valid_neighbor(manifest_job):
    store, job = manifest_job
    batch = accounting_batch(store, job, ['Событие ALPHA. Событие BETA.'])
    value = envelope(batch)
    value['input_dispositions']['i0000']['records'].append({**copy.deepcopy(value['input_dispositions']['i0000']['records'][0]), 'details': 'invalid'})
    result = classify(store, job, batch, value)
    assert len(result['records']) == 1
    assert result['unresolved_input_ids'] == ['i0000']


def test_pending_extraction_reserves_closure_before_a_provider_request(manifest_job):
    store, job = manifest_job
    batch = accounting_batch(store, job, ['Первое событие.', 'Другое событие.'])
    progress = store.begin_extraction_request(job['job_id'], batch['batch_id'],
        generation=job['generation'], expected=store.get_batch(batch['batch_id'])['payload'].get('extraction_progress'),
        scope=analysis_evidence.extraction_scope(batch['messages']))
    usage = store.storage_usage(job['job_id'])
    assert usage['progress_reserve'] > 0
    assert usage['allocated_bytes'] == sum(usage[k] for k in (
        'logical_bytes', 'receipt_reserve', 'finalization_reserve', 'telemetry_reserve', 'progress_reserve'))
    closed = store.finish_extraction_progress(job['job_id'], batch['batch_id'],
        generation=job['generation'], expected=progress)
    assert closed['payload']['outcome'] == 'partial'
    assert store.storage_usage(job['job_id'])['allocated_bytes'] <= usage['allocated_bytes']


def test_resume_after_receipt_without_observation_never_repeats_inference(manifest_job):
    from telegram_connector import analysis_pipeline

    store, job = manifest_job
    batch = accounting_batch(store, job, ['Первое событие.'])
    request = copy.deepcopy(store.get_job(job['job_id'])['request'])
    store.conn.execute('UPDATE analysis_jobs SET request_json=? WHERE job_id=?',
                       (analysis_pipeline.canonical_json(request), job['job_id']))
    store.begin_extraction_request(job['job_id'], batch['batch_id'],
        generation=job['generation'], expected=store.get_batch(batch['batch_id'])['payload'].get('extraction_progress'),
        scope=analysis_evidence.extraction_scope(batch['messages']))
    store.reserve_provider_attempt(job['job_id'], batch['batch_id'],
        generation=job['generation'], attempt_id='lost-observation', request_kind='inference',
        input_tokens=10, output_tokens=100)
    store.settle_provider_attempt(job['job_id'], batch['batch_id'],
        generation=job['generation'], attempt_id='lost-observation', outcome='succeeded',
        usage={'input_tokens': 10, 'output_tokens': 20, 'cached_input_tokens': 0})
    before = store.get_job(job['job_id'])['provider_requests']
    result = analysis_pipeline.extract_analysis_batch(store, job['job_id'], batch,
        generation=job['generation'], urlopen_func=lambda *a, **k: pytest.fail('Unexpected provider call'))
    assert result['partial'] is True
    assert store.get_job(job['job_id'])['provider_requests'] == before
    progress = store.get_batch(batch['batch_id'])['payload']['extraction_progress']
    assert 'extraction_observation_missing' in progress['diagnostics'][-1]['codes']
    assert progress['active_request'] is None


def test_deadline_after_observation_publishes_subset_without_starting_repair(manifest_job):
    from telegram_connector import analysis_pipeline
    from telegram_connector.tests.test_analysis_evidence import model_response
    from telegram_connector.tests.test_analysis_transport import Response
    store, job = manifest_job
    batch = accounting_batch(store, job, ['Первый факт.', 'Второй факт.'])
    clock, calls = [0], []
    def send(req, timeout):
        if req.full_url.endswith('input_tokens'):
            return Response({'input_tokens': 100})
        calls.append(timeout)
        clock[0] = 101
        return Response(model_response(envelope(batch)))
    with pytest.raises(TimeoutError, match='deadline'):
        analysis_pipeline.extract_analysis_batch(store, job['job_id'], batch,
            generation=job['generation'], api_key='fixture-key',
            capabilities={'model': 'fixture-model', 'context_window': 128000,
                          'max_output_tokens': 128000, 'source': 'fixture'},
            deadline=100, monotonic_func=lambda: clock[0], urlopen_func=send, sleep_func=lambda _: None)
    assert len(calls) == 1
    assert len(list(store.iter_leaf_evidence(job['job_id'], page_size=10))) == 1
    assert store.get_batch(batch['batch_id'])['payload']['outcome'] == 'partial'
