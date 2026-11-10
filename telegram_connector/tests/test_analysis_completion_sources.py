"""Completion reservations must describe the same frontier as the reducer."""
import pytest
from telegram_connector.tests.test_analysis_manifest import manifest_job,change_setting
from telegram_connector.tests.test_analysis_relations import records_fixture
from telegram_connector.analysis_store import BudgetExceededError


@pytest.mark.parametrize('command', ['topics','qa','search'])
def test_current_completion_roots_match_source_policy_without_deleting_evidence(manifest_job, command):
    store,job=manifest_job
    records_fixture(store,job,[dict(claim=f'Факт {i}.',text=('Нейродайджест за неделю (#130)\n' if i<10 else '')+f'Факт {i}.') for i in range(12)])
    store.conn.execute('UPDATE analysis_jobs SET command=? WHERE job_id=?',(command,job['job_id']))
    job=store.get_job(job['job_id'])
    batch=store.plan_batch(job['job_id'],generation=job['generation'],step_key='completion-link',kind='link',payload={})
    reserve=store._completion_reserve_no_tx(job,batch,request_kind='inference')
    assert reserve['roots']==(2 if command=='topics' else 12)
    assert store.get_job(job['job_id'])['evidence_records']==12




@pytest.mark.parametrize('standalone', [2,3])
def test_completion_keeps_exact_reserve_and_rejects_one_more_eligible_root(manifest_job,standalone):
    store,job=manifest_job
    records_fixture(store,job,[dict(claim=f'Факт {i}.',text=('Нейродайджест за неделю (#130)\n' if i<10 else '')+f'Факт {i}.') for i in range(10+standalone)])
    change_setting(store,job,'analysis','merge_fan_in',2)
    change_setting(store,job,'analysis','max_llm_calls',3)
    change_setting(store,job,'analysis','max_provider_requests',6)
    batch=store.plan_batch(job['job_id'],generation=job['generation'],step_key='exact-count',kind='link',payload={})
    if standalone == 2:
        store.reserve_provider_attempt(job['job_id'],batch['batch_id'],generation=job['generation'],attempt_id='at-limit',request_kind='token_count')
        assert store.get_job(job['job_id'])['provider_requests']==1
    else:
        with pytest.raises(BudgetExceededError,match='max_llm_calls'):
            store.reserve_provider_attempt(job['job_id'],batch['batch_id'],generation=job['generation'],attempt_id='over-limit',request_kind='token_count')
        assert store.get_job(job['job_id'])['provider_requests']==0


@pytest.mark.parametrize('command,expected', [('topics', 1), ('qa', 66), ('search', 66)])
def test_relation_proposals_use_only_the_same_current_eligible_frontier(manifest_job,command,expected):
    from telegram_connector import analysis_pipeline as pipeline
    store,job=manifest_job
    records_fixture(store,job,[dict(claim=f'Факт {i}.',text=('Нейродайджест за неделю (#130)\n' if i<10 else '')+f'Факт {i}.') for i in range(12)])
    store.conn.execute('UPDATE analysis_jobs SET command=? WHERE job_id=?', (command, job['job_id']))
    pairs=list(pipeline.iter_relation_candidates(store,job['job_id'],generation=job['generation']))
    assert len(pairs)==expected
    assert store.get_job(job['job_id'])['evidence_records']==12


def test_changed_source_interrupts_before_provider_reservation_transaction(manifest_job):
    store, job = manifest_job
    records_fixture(store, job, [dict(claim="Срок 30 дней.")])
    batch = store.plan_batch(job['job_id'], generation=job['generation'], step_key='changed-count', kind='link', payload={})
    store.conn.execute("UPDATE messages SET text='Изменено' WHERE channel_id=22 AND message_id=1")
    with pytest.raises(ValueError, match='input_changed'):
        store.reserve_provider_attempt(job['job_id'], batch['batch_id'], generation=job['generation'],
            attempt_id='not-dispatched', request_kind='token_count')
    current = store.get_job(job['job_id'])
    assert current['status'] == 'interrupted'
    assert current['provider_requests'] == current['llm_calls'] == 0
    assert not store.get_batch(batch['batch_id'])['attempts']
