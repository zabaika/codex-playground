"""Attempt writers use an application-provided feature and current schema."""
import sqlite3

import pytest

from telegram_shared import openai_usage
from telegram_shared.openai_usage import AI_USAGE_SCHEMA_SQL as AI_USAGE_SCHEMA


@pytest.mark.parametrize('feature,status', [('agent', 'succeeded'), ('topics', 'succeeded'), ('custom', 'succeeded')])
def test_attempt_writer_supports_non_digest_features_without_owning_transactions(feature, status):
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript(AI_USAGE_SCHEMA)
    cache = openai_usage.build_prompt_cache_info(cache_key='fixture',
        system_instructions='', prompt_text='fixture', shared_prefix='')
    conn.execute('BEGIN')
    row_id = openai_usage.begin_openai_attempt(conn, run_id='application-run',
        attempt_id='application-attempt', attempt_index=1, feature=feature,
        created_at='2026-11-17T10:00:00+00:00', stage='application-stage', channel=None,
        since=None, until=None, model='fixture', request_index=1, message_count=1,
        cache_info=cache, prompt_text='fixture')
    assert conn.in_transaction
    openai_usage.settle_openai_attempt(conn, row_id, status=status, error=None,
        latency_ms=12, response={'id': 'fixture-response', 'model': 'actual-fixture',
            'usage': {'input_tokens': 4, 'output_tokens': 2}})
    openai_usage.write_openai_backoff(conn, run_id='application-run',
        attempt_id='application-attempt', elapsed_ms=11, requested_ms=10)
    assert conn.in_transaction
    conn.commit()
    row = conn.execute('SELECT * FROM ai_usage_log').fetchone()
    assert row['feature'] == feature
    assert row['status'] == status
    assert row['run_id'] == 'application-run'
    assert row['backoff_ms'] == 11
    assert row['total_tokens'] == 6
    assert row['actual_model'] == 'actual-fixture'
    conn.close()
