"""A resumed worker preserves source context, roots, ranges and physical receipts."""
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest

from telegram_connector.tests.analysis_fixtures import prompt_json
from telegram_connector.tests.analysis_fixtures import CURRENT_EVIDENCE_FORMAT, CURRENT_SYNTHESIS_FORMAT, expand_wire_quotes
from telegram_connector import analysis_pipeline as pipeline, analysis_telemetry as telemetry
from telegram_connector import telegram_analysis as analysis
from telegram_connector.tests.test_analysis_manifest import manifest_job, seed, build, change_setting
from telegram_connector.tests.test_analysis_evidence import model_response
from telegram_connector.tests.test_analysis_split_recovery import transport, planned
from telegram_connector.tests.test_analysis_repair_context import command_job
from telegram_connector.tests.test_analysis_transport import Response


QUESTION = 'Когда начинается приём документов Uno и какой срок подачи?'
ANSWER = 'Uno: тридцать дней; приём начинается 4 July 2026.'


def record(fragment, *, kind='claim', claim=None):
    return dict(record_type=kind, claim=claim or fragment['message']['text'], details=[],
        entities=['Uno'], topic_key='срок' if kind == 'question' else 'fixture',
        assertion_kind='reported', support_status='reported', supporting_input_ids=[], related_input_ids=[])


class Provider:
    def __init__(self, store, job):
        self.store, self.job = store, job
        self.calls = []

    def __call__(self, req, timeout):
        payload = json.loads(req.data)
        if req.full_url.endswith('input_tokens'):
            return Response({'input_tokens': 100})
        kind = payload['text']['format']['name']
        if kind == CURRENT_EVIDENCE_FORMAT:
            output = self.extract(payload)
        elif kind == 'telegram_relations_v2':
            self.calls.append('link')
            data = prompt_json(payload['input'], 'records')
            by_id = {row['record_id']: row['payload'] for row in data['records']}
            output = {'decisions': [{'pair_id': pair['pair_id'], 'relation': 'answers' if {
                by_id[pair[key]]['record_type'] for key in ('left_id', 'right_id')} == {'question', 'answer'}
                and all(by_id[pair[key]]['topic_key'] == 'срок' for key in ('left_id', 'right_id')) else 'unrelated',
                'reason': 'Авторская fixture-разметка.'} for pair in data['pairs']], 'saturated': False}
        else:
            assert kind == CURRENT_SYNTHESIS_FORMAT, 'The exact-fit fixture must skip merge inference'
            output = self.synthesis(payload)
        response = model_response(output)
        response.update(model='fixture-model', service_tier='default')
        response['usage']['input_tokens_details'] = {'cached_tokens': 0, 'cache_write_tokens': 0}
        return Response(response)

    def extract(self, payload):
        if '<fixture:invalid_output>' in payload['input']:
            repair = prompt_json(payload, 'invalid_output')
            self.calls.append('child-repair')
            assert any(row['message']['text'] == QUESTION and row['input']['role'] == 'context'
                       for row in repair['context'])
            batch = next(self.store.get_batch(row[0]) for row in self.store.conn.execute(
                "SELECT batch_id FROM analysis_batches WHERE job_id=? AND kind='extract' AND state='running'", (self.job['job_id'],)))
            progress = batch['payload']['extraction_progress']
            identity = progress['dispositions']['i0000']['record_ids'][0]
            assert identity in {row['record_id'] for row in repair['read_only_records']}
            assert 'i0000' in repair['read_only_dispositions']
            return dict(saturated=False, input_dispositions={'i0001': dict(status='duplicate',
                records=[], retained_record_ids=[identity], reason='Дословный повтор Uno.')})
        data = prompt_json(payload['input'], 'messages')
        fragments = data['inputs']
        first = fragments[0]['message']['text']
        output = dict(saturated=first == 'Uno: событие ROOT.', input_dispositions={})
        if first.startswith('fixture-prefix-'):
            self.calls.append('prefix')
            for fragment in fragments:
                represented = fragment['message']['text'] == QUESTION
                output['input_dispositions'][fragment['input_id']] = dict(
                    status='represented' if represented else 'irrelevant',
                    records=[record(fragment, kind='question')] if represented else [],
                    retained_record_ids=[], reason='' if represented else 'Fixture background.')
            return output
        self.calls.append('parent' if output['saturated'] else 'child-initial' if first == ANSWER else 'sibling')
        chosen = fragments[:1] if output['saturated'] or first == ANSWER else fragments
        for fragment in chosen:
            value = record(fragment, kind='answer' if self.job['command'] == 'qa' else 'claim')
            if first == ANSWER:
                assert QUESTION in payload['input']
                value.update(claim='Приём документов Uno начинается 4 July 2026; срок подачи тридцать дней.',
                    supporting_input_ids=['c0000'], topic_key='срок')
            output['input_dispositions'][fragment['input_id']] = dict(status='represented',
                records=[value], retained_record_ids=[], reason='')
        return output

    def synthesis(self, payload):
        self.calls.append('synthesis')
        data = expand_wire_quotes(prompt_json(payload['input'], 'evidence'))
        assert len(data['records']) == 5
        value = next(row for row in data['records'] if '4 July 2026' in row['payload']['claim'])
        assert 'event_at' not in value['payload']
        assert '4 July 2026' in value['payload']['claim']
        assert QUESTION in [source['quote'] for source in value['payload']['source_refs']]
        return {'summary': 'Авторский локальный fixture.', 'items': [{'title': 'Срок подачи документов Uno',
            'text': value['payload']['claim'], 'contradictions': [],
            'evidence_ids': [value['record_id']]}], 'limitations': [], 'insufficient_information': False, 'saturated': False}




@pytest.mark.parametrize('command', ['topics', 'qa', 'search'])
def test_worker_preserves_context_roots_ranges_and_receipts_after_split_resume(manifest_job, monkeypatch, command):
    store, job = manifest_job
    job = command_job(store, job, command)
    change_setting(store, job, 'analysis.ai', 'messages_per_ai_pass', 6)  # Second batch: five selected + one context.
    change_setting(store, job, 'analysis', 'overlap_messages', 1)
    texts = [f'fixture-prefix-{index}' for index in range(5)] + [QUESTION, 'Uno: событие ROOT.', ANSWER, ANSWER, 'Uno: приём по записи.', 'Uno: нужен паспорт.']
    for index, text in enumerate(texts, 1):
        seed(store, 22, [index], text=text)
    build(store, job)
    provider = Provider(store, job)
    batches = planned(store, job, provider)
    assert len(batches) == 2 and batches[1]['context'][0]['message']['text'] == QUESTION
    pipeline.extract_analysis_batch(store, job['job_id'], batches[0], generation=job['generation'], **transport(provider))
    with monkeypatch.context() as patch:
        def stopped(*a, **k):
            raise InterruptedError('fixture split committed')
        patch.setattr(pipeline, '_continue_extraction_split', stopped)
        with pytest.raises(InterruptedError):
            pipeline.extract_analysis_batch(store, job['job_id'], batches[1], generation=job['generation'], **transport(provider))
    report = telemetry.summarize_job(store, job['job_id'])
    assert not report['extraction']['input_accounting_complete'] and report['extraction']['pending_batches'] == 2
    now = datetime.now(timezone.utc)
    store.finish_job(job['job_id'], generation=job['generation'], status='interrupted', now=now)
    store.resume_job(job['job_id'], generation=job['generation'], phase='extract', max_queued_jobs=8, now=now)
    resumed = store.claim_job(job['job_id'], max_running_jobs=1, now=now)
    async def ready(*a, **k):
        return None
    monkeypatch.setattr(analysis.history_client, 'ensure_analysis_history', ready)
    captured = []
    status = analysis.execute_analysis_job(store, resumed, runtime=SimpleNamespace(), client=object(), token='fixture-token',
        access_provider=lambda: {'chats': frozenset({'100'}), 'users': frozenset({'7'}), 'usernames': frozenset()},
        send_func=lambda *a, **k: (captured.append(a[2]) or {'message_id': len(captured), 'chat': {'id': 100}}), **transport(provider))
    assert status == 'completed'
    assert provider.calls == ['prefix', 'parent', 'child-initial', 'child-repair', 'sibling', 'link', 'synthesis']
    report = telemetry.summarize_job(store, job['job_id'])
    assert report['extraction']['input_accounting_complete'] and report['extraction']['accounted_messages'] == 11
    assert report['extraction']['duplicate_fragments'] == 1
    assert report['extraction']['local_date_normalizations'] == 0
    assert not report['extraction']['pending_batches'] and captured
