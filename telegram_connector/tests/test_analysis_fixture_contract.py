"""Authored test responses must not silently fix an intentionally invalid record."""
import pytest

from telegram_connector import analysis_evidence as evidence, analysis_pipeline as pipeline
from telegram_connector.tests.analysis_fixtures import owned_response_from_records
from telegram_connector.tests.test_analysis_evidence import document
from telegram_connector.tests.test_analysis_extraction_accounting import accounting_batch
from telegram_connector.tests.test_analysis_manifest import manifest_job


def test_fixture_builder_keeps_unsupported_fields_for_real_classifier(manifest_job):
    store, job = manifest_job
    batch = accounting_batch(store, job, ['Сведения источника.'])
    payload = pipeline.build_extraction_payload(job, batch['messages'], batch['context'])
    invalid = document(batch)
    invalid['records'][0]['conditions'] = ['Удалённое поле нельзя исправлять в тесте.']
    response = owned_response_from_records(payload, invalid)
    assert response['input_dispositions']['i0000']['records'][0]['conditions'] == invalid['records'][0]['conditions']
    result = evidence.classify_extraction_document(store, job['job_id'], batch['batch_id'],
        generation=job['generation'], document=response, fragments=batch['messages'], selected=batch['messages'])
    assert not result['records'] and result['unresolved_input_ids'] == ['i0000']
    assert result['record_errors'] == {'0': 'extraction_owned_record_schema'}


def test_fixture_builder_does_not_silently_drop_an_unknown_source(manifest_job):
    store, job = manifest_job
    batch = accounting_batch(store, job, ['Сведения источника.'])
    payload = pipeline.build_extraction_payload(job, batch['messages'], batch['context'])
    invalid = document(batch)
    invalid['records'][0]['source_refs'][0]['message_id'] = 999
    with pytest.raises(ValueError, match='fixture_source_not_supplied_whole'):
        owned_response_from_records(payload, invalid)
