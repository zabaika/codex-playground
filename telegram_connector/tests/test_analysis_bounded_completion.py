"""Bounded completion keeps whole-post context and honest topic provenance."""

import copy

import pytest

from telegram_connector import analysis_evidence as evidence
from telegram_connector import analysis_pipeline as pipeline
from telegram_connector.tests.test_analysis_extraction_accounting import accounting_batch
from telegram_connector.tests.test_analysis_manifest import manifest_job, change_setting
from telegram_connector.tests.test_analysis_relations import records_fixture


def test_topics_preserves_whole_post_context(manifest_job):
    store, job = manifest_job
    text = 'Компания ALPHA.\n\nВыпущен продукт A.\n\nОткрыт API B.\n\n@channel'
    batch = accounting_batch(store, job, [text])
    assert len(batch['messages']) == 1
    assert ''.join(row['message']['text'] for row in batch['messages']) == text
    scope = evidence.extraction_scope(batch['messages'])
    assert len(scope['model_input_ids']) == 1
    assert batch['messages'][0]['input']['fragment_start'] == 0
    assert batch['messages'][-1]['input']['fragment_end'] == len(text)
    assert not batch['context']


@pytest.mark.parametrize('command', ['topics', 'qa', 'search'])
def test_mixed_digest_support_cannot_launder_unique_topic_facts(
        manifest_job, command):
    store, job = manifest_job
    store.conn.execute('UPDATE analysis_jobs SET command=? WHERE job_id=?',
                       (command, job['job_id']))
    job = store.get_job(job['job_id'])
    records = records_fixture(store, job, [
        {'claim': 'Лимит сокращён вдвое; выданы кредиты 2500 USD.',
         'text': 'Дайджест\nЛимит сокращён вдвое; выданы кредиты 2500 USD.'},
        {'claim': 'Лимит сокращён вдвое.'}])
    mixed = copy.deepcopy(records[0]['payload'])
    mixed['source_refs'].extend(records[1]['payload']['source_refs'])
    before = copy.deepcopy(mixed)
    allowed = command != 'topics'
    assert evidence.record_source_eligible(store, job, mixed) is allowed
    options, uncertain = evidence._source_options(
        store, job, {'mixed': mixed}, [],
        budget=evidence.SourceCoverBudget(50000))
    assert bool(options['mixed']) is allowed
    assert not uncertain
    assert mixed == before
    assert evidence.record_source_eligible(store, job, records[1]['payload'])


def test_owned_contract_keeps_separate_events_without_counter(manifest_job):
    from telegram_connector.tests.test_analysis_owned_records import record
    store, job = manifest_job
    batch = accounting_batch(store, job, ['Выпущен продукт ALPHA.\n\nОткрыт API BETA.'])
    payload = pipeline.build_extraction_payload(job, batch['messages'], batch['context'])
    schema = payload['text']['format']['schema']
    assert 'independent_events' not in schema['$defs']['record']['properties']
    records = [record('Выпущен продукт ALPHA.'), record('Открыт API BETA.')]
    document = dict(saturated=False, input_dispositions={'i0000': dict(
        status='represented', records=records, retained_record_ids=[], reason='')})
    evidence.validate_shape(document, schema)
    result = evidence.classify_extraction_document(store, job['job_id'],
        batch['batch_id'], generation=job['generation'], document=document,
        fragments=batch['messages'], selected=batch['messages'])
    assert result['input_accounting_complete']
    assert len(result['records']) == 2
    assert all(row['payload']['source_refs'][0]['quote'] == batch['messages'][0]['message']['text']
               for row in result['records'])
