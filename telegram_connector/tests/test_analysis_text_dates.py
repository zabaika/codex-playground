"""Dates remain literal source text without a calendar execution protocol."""
import copy

import pytest

from telegram_connector import analysis_evidence as evidence, analysis_pipeline as pipeline
from telegram_connector.tests.test_analysis_manifest import manifest_job
from telegram_connector.tests.test_analysis_extraction_accounting import accounting_batch
from telegram_connector.tests.test_analysis_single_text import owned_record


@pytest.mark.parametrize('text', ['Релиз состоится 4 July 2026.',
    'Релиз состоится 1 октября.', 'Sortie prévue le 4 juillet 2026.',
    'リリース予定日：2026年7月4日。', 'Релиз запланирован на завтра.', 'Релиз анонсирован.'])
def test_literal_date_survives_without_event_at_or_date_support(manifest_job, text):
    store, job = manifest_job
    batch = accounting_batch(store, job, [text])
    document = dict(saturated=False, input_dispositions={'i0000': dict(status='represented',
        records=[owned_record(text)], retained_record_ids=[], reason='')})
    result = evidence.classify_extraction_document(store, job['job_id'], batch['batch_id'],
        generation=job['generation'], document=document, fragments=batch['messages'], selected=batch['messages'])
    assert not result['reasons'] and not result['unresolved_input_ids']
    record = result['records'][0]['payload']
    assert record['claim'] == text and record['source_refs'][0]['quote'] == text
    assert record['reported_at'] == batch['messages'][0]['input']['date_utc']
    assert not {'event_at', 'date_support', 'event_date_support'} & record.keys()


def test_extraction_wire_does_not_require_calendar_fields(manifest_job):
    store, job = manifest_job
    batch = accounting_batch(store, job, ['Релиз состоится 1 октября.'])
    payload = pipeline.build_extraction_payload(job, batch['messages'], batch['context'])
    schema = payload['text']['format']['schema']
    assert 'date_support' not in schema['properties']
    assert 'event_at' not in schema['$defs']['record']['properties']


def test_textual_dates_stay_part_of_source_and_detail_equivalence():
    base = dict(owned_record('Uno откроет офис.', ['Открытие 1 октября.']), schema_version=3)
    later = {**base, 'details': ['Открытие 9 октября.']}
    assert evidence._detail_scope(base) != evidence._detail_scope(later)
    assert evidence._source_fact_key(base) != evidence._source_fact_key(later)
    assert evidence.equivalent_detail_groups({'a': base, 'b': later}) == []


def test_extraction_rejects_calendar_envelope_instead_of_silently_adapting(manifest_job):
    store, job = manifest_job
    batch = accounting_batch(store, job, ['Релиз состоится 1 октября.'])
    value = dict(saturated=False, input_dispositions={'i0000': dict(status='represented',
        records=[owned_record('Релиз состоится 1 октября.')], retained_record_ids=[], reason='')}, date_support=[])
    with pytest.raises(ValueError, match='extraction_envelope_schema'):
        evidence.classify_extraction_document(store, job['job_id'], batch['batch_id'],
            generation=job['generation'], document=value, fragments=batch['messages'], selected=batch['messages'])


def test_worker_to_public_report_preserves_literal_date(manifest_job, monkeypatch, tmp_path):
    from telegram_connector.tests import test_analysis_fact_support as support
    values = copy.deepcopy(support.FACTS)
    values[0]['claim'] = 'OpenAI выпустила Terra 4 July 2026.'
    monkeypatch.setattr(support, 'FACTS', values)
    support.test_worker_filters_mixed_fact_through_delivery_and_report(manifest_job, monkeypatch, tmp_path)
