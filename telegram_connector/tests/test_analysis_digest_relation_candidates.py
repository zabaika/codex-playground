"""Explicit digests are weak candidate hints; these are authored ledger fixtures."""
import json

import pytest

from telegram_connector import analysis_evidence as evidence
from telegram_connector import analysis_pipeline as pipeline
from telegram_connector.tests.test_analysis_evidence import document
from telegram_connector.tests.test_analysis_manifest import manifest_job, seed, build, messages
from telegram_connector.tests.test_analysis_relations import candidates, link


DIGEST = 'Нейродайджест за неделю\nВыпущена ALPHA.\nВыпущена BETA.'


def ledger(manifest_job, *, command='topics', digest=DIGEST,
           shared=None, primary=False, replies=False, duplicate=False, qa_pair=False,
           record_ids=None):
    store, job = manifest_job
    job['request']['request']['command'] = command
    store.conn.execute('UPDATE analysis_jobs SET command=?,request_json=? WHERE job_id=?',
        (command, json.dumps(job['request']), job['job_id']))
    job = store.get_job(job['job_id'])
    seed(store, 22, [999], text=digest)
    for index, name in enumerate(('ALPHA', 'BETA'), 1):
        seed(store, 22, [index], text=f'Выпущена {name}.')
        if replies:
            store.conn.execute('UPDATE messages SET raw_json=? WHERE channel_id=22 AND message_id=?',
                (json.dumps({'reply_to': {'reply_to_msg_id': 999}}), index))
    build(store, job)
    fragments = messages(store, job)
    for fragment in fragments:
        fragment['input']['fragment_end'] = len(fragment['message']['text'])
    by_id = {f['input']['message_id']: f for f in fragments}
    rows = []
    for index, name in enumerate(('ALPHA', 'ALPHA' if duplicate else 'BETA'), 1):
        from_digest = primary is True or primary == 'first' and index == 1
        fragment = by_id[999 if from_digest else index]
        row = document({'messages': [fragment]})['records'][0]
        row.update(claim=f'Выпущена {name}.', details=[], entities=[] if duplicate else [name], topic_key=None, related_message_refs=[] if from_digest else [{'channel_id': 22, 'message_id': 999}])
        if shared == 'entity':
            row['entities'].append('Company')
        elif shared == 'topic':
            row['topic_key'] = 'Релиз'
        if from_digest:
            # The selected quote has no digest marker; classification needs the full message.
            ref = row['source_refs'][0]
            start = fragment['message']['text'].index(row['claim'])
            ref.update(quote=row['claim'], fragment_start=start,
                       fragment_end=start + len(row['claim']))
        if qa_pair:
            row['record_type'] = 'question' if index == 1 else 'answer'
        rows.append(row)
    batch = store.plan_batch(job['job_id'], generation=job['generation'],
        step_key='fixture-digest-relations', kind='extract', payload={})
    records = evidence.validate_evidence_records(store, job['job_id'], batch['batch_id'],
        generation=job['generation'], document={'records': rows, 'saturated': False},
        fragments=fragments, record_schema_version=3)
    if record_ids:
        for record, identity in zip(records, record_ids):
            record['record_id'] = identity
    store.complete_batch(job['job_id'], batch['batch_id'], generation=job['generation'],
        records=records, inputs=[f['input'] for f in fragments])
    return store, store.get_job(job['job_id']), records


@pytest.mark.parametrize('command', ['topics', 'qa', 'search'])
def test_only_shared_related_digest_creates_no_pair(manifest_job, command):
    store, job, _ = ledger(manifest_job, command=command)
    assert candidates(store, job) == []


@pytest.mark.parametrize('shared', ['entity', 'topic', 'ordinary_message'])
def test_other_candidate_signals_survive_shared_digest(manifest_job, shared):
    store, job, _ = ledger(manifest_job, shared=shared,
        digest='Обсуждение релизов.' if shared == 'ordinary_message' else DIGEST)
    assert len(candidates(store, job)) == 1


@pytest.mark.parametrize('command', ['qa', 'search'])
def test_distinct_primary_digest_facts_do_not_pair_through_headerless_quotes(manifest_job, command):
    store, job, _ = ledger(manifest_job, command=command, primary=True)
    assert candidates(store, job) == []


def test_digest_only_question_answer_keeps_independent_qa_role_candidates(manifest_job):
    store, job, _ = ledger(manifest_job, command='qa', primary=True, qa_pair=True)
    assert len(candidates(store, job)) == 1


def test_same_digest_reply_context_remains_a_candidate(manifest_job):
    store, job, _ = ledger(manifest_job, replies=True)
    assert len(candidates(store, job)) == 1


@pytest.mark.parametrize('record_ids', [('a', 'z'), ('z', 'a')])
def test_direct_reply_to_digest_fact_survives_both_index_orders(manifest_job, record_ids):
    store, job, _ = ledger(manifest_job, command='search', primary='first',
        replies=True, record_ids=record_ids)
    assert len(candidates(store, job)) == 1


def test_exact_digest_record_duplicates_still_need_no_provider(manifest_job):
    store, job, _ = ledger(manifest_job, command='search', primary=True, duplicate=True)
    assert len(candidates(store, job)) == 1
    assert not link(store, job, lambda *a, **k: pytest.fail('Exact duplicates need no API'))['partial']
    assert store.get_job(job['job_id'])['provider_requests'] == 0


def test_digest_classification_is_cached_and_outside_index_transaction(manifest_job, monkeypatch):
    store, job, _ = ledger(manifest_job)
    original = evidence._analysis_message_snapshot
    reads = []
    def snapshot(owner, channel_id, message_id, maximum):
        if message_id == 999:
            assert not store.conn.in_transaction
            reads.append(message_id)
        return original(owner, channel_id, message_id, maximum)
    monkeypatch.setattr(evidence, '_analysis_message_snapshot', snapshot)
    assert candidates(store, job) == []
    assert reads == [999]


def test_changed_auxiliary_digest_does_not_reclassify_frozen_input(manifest_job):
    store, job, _ = ledger(manifest_job, digest='Обычное обсуждение.')
    store.conn.execute('UPDATE messages SET text=? WHERE channel_id=22 AND message_id=999', (DIGEST,))
    assert len(candidates(store, job)) == 1


@pytest.mark.parametrize('unavailable', ['missing', 'oversized'])
def test_unreadable_auxiliary_source_keeps_conservative_candidate(manifest_job, monkeypatch, unavailable):
    store, job, _ = ledger(manifest_job)
    original = evidence._analysis_message_snapshot
    def snapshot(owner, channel_id, message_id, maximum):
        if message_id == 999:
            if unavailable == 'oversized':
                raise ValueError('manifest_byte_cap')
            return None, None
        return original(owner, channel_id, message_id, maximum)
    monkeypatch.setattr(evidence, '_analysis_message_snapshot', snapshot)
    assert len(candidates(store, job)) == 1


def test_retired_candidate_policy_is_rejected_without_reindexing(manifest_job):
    store, job, _ = ledger(manifest_job)
    job['request']['settings']['algorithm_version'] -= 1
    store.conn.execute('UPDATE analysis_jobs SET request_json=? WHERE job_id=?',
        (json.dumps(job['request']), job['job_id']))
    before = store.conn.total_changes
    with pytest.raises(ValueError, match='analysis_policy_incompatible'):
        candidates(store, job)
    assert store.conn.total_changes == before
