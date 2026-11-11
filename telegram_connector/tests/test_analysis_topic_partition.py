"""Current schema and validation must share a consistent event partition."""
import copy
import itertools
import json
import pytest
from telegram_connector.tests.analysis_fixtures import prompt_json
from telegram_connector import analysis_evidence as e, analysis_pipeline as p
from telegram_connector.tests.test_analysis_manifest import manifest_job
from telegram_connector.tests.test_analysis_extraction_accounting import accounting_batch
from telegram_connector.tests.test_analysis_owned_records import response


def edge(left,right,relation='complements'):
    return dict(left_id=left,right_id=right,relation=relation)


def test_consistent_partition_never_contains_an_explicit_negative():
    relations=[edge('a','b'),edge('b','c'),edge('a','c','unrelated')]
    assert e.topic_components(['a','b','c'],relations)==[['a','b','c']]
    for order in itertools.permutations(relations):
        groups=e.topic_components(['c','b','a'],order,consistent=True)
        assert groups==[['a','b'],['c']]
        assert sorted(itertools.chain.from_iterable(groups))==['a','b','c']
        assert not any({'a','c'}<=set(group) for group in groups)


def test_current_validator_allows_transitive_support_without_decorative_bridge_ids():
    relations=[edge('a','b'),edge('b','c')]
    with pytest.raises(ValueError,match='disconnected'):
        e.validate_topic_group(['a','c'],relations)
    e.validate_topic_group(['a','c'],relations,scope_identities=['a','b','c'])
    with pytest.raises(ValueError,match='unrelated'):
        e.validate_topic_group(['a','c'],relations+[edge('a','c','unrelated')],scope_identities=['a','b','c'])


@pytest.mark.parametrize('command',['topics','qa','search'])
def test_digest_aggregation_does_not_reopen_a_nonpublishable_topics_input(manifest_job,command):
    store,job=manifest_job
    batch=accounting_batch(store,job,['Нейродайджест за неделю\nПредставлены агент ALPHA и модель BETA.'])
    store.conn.execute('UPDATE analysis_jobs SET command=? WHERE job_id=?',(command,job['job_id']))
    doc=response(batch)
    result=e.classify_extraction_document(store,job['job_id'],batch['batch_id'],generation=job['generation'],
        document=doc,fragments=batch['messages'],selected=batch['messages'])
    assert result['input_accounting_complete'] and len(result['records'])==1
    assert e.record_source_eligible(store,store.get_job(job['job_id']),result['records'][0]['payload'])==(command!='topics')


def test_current_duplicate_pointer_normalization_keeps_all_source_guards(manifest_job):
    from telegram_connector.tests.test_analysis_synthesis_contract import case
    store,job=manifest_job;job,rows,view,doc=case(store,job)
    before=copy.deepcopy(doc)
    doc['items'][0]['evidence_ids']=[rows[0]['record_id']]*2
    result=p._synthesis_result(job,view,doc)
    assert result['items'][0]['evidence_ids']==[rows[0]['record_id']]
    doc['items'][0]['evidence_ids']+=['foreign']
    with pytest.raises(ValueError,match='synthesis_evidence_scope'):
        p._synthesis_result(job,view,doc)


def test_partition_is_owned_before_alias_encoding(manifest_job,monkeypatch):
    from telegram_connector.tests.test_analysis_synthesis_contract import case
    store,job=manifest_job;job,rows,view,doc=case(store,job)
    third=copy.deepcopy(rows[0]);third['record_id']='z-third';third['payload']['claim']='Ещё одно условие ALPHA.'
    view['records'].append(third)
    ids=[r['record_id'] for r in view['records']]
    ordered=sorted(ids);view['relations']=[edge(ordered[0],ordered[1]),edge(ordered[1],ordered[2]),edge(ordered[0],ordered[2],'unrelated')]
    aliases=dict(zip(ordered,['r999','r1000','r1001']))
    monkeypatch.setattr(p,'_record_aliases',lambda ids:aliases)
    payload=p.build_synthesis_payload(job,view)
    data=prompt_json(payload['input'], 'evidence');reverse={v:k for k,v in aliases.items()}
    assert [[reverse[key] for key in group] for group in data['topic_components']]==e.topic_components(ids,view['relations'],consistent=True)


def test_digest_primary_requires_support_for_every_claimed_event(manifest_job):
    store,job=manifest_job
    batch=accounting_batch(store,job,['Нейродайджест за неделю\nПредставлены ALPHA и BETA.','Выпущена ALPHA.'])
    doc=response(batch)
    first=doc['input_dispositions']['i0000']['records'][0]
    first.update(supporting_input_ids=['i0001'])
    result=e.classify_extraction_document(store,job['job_id'],batch['batch_id'],generation=job['generation'],
        document=doc,fragments=batch['messages'],selected=batch['messages'])
    assert result['input_accounting_complete'] and len(result['records'])==2
    job = store.get_job(job['job_id'])
    assert not e.record_source_eligible(store, job, result['records'][0]['payload'])
    assert e.record_source_eligible(store, job, result['records'][1]['payload'])


def test_verified_duplicate_is_kept_before_a_conflicting_complement_bridge():
    relations=[edge('a','b'),edge('b','c','duplicate'),edge('a','c','unrelated')]
    assert e.topic_components(['a','b','c'],relations,consistent=True)==[['a'],['b','c']]


def test_automatic_contradiction_expansion_stays_inside_the_same_partition(manifest_job):
    from telegram_connector.tests.test_analysis_synthesis_contract import case
    store,job=manifest_job;job,rows,view,doc=case(store,job)
    third=copy.deepcopy(rows[0]);third['record_id']='z-third'
    view['records'].append(third)
    ids=[row['record_id'] for row in view['records']]
    view['relations']=[edge(ids[0],ids[1],'duplicate'),edge(ids[1],ids[2],'contradicts'),edge(ids[0],ids[2],'unrelated')]
    doc['items'][0]['evidence_ids']=[ids[1]]
    result=p._synthesis_result(job,view,doc)
    assert result['items'][0]['evidence_ids']==[ids[1]]
    assert not result['items'][0]['contradictions']
