"""Current single-text contract; authored fixtures never measure model quality."""
import copy
import json

import pytest

from telegram_connector.tests.analysis_fixtures import prompt_json
from telegram_connector import analysis_evidence as evidence
from telegram_connector import analysis_pipeline as pipeline
from telegram_connector.tests.test_analysis_manifest import manifest_job
from telegram_connector.tests.test_analysis_extraction_accounting import accounting_batch


def owned_record(claim, details=()):
    return dict(record_type='event', claim=claim, details=list(details), entities=['ALPHA'],
                topic_key='ALPHA', assertion_kind='reported', support_status='reported',
                supporting_input_ids=[], related_input_ids=[])


def extract(store, job, text, details=(), *, entity='ALPHA'):
    batch = accounting_batch(store, job, [text])
    record = owned_record(text, details)
    record['entities'] = [entity]
    document = {'saturated': False, 'input_dispositions': {'i0000': dict(
        status='represented', records=[record], retained_record_ids=[], reason='')}}
    result = evidence.classify_extraction_document(store, job['job_id'], batch['batch_id'],
        generation=job['generation'], document=document, fragments=batch['messages'], selected=batch['messages'])
    assert result['input_accounting_complete']
    return result['records']


def test_current_owned_schema_rejects_removed_fields():
    scope = {'model_input_ids': ['i0000'], 'count_events': False}
    schema = evidence.addressed_evidence_format(scope, 5)['schema']
    value = {'saturated': False, 'input_dispositions': {'i0000': dict(status='represented',
        records=[owned_record('ALPHA требует 8 GB.')], retained_record_ids=[], reason='')}}
    evidence.validate_shape(value, schema)
    for field in ('conditions', 'exceptions'):
        broken = copy.deepcopy(value)
        broken['input_dispositions']['i0000']['records'][0][field] = []
        with pytest.raises(ValueError):
            evidence.validate_shape(broken, schema)


@pytest.mark.parametrize('command', ['topics', 'qa', 'search'])
def test_new_evidence_and_synthesis_have_one_text(manifest_job, command):
    store, job = manifest_job
    records = extract(store, job, 'ALPHA требует 8 GB. Замер выполнен на 16 GB.', ['Замер выполнен на 16 GB.'])
    assert records[0]['payload']['schema_version'] == 3
    assert not {'conditions', 'exceptions', 'event_at'} & records[0]['payload'].keys()
    job = {**job, 'command': command}
    job['request']['request']['query'] = 'ALPHA'
    view = dict(records=records, relations=[], partial=False, reasons=[], coverage={})
    payload = pipeline.build_synthesis_payload(job, view)
    assert payload['text']['format']['name'] == 'telegram_synthesis_v4'
    data = prompt_json(payload['input'], 'evidence')
    assert data['equivalent_detail_groups'] == []
    row = data['records'][0]
    assert row['details'] == ['Замер выполнен на 16 GB.']
    assert not {'conditions', 'exceptions', 'detail_groups'} & row.keys()
    document = dict(summary='Обновление ALPHA.', items=[dict(title='Выпущена ALPHA',
        text='Требуется 8 GB. Замер выполнен на 16 GB.', contradictions=[],
        evidence_ids=[row['record_id']])], limitations=[], insufficient_information=False, saturated=False)
    if command == 'qa':
        records[0]['payload']['record_type'] = 'answer'
    result = pipeline._synthesis_result(job, view, document)
    assert result['items'][0]['text'] == document['items'][0]['text']
    assert not {'conditions', 'exceptions', 'detail_groups', 'equivalent_detail_groups'} & result['items'][0].keys()


def test_detail_hint_keeps_records_and_rejects_unrelated():
    base = {**owned_record('Выпущена ALPHA.', ['Требуется 8 GB.']), 'schema_version': 3}
    known = {'r001': base, 'r002': copy.deepcopy(base)}
    before = copy.deepcopy(known)
    expected = [{'refs': [{'record_id': key, 'index': 0} for key in known]}]
    assert evidence.equivalent_detail_groups(known) == expected
    assert known == before
    assert evidence.equivalent_detail_groups(known, relations=[dict(left_id='r001', right_id='r002', relation='unrelated')]) == []
    for change in ({'entities': ['BETA']}, {'details': ['Не требуется 8 GB.']},
                   {'claim': 'Замер выполнен на ALPHA.'}):
        assert evidence.equivalent_detail_groups({'r001': base, 'r002': {**base, **change}}) == []


@pytest.mark.parametrize('details,allowed', [([], True), (['Требуется 8 GB.'], True),
    (['Подписка включает 100 кредитов.'], False)])
def test_mixed_source_requires_complete_standalone(manifest_job, details, allowed):
    store, job = manifest_job
    text = 'Выпущена ALPHA. Требуется 8 GB.'
    batch = accounting_batch(store, job, [text, 'Дайджест\n' + text + ' Подписка включает 100 кредитов.'])
    row = owned_record('Выпущена ALPHA.', details)
    row['supporting_input_ids'] = ['i0001']
    value = {'saturated': False, 'input_dispositions': {
        'i0000': dict(status='represented', records=[row], retained_record_ids=[], reason=''),
        'i0001': dict(status='represented', records=[owned_record('Подписка включает 100 кредитов.')],
                     retained_record_ids=[], reason='')}}
    result = evidence.classify_extraction_document(store, job['job_id'], batch['batch_id'],
        generation=job['generation'], document=value, fragments=batch['messages'], selected=batch['messages'])
    assert result['input_accounting_complete']
    assert evidence.record_source_eligible(store, store.get_job(job['job_id']), result['records'][0]['payload']) is allowed
    if allowed:
        wrong_subject = copy.deepcopy(result['records'][0]['payload'])
        wrong_subject['entities'] = ['BETA']
        assert not evidence.record_source_eligible(store, store.get_job(job['job_id']), wrong_subject)


@pytest.mark.parametrize('command', ['topics', 'qa', 'search'])
def test_worker_publishes_only_authored_text_and_public_report(manifest_job, command, monkeypatch, tmp_path):
    import hashlib
    import sqlite3
    import subprocess
    import sys
    from pathlib import Path
    from types import SimpleNamespace
    from telegram_connector import telegram_analysis as analysis
    from telegram_connector.tests.test_analysis_manifest import seed, change_setting
    from telegram_connector.tests.test_analysis_review_regressions import command_job
    from telegram_connector.tests.test_analysis_split_recovery import transport
    from telegram_connector.tests.test_analysis_evidence import model_response
    from telegram_connector.tests.test_analysis_transport import Response
    from telegram_connector.tests.analysis_fixtures import extraction_inputs

    store, job = command_job(manifest_job, command, sources=3)
    change_setting(store, job, 'processing', 'ocr', False)
    request = copy.deepcopy(store.get_job(job['job_id'])['request'])
    request['request']['query'] = 'ALPHA'
    store.conn.execute('UPDATE analysis_jobs SET request_json=? WHERE job_id=?',
                       (json.dumps(request), job['job_id']))
    seed(store, 22, [1], text='ALPHA требует 8 GB. Замер выполнен на 16 GB.')
    async def ready(*args, **kwargs):
        return None
    monkeypatch.setattr(analysis.history_client, 'ensure_analysis_history', ready)
    calls, sent = [], []
    def provider(req, timeout):
        payload = json.loads(req.data)
        if req.full_url.endswith('input_tokens'):
            return Response({'input_tokens': 100})
        name = payload['text']['format']['name']
        calls.append(name)
        if name == 'telegram_query_terms_v1':
            value = {'terms': ['ALPHA']}
        elif name == 'telegram_evidence_v5':
            assert 'conditions' not in json.dumps(payload['text']['format']['schema'])
            rows = {}
            for fragment in extraction_inputs(payload):
                row = owned_record('ALPHA требует 8 GB.', ['Замер выполнен на 16 GB.'])
                row['record_type'] = 'answer' if command == 'qa' else 'event'
                rows[fragment['input_id']] = dict(status='represented', records=[row], retained_record_ids=[], reason='')
            value = dict(saturated=False, input_dispositions=rows)
        elif name == 'telegram_relations_v2':
            data = prompt_json(payload['input'], 'records')
            value = dict(saturated=False, decisions=[dict(pair_id=p['pair_id'], relation='unrelated', reason='Разные записи.') for p in data['pairs']])
        else:
            assert name == 'telegram_synthesis_v4'
            data = prompt_json(payload['input'], 'evidence')
            assert data['records'][0]['details'] == ['Замер выполнен на 16 GB.']
            assert not {'conditions', 'exceptions', 'detail_groups'} & data['records'][0].keys()
            value = dict(summary='Требования ALPHA.', items=[dict(title='Требования ALPHA',
                text='Нужно 8 GB. Замер выполнен на 16 GB.', contradictions=[],
                evidence_ids=[data['records'][0]['record_id']])], limitations=[], insufficient_information=False, saturated=False)
        evidence.validate_shape(value, payload['text']['format']['schema'])
        return Response(model_response(value))
    status = analysis.execute_analysis_job(store, store.get_job(job['job_id']), runtime=SimpleNamespace(),
        client=object(), token='fixture-token', access_provider=lambda: {'chats': frozenset({'100'}),
        'users': frozenset({'7'}), 'usernames': frozenset()},
        send_func=lambda *args, **kw: (sent.append(args[2]) or {'message_id': len(sent), 'chat': {'id': 100}}),
        **transport(provider))
    assert status == 'completed', store.get_job(job['job_id']).get('error')
    assert 'telegram_evidence_v5' in calls and 'telegram_synthesis_v4' in calls
    result = store.get_job(job['job_id'])['result']
    assert result['items'][0]['text'] == 'Нужно 8 GB. Замер выполнен на 16 GB.'
    assert not {'conditions', 'exceptions', 'detail_groups'} & result['items'][0].keys()
    assert sent and all('Условия</b>' not in text and 'Исключения</b>' not in text for text in sent)
    path = tmp_path / 'history.sqlite3'
    with sqlite3.connect(path) as target:
        store.conn.backup(target)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    cli = Path(pipeline.__file__).parent / 'scripts/report_analysis.py'
    native = subprocess.run([sys.executable, str(cli), '--db', str(path), '--job-id', job['job_id']],
                            check=True, capture_output=True, text=True, timeout=30)
    report = json.loads(native.stdout)
    assert report['extraction']['input_accounting_complete']
    assert report['selection']['presented_evidence_records'] == 1
    assert report['synthesis']['detail_hints'][0]['sent_groups'] == 0
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_hint_is_omitted_before_facts_on_packing_boundary(manifest_job):
    from telegram_connector.tests.test_analysis_manifest import change_setting
    store, job = manifest_job
    records = extract(store, job, 'ALPHA требует 8 GB.', ['Требуется 8 GB.'])
    attempts_before = store.get_job(job['job_id'])['provider_requests']
    second = copy.deepcopy(records[0])
    second['record_id'] = 'second'
    records.append(second)
    view = dict(records=records, relations=[], partial=False, reasons=[], coverage={})
    before = copy.deepcopy(view)
    payload = pipeline.build_synthesis_payload(job, view)
    data = prompt_json(payload['input'], 'evidence')
    assert len(data['equivalent_detail_groups']) == 1
    change_setting(store, job, 'analysis.ai', 'message_block_max_chars', len(evidence.canonical_json(data)) - 1)
    current = store.get_job(job['job_id'])
    packed = pipeline.build_synthesis_payload(current, view)
    wire = prompt_json(packed['input'], 'evidence')
    assert wire['equivalent_detail_groups'] == []
    for key in ('records', 'quote_texts', 'source_table'):
        assert wire[key] == data[key]
    assert view == before
    current['request']['settings']['analysis_prompts']['templates']['synthesize'] = 'Edited heading\n' + current['request']['settings']['analysis_prompts']['templates']['synthesize']
    packed = pipeline.build_synthesis_payload(current, view)
    batch = pipeline._plan_synthesis_batch(store, current, view, packed, generation=current['generation'])
    assert batch['payload']['plan']['detail_hint'] == dict(candidate_groups=1, sent_groups=0, chars=2, omitted_reason='packing')
    assert store.get_job(job['job_id'])['provider_requests'] == attempts_before


@pytest.mark.parametrize('brand,details', [
    ('FreeVideo', ['Минимум: 8 GB VRAM и 16 GB RAM.', 'Замер 486 секунд: 16 GB VRAM и 32 GB RAM.']),
    ('AuK', ['Нужна отдельная модель Qwen2.5-Omni-3B.']),
    ('Haiku', ['До 100k токенов: $0.1/$0.5 за миллион входных/выходных токенов.',
               'Свыше 100k: $0.5/$2.5 за миллион входных/выходных токенов.']),
    ('HapticVLA', ['Тактильные датчики нужны при обучении.', 'При работе датчики не требуются.']),
])
def test_material_qualifiers_reach_current_synthesis_and_stay_in_text(manifest_job, brand, details):
    store, job = manifest_job
    source = f'{brand}: ' + ' '.join(details)
    rows = extract(store, job, source, details, entity=brand)
    view = dict(records=rows, relations=[], partial=False, reasons=[], coverage={})
    payload = pipeline.build_synthesis_payload(job, view)
    wire = prompt_json(payload['input'], 'evidence')
    assert wire['records'][0]['details'] == details
    text = ' '.join(details)
    document = dict(summary='', items=[dict(title=brand, text=text,
        evidence_ids=[wire['records'][0]['record_id']], contradictions=[])],
        limitations=[], insufficient_information=False, saturated=False)
    result = pipeline._synthesis_result(job, view, document)
    assert result['items'][0]['text'] == text
    assert all(result['items'][0]['text'].count(detail) == 1 for detail in details)
    assert not {'conditions', 'exceptions', 'detail_groups'} & result['items'][0].keys()
