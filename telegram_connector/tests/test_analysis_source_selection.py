"""Subject provenance and bounded source-cover regressions; never live inference."""

import copy
import itertools
import json
import random

import pytest

from telegram_connector import analysis_evidence as evidence
from telegram_connector.tests.test_analysis_manifest import manifest_job, change_setting, seed, build, messages
from telegram_connector.tests.test_analysis_review_regressions import command_job, answer_result
from telegram_connector.tests.test_analysis_relations import records_fixture, link, link_sender
from telegram_connector.tests.test_analysis_evidence import document
from telegram_connector import telegram_analysis as analysis, analysis_telemetry, analysis_settings
from telegram_connector.tests.test_analysis_delivery import frozen_output_setting


@pytest.mark.parametrize('command', ['qa', 'search'])
def test_same_phrase_for_different_banks_does_not_replace_digest(manifest_job, command):
    store, job = command_job(manifest_job, command)
    records = records_fixture(store, job, [
        {'record_type': 'answer', 'claim': 'Комиссия составляет 10 EUR.', 'entities': ['Uno'],
         'text': 'Дайджест\nБанк Uno: комиссия составляет 10 EUR.'},
        {'record_type': 'answer', 'claim': 'Комиссия составляет 10 EUR.', 'entities': ['Dos']}])
    link(store, job, link_sender('unrelated'))
    result = evidence.verify_analysis_sources(store, job['job_id'], answer_result(records[:1]), generation=job['generation'])
    assert [source['message_id'] for source in result['items'][0]['sources']] == [1]
    assert result['items'][0]['sources'][0]['is_digest']


def source(number):
    return {'channel_id': 22, 'message_id': number, 'url': f'https://t.me/fixture/{number}',
            'quote': 'x' * (10 - number), 'date_utc': '2026-11-05', 'is_digest': False}


def cover_options(sets):
    options = {}
    for number, covered in enumerate(sets, 1):
        for fact in covered:
            options.setdefault(fact, []).append(source(number))
    return options


def test_source_cover_checks_alternatives_before_dropping_valid_answer():
    required = set('abcdef')
    options = cover_options([set('abcd'), set('abe'), set('cdf')])
    selected = evidence._select_supporting_sources(options, required, 2, budget=evidence.SourceCoverBudget(50000))
    assert selected['outcome'] == 'found'
    assert {value['message_id'] for value in selected['sources']} == {2, 3}


def six_answer_records(store, job):
    sets = ('abcd', 'abe', 'cdf')
    for number, facts in enumerate(sets, 1):
        seed(store, 22, [number], text=' '.join(f'Факт {key}.' for key in facts))
    build(store, job)
    fragments = messages(store, job)
    for fragment in fragments:
        fragment['input']['fragment_end'] = len(fragment['message']['text'])
    values = []
    for key in 'abcdef':
        refs = [document({'messages': [fragment]})['records'][0]['source_refs'][0]
                for fragment, facts in zip(fragments, sets) if key in facts]
        value = document({'messages': [fragments[0]]})['records'][0]
        value.update(record_type='answer', claim=f'Факт {key}.', entities=['Uno'], details=[], source_refs=refs)
        values.append(value)
    batch = store.plan_batch(job['job_id'], generation=job['generation'], step_key='cover/extract',
                             kind='extract', payload={})
    records = evidence.validate_evidence_records(store, job['job_id'], batch['batch_id'], generation=job['generation'],
        document={'records': values, 'saturated': False}, fragments=fragments)
    store.complete_batch(job['job_id'], batch['batch_id'], generation=job['generation'], records=records,
                          inputs=[fragment['input'] for fragment in fragments])
    return records


def test_validated_six_answer_cover_keeps_summary_and_links(manifest_job):
    store, job = command_job(manifest_job, 'qa', sources=2)
    records = six_answer_records(store, job)
    original = answer_result(records)
    result = analysis.prepare_analysis_presentation(store, job['job_id'], original, generation=job['generation'])
    assert result['result']['summary'] == original['summary']
    assert {s['message_id'] for s in result['result']['items'][0]['sources']} == {2, 3}
    again = evidence.verify_analysis_sources(store, job['job_id'], original, generation=job['generation'])
    assert again['items'][0]['sources'] == result['result']['items'][0]['sources']
    assert result['result']['source_cover_fallbacks'] == 1
    assert result['result']['source_cover_states'] > 0


def test_source_cover_matches_exhaustive_small_oracle():
    rng = random.Random(12)
    for _ in range(500):
        required = set(range(rng.randrange(0, 8)))
        sets = [{key for key in required if rng.randrange(2)} for _ in range(rng.randrange(0, 9))]
        limit = rng.randrange(0, 5)
        possible = any(set().union(set(), *combo) >= required
                       for count in range(min(limit, len(sets)) + 1) for combo in itertools.combinations(sets, count))
        result = evidence._select_supporting_sources(cover_options(sets), required, limit,
                                                      budget=evidence.SourceCoverBudget(50000))
        assert result['outcome'] == ('found' if possible else 'proven_impossible'), (sets, required, limit)
        if possible:
            indices = [ref['message_id'] - 1 for ref in result['sources']]
            assert len(indices) <= limit and len(indices) == len(set(indices))
            assert set().union(set(), *(sets[index] for index in indices)) >= required


def test_state_and_deadline_limits_are_not_impossibility():
    options = cover_options([set('abcd'), set('abe'), set('cdf')])
    budget = evidence.SourceCoverBudget(1)
    result = evidence._select_supporting_sources(options, set('abcdef'), 2, budget=budget)
    assert result['outcome'] == 'search_limited' and budget.metrics['source_cover_states'] == 1
    expired = evidence.SourceCoverBudget(50000, deadline=1, monotonic_func=lambda: 1)
    result = evidence._select_supporting_sources(options, set('abcdef'), 2, budget=expired)
    assert result['outcome'] == 'search_limited'
    assert expired.metrics['source_cover_impossible_items'] == 0


def test_verification_shares_budget_across_all_items(manifest_job):
    store, job = command_job(manifest_job, 'qa', sources=2)
    records = six_answer_records(store, job)
    baseline = evidence.verify_analysis_sources(store, job['job_id'], answer_result(records), generation=job['generation'])
    cap = baseline['source_cover_states'] + 1
    change_setting(store, job, 'analysis.output', 'source_cover_max_states', cap)
    original = answer_result(records)
    original['items'] *= 2
    result = evidence.verify_analysis_sources(store, job['job_id'], original, generation=job['generation'])
    assert len(result['items']) == 1
    assert result['source_cover_states'] == cap and result['source_cover_limited_items'] == 1
    assert result['partial'] and 'source_selection_incomplete' in result['reasons']


@pytest.mark.parametrize('entities', [[], ['банк', 'Dos']])
def test_unknown_or_shared_generic_subject_never_replaces_uno(manifest_job, entities):
    store, job = command_job(manifest_job, 'qa')
    records = records_fixture(store, job, [
        {'record_type': 'answer', 'claim': 'Комиссия 10 EUR.', 'entities': ['банк', 'Uno'],
         'text': 'Дайджест\nКомиссия 10 EUR.'},
        {'record_type': 'answer', 'claim': 'Комиссия 10 EUR.', 'entities': entities}])
    link(store, job, link_sender('duplicate'))
    result = evidence.verify_analysis_sources(store, job['job_id'], answer_result(records[:1]), generation=job['generation'])
    assert all(s['message_id'] != 2 for item in result['items'] for s in item['sources'])
    if not entities:
        assert result['partial'] and 'source_selection_incomplete' in result['reasons']


def test_subject_normalization_preserves_safe_duplicate(manifest_job):
    store, job = command_job(manifest_job, 'qa')
    records = records_fixture(store, job, [
        {'record_type': 'answer', 'claim': 'Комиссия 10 EUR.', 'entities': ['Uno', 'café'],
         'text': 'Дайджест\nКомиссия 10 EUR.'},
        {'record_type': 'answer', 'claim': 'Комиссия 10 EUR.', 'entities': [' CAFE\u0301 ', ' uno ', 'UNO']}])
    link(store, job, link_sender('duplicate'))
    result = evidence.verify_analysis_sources(store, job['job_id'], answer_result(records[:1]), generation=job['generation'])
    assert [s['message_id'] for s in result['items'][0]['sources']] == [2]


@pytest.mark.parametrize('value', [0, -1, True, 1.5])
def test_cover_policy_is_positive_frozen_integer(value):
    with pytest.raises(ValueError, match='source_cover_max_states'):
        analysis_settings.resolve_analysis_policy({'analysis': {'output': {'source_cover_max_states': value}}})


@pytest.mark.parametrize('detail', ['normal', 'full'])
def test_worker_reuses_deadline_for_verify_artifact_and_delivery(manifest_job, monkeypatch, tmp_path, detail):
    store, job = command_job(manifest_job, 'qa', sources=2)
    frozen_output_setting(store, job, lambda p: p['request'].update(detail=detail))
    records = six_answer_records(store, job)
    original = answer_result(records)
    store.save_analysis_result(job['job_id'], original, generation=job['generation'])
    root = tmp_path / 'repo/telegram_connector'
    root.mkdir(parents=True)
    monkeypatch.setenv('TELEGRAM_CONNECTOR_PROJECT_ROOT', str(root))
    clock = lambda: 0
    captured = []
    factory = evidence.SourceCoverBudget
    def capture(*args, **kwargs):
        captured.append(kwargs)
        return factory(*args, **kwargs)
    monkeypatch.setattr(evidence, 'SourceCoverBudget', capture)
    access = {'chats': frozenset({'100'}), 'users': frozenset({'7'}), 'usernames': frozenset()}
    assert analysis.execute_analysis_job(store, store.get_job(job['job_id']), token='fixture', deadline=77,
        monotonic_func=clock, access_provider=lambda: access,
        send_func=lambda *a, **kw: {'message_id': 1, 'chat': {'id': 100}}) == 'completed'
    assert len(captured) >= (5 if detail == 'full' else 3)
    assert all(call['deadline'] == 77 and call['monotonic_func'] is clock for call in captured)
    saved = store.get_job(job['job_id'])
    assert saved['checkpoint']['source_verification']['source_cover_fallbacks'] == 1
    assert saved['result'] == original
    assert saved['provider_requests'] == 0
    assert analysis_telemetry.summarize_job(store, job['job_id'])['source_verification']['source_cover_fallbacks'] == 1


@pytest.mark.parametrize('subjects', [['Uno', 'Dos', 'Uno'], ['Uno', '', 'Dos']])
def test_duplicate_transitive_bridge_cannot_supply_wrong_subject(manifest_job, monkeypatch, subjects):
    store, job = command_job(manifest_job, 'qa')
    records = records_fixture(store, job, [{'record_type': 'answer', 'claim': f'Утверждение {i}.',
        'entities': [subject] if subject else [], 'text': ('Дайджест\n' if i < 2 else '') + f'Утверждение {i}.'}
        for i, subject in enumerate(subjects)])
    ids = [r['record_id'] for r in records]
    edges = [{'left_id': ids[a], 'right_id': ids[b], 'relation': 'duplicate', 'reason': 'fixture'} for a, b in ((0, 1), (1, 2))]
    monkeypatch.setattr(store, 'iter_link_decisions', lambda _: iter(edges))
    result = evidence.verify_analysis_sources(store, job['job_id'], answer_result(records[:1]), generation=job['generation'])
    assert all(s['message_id'] != 3 for item in result['items'] for s in item['sources'])


@pytest.mark.parametrize('veto', [False, True])
def test_same_subject_duplicate_chain_obeys_direct_nonduplicate_veto(manifest_job, monkeypatch, veto):
    store, job = command_job(manifest_job, 'qa')
    records = records_fixture(store, job, [{'record_type': 'answer', 'claim': f'Перефразировка {i}.',
        'entities': ['Uno'], 'text': ('Дайджест\n' if i < 2 else '') + f'Перефразировка {i}.'} for i in range(3)])
    ids = [r['record_id'] for r in records]
    edges = [{'left_id': ids[a], 'right_id': ids[b], 'relation': 'duplicate', 'reason': 'fixture'} for a, b in ((0, 1), (1, 2))]
    if veto:
        edges.append({'left_id': ids[0], 'right_id': ids[2], 'relation': 'unrelated', 'reason': 'fixture'})
    monkeypatch.setattr(store, 'iter_link_decisions', lambda _: iter(edges))
    result = evidence.verify_analysis_sources(store, job['job_id'], answer_result(records[:1]), generation=job['generation'])
    if veto:
        assert not result['items'] and 'source_selection_incomplete' in result['reasons']
    else:
        assert [s['message_id'] for s in result['items'][0]['sources']] == [3]


def test_expired_verification_retains_diagnostics_without_delivery_intent(manifest_job):
    store, job = command_job(manifest_job, 'qa', sources=2)
    records = six_answer_records(store, job)
    original = answer_result(records)
    original.update(partial=True, reasons=['max_chars'])
    store.save_analysis_result(job['job_id'], original, generation=job['generation'])
    result = analysis.prepare_analysis_presentation(store, job['job_id'], original,
        generation=job['generation'], deadline=0, monotonic_func=lambda: 1)
    assert result['result']['partial']
    assert set(result['result']['reasons']) == {'max_chars', 'source_selection_incomplete'}
    assert store.get_job(job['job_id'])['delivery'] == {}
    report = analysis_telemetry.summarize_job(store, job['job_id'])
    assert report['source_verification']['source_cover_limited_items'] == 1
    assert report['source_verification']['source_cover_impossible_items'] == 0
    assert 'source_selection_incomplete' in report['result_reasons']


def test_old_delivery_counters_are_unknown_for_new_measurements(manifest_job):
    store, job = manifest_job
    legacy = {'excluded_digest_only_items': 0, 'excluded_unsupported_items': 0,
              'excluded_source_limited_items': 0, 'digest_sources': 0}
    store.conn.execute('UPDATE analysis_jobs SET delivery_json=? WHERE job_id=?',
                       (json.dumps({'source_verification': legacy}), job['job_id']))
    report = analysis_telemetry.summarize_job(store, job['job_id'])
    assert report['source_verification']['source_cover_states'] is None
    assert report['source_verification']['source_cover_elapsed_ms'] is None


def test_cover_state_budget_is_frozen_without_live_default(manifest_job):
    store, job = manifest_job
    assert job['request']['settings']['analysis']['output']['source_cover_max_states'] == 50000
    payload = copy.deepcopy(job['request'])
    del payload['settings']['analysis']['output']['source_cover_max_states']
    store.conn.execute('UPDATE analysis_jobs SET request_json=? WHERE job_id=?', (json.dumps(payload), job['job_id']))
    with pytest.raises(ValueError, match='analysis_policy_incompatible'):
        evidence.verify_analysis_sources(store, job['job_id'], {'items': []}, generation=job['generation'])
