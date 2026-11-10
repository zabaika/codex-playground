"""Global limitations have a deterministic owner; source wording stays intact."""
import copy

import pytest

from telegram_connector import analysis_pipeline as pipeline
from telegram_connector.tests.test_analysis_manifest import manifest_job
from telegram_connector.tests.test_analysis_relations import records_fixture


def synthesis_case(store, job, claim='Uno откроет офис 1 октября.'):
    records = records_fixture(store, job, [{'claim': claim, 'text': claim,
        'details': []}])
    data = {'records': records, 'relations': [], 'groups': [], 'reasons': [],
        'partial': False, 'coverage': {'selected_messages': 1, 'processed_messages': 1,
            'selection_mode': 'all_messages', 'known_skipped': 0}}
    document = {'summary': claim, 'items': [{'title': 'Офис Uno', 'text': claim,
        'evidence_ids': [records[0]['record_id']],
        'contradictions': []}], 'limitations': [],
        'insufficient_information': False, 'saturated': False}
    return data, document


def test_new_synthesis_schema_leaves_global_limitations_to_application(manifest_job):
    store, job = manifest_job
    data, document = synthesis_case(store, job)
    payload = pipeline.build_synthesis_payload(job, data)
    assert payload['text']['format']['schema']['properties']['limitations']['maxItems'] == 0
    document['limitations'] = ['Большинство сведений — оценки источников.']
    with pytest.raises(ValueError, match='evidence_array_size'):
        pipeline._synthesis_result(job, data, document)


@pytest.mark.parametrize('claim', ['Uno откроет офис 1 октября.',
    'По оценке автора, офис откроется 1 октября.',
    'Компания заявляет, что офис откроется 1 октября.'])
def test_source_wording_is_preserved_without_added_attribution(manifest_job, claim):
    store, job = manifest_job
    data, document = synthesis_case(store, job, claim)
    original = copy.deepcopy(data)
    result = pipeline._synthesis_result(job, data, document)
    assert result['summary'] == claim and result['items'][0]['text'] == claim
    assert not result['limitations'] and data == original
    payload = pipeline.build_synthesis_payload(job, data)
    assert 'Не добавляй атрибуции или оговорки' in payload['input']
    assert 'Неизвестную дату уточняй только при явном вопросе' in payload['input']
    assert 'не по вопросу внутри цитируемого сообщения' in payload['input']
