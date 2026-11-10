"""Presentation provenance is measured at the existing publication owner."""
import json

from telegram_connector import telegram_analysis as analysis, analysis_telemetry as telemetry
from telegram_connector.tests.test_analysis_manifest import manifest_job
from telegram_connector.tests.test_analysis_delivery import sourced_result
from telegram_connector.tests.test_analysis_manifest import change_setting
from telegram_connector.tests.test_analysis_artifacts import artifact_job, write_report
import pytest


def test_delivery_records_actual_presented_ids_without_reinterpreting_result(manifest_job):
    store, job = manifest_job
    result = sourced_result(store, job)
    store.save_analysis_result(job['job_id'], result, generation=job['generation'])
    original = store.get_job(job['job_id'])['result']
    sent = []
    analysis.deliver_analysis_result(store, job['job_id'], generation=job['generation'], token='fixture-token',
        deadline=100, monotonic_func=lambda: 0, sleep_func=lambda _: None,
        access_provider=lambda: {'chats': frozenset({'100'}), 'users': frozenset({'7'}), 'usernames': frozenset()},
        send_func=lambda *a, **k: (sent.append(a[2]) or {'message_id': len(sent), 'chat': {'id': 100}}))
    saved = store.get_job(job['job_id'])
    wanted = sorted({identity for item in result['items'] for identity in item['evidence_ids']})
    assert saved['delivery']['presented_evidence_ids'] == wanted
    assert saved['result'] == original and saved['delivery']['outcome'] == 'confirmed'
    report = telemetry.summarize_job(store, job['job_id'])
    assert report['selection']['presented_evidence_ids'] == wanted
    assert report['selection']['presented_evidence_records'] == len(wanted)


def test_legacy_presentation_absence_stays_unknown(manifest_job):
    store, job = manifest_job
    result = sourced_result(store, job)
    store.save_analysis_result(job['job_id'], result, generation=job['generation'])
    report = telemetry.summarize_job(store, job['job_id'])
    assert report['selection']['presented_evidence_ids'] is None
    assert report['selection']['source_excluded_evidence_ids'] is None


@pytest.mark.parametrize('command', ['topics', 'qa', 'search'])
@pytest.mark.parametrize('raw', [
    ['id-z', 'id-a', 'id-z'], ['r003', 'r001', 'r003'], ['id-z', 'r001', 'id-z']])
def test_model_selection_compares_canonical_ids_before_result_filtering(
        manifest_job, command, raw):
    store, job = manifest_job
    job['command'] = command
    # Historical data remains readable without any supported execution policy.
    job['request']['settings'].pop('algorithm_version')
    job['request']['settings'].pop('schema_version')
    job['result'] = {'items': [{'evidence_ids': ['id-a']}]}
    payload = {'plan': {'evidence': [{'record_id': key}
        for key in ['id-z', 'id-a', 'id-m']]},
        'output': {'items': [{'evidence_ids': raw}]}}
    batches = [{'kind': 'synthesize', 'state': 'succeeded',
                'payload_json': json.dumps(payload)}]
    report = telemetry.selection_summary(store, job, batches)
    assert report['model_selected_evidence_ids'] == ['id-a', 'id-z']
    assert report['not_selected_by_model_ids'] == ['id-m']
    assert report['unresolved_model_evidence_ids'] == []
    assert report['result_evidence_ids'] == ['id-a']


def test_saved_identity_takes_priority_over_generated_alias(manifest_job):
    store, job = manifest_job
    payload = {'plan': {'evidence': [{'record_id': 'r001'}, {'record_id': 'id-a'}]},
        'output': {'items': [{'evidence_ids': ['r001']}]}}
    report = telemetry.selection_summary(store, job, [{'kind': 'synthesize', 'state': 'succeeded',
        'payload_json': json.dumps(payload)}])
    assert report['model_selected_evidence_ids'] == ['r001']
    assert report['not_selected_by_model_ids'] == ['id-a']


@pytest.mark.parametrize('unknown', ['r999', 'foreign-record'])
def test_unknown_model_identity_does_not_invent_omission_count(manifest_job, unknown):
    store, job = manifest_job
    payload = {'plan': {'evidence': [{'record_id': 'id-a'}, {'record_id': 'id-b'}]},
        'output': {'items': [{'evidence_ids': ['r001', unknown]}]}}
    batches = [{'kind': 'synthesize', 'state': 'succeeded',
                'payload_json': json.dumps(payload)}]
    report = telemetry.selection_summary(store, job, batches)
    assert report['model_selected_evidence_ids'] == ['id-a']
    assert report['unresolved_model_evidence_ids'] == [unknown]
    assert report['not_selected_by_model_ids'] is None


def test_compact_selection_without_saved_scope_stays_unresolved(manifest_job):
    store, job = manifest_job
    batches = [{'kind': 'synthesize', 'state': 'succeeded', 'payload_json': json.dumps(
        {'output': {'items': [{'evidence_ids': ['r001']}]}})}]
    report = telemetry.selection_summary(store, job, batches)
    assert report['model_selected_evidence_ids'] is None
    assert report['unresolved_model_evidence_ids'] == ['r001']
    assert report['not_selected_by_model_ids'] is None


def test_invalidated_synthesis_cannot_change_alias_scope(manifest_job):
    store, job = manifest_job
    payload = {'plan': {'evidence': [{'record_id': 'id-a'}]},
        'output': {'items': [{'evidence_ids': ['r001']}]}}
    batches = [{'kind': 'synthesize', 'state': 'succeeded',
                'payload_json': json.dumps(payload)},
        {'kind': 'synthesize', 'state': 'succeeded', 'payload_json': json.dumps(
            {**payload, 'invalidated': True, 'plan': {'evidence': [{'record_id': 'other'}]}})}]
    report = telemetry.selection_summary(store, job, batches)
    assert report['model_selected_evidence_ids'] == ['id-a']
    assert report['not_selected_by_model_ids'] == []


@pytest.mark.parametrize('invalid', ['foreign', 'duplicate'])
def test_publication_ids_cannot_claim_foreign_or_repeated_facts(manifest_job, invalid):
    store, job = manifest_job
    result = sourced_result(store, job)
    store.save_analysis_result(job['job_id'], result, generation=job['generation'])
    own = result['items'][0]['evidence_ids'][0]
    ids = ['foreign'] if invalid == 'foreign' else [own, own]
    with pytest.raises(ValueError, match='presentation evidence scope'):
        store.prepare_delivery(job['job_id'], generation=job['generation'], chunks=['Fixture'], parse_mode='HTML',
            detail='normal', analysis_partial=False, presented_evidence_ids=ids)
    assert not store.get_job(job['job_id'])['delivery']


def test_presentation_limit_records_the_kept_subset_and_reason(manifest_job):
    store, job = manifest_job
    result = sourced_result(store, job)
    ids = result['items'][0]['evidence_ids']
    result['summary'] = ''
    result['items'] = [{'title': f'Факт {index}', 'text': 'Подтверждённый факт. ' * 24, 'contradictions': [], 'evidence_ids': [identity]}
        for index, identity in enumerate(ids)]
    change_setting(store, job, 'analysis.output.profiles.normal', 'max_chars', 1000)
    store.save_analysis_result(job['job_id'], result, generation=job['generation'])
    analysis.deliver_analysis_result(store, job['job_id'], generation=job['generation'], token='fixture',
        deadline=100, monotonic_func=lambda: 0, sleep_func=lambda _: None,
        access_provider=lambda: {'chats': frozenset({'100'}), 'users': frozenset({'7'}), 'usernames': frozenset()},
        send_func=lambda *a, **k: {'message_id': 1, 'chat': {'id': 100}})
    report = telemetry.summarize_job(store, job['job_id'])['selection']
    assert report['result_evidence_ids'] == sorted(ids)
    assert report['presented_evidence_ids'] == ids[:1]
    assert report['not_presented_after_selection_ids'] == ids[1:]
    assert report['presentation_limit_reason'] == 'max_chars'


def test_full_artifact_records_the_rendered_subset_without_mutating_result(artifact_job):
    store, job = artifact_job
    result = store.get_job(job['job_id'])['result']
    ids = result['items'][0]['evidence_ids']
    result['summary'] = ''
    result['items'] = [{'title': f'Факт {index}', 'text': 'Подтверждённый факт. ' * 24, 'contradictions': [], 'evidence_ids': [identity]}
        for index, identity in enumerate(ids)]
    change_setting(store, job, 'analysis.output.profiles.full', 'max_chars', 1100)
    store.save_analysis_result(job['job_id'], result, generation=job['generation'])
    write_report(store, job)
    saved = store.get_job(job['job_id'])
    artifact = saved['checkpoint']['artifact']
    assert artifact['presented_evidence_ids'] == ids[:1]
    assert artifact['presentation_limit_reason'] == 'max_chars'
    assert saved['result'] == result
