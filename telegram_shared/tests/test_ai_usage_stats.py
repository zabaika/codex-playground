"""Common summaries accept application-owned identities and canonical statuses."""
import sqlite3
import pytest

from telegram_shared.ai_usage_stats import fetch_ai_usage_summary, format_ai_usage_summary
from telegram_shared.openai_usage import AI_USAGE_SCHEMA_SQL as AI_USAGE_SCHEMA


def test_non_digest_counts_application_operations_and_canonical_statuses(tmp_path):
    path = tmp_path / 'usage.db'
    conn = sqlite3.connect(path)
    conn.executescript(AI_USAGE_SCHEMA)
    for number, status in enumerate(('failed', 'succeeded', 'failed', 'succeeded')):
        conn.execute("""INSERT INTO ai_usage_log
            (created_at,feature,stage,channel,model,status,job_id,batch_id,attempt_id)
            VALUES ('2026-11-17','custom','work',?,'fixture',?,'job',?,?)""",
            ('a' if number < 2 else 'b', status, str(number // 2), str(number)))
    conn.commit()
    conn.close()
    summary = fetch_ai_usage_summary(path, feature='custom', row_limit=4,
        filter_channel='a', operation_key=lambda row: (row['job_id'], row['batch_id']))
    assert summary['global']['physical_attempts'] == 4
    assert summary['global']['observed_logical_operations'] == 2
    assert summary['filtered']['observed_logical_operations'] == 1
    assert summary['global']['ok_requests'] == 2
    assert summary['global']['error_requests'] == 2
    assert summary['filtered']['ok_requests'] == summary['filtered']['error_requests'] == 1
    rendered = format_ai_usage_summary(summary, title='Custom')
    assert 'observed logical operations: 2' in rendered
    assert 'digest' not in rendered.lower()
    bounded = fetch_ai_usage_summary(path, feature='custom', row_limit=2,
        filter_channel='a', operation_key=lambda row: (row['job_id'], row['batch_id']))
    assert bounded['global']['physical_attempts'] == 2
    assert bounded['global']['observed_logical_operations'] == 1
    assert bounded['filtered']['total_requests'] == 0
    assert bounded['filtered']['observed_logical_operations'] == 0


def test_reader_rejects_incomplete_schema_with_explicit_error(tmp_path):
    path = tmp_path / 'incomplete.db'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE ai_usage_log (id INTEGER PRIMARY KEY)')
    with pytest.raises(RuntimeError, match=r'Incompatible ai_usage_log schema: missing columns .*attempt_id'):
        fetch_ai_usage_summary(path, feature='agent', row_limit=1)


def test_current_schema_preserves_unknown_metrics_and_missing_identities(tmp_path):
    path = tmp_path / 'current.db'
    conn = sqlite3.connect(path)
    conn.executescript(AI_USAGE_SCHEMA)
    conn.execute("""INSERT INTO ai_usage_log
        (created_at,feature,stage,model,status) VALUES ('2026-11-17','agent','work','fixture','succeeded')""")
    conn.commit()
    conn.close()
    summary = fetch_ai_usage_summary(path, feature='agent', row_limit=1)
    assert summary['global']['observed_logical_operations'] is None
    assert summary['global']['physical_attempts'] == 0
    assert summary['global']['unknown_usage_requests'] == 1
    for metric in ('cache_write_tokens', 'reasoning_tokens', 'output_chars', 'incomplete_responses'):
        assert summary['global'][metric] is None
    selected = fetch_ai_usage_summary(path, feature='agent', row_limit=1,
                                    operation_key=lambda row: row.get('job_id'))
    assert selected['global']['observed_logical_operations'] is None
