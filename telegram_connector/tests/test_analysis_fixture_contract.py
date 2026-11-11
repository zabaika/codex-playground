"""Mock protocols and production assembly have separate, observable contracts."""
import copy
import json
from pathlib import Path
from string import Formatter

import pytest

from telegram_connector import analysis_evidence as evidence, analysis_pipeline as pipeline, analysis_settings
from telegram_connector.tests.analysis_fixtures import (
    owned_response_from_records, prompt_json, prompt_field, write_prompt_bundle,
)
from telegram_connector.tests.test_analysis_evidence import document
from telegram_connector.tests.test_analysis_extraction_accounting import accounting_batch
from telegram_connector.tests.test_analysis_manifest import manifest_job
from telegram_connector.tests.test_analysis_synthesis_contract import case
from telegram_connector.tests.test_analysis_batching import batches


CONFIG = Path(__file__).resolve().parents[1] / 'config'


def production_bundle(edited=False):
    bundle = analysis_settings.load_analysis_prompts(
        {'analysis_prompts': {'file': 'analysis_prompts.toml'}}, base_dir=CONFIG)
    if edited:
        # Change every editorial literal and reorder fields in memory only.
        for stage, template in bundle['templates'].items():
            fields = [field for _, field, _, _ in Formatter().parse(template) if field]
            bundle['templates'][stage] = '\n\n'.join(
                f'Alternative paragraph {i}\n{{{field}}}'
                for i, field in enumerate(reversed(fields)))
        bundle['system_instructions'] = 'Alternative system instructions.'
        bundle['shared_prefix'] = 'Alternative shared wording.\n\n'
    return bundle


@pytest.mark.parametrize('stage', analysis_settings.PROMPT_FIELDS)
@pytest.mark.parametrize('edited', [False, True])
def test_production_templates_deliver_every_declared_argument(stage, edited):
    # Values, including braces and newlines, must be substituted only once.
    values = {key: f'fixture-{key}: {{literal}}\nEND'
              for key in analysis_settings.PROMPT_FIELDS[stage]}
    rendered = analysis_settings.render_analysis_prompt(production_bundle(edited), stage, **values)
    for value in values.values():
        assert value in rendered


@pytest.mark.parametrize('command', ['topics', 'search', 'qa'])
@pytest.mark.parametrize('edited', [False, True])
def test_production_builders_keep_final_data_and_instruction_boundary(manifest_job, monkeypatch, command, edited):
    store, job = manifest_job
    job, rows, view, _ = case(store, job)
    job = copy.deepcopy(job)
    job['command'] = command
    job['request']['request']['query'] = 'fixture-user-query {untrusted}'
    bundle = production_bundle(edited)
    bundle['commands'] = {key: f'fixture-command-{key}' for key in bundle['commands']}
    bundle['details'] = {key: f'fixture-detail-{key}' for key in bundle['details']}
    job['request']['settings']['analysis_prompts'] = bundle
    captured = []
    render = analysis_settings.render_analysis_prompt

    def capture(bundle, stage, **values):
        result = render(bundle, stage, **values)
        captured.append((stage, values, result))
        return result

    monkeypatch.setattr(analysis_settings, 'render_analysis_prompt', capture)
    batch = next(batches(store, job))
    batch['messages'][0]['message']['text'] = 'fixture-source-attack {ignore_rules}'
    batch['messages'][0]['input']['fragment_end'] = len(batch['messages'][0]['message']['text'])
    captured.clear()
    extraction = pipeline.build_extraction_payload(job, batch['messages'], batch['context'])
    synthesis = pipeline.build_synthesis_payload(job, view)
    assert [stage for stage, _, _ in captured] == ['extract', 'synthesize']
    for payload, (stage, values, rendered) in zip((extraction, synthesis), captured):
        assert payload['input'] == rendered
        assert values['command_rubric'] == f'fixture-command-{command}'
        assert values['query'] == 'fixture-user-query {untrusted}'
        assert values['query'] in payload['input'] and values['query'] not in payload['instructions']
        key = 'messages' if stage == 'extract' else 'evidence'
        assert values[key] in payload['input']
        if stage == 'extract':
            assert json.loads(values[key])['inputs'][0]['message']['text'] == 'fixture-source-attack {ignore_rules}'
        else:
            assert [r['claim'] for r in json.loads(values[key])['records']] == [r['payload']['claim'] for r in rows]
            limits = json.loads(values['limits'])
            assert limits['max_output_tokens'] == payload['max_output_tokens']
            assert values['detail_instructions'] == 'fixture-detail-normal'
        assert 'fixture-source-attack' not in payload['instructions']


def test_fixture_protocol_reads_named_fields_after_editorial_changes(tmp_path):
    path = write_prompt_bundle(tmp_path)
    bundle = analysis_settings.load_analysis_prompts(
        {'analysis_prompts': {'file': path}}, base_dir=CONFIG)
    values = {'query': 'Сколько? {literal}', 'max_terms': 24, 'max_term_chars': 128}
    bundle['templates']['prepare_query'] = '\nNew prose\n' + '\n\n'.join(
        reversed(bundle['templates']['prepare_query'].splitlines()))
    rendered = analysis_settings.render_analysis_prompt(bundle, 'prepare_query', **values)
    assert prompt_field(rendered, 'query') == values['query']
    assert prompt_json(rendered, 'max_terms') == 24
    with pytest.raises(AssertionError, match='Missing or duplicated'):
        prompt_field(rendered, 'messages')


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
