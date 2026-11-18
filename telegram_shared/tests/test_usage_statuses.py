"""Only canonical usage statuses are accepted."""
import sqlite3

import pytest

from telegram_shared import openai_usage
from telegram_shared.openai_usage import AI_USAGE_SCHEMA_SQL as AI_USAGE_SCHEMA


@pytest.mark.parametrize('status', ['ok', 'error', 'unexpected'])
def test_writer_rejects_noncanonical_status_without_insert(status):
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript(AI_USAGE_SCHEMA)
    cache = openai_usage.build_prompt_cache_info(cache_key='fixture',
        system_instructions='', prompt_text='', shared_prefix='')
    with pytest.raises(ValueError, match='usage status'):
        openai_usage.insert_openai_usage(conn, feature='custom', created_at='fixture',
            stage='fixture', channel='', since=None, until=None, model='fixture',
            request_index=1, message_count=0, status=status, cache_info=cache, prompt_text='')
    assert conn.execute('SELECT count(*) FROM ai_usage_log').fetchone()[0] == 0
    conn.close()


def test_usage_insert_requires_transaction_and_allows_caller_rollback():
    conn = sqlite3.connect(':memory:', isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(AI_USAGE_SCHEMA)
    cache = openai_usage.build_prompt_cache_info(cache_key='fixture',
        system_instructions='', prompt_text='', shared_prefix='')
    fields = dict(feature='custom', created_at='fixture', stage='fixture', channel='',
        since=None, until=None, model='fixture', request_index=1, message_count=0,
        status='succeeded', cache_info=cache, prompt_text='')
    with pytest.raises(RuntimeError, match='transaction'):
        openai_usage.insert_openai_usage(conn, **fields)
    conn.execute('BEGIN')
    openai_usage.insert_openai_usage(conn, **fields)
    assert conn.in_transaction
    conn.rollback()
    assert conn.execute('SELECT count(*) FROM ai_usage_log').fetchone()[0] == 0
    conn.close()
