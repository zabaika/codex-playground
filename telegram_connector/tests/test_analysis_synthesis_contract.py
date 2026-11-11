"""Event boundaries, addressed selection and final validation share their owner."""
import copy

import pytest

from telegram_connector.tests.analysis_fixtures import prompt_json
from telegram_connector import analysis_evidence as evidence, analysis_pipeline as pipeline
from telegram_connector.tests.test_analysis_manifest import manifest_job
from telegram_connector.tests.test_analysis_relations import records_fixture


def case(store, job, *, related=True):
    records = records_fixture(store, job, [
        {'claim': 'Выпущена ALPHA.', 'entities': ['ALPHA'], 'details': ['Требуется 8 ГБ.']},
        {'claim': 'ALPHA поддерживает новый режим.', 'entities': ['ALPHA'],
         'details': ['Доступ по подписке.', 'Режим не работает offline.']}])
    edge = {'left_id': records[0]['record_id'], 'right_id': records[1]['record_id'], 'relation': 'complements'}
    view = {'records': records, 'relations': [edge] if related else [], 'reasons': [], 'partial': False, 'coverage': {}}
    document = {'summary': 'Обновления ALPHA.', 'items': [{'title': 'Выпущена ALPHA с новым режимом',
        'text': 'Требуется 8 ГБ. Доступ по подписке; режим не работает offline.', 'contradictions': [],
        'evidence_ids': [r['record_id'] for r in records]}],
        'limitations': [], 'insufficient_information': False, 'saturated': False}
    return job, records, view, document


def test_synthesis_keeps_authored_text_without_automatic_detail_append(manifest_job):
    store, job = manifest_job
    job, records, view, document = case(store, job)
    payload = pipeline.build_synthesis_payload(job, view)
    result = pipeline._synthesis_result(job, view, document)
    assert result['items'][0]['text'] == document['items'][0]['text']
    assert not {'conditions', 'exceptions', 'detail_groups'} & result['items'][0].keys()


@pytest.mark.parametrize('relation', [None, 'unrelated'])
def test_topics_cannot_join_disconnected_or_unrelated_events(manifest_job, relation):
    store, job = manifest_job
    job, records, view, document = case(store, job, related=relation is not None)
    if relation:
        view['relations'][0]['relation'] = relation
    with pytest.raises(ValueError, match='topics_group_'):
        pipeline._synthesis_result(job, view, document)


def test_partial_does_not_mean_topics_with_supported_items_have_no_information(manifest_job):
    store, job = manifest_job
    job, records, view, document = case(store, job)
    view.update(partial=True, reasons=['extraction_coverage_incomplete'])
    document['insufficient_information'] = True
    result = pipeline._synthesis_result(job, view, document)
    assert result['partial'] and result['reasons'] == ['extraction_coverage_incomplete']
    assert not result['insufficient_information']






def test_short_addresses_round_trip_to_canonical_records(manifest_job):
    import json
    store, job = manifest_job
    job, records, view, document = case(store, job)
    original = copy.deepcopy(view)
    view['groups'] = [{'node_id': 'group', 'evidence_ids': [r['record_id'] for r in records]}]
    payload = pipeline.build_synthesis_payload(job, view)
    wire = prompt_json(payload['input'], 'evidence')
    aliases = {r['record_id']: f'r{index:03}' for index, r in enumerate(sorted(records, key=lambda r:r['record_id']),1)}
    assert [r['record_id'] for r in wire['records']] == [aliases[r['record_id']] for r in records]
    assert wire['relations'][0]['left_id'] == aliases[records[0]['record_id']]
    assert wire['topic_components'] == [sorted(aliases.values())]
    document['items'][0]['evidence_ids'] = [aliases[r['record_id']] for r in records]
    result = pipeline._synthesis_result(job, view, document)
    assert result['items'][0]['evidence_ids'] == [r['record_id'] for r in records]
    assert view['records'] == original['records'] and view['relations'] == original['relations']


def test_repair_reports_all_bad_items_in_one_pass(manifest_job):
    store, job = manifest_job
    job, records, view, document = case(store, job, related=False)
    document['items'].append(copy.deepcopy(document['items'][0]))
    document['items'][1]['evidence_ids'] = ['foreign']
    with pytest.raises(ValueError) as error: pipeline._synthesis_result(job, view, document)
    assert 'items[0]' in str(error.value) and 'topics_group_' in str(error.value)
    assert 'items[1]' in str(error.value) and 'synthesis_evidence_scope' in str(error.value)


def test_wire_blocks_disconnected_topics_and_impossible_detail_groups(manifest_job):
    store, job = manifest_job
    job, rows, view, doc = case(store, job, related=False)
    payload = pipeline.build_synthesis_payload(job, view)
    aliases = pipeline._record_aliases(r['record_id'] for r in rows)
    for item in doc['items']: item['evidence_ids'] = [aliases[key] for key in item['evidence_ids']]
    schema = payload['text']['format']['schema']
    with pytest.raises(ValueError): evidence.validate_shape(doc, schema)
    doc['items'][0]['evidence_ids'] = [aliases[rows[0]['record_id']]]
    evidence.validate_shape(doc, schema)
    doc['items'][0]['detail_groups'] = [{'field': 'conditions',
        'refs': [{'record_id': aliases[r['record_id']], 'index': 0} for r in rows],
        'representative': {'record_id': aliases[rows[0]['record_id']], 'index': 0}}]
    with pytest.raises(ValueError): evidence.validate_shape(doc, schema)


def test_private_metadata_removal_does_not_relax_detail_equivalence(manifest_job):
    import json
    store, job = manifest_job
    job, records, view, document = case(store, job)
    for row in records:
        row['payload']['details'] = ['Только Pro.']
    payload = pipeline.build_synthesis_payload(job, view)
    wire = prompt_json(payload['input'], 'evidence')
    assert wire['equivalent_detail_groups'] == []
    assert 'detail_groups' not in payload['text']['format']['schema']['properties']['items']['items']['properties']
    assert all('schema_version' not in row for row in wire['records'])
