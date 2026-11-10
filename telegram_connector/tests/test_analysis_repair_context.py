from telegram_connector.tests.analysis_fixtures import CURRENT_EVIDENCE_FORMAT, CURRENT_SYNTHESIS_FORMAT
"""Residual requests retain source context and immutable represented roots."""
import copy
import json

from telegram_connector.tests.analysis_fixtures import expand_wire_quotes

import pytest

from telegram_connector import analysis_pipeline as pipeline
from telegram_connector.tests.test_analysis_manifest import manifest_job
from telegram_connector.tests.test_analysis_extraction_accounting import accounting_batch, envelope
from telegram_connector.tests.test_analysis_evidence import model_response
from telegram_connector.tests.test_analysis_transport import Response
from telegram_connector.tests.test_analysis_split_recovery import transport
from telegram_connector.tests.test_analysis_merge import merge
from telegram_connector.tests.test_analysis_synthesis import synthesize


def command_job(store, job, command):
    request = copy.deepcopy(store.get_job(job['job_id'])['request'])
    request['request'].update(command=command, query='Срок подачи документов Uno')
    store.conn.execute('UPDATE analysis_jobs SET command=?,request_json=? WHERE job_id=?',
        (command, pipeline.canonical_json(request), job['job_id']))
    return store.get_job(job['job_id'])


@pytest.mark.parametrize('command', ['topics', 'qa', 'search'])
def test_repair_keeps_original_context_through_final_synthesis(manifest_job, command):
    store, job = manifest_job
    question = 'Какой срок подачи документов Uno?'
    batch = accounting_batch(store, job, ['Тридцать дней.', question])
    job = command_job(store, job, command)
    answer, context = batch['messages']
    context = {**context, 'input': {**context['input'], 'role': 'context'}}
    planned = store.plan_batch(job['job_id'], generation=job['generation'], step_key='context-answer',
        kind='extract', payload={'inputs': [answer['input']], 'context': [context['input']]}, input_digest='context-answer')
    batch = {'batch_id': planned['batch_id'], 'messages': [answer], 'context': [context]}
    fixed = envelope(batch)
    record = fixed['input_dispositions']['i0000']['records'][0]
    record.update(record_type='answer' if command == 'qa' else 'claim',
        claim='Срок подачи документов Uno — тридцать дней.', entities=['Uno'])
    record['supporting_input_ids'] = ['c0000']
    repairs, final_requests = [], []
    def provider(req, timeout):
        payload = json.loads(req.data)
        if req.full_url.endswith('input_tokens'):
            return Response({'input_tokens': 100})
        if payload['text']['format']['name'] == CURRENT_EVIDENCE_FORMAT:
            repairs.append(payload)
            if len(repairs) == 1:
                result = {'saturated': False, 'input_dispositions': {}}
            else:
                assert question in payload['input']
                assert '"role":"context"' in payload['input']
                result = fixed
        else:
            assert payload['text']['format']['name'] == CURRENT_SYNTHESIS_FORMAT
            data = expand_wire_quotes(json.loads(payload['input'].split('Опорные записи: ', 1)[1]))
            final_requests.append(data)
            saved = data['records'][0]
            assert saved['payload']['claim'] == record['claim']
            assert question in [ref['quote'] for ref in saved['payload']['source_refs']]
            result = {'summary': 'Сроки подачи.', 'items': [{'title': 'Срок подачи документов Uno',
                'text': saved['payload']['claim'], 'contradictions': [],
                'evidence_ids': [saved['record_id']]}], 'limitations': [], 'insufficient_information': False, 'saturated': False}
        return Response(model_response(result))
    outcome = pipeline.extract_analysis_batch(store, job['job_id'], batch, generation=job['generation'], **transport(provider))
    assert not outcome['partial'] and len(repairs) == 2
    merge(store, job, provider)
    synthesize(store, job, provider)
    assert len(final_requests) == 1
    progress = store.get_batch(batch['batch_id'])['payload']['extraction_progress']
    assert len(progress['scope']['inputs']) == len(progress['dispositions']) == 1


@pytest.mark.parametrize('command', ['topics', 'qa', 'search'])
def test_duplicate_repair_uses_saved_represented_root_without_reinserting_it(manifest_job, command):
    store, job = manifest_job
    text = 'Банк Uno: комиссия 10 EUR.'
    batch = accounting_batch(store, job, [text, text])
    job = command_job(store, job, command)
    initial = envelope(batch)
    initial['input_dispositions']['i0000']['records'][0]['entities'] = ['Uno']
    calls = []
    def provider(req, timeout):
        if req.full_url.endswith('input_tokens'):
            return Response({'input_tokens': 100})
        payload = json.loads(req.data)
        calls.append(payload)
        if len(calls) == 1:
            result = initial
        else:
            progress = store.get_batch(batch['batch_id'])['payload']['extraction_progress']
            identity = progress['dispositions']['i0000']['record_ids'][0]
            assert identity in payload['input'], 'Retained root A must be present in the residual request for B'
            result = {'saturated': False, 'input_dispositions': {'i0001': dict(status='duplicate', records=[],
                 retained_record_ids=[identity], reason='Дословный повтор о том же банке.')}}
        return Response(model_response(result))
    outcome = pipeline.extract_analysis_batch(store, job['job_id'], batch, generation=job['generation'], **transport(provider))
    assert not outcome['partial'] and len(calls) == 2
    progress = store.get_batch(batch['batch_id'])['payload']['extraction_progress']
    assert [decision['status'] for decision in progress['dispositions'].values()] == ['represented', 'duplicate']
    assert len(list(store.iter_leaf_evidence(job['job_id'], page_size=10))) == 1
    assert next(iter(progress['accepted_records'].values()))['attempt_id'] == progress['diagnostics'][0]['attempt_id']


def test_source_edit_after_progress_commit_stops_before_repair_dispatch(manifest_job, monkeypatch):
    store, job = manifest_job
    batch = accounting_batch(store, job, ['Банк Uno: комиссия 10 EUR.', 'Другой факт.'])
    initial = envelope(batch)
    original = store.save_extraction_progress
    def edited(*args, **kwargs):
        result = original(*args, **kwargs)
        store.conn.execute("UPDATE messages SET text='Изменённый источник' WHERE channel_id=22 AND message_id=1")
        return result
    monkeypatch.setattr(store, 'save_extraction_progress', edited)
    calls = []
    def provider(req, timeout):
        if req.full_url.endswith('input_tokens'):
            return Response({'input_tokens': 100})
        calls.append(1)
        return Response(model_response(initial))
    with pytest.raises(ValueError, match='input_changed'):
        pipeline.extract_analysis_batch(store, job['job_id'], batch, generation=job['generation'], **transport(provider))
    assert calls == [1]
    assert store.get_job(job['job_id'])['status'] == 'interrupted'


def test_residual_budget_stop_publishes_previously_accepted_records(manifest_job, monkeypatch):
    from telegram_connector.analysis_store import BudgetExceededError
    store, job = manifest_job
    batch = accounting_batch(store, job, ['Банк Uno: комиссия 10 EUR.', 'Другой факт.'])
    original = pipeline.prepare_analysis_payload
    def limited(*args, **kwargs):
        if store.get_job(job['job_id'])['llm_calls']:
            raise BudgetExceededError('max_total_tokens')
        return original(*args, **kwargs)
    monkeypatch.setattr(pipeline, 'prepare_analysis_payload', limited)
    def provider(req, timeout):
        return Response({'input_tokens': 100} if req.full_url.endswith('input_tokens') else model_response(envelope(batch)))
    result = pipeline.extract_analysis_batch(store, job['job_id'], batch, generation=job['generation'], **transport(provider))
    assert result['partial'] and result['outcome'] == 'budget_exhausted'
    assert len(list(store.iter_leaf_evidence(job['job_id'], page_size=10))) == 1
    saved = store.get_batch(batch['batch_id'])
    assert saved['payload']['extraction_progress']['closed']
    assert store.get_job(job['job_id'])['llm_calls'] == 1


@pytest.mark.parametrize('defect', ['different_subject', 'different_text', 'foreign_id', 'duplicate_root'])
def test_retained_duplicate_cannot_bypass_scope_subject_or_represented_root(manifest_job, defect):
    from telegram_connector import analysis_evidence as evidence
    store, job = manifest_job
    a = 'Банк Uno: комиссия 10 EUR.'
    b = 'Банк Dos: комиссия 10 EUR.' if defect == 'different_subject' else a + ' Уточнение.' if defect == 'different_text' else a
    batch = accounting_batch(store, job, [a, b])
    initial = envelope(batch)
    initial['input_dispositions']['i0000']['records'][0]['entities'] = ['Uno']
    saved = evidence.classify_extraction_document(store, job['job_id'], batch['batch_id'], generation=job['generation'],
        document=initial, fragments=batch['messages'], selected=batch['messages'])
    record = saved['records'][0]
    roots = copy.deepcopy(saved['dispositions'])
    if defect == 'duplicate_root':
        roots['i0000']['status'] = 'duplicate'
    identity = 'foreign-record' if defect == 'foreign_id' else record['record_id']
    value = {'saturated': False, 'input_dispositions': {'i0001': dict(status='duplicate', records=[], retained_record_ids=[identity], reason='Повтор.')}}
    result = evidence.classify_extraction_document(store, job['job_id'], batch['batch_id'], generation=job['generation'],
        document=value, fragments=batch['messages'][1:], selected=batch['messages'],
        retained_records={record['record_id']: record}, retained_dispositions=roots)
    assert 'i0001' in result['unresolved_input_ids']
    assert not result['input_accounting_complete']


def test_repair_packs_one_literal_root_when_saved_roots_are_equivalent(manifest_job):
    store, job = manifest_job
    text = 'Банк Uno: комиссия 10 EUR.'
    batch = accounting_batch(store, job, [text, text, text])
    initial = envelope(batch)
    initial['input_dispositions']['i0000']['records'][0]['entities'] = ['Uno']
    second = copy.deepcopy(initial['input_dispositions']['i0000'])
    initial['input_dispositions']['i0001'] = second
    calls = []
    def provider(req, timeout):
        if req.full_url.endswith('input_tokens'):
            return Response({'input_tokens': 100})
        payload = json.loads(req.data)
        calls.append(payload)
        if len(calls) == 1:
            output = initial
        else:
            progress = store.get_batch(batch['batch_id'])['payload']['extraction_progress']
            first_id = progress['dispositions']['i0000']['record_ids'][0]
            redundant_id = progress['dispositions']['i0001']['record_ids'][0]
            assert first_id in payload['input'] and redundant_id not in payload['input']
            output = {'saturated': False, 'input_dispositions': {'i0002': dict(status='duplicate', records=[],
                 retained_record_ids=[first_id], reason='Дословный повтор Uno.')}}
        return Response(model_response(output))
    result = pipeline.extract_analysis_batch(store, job['job_id'], batch, generation=job['generation'], **transport(provider))
    assert not result['partial'] and len(calls) == 2
    assert len(list(store.iter_leaf_evidence(job['job_id'], page_size=10))) == 2
