"""Admission and resume use the current policy owner, without frozen executors."""
import copy
from datetime import datetime, timezone
import tomllib

import pytest

from telegram_connector import analysis_settings as settings, telegram_analysis as analysis, analysis_pipeline, analysis_evidence
from telegram_connector.tests.test_analysis_manifest import manifest_job


def test_admission_uses_supported_policy_and_adaptive_default(manifest_job):
    _, job = manifest_job
    frozen = job['request']['settings']
    assert settings.supported_analysis_policy(frozen)
    assert (frozen['schema_version'], frozen['algorithm_version']) == (
        settings.ANALYSIS_SCHEMA_VERSION, settings.ANALYSIS_ALGORITHM_VERSION)
    payload = analysis_pipeline.build_extraction_payload(job, [], [])
    assert payload['text']['format']['name'] == analysis_evidence.ADDRESSED_EVIDENCE_FORMATS[frozen['schema_version']]
    config = tomllib.loads(settings.DEFAULT_CONFIG_FILE.read_text())
    assert settings.resolve_analysis_policy(config)['analysis']['link_pairs_per_request'] == 0
    assert frozen['analysis_prompts']['hash']


@pytest.mark.parametrize('value', [-1, True, 1.2])
def test_adaptive_setting_does_not_allow_other_invalid_caps(value):
    config = tomllib.loads(settings.DEFAULT_CONFIG_FILE.read_text())
    config['analysis']['link_pairs_per_request'] = value
    with pytest.raises(ValueError):
        settings.resolve_analysis_policy(config)


def test_resume_keeps_frozen_prompt_but_rejects_changed_execution_policy(manifest_job):
    _, job = manifest_job
    frozen = copy.deepcopy(job['request']['settings'])
    current = copy.deepcopy(frozen)
    current['analysis_prompts']['hash'] = 'changed-file-without-mutating-frozen-request'
    assert settings.resume_policy_compatible(current, frozen)
    current['analysis']['max_total_tokens'] += 1
    assert not settings.resume_policy_compatible(current, frozen)


def test_internal_index_uses_current_admission_settings(manifest_job):
    store, _ = manifest_job
    config = tomllib.loads(settings.DEFAULT_CONFIG_FILE.read_text())
    job = analysis.enqueue_fts_backfill(store, config, rebuild=False, now=datetime.now(timezone.utc))
    analysis.validate_fts_backfill_job(job)
    assert settings.supported_analysis_policy(job['request']['settings'])
