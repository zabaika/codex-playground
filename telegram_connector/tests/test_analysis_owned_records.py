"""Records belong to an input; source text and indices are application-owned."""
import copy

import pytest

from telegram_connector import analysis_evidence as evidence
from telegram_connector import analysis_settings
from telegram_connector.tests.test_analysis_manifest import manifest_job
from telegram_connector.tests.test_analysis_extraction_accounting import accounting_batch


def record(claim):
    return dict(record_type='event', claim=claim, details=[], entities=['ALPHA'], topic_key='релиз ALPHA',
        assertion_kind='reported', support_status='reported',
        supporting_input_ids=[], related_input_ids=[])


def fragmented_batch(store, job, text):
    """Explicit contiguous ranges exercise split ownership without a retired iterator."""
    batch = accounting_batch(store, job, [text])
    source = batch['messages'][0]
    batch['messages'] = []
    offset = source['input']['fragment_start']
    parts = text.split('\n\n')
    for index, part in enumerate(parts):
        fragment = copy.deepcopy(source)
        fragment['message']['text'] = part + ('\n\n' if index < len(parts) - 1 else '')
        end = offset + len(fragment['message']['text'])
        fragment['input'].update(fragment_start=offset, fragment_end=end)
        batch['messages'].append(fragment)
        offset = end
    return batch


def response(batch):
    scope = evidence.extraction_scope(batch['messages'])
    return {'saturated': False, 'input_dispositions': {key: dict(status='represented',
        records=[record(fragment['message']['text'])], retained_record_ids=[], reason='')
        for key, fragment in zip(scope['inputs'], batch['messages'])}}


def test_owned_wire_has_no_model_indices_or_source_copy_fields():
    schema = evidence.addressed_evidence_format({'model_input_ids':['i0000'], 'count_events': False}, analysis_settings.ANALYSIS_SCHEMA_VERSION)['schema']
    assert 'records' not in schema['properties']
    assert 'record_indices' not in str(schema)
    assert 'content_digest' not in str(schema) and 'quote' not in str(schema)
    value = {'saturated':False,'input_dispositions':{'i0000':dict(status='represented',records=[],retained_record_ids=[],reason='')}}
    with pytest.raises(ValueError): evidence.validate_shape(value, schema)
    value['input_dispositions']['i0000']['records'] = [record('Выпущена ALPHA.')]
    evidence.validate_shape(value, schema)


def test_owned_records_materialize_exact_primary_sources_without_model_quotes(manifest_job):
    store,job=manifest_job
    texts=['Сбер выпустил ALPHA.\n\nПараграф с е, а не ё.\n\nЕщё один параграф.',
           'HapticVLA работает без датчиков. Подробности в соседнем посте.']
    batch=accounting_batch(store,job,texts)
    value=response(batch);original=copy.deepcopy(value)
    result=evidence.classify_extraction_document(store,job['job_id'],batch['batch_id'],generation=job['generation'],
        document=value,fragments=batch['messages'],selected=batch['messages'])
    assert result['input_accounting_complete'] and len(result['records'])==len(batch['messages'])
    assert [r['payload']['source_refs'][0]['quote'] for r in result['records']]==[f['message']['text'] for f in batch['messages']]
    assert [r['payload']['source_refs'][0]['message_id'] for r in result['records']]==[f['input']['message_id'] for f in batch['messages']]
    assert value==original


def test_owned_bad_record_does_not_drop_other_input(manifest_job):
    store,job=manifest_job
    batch=accounting_batch(store,job,['Выпущена ALPHA.','Выпущена BETA.'])
    value=response(batch);keys=list(value['input_dispositions']);value['input_dispositions'][keys[1]]['records'][0]['supporting_input_ids']=['foreign']
    result=evidence.classify_extraction_document(store,job['job_id'],batch['batch_id'],generation=job['generation'],
        document=value,fragments=batch['messages'],selected=batch['messages'])
    assert len(result['records'])==1
    assert keys[0] in result['dispositions'] and keys[1] in result['unresolved_input_ids']


def test_owned_context_uses_exact_supplied_context_and_selected_answer(manifest_job):
    store, job = manifest_job
    batch = accounting_batch(store, job, ['Каков срок подачи?', 'Тридцать дней.'])
    selected = [batch['messages'][1]]
    context = copy.deepcopy(batch['messages'][0])
    context['input']['role'] = 'context'
    value = response({'messages': selected})
    value['input_dispositions']['i0000']['records'][0].update(
        record_type='answer', supporting_input_ids=['c0000'])
    result = evidence.classify_extraction_document(store, job['job_id'], batch['batch_id'],
        generation=job['generation'], document=value, fragments=[*selected, context], selected=selected, context=[context])
    assert result['input_accounting_complete']
    refs = result['records'][0]['payload']['source_refs']
    assert [(r['message_id'], r['role'], r['quote']) for r in refs] == [
        (2, 'selected', 'Тридцать дней.'), (1, 'context', 'Каков срок подачи?')]


def test_owned_repeated_text_keeps_exact_fragment_positions(manifest_job):
    store, job = manifest_job
    part = 'Релиз ALPHA. '
    batch = accounting_batch(store, job, [part * 2])
    selected = []
    for start in (0, len(part)):
        fragment = copy.deepcopy(batch['messages'][0])
        fragment['message']['text'] = part
        fragment['input'].update(fragment_start=start, fragment_end=start + len(part))
        selected.append(fragment)
    result = evidence.classify_extraction_document(store, job['job_id'], batch['batch_id'],
        generation=job['generation'], document=response({'messages': selected}), fragments=selected, selected=selected)
    assert result['input_accounting_complete']
    assert [r['payload']['source_refs'][0]['fragment_start'] for r in result['records']] == [0, len(part)]


def test_owned_expansion_checks_existing_storage_ceiling(manifest_job):
    store, job = manifest_job
    batch = accounting_batch(store, job, ['Релиз ALPHA. ' * 1000])
    value = response(batch)
    value['input_dispositions']['i0000']['records'][0]['claim'] = 'Релиз ALPHA.'
    with pytest.raises(ValueError, match='evidence_output_limit'):
        evidence._materialize_owned_records(value, batch['messages'], [], fragments=batch['messages'],
            maximum=len(evidence.canonical_json(value).encode()) + 32, max_records=20000, allow_retained=False)


def test_owned_duplicate_requires_and_reuses_verified_retained_root(manifest_job):
    store, job = manifest_job
    batch = accounting_batch(store, job, ['Релиз ALPHA.', 'Релиз ALPHA.'])
    first = response(batch)
    first['input_dispositions']['i0001'] = dict(status='unresolved', records=[], retained_record_ids=[], reason='Остаток.')
    initial = evidence.classify_extraction_document(store, job['job_id'], batch['batch_id'],
        generation=job['generation'], document=first, fragments=batch['messages'], selected=batch['messages'])
    root = initial['records'][0]
    repaired = {'saturated': False, 'input_dispositions': {'i0001': dict(status='duplicate', records=[],
        retained_record_ids=[root['record_id']], reason='Дословный повтор.')}}
    result = evidence.classify_extraction_document(store, job['job_id'], batch['batch_id'], generation=job['generation'],
        document=repaired, fragments=[batch['messages'][1]], selected=batch['messages'],
        retained_records={root['record_id']: root}, retained_dispositions=initial['dispositions'])
    assert result['input_accounting_complete']
    assert result['dispositions']['i0001']['record_ids'] == [root['record_id']]


def test_compact_quotes_are_lossless_and_do_not_mutate_records():
    from telegram_connector.analysis_pipeline import _compact_record_quotes
    from telegram_connector.tests.analysis_fixtures import expand_wire_quotes
    refs = [{'channel_id': 1, 'message_id': n, 'quote': 'Дословный источник.'} for n in (2, 3)]
    records = [{'record_id': str(n), 'payload': {'source_refs': [ref]}} for n, ref in enumerate(refs)]
    before = copy.deepcopy(records)
    wire, texts = _compact_record_quotes(records)
    assert records == before and len(texts) == 1
    assert all('quote' not in r['payload']['source_refs'][0] for r in wire)
    assert expand_wire_quotes({'records': wire, 'quote_texts': texts})['records'] == records


def test_historical_period_does_not_vary_with_acceptance_cutoff(manifest_job):
    from telegram_connector.analysis_pipeline import _analysis_period
    _, job = manifest_job
    job = copy.deepcopy(job)
    request = job['request']['request']
    request.update(since='2026-09-08T00:00:00+00:00', until='2026-10-08T23:59:59+00:00', cutoff_at='2026-11-08T10:00:00+00:00')
    before = _analysis_period(job)
    request['cutoff_at'] = '2026-11-08T11:00:00+00:00'
    assert _analysis_period(job) == before == {'since': request['since'], 'until': request['until']}
    request['cutoff_at'] = '2026-10-07T00:00:00+00:00'
    assert _analysis_period(job)['until'] == request['cutoff_at']


def test_owned_repair_requests_only_residual_and_keeps_good_records(manifest_job):
    import json
    import time
    from telegram_connector import analysis_pipeline as pipeline
    from telegram_connector.tests.test_analysis_transport import Response
    from telegram_connector.tests.test_analysis_evidence import model_response
    from telegram_connector.tests.test_analysis_split_recovery import transport
    store, job = manifest_job
    batch = accounting_batch(store, job, ['Анонсированы ALPHA и BETA.', 'Выпущена DELTA.'])
    first = response(batch)
    first['input_dispositions']['i0000'].update(status='unresolved', records=[], reason='Требуется уточнение.')
    first['input_dispositions']['i0001']['records'][0]['entities'] = ['DELTA']
    calls, saved = [], []
    def provider(req, timeout):
        if req.full_url.endswith('input_tokens'): return Response({'input_tokens': 100})
        payload = json.loads(req.data)
        calls.append(payload)
        if len(calls) == 1:
            value = first
        else:
            assert list(payload['text']['format']['schema']['properties']['input_dispositions']['properties']) == ['i0000']
            assert 'DELTA' not in payload['input']
            progress = store.get_batch(batch['batch_id'])['payload']['extraction_progress']
            saved.extend(value['record'] for value in progress['accepted_records'].values())
            assert len(saved) == 1 and saved[0]['payload']['claim'] == 'Выпущена DELTA.'
            value = {'saturated': False, 'input_dispositions': {'i0000': dict(status='represented',
                records=[record('Анонсирована ALPHA.'), {**record('Анонсирована BETA.'), 'entities':['BETA']}],
                retained_record_ids=[], reason='')}}
        evidence.validate_shape(value, payload['text']['format']['schema'])
        return Response(model_response(value))
    outcome = pipeline.extract_analysis_batch(store, job['job_id'], batch, generation=job['generation'],
        **transport(provider))
    assert not outcome['partial'] and len(calls) == 2
    records = list(store.iter_leaf_evidence(job['job_id'], page_size=100))
    assert len(records) == 3
    kept = next(r for r in records if r['record_id'] == saved[0]['record_id'])
    assert kept['payload'] == saved[0]['payload']


def test_owned_failure_does_not_reopen_valid_ranges_of_the_same_post(manifest_job):
    store, job = manifest_job
    batch = fragmented_batch(store, job, 'Выпущена ALPHA.\n\nАнонсированы BETA и GAMMA.\n\nВыпущена DELTA.')
    value = response(batch)
    value['input_dispositions']['i0001'].update(status='unresolved', records=[], reason='Требуется уточнение.')
    result = evidence.classify_extraction_document(store, job['job_id'], batch['batch_id'], generation=job['generation'],
        document=value, fragments=batch['messages']+batch['context'], selected=batch['messages'], context=batch['context'])
    assert result['unresolved_input_ids'] == ['i0001']
    assert set(result['dispositions']) == {'i0000','i0002'}
    assert len(result['records']) == 2




@pytest.mark.parametrize('reference',['i0000:9','i0001:0','i0000:0'])
def test_current_record_reference_cannot_invent_roots_or_cross_posts(manifest_job,reference):
    store,job=manifest_job
    batch=accounting_batch(store,job,['Выпущена ALPHA.','Цена BETA — 10 EUR.'])
    value=response(batch)
    value['input_dispositions']['i0000']['records'][0]['supporting_input_ids']=['i0001']
    value['input_dispositions']['i0001'].update(records=[],retained_record_ids=[reference])
    result=evidence.classify_extraction_document(store,job['job_id'],batch['batch_id'],generation=job['generation'],
        document=value,fragments=batch['messages'],selected=batch['messages'])
    assert result['unresolved_input_ids']==['i0001']




def test_repair_schema_separates_new_records_from_retained_roots():
    schema = evidence.addressed_evidence_format({'model_input_ids': ['i0001'], 'allow_retained': True, 'count_events': False}, analysis_settings.ANALYSIS_SCHEMA_VERSION)['schema']
    decision = dict(status='represented', records=[record('Выпущена ALPHA.')], retained_record_ids=['i0001:0'], reason='')
    document = {'saturated': False, 'input_dispositions': {'i0001': decision}}
    with pytest.raises(ValueError):
        evidence.validate_shape(document, schema)
    decision['retained_record_ids'] = []
    evidence.validate_shape(document, schema)
    decision.update(records=[], retained_record_ids=['verified-root'])
    evidence.validate_shape(document, schema)
