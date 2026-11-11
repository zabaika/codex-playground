"""Explicit current fixtures; negative responses pass through without repair."""
import copy
import json
from pathlib import Path
import tomllib

from telegram_connector import analysis_evidence, analysis_settings

CURRENT_EVIDENCE_FORMAT = analysis_evidence.ADDRESSED_EVIDENCE_FORMATS[analysis_settings.ANALYSIS_SCHEMA_VERSION]
CURRENT_SYNTHESIS_FORMAT = analysis_evidence.synthesis_format()['name']


def write_prompt_bundle(directory):
    """Explicit mock-provider protocol, separate from editable production prose."""
    source = Path(__file__).resolve().parents[1] / 'config/analysis_prompts.toml'
    bundle = tomllib.loads(source.read_text())['analysis_prompts']
    bundle['templates'] = {
        stage: '\n'.join(f'<fixture:{key}>{{{key}}}</fixture:{key}>'
                         for key in sorted(fields))
        for stage, fields in analysis_settings.PROMPT_FIELDS.items()}
    bundle['commands'] = {key: f'fixture-command-{key}' for key in bundle['commands']}
    bundle['details'] = {key: f'fixture-detail-{key}' for key in bundle['details']}
    lines = ['[analysis_prompts]']
    for key in ('version', 'system_instructions', 'shared_prefix'):
        lines.append(f'{key} = {json.dumps(bundle[key], ensure_ascii=False)}')
    for section in ('templates', 'commands', 'details'):
        lines.append(f'[analysis_prompts.{section}]')
        lines.extend(f'{key} = {json.dumps(value, ensure_ascii=False)}'
                     for key, value in bundle[section].items())
    destination = directory / 'fixture-prompts.toml'
    destination.write_text('\n'.join(lines) + '\n')
    return str(destination)


def prompt_field(payload, key):
    """Read one field from the actual serialized test-provider request."""
    text = payload['input'] if isinstance(payload, dict) else payload
    start, end = f'<fixture:{key}>', f'</fixture:{key}>'
    assert text.count(start) == 1, f'Missing or duplicated fixture field: {key}'
    return text.split(start, 1)[1].split(end, 1)[0]


def prompt_json(payload, key):
    return json.loads(prompt_field(payload, key))


def extraction_inputs(payload):
    text = payload['input']
    if '<fixture:invalid_output>' in text:
        return prompt_json(payload, 'invalid_output')['inputs']
    return prompt_json(payload, 'messages')['inputs']


def owned_response_from_records(payload, document):
    """Author a mock response from fixture facts, before applying fault mutations."""
    inputs = extraction_inputs(payload)
    text = payload['input']
    if '<fixture:invalid_output>' in text:
        repair = prompt_json(payload, 'invalid_output')
        context = repair['context']
    else:
        context = prompt_json(payload, 'context')
    supplied = [*inputs, *context]
    def resolve(ref):
        matches = [fragment['input_id'] for fragment in supplied
            if (fragment['input']['channel_id'], fragment['input']['message_id']) ==
               (ref['channel_id'], ref['message_id'])
            and fragment['message']['text'] == ref.get('quote')]
        if not matches:
            raise ValueError('fixture_source_not_supplied_whole')
        return matches
    records = {}
    for index, record in enumerate(document['records']):
        ids = list(dict.fromkeys(key for ref in record['source_refs'] for key in resolve(ref)))
        if not ids:
            raise ValueError('fixture_source_missing')
        records[index] = ids
    decisions = {}
    represented = set()
    for fragment in inputs:
        key = fragment['input_id']
        owned = []
        for index, record in enumerate(document['records']):
            if key not in records[index]:
                continue
            value = copy.deepcopy(record)
            refs = value.pop('source_refs')
            related = value.pop('related_message_refs')
            value['supporting_input_ids'] = [identity for identity in records[index] if identity != key]
            value['related_input_ids'] = list(dict.fromkeys(
                row['input_id'] for ref in related for row in supplied
                if (row['input']['channel_id'], row['input']['message_id']) == (ref['channel_id'], ref['message_id'])))
            if any(not any((row['input']['channel_id'], row['input']['message_id']) ==
                    (ref['channel_id'], ref['message_id']) for row in supplied) for ref in related):
                raise ValueError('fixture_related_source_not_supplied')
            owned.append(value)
            represented.add(index)
        decisions[key] = dict(status='represented' if owned else 'irrelevant', records=owned,
            retained_record_ids=[], reason='' if owned else 'Нерелевантно согласно fixture.')
    if len(represented) != len(document['records']):
        raise ValueError('fixture_record_without_selected_source')
    return {**{key:copy.deepcopy(value) for key,value in document.items() if key not in {'records', 'saturated'}},
            'saturated': document['saturated'], 'input_dispositions': decisions}


def expand_wire_quotes(data):
    """Restore exact private quote references for payload equivalence assertions."""
    value = copy.deepcopy(data)
    for record in value.get('records', []):
        if 'payload' not in record:
            identity = record['record_id']
            payload = {key:item for key,item in record.items() if key != 'record_id'}
            record.clear()
            record.update(record_id=identity, record_type=payload['record_type'], payload=payload)
        for ref in record['payload']['source_refs']:
            if 'source_id' in ref:
                metadata = copy.deepcopy(value['source_table'][ref.pop('source_id')])
                if 'published_at' in metadata:
                    metadata['date_utc'] = metadata.pop('published_at')
                ref.update(metadata)
            if 'quote_ref' in ref:
                ref['quote'] = value['quote_texts'][ref.pop('quote_ref')]
    return value



def semantic_fact_payload(payload, *, synthesis=False):
    value = copy.deepcopy(payload)
    for field in ('schema_version', 'reported_at', 'related_record_ids'):
        value.pop(field, None)
    for ref in value['source_refs']:
        for field in ('content_digest', 'fragment_start', 'fragment_end', 'text_view_version'):
            ref.pop(field, None)
    if synthesis:
        value.pop('related_message_refs', None)
        value.pop('topic_key', None)
    return value



def synthesis_fixture_groups(data):
    if 'groups' in data:
        return data['groups']
    return [{'evidence_ids':ids} for ids in data.get('topic_components', [])]
