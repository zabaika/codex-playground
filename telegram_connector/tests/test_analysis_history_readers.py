"""Historical data is readable without a frozen prompt bundle or old executor."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import sqlite3

import pytest

from telegram_connector import analysis_evidence, analysis_pipeline, analysis_settings, analysis_telemetry
from telegram_connector.analysis_store import canonical_json
from telegram_connector.tests.test_analysis_manifest import manifest_job


HISTORY = json.loads((Path(__file__).parent / 'fixtures/analysis_history.json').read_text())


@pytest.mark.parametrize('archive', HISTORY, ids=['canonical-confirmed', 'alias-unknown'])
def test_historical_result_delivery_and_report_do_not_invoke_execution(manifest_job, monkeypatch, archive, tmp_path):
    store, admitted = manifest_job
    identity = admitted['job_id']
    request = copy.deepcopy(admitted['request'])
    request['settings'].update(archive['policy'])
    request['settings'].pop('analysis_prompts')
    batch = store.plan_batch(identity, generation=admitted['generation'],
        step_key='archive', kind='synthesize', payload=archive['synthesis'])
    store.conn.execute("UPDATE analysis_batches SET state='succeeded',payload_json=? WHERE batch_id=?",
        (canonical_json(archive['synthesis']), batch['batch_id']))
    store.conn.execute('UPDATE analysis_jobs SET request_json=?,result_json=?,delivery_json=?,status=? WHERE job_id=?',
        (canonical_json(request), canonical_json(archive['result']), canonical_json(archive['delivery']), 'partial', identity))
    def forbidden(*args, **kwargs):
        pytest.fail('Historical reading cannot execute or reinterpret an old analysis')
    assert not hasattr(analysis_settings, 'synthesis_version')
    for owner, name in [(analysis_evidence, 'classify_extraction_document'), (analysis_evidence, 'classify_relation_document'),
        (analysis_pipeline, '_synthesis_result')]:
        monkeypatch.setattr(owner, name, forbidden)
    before = store.conn.total_changes
    saved = store.get_job(identity)
    report = analysis_telemetry.summarize_job(store, identity)
    assert saved['result'] == archive['result'] and saved['delivery'] == archive['delivery']
    assert saved['request'] == request
    assert report['selection']['model_selected_evidence_ids'] == ['archive-a']
    assert report['selection']['not_selected_by_model_ids'] == ['archive-b']
    assert report['source_verification'] is None
    assert report['result_reasons'] == sorted(archive['result']['reasons'])
    assert report['elapsed_ms'] is None
    assert report['cost']['usd'] is None
    assert store.conn.total_changes == before
    database = tmp_path / 'history.sqlite3'
    target = sqlite3.connect(database)
    try:
        store.conn.backup(target)
    finally:
        target.close()
    raw_before = database.read_bytes()
    run = subprocess.run([sys.executable, str(Path(__file__).parents[1] / 'scripts/report_analysis.py'),
        '--db', database, '--job-id', identity], capture_output=True, text=True, check=True)
    public = json.loads(run.stdout)
    assert public['selection'] == report['selection']
    assert public['cost']['usd'] is None
    assert Path(database).read_bytes() == raw_before
