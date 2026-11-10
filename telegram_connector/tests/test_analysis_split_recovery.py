from telegram_connector.tests.analysis_fixtures import CURRENT_EVIDENCE_FORMAT, CURRENT_SYNTHESIS_FORMAT
from telegram_connector.tests.analysis_fixtures import expand_wire_quotes
"""Public resume and coverage must account for every delegated source range."""
import json
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

from telegram_connector import analysis_pipeline as pipeline, analysis_telemetry as telemetry
from telegram_connector import telegram_analysis as analysis
from telegram_connector.tests.test_analysis_manifest import manifest_job, seed, build
from telegram_connector.tests.test_analysis_extraction_accounting import envelope
from telegram_connector.tests.test_analysis_evidence import model_response
from telegram_connector.tests.test_analysis_transport import Response


def transport(provider):
    return {'api_key': 'fixture-key', 'capabilities': {'model': 'fixture-model',
        'context_window': 128000, 'max_output_tokens': 128000, 'source': 'fixture'},
        'deadline': 100, 'monotonic_func': lambda: 0, 'urlopen_func': provider,
        'sleep_func': lambda _: None}


def planned(store, job, provider):
    return list(pipeline.iter_analysis_batches(store, job['job_id'], generation=job['generation'],
        build_payload=lambda selected, context: pipeline.build_extraction_payload(
            store.get_job(job['job_id']), selected, context), **transport(provider)))


def split_after_commit(store, job, monkeypatch):
    seed(store, 22, [1], text='Банк Uno: комиссия 10 EUR. Срок обработки тридцать дней.')
    build(store, job)
    batch = planned(store, job, lambda *a, **k: Response({'input_tokens': 100}))[0]
    initial = envelope(batch)
    initial['saturated'] = True
    def provider(req, timeout):
        return Response({'input_tokens': 100} if req.full_url.endswith('input_tokens') else
            model_response(initial))
    with monkeypatch.context() as patch:
        def stopped(*a, **k):
            raise InterruptedError('fixture stop after split commit')
        patch.setattr(pipeline, '_continue_extraction_split', stopped)
        with pytest.raises(InterruptedError, match='split commit'):
            pipeline.extract_analysis_batch(store, job['job_id'], batch, generation=job['generation'], **transport(provider))
    parent = store.get_batch(batch['batch_id'])
    assert parent['state'] == 'succeeded'
    assert len(parent['payload']['split_children']) == 2
    assert all(store.get_batch(key)['state'] == 'planned' for key in parent['payload']['split_children'])
    return batch, parent


def irrelevant_provider(req, timeout):
    if req.full_url.endswith('input_tokens'):
        return Response({'input_tokens': 100})
    payload = json.loads(req.data)
    data = json.loads(payload['input'].split('Сообщения: ', 1)[1])
    return Response(model_response({'saturated': False, 'input_dispositions': {
        value['input_id']: dict(status='irrelevant', records=[], retained_record_ids=[],
            reason='Локальная fixture-разметка.') for value in data['inputs']}}))


def test_public_iterator_resumes_children_after_succeeded_parent(manifest_job, monkeypatch):
    store, job = manifest_job
    original, parent = split_after_commit(store, job, monkeypatch)
    pending = planned(store, job, lambda *a, **k: Response({'input_tokens': 100}))
    assert [batch['batch_id'] for batch in pending] == [original['batch_id']]
    assert 'extraction_coverage_incomplete' in store.incomplete_evidence_reasons(job['job_id'])
    report = telemetry.summarize_job(store, job['job_id'])
    assert report['extraction']['input_accounting_complete'] is False


def test_first_child_cannot_close_the_other_half_of_a_message(manifest_job, monkeypatch):
    store, job = manifest_job
    _, parent = split_after_commit(store, job, monkeypatch)
    first, second = parent['payload']['split_children']
    child = pipeline._restore_extraction_batch(store, store.get_job(job['job_id']), first)
    pipeline.extract_analysis_batch(store, job['job_id'], child, generation=job['generation'], **transport(irrelevant_provider))
    changes = store.conn.total_changes
    report = telemetry.summarize_job(store, job['job_id'])
    assert report['extraction']['input_accounting_complete'] is False
    assert report['extraction']['accounted_messages'] == 0
    assert report['extraction']['unaccounted_fragments'] == 1
    assert store.processed_analysis_messages(job['job_id']) == 0
    assert store.conn.total_changes == changes
    pipeline.extract_analysis_batch(store, job['job_id'], pipeline._restore_extraction_batch(
        store, store.get_job(job['job_id']), second), generation=job['generation'], **transport(irrelevant_provider))
    report = telemetry.summarize_job(store, job['job_id'])
    assert report['extraction']['input_accounting_complete'] is True
    assert report['extraction']['accounted_messages'] == 1
    assert not store.incomplete_evidence_reasons(job['job_id'])


def test_worker_continues_saved_split_before_synthesis_and_local_delivery(manifest_job, monkeypatch):
    store, job = manifest_job
    _, parent = split_after_commit(store, job, monkeypatch)
    before = store.get_job(job['job_id'])['llm_calls']
    calls, captured = [], []
    async def ready(*args, **kwargs):
        return None
    monkeypatch.setattr(analysis.history_client, 'ensure_analysis_history', ready)
    def provider(req, timeout):
        payload = json.loads(req.data)
        if req.full_url.endswith('input_tokens'):
            return Response({'input_tokens': 100})
        kind = payload['text']['format']['name']
        calls.append(kind)
        if kind == CURRENT_EVIDENCE_FORMAT:
            return irrelevant_provider(req, timeout)
        assert kind == CURRENT_SYNTHESIS_FORMAT
        assert all(store.get_batch(key)['state'] == 'succeeded' for key in parent['payload']['split_children'])
        evidence = expand_wire_quotes(json.loads(payload['input'].split('Опорные записи: ', 1)[1]))
        record = evidence['records'][0]
        return Response(model_response({'summary': 'Сведения банка Uno.', 'items': [{'title': 'Комиссия и срок', 'text': record['payload']['claim'], 'contradictions': [], 'evidence_ids': [record['record_id']]}], 'limitations': [], 'insufficient_information': False, 'saturated': False}))
    result = analysis.execute_analysis_job(store, store.get_job(job['job_id']),
        runtime=SimpleNamespace(), client=object(), token='fixture-token',
        access_provider=lambda: {'chats': frozenset({'100'}), 'users': frozenset({'7'}), 'usernames': frozenset()},
        send_func=lambda *a, **k: (captured.append(a[2]) or {'message_id': len(captured), 'chat': {'id': 100}}),
        **transport(provider))
    assert result == 'completed'
    assert calls == [CURRENT_EVIDENCE_FORMAT, CURRENT_EVIDENCE_FORMAT, CURRENT_SYNTHESIS_FORMAT]
    assert store.get_job(job['job_id'])['llm_calls'] == before + 3
    assert 'комиссия' in '\n'.join(captured)
    assert telemetry.summarize_job(store, job['job_id'])['extraction']['input_accounting_complete']


@pytest.mark.parametrize('defect', ['bounds', 'digest', 'parent', 'invalidated'])
def test_invalid_child_lineage_never_closes_parent_coverage(manifest_job, monkeypatch, defect):
    store, job = manifest_job
    _, parent = split_after_commit(store, job, monkeypatch)
    child = store.get_batch(parent['payload']['split_children'][0])
    if defect == 'bounds':
        child['payload']['plan']['inputs'][0]['fragment_start'] = -1
    elif defect == 'digest':
        child['payload']['plan']['inputs'][0]['content_digest'] = 'f' * 64
    elif defect == 'parent':
        child['payload']['plan']['parent_batch_id'] = 'foreign-parent'
    else:
        child['payload']['invalidated'] = True
    store.conn.execute('UPDATE analysis_batches SET payload_json=? WHERE batch_id=?',
        (pipeline.canonical_json(child['payload']), child['batch_id']))
    report = telemetry.summarize_job(store, job['job_id'])
    assert report['extraction']['input_accounting_complete'] is False
    assert report['extraction']['accounted_messages'] == 0
    with pytest.raises(ValueError, match='child|scope'):
        store.extraction_children_pending(job['job_id'], parent['batch_id'])


def test_nested_split_resumes_only_pending_grandchildren_and_sibling(manifest_job, monkeypatch):
    store, job = manifest_job
    root, parent = split_after_commit(store, job, monkeypatch)
    first = pipeline._restore_extraction_batch(store, job, parent['payload']['split_children'][0])
    saturated = envelope(first)
    saturated['saturated'] = True
    def provider(req, timeout):
        return Response({'input_tokens': 100} if req.full_url.endswith('input_tokens') else
            model_response(saturated))
    with monkeypatch.context() as patch:
        def stopped(*a, **k):
            raise InterruptedError('grandchildren saved')
        patch.setattr(pipeline, '_continue_extraction_split', stopped)
        with pytest.raises(InterruptedError, match='grandchildren'):
            pipeline.extract_analysis_batch(store, job['job_id'], first, generation=job['generation'], **transport(provider))
    pending = planned(store, job, lambda *a, **k: Response({'input_tokens': 100}))
    assert [value['batch_id'] for value in pending] == [root['batch_id']]
    calls = []
    def count_calls(req, timeout):
        if req.full_url.endswith('responses'):
            calls.append(1)
        return irrelevant_provider(req, timeout)
    result = pipeline.extract_analysis_batch(store, job['job_id'], pending[0], generation=job['generation'], **transport(count_calls))
    assert not result['partial'] and len(calls) == 3
    assert not planned(store, job, lambda *a, **k: pytest.fail('Settled counts must be reused'))
    assert telemetry.summarize_job(store, job['job_id'])['extraction']['accounted_messages'] == 1


def test_closed_partial_child_is_not_replayed_but_retains_incomplete_reason(manifest_job, monkeypatch):
    store, job = manifest_job
    root, parent = split_after_commit(store, job, monkeypatch)
    def refused(req, timeout):
        return Response({'input_tokens': 100} if req.full_url.endswith('input_tokens') else
            {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'refusal', 'refusal': 'fixture'}]}],
             'usage': {'input_tokens': 100, 'output_tokens': 1}})
    first, second = parent['payload']['split_children']
    result = pipeline.extract_analysis_batch(store, job['job_id'], pipeline._restore_extraction_batch(store, job, first),
        generation=job['generation'], **transport(refused))
    assert result['partial']
    pipeline.extract_analysis_batch(store, job['job_id'], pipeline._restore_extraction_batch(store, job, second),
        generation=job['generation'], **transport(irrelevant_provider))
    assert not planned(store, job, lambda *a, **k: pytest.fail('Terminal partial is not fresh work'))
    report = telemetry.summarize_job(store, job['job_id'])
    assert report['extraction']['pending_batches'] == 0
    assert report['extraction']['input_accounting_complete'] is False
    assert 'extraction_coverage_incomplete' in store.incomplete_evidence_reasons(job['job_id'])


def test_new_generation_resumes_split_without_recharging_parent_or_first_child(manifest_job, monkeypatch):
    store, job = manifest_job
    _, parent = split_after_commit(store, job, monkeypatch)
    first = pipeline._restore_extraction_batch(store, job, parent['payload']['split_children'][0])
    pipeline.extract_analysis_batch(store, job['job_id'], first, generation=job['generation'], **transport(irrelevant_provider))
    receipts = {key: store.get_batch(key)['attempts'] for key in [parent['batch_id'], first['batch_id']]}
    now = datetime.now(timezone.utc)
    store.finish_job(job['job_id'], generation=job['generation'], status='interrupted', now=now)
    store.resume_job(job['job_id'], generation=job['generation'], phase='extract', max_queued_jobs=8, now=now)
    resumed = store.claim_job(job['job_id'], max_running_jobs=1, now=now)
    assert resumed['generation'] > job['generation']
    calls = []
    def provider(req, timeout):
        calls.append(req.full_url)
        return irrelevant_provider(req, timeout)
    pending = planned(store, resumed, provider)
    assert len(pending) == 1
    result = pipeline.extract_analysis_batch(store, resumed['job_id'], pending[0], generation=resumed['generation'], **transport(provider))
    assert not result['partial']
    assert len(calls) == 2  # Count + inference for only the unprocessed second child.
    for key, attempts in receipts.items():
        assert store.get_batch(key)['attempts'] == attempts
    assert store.extraction_work_state(job['job_id'])['input_accounting_complete']


def test_public_report_ranges_match_independent_sql_without_writes(manifest_job, monkeypatch, tmp_path):
    store, job = manifest_job
    _, parent = split_after_commit(store, job, monkeypatch)
    child = pipeline._restore_extraction_batch(store, job, parent['payload']['split_children'][0])
    pipeline.extract_analysis_batch(store, job['job_id'], child, generation=job['generation'], **transport(irrelevant_provider))
    folder = tmp_path / 'report-ranges' / job['job_id']
    folder.mkdir(parents=True, exist_ok=False)
    path = folder / 'history.sqlite3'
    conn = sqlite3.connect(path)
    store.conn.backup(conn)
    conn.close()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    script = Path(analysis.__file__).resolve().parent / 'scripts/report_analysis.py'
    output = subprocess.run([sys.executable, str(script), '--db', str(path), '--job-id', job['job_id']],
        capture_output=True, text=True, check=True, timeout=45).stdout
    report = json.loads(output)
    conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    rows = conn.execute('SELECT state,payload_json FROM analysis_batches WHERE batch_id IN (?,?)',
                        tuple(parent['payload']['split_children'])).fetchall()
    refs = [json.loads(raw)['plan']['inputs'][0] for _, raw in rows]
    assert sum(state == 'planned' for state, _ in rows) == 1
    ranges = sorted((ref['fragment_start'], ref['fragment_end']) for ref in refs)
    # This fixture has plain text and no media: SQL LENGTH is an independent
    # whole-message bound, unlike legacy manifest rows whose end is a placeholder.
    frozen = conn.execute('SELECT 0,LENGTH(text) FROM messages WHERE channel_id=22 AND message_id=1').fetchone()
    assert ranges[0][0] == frozen[0] and ranges[0][1] == ranges[1][0] and ranges[1][1] == frozen[1]
    assert report['extraction']['pending_batches'] == 1
    assert report['extraction']['accounted_messages'] == 0
    assert not report['extraction']['input_accounting_complete']
    assert report['extraction']['selected_fragments'] == 2
    counters = conn.execute('SELECT COUNT(*),SUM(input_tokens),SUM(output_tokens) FROM ai_usage_log WHERE job_id=?',
                            (job['job_id'],)).fetchone()
    assert counters[0] == report['usage']['provider_attempts']
    assert counters[1:3] == (report['usage']['input_tokens'], report['usage']['output_tokens'])
    conn.close()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    (folder / 'report.json').write_text(output)


def test_overlapping_children_cannot_hide_a_hole_in_the_delegated_source(manifest_job, monkeypatch):
    store, job = manifest_job
    _, parent = split_after_commit(store, job, monkeypatch)
    first, second = [store.get_batch(key) for key in parent['payload']['split_children']]
    second['payload']['plan']['inputs'] = json.loads(pipeline.canonical_json(first['payload']['plan']['inputs']))
    store.conn.execute('UPDATE analysis_batches SET payload_json=? WHERE batch_id=?',
        (pipeline.canonical_json(second['payload']), second['batch_id']))
    for child in (first, second):
        pipeline.extract_analysis_batch(store, job['job_id'], pipeline._restore_extraction_batch(store, job, child['batch_id']),
            generation=job['generation'], **transport(irrelevant_provider))
    report = telemetry.summarize_job(store, job['job_id'])
    assert report['extraction']['input_accounting_complete'] is False
    assert report['extraction']['accounted_messages'] == 0
    assert report['extraction']['scope_errors'] > 0


@pytest.mark.parametrize('ocr', [False, True])
def test_unscheduled_tail_of_frozen_text_or_ocr_cannot_be_accounted(manifest_job, ocr):
    store, job = manifest_job
    seed(store, 22, [1], text='Первый факт. Необработанный конец сообщения.')
    if ocr:
        store.conn.execute('''INSERT INTO media_assets(channel_id,message_id,ordinal,media_kind,ocr_text,ocr_status,created_at)
            VALUES (22,1,0,'photo','Ещё один факт из OCR.','done','now')''')
    build(store, job)
    source = next(pipeline.iter_analysis_manifest_messages(store, job['job_id'], generation=job['generation']))
    ref = {**source['input'], 'fragment_start': 0, 'fragment_end': 12}
    fragment = {'input': ref, 'message': {**source['message'], 'text': source['message']['text'][:12]}}
    batch = store.plan_batch(job['job_id'], generation=job['generation'], step_key='extract/only-first-half', kind='extract',
        payload={'inputs': [ref], 'context': []}, input_digest='left-only')
    case = {'batch_id': batch['batch_id'], 'messages': [fragment], 'context': []}
    def provider(req, timeout):
        return Response({'input_tokens': 100} if req.full_url.endswith('input_tokens') else
            model_response(envelope(case)))
    pipeline.extract_analysis_batch(store, job['job_id'], case, generation=job['generation'], **transport(provider))
    report = telemetry.summarize_job(store, job['job_id'])
    assert not report['extraction']['input_accounting_complete']
    assert report['extraction']['accounted_messages'] == 0
    assert report['extraction']['unaccounted_fragments'] == 1
    assert not report['extraction']['unknown_range_messages']
    assert store.processed_analysis_messages(job['job_id']) == 0


def test_synthesis_rechecks_range_completeness_in_an_existing_ready_checkpoint(manifest_job, monkeypatch):
    from telegram_connector.tests.test_analysis_merge import merge
    store, job = manifest_job
    _, parent = split_after_commit(store, job, monkeypatch)
    merge(store, job, lambda *a, **k: Response({'input_tokens': 100}))
    current = store.get_job(job['job_id'])
    current['checkpoint']['merge'].update(partial=False, reasons=[])
    store.save_checkpoint(job['job_id'], current['checkpoint'], generation=job['generation'], max_bytes=67108864)
    evidence = pipeline._synthesis_evidence(store, store.get_job(job['job_id']))
    assert evidence['partial']
    assert 'extraction_coverage_incomplete' in evidence['reasons']
