from telegram_connector.tests.analysis_fixtures import CURRENT_EVIDENCE_FORMAT, CURRENT_SYNTHESIS_FORMAT
from telegram_connector.tests.analysis_fixtures import expand_wire_quotes
"""Per-fact source support; providers and delivery are local authored fixtures."""
import json
import hashlib
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

from telegram_connector.tests.analysis_fixtures import prompt_json
from telegram_connector import analysis_evidence as evidence
from telegram_connector import analysis_pipeline as pipeline
from telegram_connector import telegram_analysis as analysis
from telegram_connector.tests.analysis_fixtures import owned_response_from_records, extraction_inputs
from telegram_connector.tests.test_analysis_manifest import manifest_job, change_setting, seed
from telegram_connector.tests.test_analysis_relations import records_fixture, link_sender
from telegram_connector.tests.test_analysis_review_regressions import answer_result, command_job
from telegram_connector.tests.test_analysis_merge import merge, merge_sender
from telegram_connector.tests.test_analysis_evidence import document, model_response
from telegram_connector.tests.test_analysis_split_recovery import transport
from telegram_connector.tests.test_analysis_transport import Response


FACTS = [
    {'claim': 'OpenAI выпустила Terra.', 'entities': ['OpenAI', 'Terra']},
    {'claim': 'OpenAI удвоила лимиты Pro.', 'entities': ['OpenAI', 'Pro'],
     'text': 'Дайджест\nOpenAI удвоила лимиты Pro.'},
]


def test_direct_input_excludes_digest_only_fact_before_model(manifest_job):
    store, job = manifest_job
    records = records_fixture(store, job, FACTS)
    seen = []
    def count(req, timeout):
        value = expand_wire_quotes(prompt_json(json.loads(req.data)['input'], 'evidence'))
        seen.extend(value['records'])
        return Response({'input_tokens': 100})
    merge(store, job, count)
    # A singleton eligible frontier does not need direct preflight.
    view = pipeline._synthesis_evidence(store, store.get_job(job['job_id']))
    assert {r['record_id'] for r in view['records']} == {records[0]['record_id']}
    assert all(r['record_id'] != records[1]['record_id'] for r in seen)


def test_mixed_digest_fact_cannot_borrow_other_records_link(manifest_job):
    store, job = manifest_job
    records = records_fixture(store, job, FACTS)
    result = answer_result(records)
    result['items'][0]['text'] = 'OpenAI выпустила Terra и удвоила лимиты Pro.'
    result['summary'] = result['items'][0]['text']
    checked = evidence.verify_analysis_sources(store, job['job_id'], result, generation=job['generation'])
    assert not checked['items'] and not checked['summary']
    assert checked['excluded_unsupported_items'] == 1


@pytest.mark.parametrize('command', ['qa', 'search'])
def test_unique_digest_fact_remains_available_for_other_commands(manifest_job, command):
    store, job = command_job(manifest_job, command, sources=2)
    records = records_fixture(store, job, [{**value, 'record_type': 'answer'} for value in FACTS])
    result = answer_result(records)
    checked = evidence.verify_analysis_sources(store, job['job_id'], result, generation=job['generation'])
    assert checked['items']
    assert {s['message_id'] for s in checked['items'][0]['sources']} == {1, 2}


def test_worker_filters_mixed_fact_through_delivery_and_report(manifest_job, monkeypatch, tmp_path):
    store, job = manifest_job
    change_setting(store, job, 'processing', 'ocr', False)
    for number, fact in enumerate(FACTS, 1):
        seed(store, 22, [number], text=fact.get('text', fact['claim']))
    async def ready(*args, **kwargs):
        return None
    monkeypatch.setattr(analysis.history_client, 'ensure_analysis_history', ready)
    sent, synthesis = [], []
    def provider(req, timeout):
        payload = json.loads(req.data)
        if req.full_url.endswith('input_tokens'):
            return Response({'input_tokens': 100})
        name = payload['text']['format']['name']
        if name in {'telegram_evidence_v2', 'telegram_evidence_v3', CURRENT_EVIDENCE_FORMAT}:
            values = []
            for fragment, fact in zip(extraction_inputs(payload), FACTS):
                row = document({'messages': [fragment]})['records'][0]
                row.update(claim=fact['claim'], entities=fact['entities'], details=[])
                values.append(row)
            return Response(model_response(owned_response_from_records(payload, {'records': values, 'saturated': False})))
        if name.startswith('telegram_relations_'):
            return link_sender('unrelated')(req, timeout)
        if name.startswith('telegram_merge_'):
            return merge_sender()(req, timeout)
        value = expand_wire_quotes(prompt_json(payload['input'], 'evidence'))
        synthesis.append(value)
        assert len(value['records']) == 1
        record = value['records'][0]
        return Response(model_response({'summary': record['payload']['claim'], 'items': [{
            'title': 'Terra', 'text': record['payload']['claim'], 'evidence_ids': [record['record_id']],
            'contradictions': []}],
            'limitations': [], 'insufficient_information': False, 'saturated': False}))
    current = store.get_job(job['job_id'])
    status = analysis.execute_analysis_job(store, current, runtime=SimpleNamespace(), client=object(),
        token='fixture-token', access_provider=lambda: {'chats': frozenset({'100'}),
            'users': frozenset({'7'}), 'usernames': frozenset()},
        send_func=lambda *a, **k: (sent.append(a[2]) or {'message_id': len(sent), 'chat': {'id': 100}}),
        **transport(provider))
    from telegram_connector.analysis_telemetry import summarize_job
    report = summarize_job(store, job['job_id'])
    assert status == 'completed' and synthesis and sent
    assert 'Pro' not in '\n'.join(sent)
    assert FACTS[0]['claim'] in '\n'.join(sent)
    assert report['coverage']['eligible_evidence_records'] == 1
    assert report['coverage']['source_excluded_evidence_records'] == 1
    assert report['delivery'] == 'confirmed'
    path = tmp_path / 'public-report.sqlite3'
    with sqlite3.connect(path) as target:
        store.conn.backup(target)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    cli = Path(pipeline.__file__).parent / 'scripts/report_analysis.py'
    response = subprocess.run([sys.executable, str(cli), '--db', str(path), '--job-id', job['job_id']],
        capture_output=True, text=True, check=True, timeout=30)
    public = json.loads(response.stdout)
    assert public['selection']['presented_evidence_records'] == 1
    assert public['coverage']['source_excluded_evidence_records'] == 1
    assert public['extraction']['input_accounting_complete']
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
