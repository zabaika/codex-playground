"""Explicit current fixtures; negative responses pass through without repair."""
import copy
import json

from telegram_connector import analysis_evidence, analysis_settings

CURRENT_EVIDENCE_FORMAT = analysis_evidence.ADDRESSED_EVIDENCE_FORMATS[analysis_settings.ANALYSIS_SCHEMA_VERSION]
CURRENT_SYNTHESIS_FORMAT = analysis_evidence.synthesis_format()['name']


def extraction_inputs(payload):
    text = payload['input']
    if 'Некорректный результат: ' in text:
        return json.loads(text.split('Некорректный результат: ', 1)[1].split('\nОшибки валидации:', 1)[0])['inputs']
    return json.loads(text.split('Сообщения: ', 1)[1])['inputs']


def owned_response_from_records(payload, document):
    """Author a mock response from fixture facts, before applying fault mutations."""
    inputs = extraction_inputs(payload)
    text = payload['input']
    if 'Некорректный результат: ' in text:
        repair = json.loads(text.split('Некорректный результат: ', 1)[1].split('\nОшибки валидации:', 1)[0])
        context = repair['context']
    else:
        context = json.loads(text.split('Контекст: ', 1)[1].split('\nСообщения: ', 1)[0])
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
