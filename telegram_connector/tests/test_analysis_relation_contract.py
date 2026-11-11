"""Readable wire aliases never change canonical endpoints or duplicate support."""
import copy

import pytest

from telegram_connector.tests.analysis_fixtures import prompt_json
from telegram_connector import analysis_pipeline as pipeline, analysis_evidence as evidence
from telegram_connector.tests.test_analysis_manifest import manifest_job
from telegram_connector.tests.test_analysis_relations import records_fixture


def test_compact_aliases_are_stable_for_residual_and_do_not_mutate_records():
    records = {key: {'claim': key, 'entities': [], 'assertion_kind': 'reported', 'details': [], 'related_record_ids': [], 'source_refs': []} for key in ('c' * 32, 'a' * 32, 'b' * 32)}
    original = copy.deepcopy(records)
    pairs = {'p001': ['a' * 32, 'b' * 32], 'p002': ['b' * 32, 'c' * 32]}
    full = pipeline.build_relation_data(pairs, records)
    residual = pipeline.build_relation_data({'p002': pairs['p002']}, records, repair_index=1)
    assert full['pairs'] == [{'pair_id': 'p001', 'left_id': 'r001', 'right_id': 'r002', 'duplicate_allowed': False, 'left_claim': 'a' * 32, 'right_claim': 'b' * 32},
                             {'pair_id': 'p002', 'left_id': 'r002', 'right_id': 'r003', 'duplicate_allowed': False, 'left_claim': 'b' * 32, 'right_claim': 'c' * 32}]
    assert residual['pairs'] == [full['pairs'][1]]
    assert [r['record_id'] for r in residual['records']] == ['r002', 'r003']
    assert records == original


@pytest.mark.parametrize('mismatch', ['subject', 'constraints', 'unknown_subject'])
def test_wrong_duplicate_is_residual_without_losing_good_decisions(manifest_job, mismatch):
    store, job = manifest_job
    values = [{'claim': 'Модель ALPHA.', 'entities': ['ALPHA'], 'details': []},
              {'claim': 'Модель BETA.', 'entities': ['BETA'], 'details': []},
              {'claim': 'Модель GAMMA.', 'entities': ['GAMMA'], 'details': []}]
    if mismatch == 'constraints':
        values[1]['entities'] = ['ALPHA']
        values[1]['details'] = ['Не менее 16 ГБ.']
    elif mismatch == 'unknown_subject':
        values[0]['entities'] = values[1]['entities'] = []
    rows = records_fixture(store, job, values)
    ids = [r['record_id'] for r in rows]
    pairs = {'p001': ids[:2], 'p002': ids[1:]}
    response = {'decisions': [{'pair_id': 'p001', 'relation': 'duplicate', 'reason': 'Ошибка fixture.'},
                             {'pair_id': 'p002', 'relation': 'unrelated', 'reason': 'Разные события.'}], 'saturated': False}
    result = evidence.classify_relation_document(response, pairs, max_bytes=100000,
        record_payloads={r['record_id']: r['payload'] for r in rows})
    assert result['unresolved_pair_ids'] == ['p001']
    assert set(result['accepted_decisions']) == {'p002'}
    assert result['diagnostics']['semantic_rejected'] == 1
    assert any(code.startswith('relation_') for code in result['diagnostics']['codes'])
    structural = evidence.classify_relation_document(response, pairs, max_bytes=100000)
    assert len(structural['accepted_decisions']) == 2


def test_compatible_named_duplicate_keeps_canonical_pair(manifest_job):
    store, job = manifest_job
    rows = records_fixture(store, job, [{'claim': 'Выпущена ALPHA.', 'entities': [' Alpha ']},
                                      {'claim': 'ALPHA выпущена.', 'entities': ['ALPHA']}])
    pair = [r['record_id'] for r in rows]
    value = {'decisions': [{'pair_id': 'p001', 'relation': 'duplicate', 'reason': 'Один релиз.'}], 'saturated': False}
    result = evidence.classify_relation_document(value, {'p001': pair}, max_bytes=100000,
        record_payloads={r['record_id']: r['payload'] for r in rows})
    assert not result['unresolved_pair_ids']
    assert result['accepted_decisions']['p001']['left_id'] == pair[0]


def test_semantic_repair_keeps_good_pairs_and_stable_aliases(manifest_job):
    import json
    from telegram_connector.tests.test_analysis_relations import link
    from telegram_connector.tests.test_analysis_transport import Response
    from telegram_connector.tests.test_analysis_evidence import model_response
    store, job = manifest_job
    records_fixture(store, job, [
        {'claim': 'Uno выпустил ALPHA.', 'entities': ['Uno'], 'details': []},
        {'claim': 'Dos выпустил BETA.', 'entities': ['Dos'], 'details': []},
        {'claim': 'Uno выпустил GAMMA.', 'entities': ['Uno'], 'details': []}])
    sent, bad_pair = [], []
    def provider(req, timeout):
        if req.full_url.endswith('input_tokens'):
            return Response({'input_tokens': 100})
        wire = prompt_json(json.loads(req.data)['input'], 'records')
        sent.append(wire)
        assert len(wire['pairs']) == (3 if len(sent) == 1 else 1)
        if len(sent) == 1:
            subjects = {r['record_id']: r['payload']['entities'] for r in wire['records']}
            bad_pair.append(next(pair for pair in wire['pairs'] if subjects[pair['left_id']] != subjects[pair['right_id']]))
        decisions = [{'pair_id': pair['pair_id'], 'relation': 'duplicate'
            if len(sent) == 1 and pair == bad_pair[0] else 'unrelated', 'reason': 'Authored semantic repair fixture.'}
            for pair in wire['pairs']]
        return Response(model_response({'decisions': decisions, 'saturated': False}))
    assert not link(store, job, provider)['partial']
    assert len(sent) == 2
    assert sent[1]['pairs'] == bad_pair
    assert len(list(store.iter_link_decisions(job['job_id']))) == 3
    before = store.get_job(job['job_id'])['provider_requests']
    link(store, job, lambda *a, **k: pytest.fail('Accepted decisions cannot be requested again'))
    assert store.get_job(job['job_id'])['provider_requests'] == before


def test_model_schema_forbids_unsafe_duplicate_but_keeps_complements(manifest_job):
    store, job = manifest_job
    rows = records_fixture(store, job, [{'claim': 'ALPHA вышла.', 'entities': ['ALPHA']},
        {'claim': 'BETA вышла.', 'entities': ['BETA']}, {'claim': 'ALPHA доступна.', 'entities': ['ALPHA']}])
    ids = [r['record_id'] for r in rows]
    pairs = {'p001': ids[:2], 'p002': [ids[0], ids[2]]}
    payloads = {r['record_id']: r['payload'] for r in rows}
    schema = evidence.relation_format(pairs, record_payloads=payloads)['schema']
    def doc(key, relation): return {'decisions': [{'pair_id': key, 'relation': relation, 'reason': 'fixture'}], 'saturated': False}
    with pytest.raises(ValueError): evidence.validate_shape(doc('p001', 'duplicate'), schema)
    evidence.validate_shape(doc('p001', 'complements'), schema)
    evidence.validate_shape(doc('p002', 'duplicate'), schema)
    assert all(value <= evidence.MODEL_SCHEMA_LIMITS[key] for key, value in evidence.model_schema_usage(schema).items())
