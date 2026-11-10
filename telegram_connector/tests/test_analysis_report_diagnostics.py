"""Trace, input accounting and billing uncertainty are different facts."""
from telegram_connector import analysis_pipeline as pipeline
from telegram_connector.analysis_telemetry import _billing, summarize_job
from telegram_connector.tests.test_analysis_billing import RATES, receipt
from telegram_connector.tests.test_analysis_manifest import manifest_job
from telegram_connector.tests.test_analysis_extraction_accounting import accounting_batch, envelope
from telegram_connector.tests.test_analysis_evidence import model_response
from telegram_connector.tests.test_analysis_transport import Response
from telegram_connector.tests.test_analysis_split_recovery import transport


def test_no_pricing_does_not_erase_known_actual_model_and_tier():
    value = receipt(actual_model='fixture-model', actual_service_tier='default')
    cost, warnings = _billing([value], None, False)
    assert warnings == ['pricing_not_supplied']
    assert cost['usd'] is None


def test_missing_expected_metadata_is_not_missing_native_receipt():
    value = receipt(actual_model='fixture-model', actual_service_tier='default')
    _, warnings = _billing([value], RATES, False)
    assert 'pricing_model_unavailable' in warnings and 'pricing_service_tier_unavailable' in warnings
    assert 'actual_model_unavailable' not in warnings


def test_validation_rejected_input_is_not_model_declared_unresolved(manifest_job):
    store, job = manifest_job
    batch = accounting_batch(store, job, ['Uno Uno'])
    value = envelope(batch)
    value['input_dispositions']['i0000']['records'][0]['supporting_input_ids'] = ['foreign']
    def provider(req, timeout):
        return Response({'input_tokens': 100} if req.full_url.endswith('input_tokens') else model_response(value))
    result = pipeline.extract_analysis_batch(store, job['job_id'], batch, generation=job['generation'], **transport(provider))
    assert result['partial']
    report = summarize_job(store, job['job_id'])['extraction']
    assert not report['input_accounting_complete']
    assert report['validation_rejected_fragments'] == 1
    assert report['model_unresolved_fragments'] == 0
    assert report['validation_rejections'][0]['input_id'] == 'i0000'
    assert report['validation_rejections'][0]['reasons'] == ['extraction_owned_record_schema']


def test_unattributed_scope_error_remains_visible_without_invented_fragment_count(manifest_job):
    store, job = manifest_job
    batch = accounting_batch(store, job, ['Uno'])
    value = envelope(batch)
    value['unexpected'] = True
    def provider(req, timeout):
        return Response({'input_tokens': 100} if req.full_url.endswith('input_tokens') else model_response(value))
    pipeline.extract_analysis_batch(store, job['job_id'], batch, generation=job['generation'], **transport(provider))
    report = summarize_job(store, job['job_id'])['extraction']
    assert not report['input_accounting_complete']
    assert report['validation_rejected_fragments'] == 0
    assert report['validation_errors'] == {'extraction_envelope_schema': 1}
