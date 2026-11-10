"""Public resume scheduling and addressed empty scopes share their contract owners."""
import copy
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest

from telegram_connector import analysis_pipeline as pipeline
from telegram_connector import analysis_telemetry as telemetry
from telegram_connector import telegram_analysis as analysis
from telegram_connector.tests.test_analysis_lifecycle import (
    access, admit, config, control, FixtureProcess, store, supervisor, update,
)
from telegram_connector.tests.test_analysis_manifest import manifest_job, seed, build, change_setting
from telegram_connector.tests.test_analysis_split_recovery import transport
from telegram_connector.tests.test_analysis_transport import Response


def test_resumed_current_job_reaches_supervisor_launch(store, config, access, tmp_path):
    config['paths']['history_db'] = str(tmp_path / 'supervisor.sqlite3')
    now = datetime.now(timezone.utc)
    queued, _ = admit(store, config, access, update(), now=now)
    job = store.claim_job(queued['job_id'], max_running_jobs=1, now=now)
    store.finish_job(job['job_id'], generation=job['generation'], status='interrupted', now=now)
    frozen = copy.deepcopy(store.get_job(job['job_id'])['request'])
    assert control(store, config, access, 'resume', job, now=now)['status'] == 'queued'
    launches = []
    runtime = supervisor(config, access, launch=lambda claimed: launches.append(claimed) or FixtureProcess())
    runtime._tick(store)
    assert len(launches) == 1 and launches[0]['status'] == 'running'
    assert store.get_job(job['job_id'])['status'] == 'running'
    assert store.get_job(job['job_id'])['request'] == frozen
    runtime._tick(store)
    assert len(launches) == 1, 'An active worker cannot be launched twice'


@pytest.mark.parametrize('schema, algorithm', [(2, 10), (2, 11), (3, 12), (4, 13),
                                             (4, 14), (4, 15), (5, 16)])
def test_retired_resume_is_explicit_and_does_not_queue_or_spend(store, config, access, schema, algorithm):
    now = datetime.now(timezone.utc)
    queued, _ = admit(store, config, access, update(), now=now)
    job = store.claim_job(queued['job_id'], max_running_jobs=1, now=now)
    request = copy.deepcopy(job['request'])
    request['settings'].update(schema_version=schema, algorithm_version=algorithm)
    store.conn.execute('UPDATE analysis_jobs SET request_json=? WHERE job_id=?',
        (json.dumps(request), job['job_id']))
    store.finish_job(job['job_id'], generation=job['generation'], status='interrupted', now=now)
    before = store.get_job(job['job_id'])
    with pytest.raises(analysis.AnalysisRejection, match='policy_retired'):
        control(store, config, access, 'resume', before, now=now)
    assert store.get_job(job['job_id']) == before
    assert 'не поддерживается' in analysis.AnalysisRejection.MESSAGES['policy_retired']


def test_supervisor_still_rejects_unsupported_version(store, config, access, tmp_path):
    config['paths']['history_db'] = str(tmp_path / 'supervisor.sqlite3')
    queued, _ = admit(store, config, access, update(), now=datetime.now(timezone.utc))
    request = copy.deepcopy(queued['request'])
    request['settings']['algorithm_version'] = 99
    store.conn.execute('UPDATE analysis_jobs SET request_json=? WHERE job_id=?',
        (pipeline.canonical_json(request), queued['job_id']))
    runtime = supervisor(config, access, launch=lambda _: pytest.fail('Unsupported policy cannot launch'))
    runtime._tick(store)
    assert store.get_job(queued['job_id'])['status'] == 'failed'


@pytest.mark.parametrize('endpoint', ['input_tokens', 'responses'])
def test_retired_policy_cannot_dispatch_or_reserve_provider_attempt(manifest_job, endpoint):
    store, job = manifest_job
    batch = store.plan_batch(job['job_id'], generation=job['generation'],
        step_key='retired-provider', kind='extract', payload={})
    request = copy.deepcopy(job['request'])
    request['settings']['algorithm_version'] -= 1
    store.conn.execute('UPDATE analysis_jobs SET request_json=? WHERE job_id=?',
        (json.dumps(request), job['job_id']))
    before = store.get_job(job['job_id'])
    with pytest.raises(ValueError, match='analysis_policy_incompatible'):
        pipeline.request_analysis_json(store, job['job_id'], batch['batch_id'],
            generation=job['generation'], endpoint=endpoint, api_key='fixture-key',
            payload={'model': request['settings']['processing']['model'], 'input': 'Fixture', 'max_output_tokens': 1},
            counted_input_tokens=1, deadline=100, monotonic_func=lambda: 0,
            urlopen_func=lambda *a, **k: pytest.fail('Retired policy cannot contact a provider'))
    assert store.get_job(job['job_id']) == before
    assert not store.get_batch(batch['batch_id'])['attempts']


def planned(store, job, provider):
    return list(pipeline.iter_analysis_batches(store, job['job_id'], generation=job['generation'],
        build_payload=lambda messages, context: pipeline.build_extraction_payload(job, messages, context),
        **transport(provider)))


@pytest.mark.parametrize('texts', [[], [''], ['', ''], [' \n']])
def test_empty_corpus_finishes_locally_without_any_provider_attempt(manifest_job, texts):
    store, job = manifest_job
    for number, text in enumerate(texts, 1):
        seed(store, 22, [number], text=text)
    build(store, job)
    def offline(*args, **kwargs):
        pytest.fail('An empty addressed scope must work with no token-count or inference API')
    for batch in planned(store, job, offline):
        assert batch['local_only']
        progress = store.get_batch(batch['batch_id'])['payload']['extraction_progress']
        assert all(d['status'] == 'empty_text' for d in progress['dispositions'].values())
        assert not pipeline.extract_analysis_batch(store, job['job_id'], batch,
            generation=job['generation'], **transport(offline))['partial']
    assert not planned(store, job, offline), 'Completed empty inputs must not be replayed'
    report = telemetry.summarize_job(store, job['job_id'])
    assert report['usage']['provider_attempts'] == report['usage']['inference_attempts'] == 0
    if texts:
        assert report['extraction']['input_accounting_complete']
        assert report['extraction']['accounted_messages'] == len(texts)
    else:
        assert report['coverage']['selected'] == 0


def test_addressed_scope_is_saved_before_counting_nonempty_inputs(manifest_job):
    store, job = manifest_job
    seed(store, 22, [1], text='')
    seed(store, 22, [2], text='Подтверждённый факт.')
    build(store, job)
    requests = []
    def count(req, timeout):
        assert req.full_url.endswith('input_tokens')
        requests.append(req)
        saved = store.conn.execute("SELECT payload_json FROM analysis_batches WHERE state='running' AND kind='extract'").fetchone()
        progress = json.loads(saved[0])['extraction_progress']
        assert progress['dispositions']['i0000']['status'] == 'empty_text'
        assert progress['unresolved_input_ids'] == ['i0001']
        return Response({'input_tokens': 100})
    assert len(planned(store, job, count)) == 1
    assert len(requests) == 1, 'Only the actual nonempty scope needs API-count'


@pytest.mark.parametrize('has_empty_message', [False, True])
def test_empty_worker_finishes_with_model_api_offline(manifest_job, monkeypatch, access, has_empty_message):
    store, job = manifest_job
    change_setting(store, job, 'processing', 'ocr', False)
    if has_empty_message:
        seed(store, 22, [1], text='')
    async def history_ready(*args, **kwargs):
        return None
    monkeypatch.setattr(analysis.history_client, 'ensure_analysis_history', history_ready)
    def offline(*args, **kwargs):
        pytest.fail('The complete empty worker must not contact any model endpoint')
    sent = []
    status = analysis.execute_analysis_job(store, store.get_job(job['job_id']),
        runtime=SimpleNamespace(), client=object(), token='fixture-token',
        access_provider=lambda: access,
        send_func=lambda *a, **k: sent.append(a[2]) or {'message_id': len(sent), 'chat': {'id': 100}},
        **transport(offline))
    assert status == 'completed' and sent
    saved = store.get_job(job['job_id'])
    assert saved['provider_requests'] == saved['llm_calls'] == 0
    assert saved['delivery']['outcome'] == 'confirmed'
