"""Fast capacity checks complement the opt-in native RSS workload."""

import copy

import pytest

from common import process as common_process
from telegram_connector import telegram_analysis as analysis
from telegram_connector.tests.test_analysis_history import (
    store, job, runtime, PageClient, posts, load_source, reset_source_plan,
)
from telegram_connector.tests.test_analysis_manifest import manifest_job, seed, change_setting, build
from telegram_connector.tests.test_analysis_relations import records_fixture


@pytest.mark.parametrize("sync_limit,max_messages", [(6000, 200000), (0, 6000)])
def test_s35_resource_bounds_inherited_download_limit_keeps_pages_bounded(store, job, runtime, sync_limit, max_messages):
    frozen = copy.deepcopy(job["request"])
    frozen["settings"]["sync"].update(sync_limit=sync_limit, batch_size=500)
    frozen["settings"]["analysis"].update(max_messages=max_messages, history_page_size=100)
    store.conn.execute("UPDATE analysis_jobs SET request_json=? WHERE job_id=?", (analysis.canonical_json(frozen), job["job_id"]))
    reset_source_plan(store, job)
    class LazyClient(PageClient):
        generated = 0
        max_buffer = 0
        async def get_messages(self, peer, **options):
            assert not self.store.conn.in_transaction
            self.calls.append((peer.id, options))
            start = min(10000, options["offset_id"] - 1 if options.get("offset_id") else 10000)
            amount = min(options["limit"], start)
            values = posts(range(start, start - amount, -1))
            if values and values[0].date >= options["offset_date"]:
                return []
            self.generated += len(values)
            self.max_buffer = max(self.max_buffer, len(values))
            return values
    client = LazyClient(store, [])
    deadline = common_process.continuous_time() + frozen["settings"]["analysis"]["telegram_request_timeout_seconds"]
    first = load_source(store, job, runtime, client, deadline=deadline)
    calls = len(client.calls)
    second = load_source(store, job, runtime, client, channel="@two", deadline=deadline)
    assert first["state"] == second["state"] == "partial" and first["reason"] == second["reason"] == "download_cap"
    assert store.get_job(job["job_id"])["downloaded_messages"] == client.generated == 6000
    assert client.max_buffer <= 100 and len(client.calls) == calls
    assert store.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 6000


def test_s35_resource_bounds_manifest_capacity_stop_is_accounted_and_explicit(manifest_job):
    store, job = manifest_job
    seed(store, 22, range(1, 10001), text="Документы: паспорт, 1250 EUR.")
    change_setting(store, job, "sync", "batch_size", 500)
    change_setting(store, job, "analysis", "max_intermediate_bytes", 262144)
    manifest = build(store, job)
    assert manifest["state"] == "complete" and manifest["partial"]
    assert 0 < manifest["selected"] < manifest["stored_in_window"] == 10000
    assert manifest["known_skipped"] == 10000 - manifest["selected"]
    assert store.storage_usage(job["job_id"])["allocated_bytes"] <= 262144


def test_s35_resource_bounds_relation_candidates_stream_under_dense_entity_collisions(manifest_job):
    store, job = manifest_job
    change_setting(store, job, "sync", "batch_size", 500)
    records_fixture(store, job, [{"claim": f"Случай Uno {number}", "entities": ["Uno"], "topic_key": "подача"}
        for number in range(1000)])
    per_left, total = {}, 0
    for pair in analysis.iter_relation_candidates(store, job["job_id"], generation=job["generation"]):
        key = pair["left"]["record_id"]
        per_left[key] = per_left.get(key, 0) + 1
        total += 1
        assert per_left[key] <= 20 and total <= 20000
    assert total > 1000 and store.get_job(job["job_id"])["checkpoint"]["relations"]["limited"]
