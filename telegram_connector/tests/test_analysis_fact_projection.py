"""Private model projections keep service metadata and citations canonical."""
import copy,json
import pytest
from telegram_connector import analysis_pipeline as pipeline
from telegram_connector.tests.test_analysis_manifest import manifest_job
from telegram_connector.tests.test_analysis_extraction_accounting import accounting_batch
from telegram_connector.tests.test_analysis_synthesis_contract import case


@pytest.mark.parametrize('command',['topics','qa','search'])
def test_extraction_keeps_explicit_dates_but_not_publication_metadata(manifest_job,command):
    store,job=manifest_job
    job=copy.deepcopy(job);job['command']=command
    if command=='search':job['request']['request'].update(mode='full',query='Когда релиз?')
    batch=accounting_batch(store,job,['Релиз запланирован на 4 июля.'])
    batch['context']=[copy.deepcopy(batch['messages'][0])]
    batch['context'][0]['input']['role']='context'
    before=copy.deepcopy(batch)
    payload=pipeline.build_extraction_payload(job,batch['messages'],batch['context'])
    wire=json.loads(payload['input'].split('Сообщения: ',1)[1])
    for fragment in wire['inputs']:
        assert not {'date_utc','content_digest','fragment_start','fragment_end','step_key'} & fragment['input'].keys()
        assert 'date_utc' not in fragment['message']
        assert '4 июля' in fragment['message']['text']
    context=json.loads(payload['input'].split('Контекст: ',1)[1].split('\nСообщения: ',1)[0])
    assert all('date_utc' not in f['input'] and 'date_utc' not in f['message'] for f in context)
    assert batch==before and 'date_utc' in batch['messages'][0]['input']


def test_synthesis_transmits_all_facts_and_exact_quotes_with_shared_source_identity(manifest_job):
    store,job=manifest_job
    job,rows,view,document=case(store,job)
    before=copy.deepcopy(view)
    payload=pipeline.build_synthesis_payload(job,view)
    from telegram_connector.tests.analysis_fixtures import expand_wire_quotes
    raw=json.loads(payload['input'].split('Опорные записи: ',1)[1])
    assert raw['source_table'] and raw['quote_texts']
    assert 'groups' not in raw and raw['topic_components']
    wire=expand_wire_quotes(raw)
    assert all('related_message_refs' not in r['payload'] and 'topic_key' not in r['payload'] for r in wire['records'])
    for original,projected in zip(view['records'],wire['records']):
        for field in ('claim','details','entities','assertion_kind','support_status'):
            assert projected['payload'][field]==original['payload'][field]
        assert not {'conditions', 'exceptions', 'event_at'} & projected['payload'].keys()
        for old,ref in zip(original['payload']['source_refs'],projected['payload']['source_refs']):
            assert all(ref[key]==old[key] for key in ('channel_id','message_id','date_utc','role'))
            assert ref['quote']==old['quote'] and 'quote_ref' not in ref and 'source_id' not in ref
    assert view==before and all(ref['quote'] for row in rows for ref in row['payload']['source_refs'])


def test_context_provenance_still_changes_model_and_cache_identity(manifest_job):
    store,job=manifest_job
    batch=accounting_batch(store,job,['Выпущена ALPHA.'])
    context=[copy.deepcopy(batch['messages'][0])]
    context[0]['input']['role']='context'
    first=pipeline.build_extraction_payload(job,batch['messages'],context)
    context[0]['input']['content_digest']='changed-version'
    second=pipeline.build_extraction_payload(job,batch['messages'],context)
    assert first!=second
    assert 'changed-version' not in second['input']


def test_repeated_source_text_does_not_displace_supported_facts(manifest_job):
    from telegram_connector.tests.test_analysis_relations import records_fixture
    from telegram_connector.tests.test_analysis_merge import merge
    from telegram_connector.tests.test_analysis_transport import Response
    from telegram_connector.tests.analysis_fixtures import expand_wire_quotes
    store,job=manifest_job
    text=' '.join(f'Выпущен продукт {i}.' for i in range(8))+'\n'+('Технический контекст. '*900)
    records=records_fixture(store,job,[dict(claim=f'Выпущен продукт {i}.',text=text) for i in range(8)])
    before=copy.deepcopy(records)
    state=merge(store,job,lambda *a,**k:Response({'input_tokens':100}))
    assert state['route']=='direct'
    view=pipeline._synthesis_evidence(store,store.get_job(job['job_id']))
    assert view['coverage']['ranking_scope']=='complete' and len(view['records'])==8
    data=json.loads(pipeline.build_synthesis_payload(store.get_job(job['job_id']),view)['input'].split('Опорные записи: ',1)[1])
    expanded=expand_wire_quotes(data)
    assert len(data['quote_texts'])==1
    assert all(row['payload']['source_refs'][0]['quote']==text for row in expanded['records'])
    assert records==before


def test_relation_source_table_is_lossless_and_reuses_metadata(manifest_job):
    from telegram_connector import analysis_evidence as evidence
    from telegram_connector.tests.analysis_fixtures import expand_wire_quotes, semantic_fact_payload
    store, job = manifest_job
    job, rows, view, document = case(store, job)
    original = rows[0]['payload']
    records = {f'fact-{i:03}': copy.deepcopy(original) for i in range(100)}
    pairs = {f'p{i:03}': ['fact-000', f'fact-{i:03}'] for i in range(1, 100)}
    wire = pipeline.build_relation_data(pairs, records)
    expanded = expand_wire_quotes(wire)
    assert len(wire['source_table']) == len({evidence.canonical_json(pipeline._synthesis_source_metadata(ref))
        for ref in original['source_refs']})
    assert all(semantic_fact_payload(row['payload']) == semantic_fact_payload(original)
               for row in expanded['records'])
    assert all(row['payload']['entities'] == original['entities'] and
               row['payload']['topic_key'] == original['topic_key'] for row in expanded['records'])
    naive = copy.deepcopy(wire)
    naive['records'], naive['quote_texts'] = pipeline._compact_record_quotes([
        {'record_id': row['record_id'], 'payload': original} for row in wire['records']])
    naive.pop('source_table')
    assert len(evidence.canonical_json(wire)) < len(evidence.canonical_json(naive))
