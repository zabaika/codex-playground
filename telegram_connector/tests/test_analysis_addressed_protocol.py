from telegram_connector.tests.analysis_fixtures import expand_wire_quotes
"""New wire contracts preserve the shared durable evidence/progress owners."""
import copy

import pytest

from telegram_connector.tests.analysis_fixtures import prompt_json
from telegram_connector import analysis_evidence as evidence, analysis_settings as settings
from telegram_connector.tests.analysis_fixtures import CURRENT_EVIDENCE_FORMAT, CURRENT_SYNTHESIS_FORMAT
from telegram_connector.tests.test_analysis_manifest import manifest_job
from telegram_connector.tests.test_analysis_extraction_accounting import accounting_batch, envelope


def test_addressed_requires_every_model_input_once_and_reuses_decision_definition():
    scope = {'count_events': False, 'model_input_ids': [f'i{n:04d}' for n in range(500)]}
    schema = evidence.addressed_evidence_format(scope, settings.ANALYSIS_SCHEMA_VERSION)['schema']
    decisions = schema['properties']['input_dispositions']
    assert decisions['type'] == 'object'
    assert decisions['required'] == scope['model_input_ids']
    assert set(decisions['properties']) == set(scope['model_input_ids'])
    assert not decisions['additionalProperties']
    assert len({str(value) for value in decisions['properties'].values()}) == 1
    assert '$defs' in schema and 'date_support' not in schema['properties']
    assert 'event_at' not in str(schema)
    usage = evidence.model_schema_usage(schema)
    assert usage['enum_values'] < 1000 and usage['properties'] < 5000
    assert usage['depth'] <= 10 and usage['string_chars'] <= 120000


def test_addressed_schema_rejects_missing_and_foreign_keys():
    schema = evidence.addressed_evidence_format({'count_events': False, 'model_input_ids': ['i0000', 'i0001']}, settings.ANALYSIS_SCHEMA_VERSION)['schema']
    decision = dict(status='irrelevant', records=[], retained_record_ids=[], reason='Нет события.')
    value = {'saturated': False, 'input_dispositions': {
        'i0000': decision, 'i0001': decision}}
    evidence.validate_shape(value, schema)
    for bad in [{}, {'i0000': decision}, {**value['input_dispositions'], 'foreign': decision}]:
        with pytest.raises(ValueError):
            evidence.validate_shape({**value, 'input_dispositions': bad}, schema)


@pytest.mark.parametrize('malformed', ['missing', 'foreign', 'invalid_decision', 'unresolved'])
def test_addressed_classification_keeps_valid_records_and_dispositions(manifest_job, malformed):
    store, job = manifest_job
    batch = accounting_batch(store, job, ['Релиз ALPHA.', 'Релиз BETA.'])
    value = envelope(batch)
    key = 'i0000'
    first = value['input_dispositions'][key]
    second = list(evidence.extraction_scope(batch['messages'])['model_input_ids'])[1]
    if malformed == 'foreign':
        value['input_dispositions']['foreign'] = copy.deepcopy(first)
    elif malformed == 'invalid_decision':
        value['input_dispositions'][second] = {'status': 'irrelevant'}
    elif malformed == 'unresolved':
        value['input_dispositions'][second] = dict(status='unresolved', records=[],
            retained_record_ids=[], reason='Остаток не обработан.')
    result = evidence.classify_extraction_document(store, job['job_id'], batch['batch_id'],
        generation=job['generation'], document=value, fragments=batch['messages'], selected=batch['messages'])
    assert len(result['records']) == 1
    if malformed == 'foreign':
        # A foreign owned slot invalidates accounting, without discarding a valid fact.
        assert not result['dispositions']
        assert result['unresolved_input_ids'] == [key, second]
    else:
        assert result['dispositions'][key]['status'] == 'represented'
        assert result['unresolved_input_ids'] == [second]
    assert not result['input_accounting_complete']
    assert bool(result['model_unresolved_input_ids']) == (malformed == 'unresolved')




def test_addressed_worker_repairs_only_residual_and_reaches_local_delivery(manifest_job, monkeypatch):
    import json
    from types import SimpleNamespace
    from telegram_connector import analysis_pipeline as pipeline, analysis_telemetry as telemetry
    from telegram_connector import telegram_analysis as analysis
    from telegram_connector.tests.test_analysis_manifest import seed, build
    from telegram_connector.tests.test_analysis_split_recovery import transport
    from telegram_connector.tests.test_analysis_evidence import model_response
    from telegram_connector.tests.test_analysis_transport import Response

    store, job = manifest_job
    job = store.get_job(job['job_id'])
    seed(store, 22, [1], text='Произошёл релиз ALPHA.')
    seed(store, 22, [2], text='Спасибо за обсуждение.')
    build(store, job)
    async def ready(*args, **kwargs):
        return None
    monkeypatch.setattr(analysis.history_client, 'ensure_analysis_history', ready)
    initial = next(pipeline.iter_analysis_batches(store, job['job_id'], generation=job['generation'],
        build_payload=lambda messages, context: pipeline.build_extraction_payload(job, messages, context),
        **transport(lambda *a, **k: Response({'input_tokens': 100}))))
    authored = envelope(initial)['input_dispositions']['i0000']['records'][0]
    scope = evidence.extraction_scope(initial['messages'])
    keys = scope['model_input_ids']
    value = {'saturated': False, 'input_dispositions': {
        keys[0]: dict(status='represented', records=[authored], retained_record_ids=[], reason=''),
        keys[1]: dict(status='unresolved', records=[], retained_record_ids=[], reason='Требуется остаток.')}}
    extraction_scopes, sent = [], []
    def provider(req, timeout):
        if req.full_url.endswith('input_tokens'):
            return Response({'input_tokens': 100})
        payload = json.loads(req.data)
        name = payload['text']['format']['name']
        if name == CURRENT_EVIDENCE_FORMAT:
            requested = list(payload['text']['format']['schema']['properties']['input_dispositions']['properties'])
            extraction_scopes.append(requested)
            response = value if len(extraction_scopes) == 1 else {'saturated': False,
                'input_dispositions': {keys[1]: dict(status='irrelevant', records=[],
                    retained_record_ids=[], reason='Благодарность без события.')}}
        else:
            assert name == CURRENT_SYNTHESIS_FORMAT
            records = expand_wire_quotes(prompt_json(payload['input'], 'evidence'))['records']
            response = {'summary': 'Релиз ALPHA.', 'items': [
                {'title': 'Релиз ALPHA', 'text': records[0]['payload']['claim'], 'contradictions': [], 'evidence_ids': [records[0]['record_id']]}],
                'limitations': [], 'insufficient_information': False, 'saturated': False}
        if name == CURRENT_EVIDENCE_FORMAT:
            evidence.validate_shape(response, payload['text']['format']['schema'])
        return Response(model_response(response))
    status = analysis.execute_analysis_job(store, store.get_job(job['job_id']), runtime=SimpleNamespace(),
        client=object(), token='fixture-token',
        access_provider=lambda: {'chats': frozenset({'100'}), 'users': frozenset({'7'}), 'usernames': frozenset()},
        send_func=lambda *a, **k: sent.append(a[2]) or {'message_id': len(sent), 'chat': {'id': 100}},
        **transport(provider))
    assert status == 'completed'
    assert extraction_scopes == [keys, [keys[1]]]
    assert 'Релиз ALPHA' in '\n'.join(sent)
    report = telemetry.summarize_job(store, job['job_id'])
    assert report['extraction']['input_accounting_complete']
    assert report['extraction']['accounted_messages'] == 2
    assert store.get_job(job['job_id'])['llm_calls'] == 3


def test_addressed_schema_requires_positive_proof_for_represented_input():
    from telegram_connector.tests.test_analysis_owned_records import record
    scope = {'count_events': False, 'model_input_ids': ['i0000']}
    schema = evidence.addressed_evidence_format(scope, settings.ANALYSIS_SCHEMA_VERSION)['schema']
    value = {'saturated': False, 'input_dispositions': {'i0000': {
        'status': 'represented', 'records': [], 'retained_record_ids': [], 'reason': ''}}}
    with pytest.raises(ValueError): evidence.validate_shape(value, schema)
    value['input_dispositions']['i0000']['records'] = [record('Выпущена ALPHA.')]
    evidence.validate_shape(value, schema)
    value['input_dispositions']['i0000'].update(records=[], retained_record_ids=['verified-root'])
    with pytest.raises(ValueError): evidence.validate_shape(value, schema)
    evidence.validate_shape(value, evidence.addressed_evidence_format({**scope, 'allow_retained': True}, settings.ANALYSIS_SCHEMA_VERSION)['schema'])
