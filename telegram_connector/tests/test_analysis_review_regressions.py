"""Review boundary regressions over isolated stores and mocked transports."""

import copy
import json
from datetime import timedelta

import pytest

from telegram_connector.tests.analysis_fixtures import prompt_json
from telegram_connector import telegram_analysis as analysis
from telegram_connector import analysis_pipeline, analysis_telemetry
from telegram_connector.analysis_store import canonical_json
from telegram_connector.tests.test_analysis_lifecycle import (
    NOW, access, admit, config, store, supervisor, update, FixtureProcess,
)
from telegram_connector.tests.test_analysis_manifest import manifest_job
from telegram_connector.tests.test_analysis_relation_progress import fixture_pairs, request_pairs, decision
from telegram_connector.tests.test_analysis_relations import link, link_sender, records_fixture
from telegram_connector.tests.test_analysis_evidence import model_response
from telegram_connector.tests.test_analysis_transport import Response
from telegram_connector.tests.test_analysis_merge import merge, merge_sender
from telegram_connector.tests.test_analysis_synthesis import synthesize


def set_cap(store, job, maximum):
    payload = copy.deepcopy(store.get_job(job['job_id'])['request'])
    payload['settings']['analysis']['max_intermediate_bytes'] = maximum
    store.conn.execute('UPDATE analysis_jobs SET request_json=? WHERE job_id=?',
                       (canonical_json(payload), job['job_id']))


def test_admission_reserves_telemetry_before_accepting_near_full_job(store, config, access):
    event = update(text='/topics @fixture since=today until=today')
    probe, _ = admit(store, config, access, event)
    size = store.storage_usage(probe['job_id'])
    assert size['telemetry_reserve'] > 0
    # Fit the actual frozen request and closure, but not all telemetry.
    # Prompt edits may change request size without changing this boundary.
    cap = size['allocated_bytes'] - size['telemetry_reserve'] // 2
    assert size['allocated_bytes'] - size['telemetry_reserve'] < cap
    store.conn.execute('DELETE FROM analysis_jobs WHERE job_id=?', (probe['job_id'],))
    config['analysis']['max_intermediate_bytes'] = cap
    with pytest.raises(analysis.AnalysisRejection, match='invalid_request'):
        admit(store, config, access, event)
    assert list(store.iter_jobs('queued')) == []
    assert store.conn.execute('SELECT COUNT(*) FROM analysis_phase_runs').fetchone()[0] == 0


@pytest.mark.parametrize('command', ['topics', 'qa', 'search'])
def test_claim_budget_failure_is_terminal_and_next_job_launches(store, config, access, command, tmp_path):
    config['analysis']['max_running_jobs'] = 2
    bad, _ = admit(store, config, access, update(text=f'/{command} @fixture "Сведения"' if command == 'search'
                                               else f'/{command} @fixture'))
    good, _ = admit(store, config, access, update(43), now=NOW + timedelta(seconds=1))
    # Emulate an old queued snapshot without telemetry headroom, preserving its
    # existing finalization allowance. No current admission bypass is introduced.
    used = store.storage_usage(bad['job_id'])
    set_cap(store, bad, used['allocated_bytes'] - used.get('telemetry_reserve', 0) + 10)
    launched = []
    service = supervisor(config, access, launch=lambda job: launched.append(job['job_id']) or FixtureProcess())
    service.db_path = tmp_path / 'locks.sqlite3'
    service._tick(store)
    failed = store.get_job(bad['job_id'])
    assert failed['status'] == 'failed'
    assert failed['checkpoint']['last_stop']['reason'] == 'max_intermediate_bytes'
    assert failed['generation'] == bad['generation']
    assert store.phase_runs(bad['job_id']) == []
    assert failed['provider_requests'] == failed['llm_calls'] == 0
    assert launched == [good['job_id']]
    service._tick(store)
    assert launched == [good['job_id']] and not service.error


def test_resume_checks_next_generation_telemetry_atomically(store, config, access):
    queued, _ = admit(store, config, access, update())
    job = store.claim_job(queued['job_id'], max_running_jobs=1, now=NOW)
    store.finish_job(job['job_id'], generation=job['generation'], status='interrupted', now=NOW)
    terminal = store.get_job(job['job_id'])
    set_cap(store, job, store.storage_usage(job['job_id'])['allocated_bytes'] + 10)
    with pytest.raises(analysis.BudgetExceededError, match='max_intermediate_bytes'):
        store.resume_job(job['job_id'], generation=terminal['generation'], phase='sync',
                         max_queued_jobs=10, now=NOW + timedelta(seconds=1))
    assert store.get_job(job['job_id'])['status'] == 'interrupted'
    assert store.get_job(job['job_id'])['generation'] == terminal['generation']


def test_reserved_lifecycle_fits_without_extra_headroom(store, config, access):
    queued, _ = admit(store, config, access, update())
    maximum = store.storage_usage(queued['job_id'])['allocated_bytes']
    set_cap(store, queued, maximum)
    job = store.claim_job(queued['job_id'], max_running_jobs=1, now=NOW)
    for phase in ('startup', 'sync', 'select', 'extract', 'link', 'merge', 'synthesize', 'verify', 'deliver'):
        seq = store.begin_phase(job['job_id'], generation=job['generation'], phase=phase,
                                process_run_id='123:fixture', implementation_sha256='a' * 64, now=NOW)
        store.end_phase(job['job_id'], generation=job['generation'], sequence=seq,
                        now=NOW, elapsed_ms=0, outcome='succeeded')
        assert store.storage_usage(job['job_id'])['allocated_bytes'] <= maximum
    store.finish_job(job['job_id'], generation=job['generation'], status='completed', now=NOW)
    assert store.get_job(job['job_id'])['status'] == 'completed'
    assert all(row['finished_at'] is not None for row in store.phase_runs(job['job_id']))


@pytest.mark.parametrize('command', ['topics', 'qa', 'search'])
def test_invalidated_split_parent_never_returns_old_edge_or_active_counts(manifest_job, command):
    store, job = fixture_pairs(command_job(manifest_job, command))
    calls = []
    def send(req, timeout):
        _, pairs = request_pairs(req)
        if req.full_url.endswith('input_tokens'):
            return Response({'input_tokens': 100})
        calls.append(pairs)
        return Response(model_response({'decisions': [decision(pairs[0]['pair_id'], 'complements')],
                                        'saturated': len(pairs) > 1}))
    assert not link(store, job, send)['partial']
    parent = next(store.get_batch(row[0]) for row in store.conn.execute(
        "SELECT batch_id FROM analysis_batches WHERE kind='link' AND state='skipped'"))
    kept = next(iter(parent['payload']['link_progress']['accepted_decisions'].values()))
    removed = next(key for key in parent['payload']['plan']['record_digests'] if key not in (kept['left_id'], kept['right_id']))
    extraction = store.leaf_evidence(job['job_id'], removed)['batch_id']
    before = store.get_job(job['job_id'])
    store.invalidate_batch_dependencies(job['job_id'], extraction, generation=job['generation'])
    assert not list(store.iter_link_decisions(job['job_id']))
    assert not link(store, job, link_sender('unrelated'))['partial']
    active = list(store.iter_link_decisions(job['job_id']))
    assert len(active) == 1 and active[0]['relation'] == 'unrelated'
    merge(store, job, merge_sender(no_progress=True))
    evidence = analysis_pipeline._synthesis_evidence(store, store.get_job(job['job_id']))
    assert evidence['relations'] == active
    report = analysis_telemetry.summarize_job(store, job['job_id'])
    assert report['relations']['planned_pairs'] == report['relations']['accepted_pairs'] == 1
    assert report['relations']['unresolved_pairs'] == 0
    assert any(batch['invalidated'] for batch in report['relations']['batches'])
    assert store.get_batch(parent['batch_id'])['payload']['link_split']
    assert store.get_job(job['job_id'])['used_tokens'] > before['used_tokens']


@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('state', ['succeeded', 'failed', 'skipped'])
def test_invalidated_flag_wins_over_split_and_progress(manifest_job, legacy, state):
    store, job = manifest_job
    batch = store.plan_batch(job['job_id'], generation=job['generation'], kind='link',
                             step_key='fixture/old', input_digest='a' * 64, payload={})
    edge = {'left_id': 'a', 'right_id': 'b', 'relation': 'answers', 'reason': 'old'}
    payload = {'invalidated': True, 'link_split': True}
    if legacy:
        payload['output'] = {'decisions': [edge]}
    else:
        payload['link_progress'] = {'accepted_decisions': {'p001': edge}}
    store.conn.execute('UPDATE analysis_batches SET state=?,payload_json=? WHERE batch_id=?',
                       (state, canonical_json(payload), batch['batch_id']))
    assert list(store.iter_link_decisions(job['job_id'])) == []


def command_job(fixture, command, sources=1):
    store, job = fixture
    payload = copy.deepcopy(job['request'])
    payload['request'].update(command=command, sources=sources, query='Документы' if command == 'search' else None)
    store.conn.execute('UPDATE analysis_jobs SET command=?,request_json=? WHERE job_id=?',
                       (command, canonical_json(payload), job['job_id']))
    return store, store.get_job(job['job_id'])


def answer_result(records):
    return {'summary': 'Нужен паспорт.', 'items': [{'title': 'Документы', 'text': 'Нужен паспорт.', 'contradictions': [], 'evidence_ids': [r['record_id'] for r in records]}], 'limitations': [],
        'partial': False, 'reasons': [], 'coverage': {'selected_messages': 2}}


@pytest.mark.parametrize('command', ['qa', 'search'])
def test_unique_digest_answer_has_answer_link_even_with_one_source(manifest_job, command):
    store, job = command_job(manifest_job, command)
    records = records_fixture(store, job, [
        {'record_type': 'question', 'claim': 'Какие документы нужны?'},
        {'record_type': 'answer', 'claim': 'Нужен паспорт.', 'text': 'Дайджест\nНужен паспорт.\n#дайджест'}])
    link(store, job, link_sender('answers'))
    original = answer_result(records)
    view = analysis.prepare_analysis_presentation(store, job['job_id'], original, generation=job['generation'])
    item = view['result']['items'][0]
    assert [s['message_id'] for s in item['sources']] == [2]
    assert item['sources'][0]['is_digest'] is True
    assert 'дайджест' in '\n'.join(view['chunks']).lower()
    assert 'https://t.me/peer22/2' in '\n'.join(view['chunks'])
    assert original == answer_result(records)
    again = analysis.verify_analysis_sources(store, job['job_id'], view['result'], generation=job['generation'])
    assert again == view['result']


@pytest.mark.parametrize('command', ['qa', 'search'])
def test_equivalent_standalone_answer_outside_item_replaces_digest(manifest_job, command):
    store, job = command_job(manifest_job, command)
    records = records_fixture(store, job, [
        {'record_type': 'question', 'claim': 'Какие документы нужны?'},
        {'record_type': 'answer', 'claim': 'Нужен паспорт.', 'text': 'Дайджест\nНужен паспорт.\n#дайджест'},
        {'record_type': 'answer', 'claim': 'Нужен паспорт.'}])
    types = {record['record_id']: record['record_type'] for record in records}
    def provider(req, timeout):
        if req.full_url.endswith('input_tokens'):
            return Response({'input_tokens': 100})
        wire = prompt_json(json.loads(req.data)['input'], 'records')
        pairs = wire['pairs']
        types = {row['record_id']: row['payload']['record_type'] for row in wire['records']}
        return Response(model_response({'decisions': [decision(pair['pair_id'],
            'answers' if 'question' in {types[pair['left_id']], types[pair['right_id']]} else 'duplicate')
            for pair in pairs], 'saturated': False}))
    link(store, job, provider)
    result = analysis.verify_analysis_sources(store, job['job_id'], answer_result(records[:2]), generation=job['generation'])
    assert [s['message_id'] for s in result['items'][0]['sources']] == [3]


def test_qa_answer_without_admissible_source_is_excluded_and_summary_cleared(manifest_job):
    store, job = command_job(manifest_job, 'qa')
    records = records_fixture(store, job, [
        {'record_type': 'question', 'claim': 'Какие документы нужны?'},
        {'record_type': 'answer', 'claim': 'Нужен паспорт.', 'text': 'Дайджест\nНужен паспорт.'}])
    link(store, job, link_sender('answers'))
    current = store.get_job(job['job_id'])
    checkpoint = copy.deepcopy(current['checkpoint'])
    checkpoint['relations']['partial'] = True
    store.save_checkpoint(job['job_id'], checkpoint, generation=job['generation'], max_bytes=1000000)
    result = analysis.verify_analysis_sources(store, job['job_id'], answer_result(records), generation=job['generation'])
    assert result['items'] == [] and result['summary'] == ''
    assert result['insufficient_information']
    assert result['partial'] and 'source_selection_incomplete' in result['reasons']


def test_composite_answer_does_not_publish_question_link_when_answer_sources_do_not_fit(manifest_job):
    store, job = command_job(manifest_job, 'qa')
    records = records_fixture(store, job, [
        {'record_type': 'question', 'claim': 'Какие документы нужны?'},
        {'record_type': 'answer', 'claim': 'Нужен паспорт.'},
        {'record_type': 'answer', 'claim': 'Нужен NIE.'}])
    link(store, job, link_sender('answers'))
    result = analysis.verify_analysis_sources(store, job['job_id'], answer_result(records), generation=job['generation'])
    assert not result['items'] and result['summary'] == ''
    assert result['excluded_unsupported_items'] == 1


@pytest.mark.parametrize('command', ['qa', 'search'])
@pytest.mark.parametrize('detail', ['Подача только по предварительной записи.',
    'Для студентов справка не требуется.', 'Срок подачи — тридцать дней.'])
def test_unique_digest_detail_is_not_replaced_by_incomplete_standalone(manifest_job, command, detail):
    store, job = command_job(manifest_job, command)
    records = records_fixture(store, job, [
        {'record_type': 'answer', 'claim': 'Нужен паспорт.', 'text': 'Дайджест\nНужен паспорт. ' + detail,
         'details': [detail]},
        {'record_type': 'answer', 'claim': 'Нужен паспорт.', 'details': []}])
    link(store, job, link_sender('complements'))
    result = analysis.verify_analysis_sources(store, job['job_id'], answer_result(records[:1]), generation=job['generation'])
    assert [s['message_id'] for s in result['items'][0]['sources']] == [1]
    assert result['items'][0]['sources'][0]['is_digest']


def test_accepted_paraphrase_duplicate_can_supply_standalone_answer(manifest_job):
    store, job = command_job(manifest_job, 'qa')
    records = records_fixture(store, job, [
        {'record_type': 'answer', 'claim': 'Нужен паспорт.', 'text': 'Дайджест\nНужен паспорт.'},
        {'record_type': 'answer', 'claim': 'Для подачи требуется паспорт.'}])
    link(store, job, link_sender('duplicate'))
    result = analysis.verify_analysis_sources(store, job['job_id'], answer_result(records[:1]), generation=job['generation'])
    assert [s['message_id'] for s in result['items'][0]['sources']] == [2]


@pytest.mark.parametrize('command', ['qa', 'search'])
def test_incomplete_uniqueness_preserves_other_partial_reasons_and_is_idempotent(manifest_job, command):
    store, job = command_job(manifest_job, command)
    records = records_fixture(store, job, [{'record_type': 'answer', 'claim': 'Нужен паспорт.',
                                           'text': 'Дайджест\nНужен паспорт.'}])
    checkpoint = copy.deepcopy(store.get_job(job['job_id'])['checkpoint'])
    checkpoint['relations'] = {'partial': True, 'limited': True}
    store.save_checkpoint(job['job_id'], checkpoint, generation=job['generation'], max_bytes=1000000)
    result = answer_result(records)
    result.update(partial=True, reasons=['relations_incomplete', 'max_chars'])
    verified = analysis.verify_analysis_sources(store, job['job_id'], result, generation=job['generation'])
    assert not verified['items'] and verified['summary'] == ''
    assert set(verified['reasons']) == {'relations_incomplete', 'max_chars', 'source_selection_incomplete'}
    assert analysis.verify_analysis_sources(store, job['job_id'], verified, generation=job['generation']) == verified


def test_composite_qa_sources_three_covers_answers_before_question(manifest_job):
    store, job = command_job(manifest_job, 'qa', sources=3)
    records = records_fixture(store, job, [
        {'record_type': 'question', 'claim': 'Какие документы нужны?'},
        {'record_type': 'answer', 'claim': 'Нужен паспорт.'},
        {'record_type': 'answer', 'claim': 'Нужен NIE.'}])
    link(store, job, link_sender('answers'))
    result = analysis.verify_analysis_sources(store, job['job_id'], answer_result(records), generation=job['generation'])
    assert {s['message_id'] for s in result['items'][0]['sources'][:2]} == {2, 3}


def test_queued_failure_does_not_overwrite_new_generation_or_cancel(store, config, access):
    job, _ = admit(store, config, access, update())
    assert not store.fail_queued_job(job['job_id'], generation=job['generation'] + 1,
                                      reason='max_intermediate_bytes', now=NOW)
    store.request_cancel(job['job_id'], now=NOW)
    assert not store.fail_queued_job(job['job_id'], generation=job['generation'],
                                      reason='max_intermediate_bytes', now=NOW)
    assert store.get_job(job['job_id'])['status'] == 'cancelled'


def test_admission_exact_derived_storage_boundary(store, config, access):
    config['analysis']['max_intermediate_bytes'] = 90000
    first, _ = admit(store, config, access, update())
    minimum = store.storage_usage(first['job_id'])['allocated_bytes']
    store.conn.execute('DELETE FROM analysis_jobs WHERE job_id=?', (first['job_id'],))
    for maximum in (minimum - 1, minimum, minimum + 1):
        config['analysis']['max_intermediate_bytes'] = maximum
        if maximum < minimum:
            with pytest.raises(analysis.AnalysisRejection):
                admit(store, config, access, update())
            assert not list(store.iter_jobs('queued'))
        else:
            job, _ = admit(store, config, access, update())
            assert job['intermediate_bytes'] == minimum
            store.conn.execute('DELETE FROM analysis_jobs WHERE job_id=?', (job['job_id'],))


def test_delivery_and_report_keep_source_verification_without_rewriting_synthesis(manifest_job, access):
    store, job = command_job(manifest_job, 'qa')
    records = records_fixture(store, job, [{'record_type': 'answer', 'claim': 'Нужен паспорт.',
                                           'text': 'Дайджест\nНужен паспорт.'}])
    checkpoint = copy.deepcopy(store.get_job(job['job_id'])['checkpoint'])
    checkpoint['relations'] = {'partial': True}
    store.save_checkpoint(job['job_id'], checkpoint, generation=job['generation'], max_bytes=1000000)
    result = answer_result(records)
    result.update(partial=True, reasons=['relations_incomplete'])
    store.save_analysis_result(job['job_id'], result, generation=job['generation'])
    delivery = analysis.deliver_analysis_result(store, job['job_id'], generation=job['generation'],
        token='fixture', access_provider=lambda: access,
        send_func=lambda *a, **kw: {'message_id': 1, 'chat': {'id': 100}})
    assert delivery['analysis_partial']
    assert delivery['source_verification']['excluded_source_limited_items'] == 1
    report = analysis_telemetry.summarize_job(store, job['job_id'])
    assert 'source_selection_incomplete' in report['result_reasons']
    assert report['source_verification'] == delivery['source_verification']
    assert store.get_job(job['job_id'])['result'] == result


def test_pending_extraction_cannot_establish_digest_uniqueness(manifest_job):
    store, job = command_job(manifest_job, 'qa')
    records = records_fixture(store, job, [
        {'record_type': 'answer', 'claim': 'Нужен паспорт.', 'text': 'Дайджест\nНужен паспорт.'},
        {'record_type': 'claim', 'claim': 'Отдельное сообщение ещё требует повторной extraction.'}])
    extraction = store.leaf_evidence(job['job_id'], records[1]['record_id'])['batch_id']
    store.invalidate_batch_dependencies(job['job_id'], extraction, generation=job['generation'])
    # A missing active range is incomplete even without a failed batch row.
    assert 'extraction_coverage_incomplete' in store.incomplete_evidence_reasons(job['job_id'])
    result = analysis.verify_analysis_sources(store, job['job_id'], answer_result(records[:1]), generation=job['generation'])
    assert not result['items'] and 'source_selection_incomplete' in result['reasons']


@pytest.mark.parametrize('command', ['qa', 'search'])
def test_relink_synthesis_source_policy_and_delivery_compose(manifest_job, command, access):
    store, job = command_job(manifest_job, command)
    records = records_fixture(store, job, [
        {'record_type': 'question', 'claim': 'Какие документы нужны?'},
        {'record_type': 'answer', 'claim': 'Нужен паспорт.', 'text': 'Дайджест\nНужен паспорт.'}])
    link(store, job, link_sender('unrelated'))
    old = store.conn.execute("SELECT batch_id FROM analysis_batches WHERE kind='link'").fetchone()[0]
    store.invalidate_batch_dependencies(job['job_id'], old, generation=job['generation'])
    assert not list(store.iter_link_decisions(job['job_id']))
    link(store, job, link_sender('answers'))
    merge(store, job, merge_sender(no_progress=True))
    def synthesis_provider(req, timeout):
        if req.full_url.endswith('input_tokens'):
            return Response({'input_tokens': 100})
        payload = json.loads(req.data)
        wire = prompt_json(payload['input'], 'evidence')
        assert {edge['relation'] for edge in wire['relations']} == {'answers'}
        document = answer_result(records)
        document = {key: document[key] for key in ('summary', 'items', 'limitations')}
        document.update(insufficient_information=False, saturated=False)
        return Response(model_response(document))
    result = synthesize(store, job, synthesis_provider)
    assert not result['partial']
    delivery = analysis.deliver_analysis_result(store, job['job_id'], generation=job['generation'],
        token='fixture', access_provider=lambda: access,
        send_func=lambda *a, **kw: {'message_id': 1, 'chat': {'id': 100}})
    assert delivery['outcome'] == 'confirmed'
    assert delivery['source_verification']['digest_sources'] == 1
    assert 'https://t.me/peer22/2' in '\n'.join(part['text'] for part in delivery['parts'])
    current = store.get_job(job['job_id'])
    before = (current['llm_calls'], current['provider_requests'])
    analysis.deliver_analysis_result(store, job['job_id'], generation=job['generation'], token='fixture',
        access_provider=lambda: access, send_func=lambda *a, **kw: pytest.fail('Confirmed delivery must not repeat'))
    after = store.get_job(job['job_id'])
    assert before == (after['llm_calls'], after['provider_requests'])
