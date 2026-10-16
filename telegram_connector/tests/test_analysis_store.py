import sqlite3
import importlib
import importlib.util
import json

import pytest

from common import sqlite as common_sqlite
from telegram_connector import telegram_history_client as history


@pytest.fixture
def db():
    conn = common_sqlite.connect_sqlite(":memory:")
    history.init_db(conn)
    try:
        yield conn
    finally:
        conn.close()


def insert_job(db, job_id="job", *, kind="analysis", command="topics", generation=0, admission_key=None, request_json="{}",
               accepted_at="2026-10-15T00:00:00+00:00", cutoff_at="2026-10-15T00:00:00+00:00"):
    db.execute("""INSERT INTO analysis_jobs
        (job_id,kind,command,request_json,request_hash,accepted_at,cutoff_at,
         created_at,updated_at,status,phase,generation,admission_key)
        VALUES (?,?,?,?,'hash',?,?,
         'now','now','queued','sync',?,?)""", (job_id, kind, command, request_json, accepted_at, cutoff_at, generation, admission_key))


def insert_batch(db, batch_id="batch", *, job_id="job", step_key="extract/0", kind="extract", state="planned"):
    db.execute("""INSERT INTO analysis_batches
        (batch_id,job_id,step_key,kind,state,created_at,updated_at)
        VALUES (?,?,?,?,?,'now','now')""", (batch_id, job_id, step_key, kind, state))


def insert_input(db, *, channel_id=1, start=0, end=0, role="selected"):
    db.execute("""INSERT INTO analysis_inputs
        (batch_id,channel_id,message_id,fragment_start,fragment_end,role,date_utc,content_digest)
        VALUES ('batch',?,42,?,?,?,'2026-10-14T00:00:00+00:00','digest')""", (channel_id, start, end, role))


def store_module():
    assert importlib.util.find_spec("telegram_connector.analysis_store") is not None, "Analytics needs its repository owner"
    return importlib.import_module("telegram_connector.analysis_store")


def test_s03_store_integrity_initialization_and_existing_history(db):
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"analysis_jobs", "analysis_batches", "analysis_evidence", "analysis_inputs"} <= tables
    # S11 installs the derived table only on a capable SQLite runtime.
    assert ("messages_fts" in tables) is history.fts5_available(db)
    db.execute("INSERT INTO channels VALUES (1,NULL,'fixture','Fixture','channel','{}','now','now')")
    db.execute("""INSERT INTO messages
        (channel_id,message_id,date_utc,text,raw_json,content_hash,imported_at)
        VALUES (1,42,'2026-10-14T12:00:00+00:00','source text','{}','fixture','now')""")
    history.init_db(db)
    history.init_db(db)
    assert db.execute("SELECT text FROM messages WHERE channel_id=1 AND message_id=42").fetchone()[0] == "source text"
    assert list(db.execute("PRAGMA foreign_key_check")) == []


@pytest.mark.parametrize("job_id,kind,command,generation", [
    (None, "analysis", "topics", 0), ("", "analysis", "topics", 0),
    ("job", "other", "topics", 0), ("job", "analysis", None, 0),
    ("job", "analysis", "other", 0), ("job", "fts_backfill", "topics", 0),
    ("job", "analysis", "topics", -1), ("job", "analysis", "topics", "wrong"),
])
def test_s03_store_integrity_job_identity_and_kind(db, job_id, kind, command, generation):
    with pytest.raises(sqlite3.IntegrityError):
        insert_job(db, job_id, kind=kind, command=command, generation=generation)


def test_s03_store_integrity_unique_admission_and_nullable_local_keys(db):
    insert_job(db, "one", admission_key="fixturebot:42")
    with pytest.raises(sqlite3.IntegrityError):
        insert_job(db, "two", admission_key="fixturebot:42")
    insert_job(db, "three")
    insert_job(db, "four", kind="fts_backfill", command=None)
    assert db.execute("SELECT COUNT(*) FROM analysis_jobs").fetchone()[0] == 3


@pytest.mark.parametrize("field", ["generation", "llm_calls", "provider_requests", "used_tokens", "reserved_tokens", "downloaded_messages", "intermediate_bytes", "evidence_records"])
def test_s03_store_integrity_nonnegative_typed_counters(db, field):
    insert_job(db)
    # Field names come only from the explicit test parametrization above.
    for invalid in (-1, "wrong"):
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(f"UPDATE analysis_jobs SET {field}=? WHERE job_id='job'", (invalid,))


@pytest.mark.parametrize("batch_id,job_id,step_key,kind,state", [
    (None, "job", "one", "extract", "planned"), ("", "job", "one", "extract", "planned"),
    ("batch", None, "one", "extract", "planned"), ("batch", "missing", "one", "extract", "planned"),
    ("batch", "job", None, "extract", "planned"), ("batch", "job", "one", "other", "planned"),
    ("batch", "job", "one", "extract", "other"),
])
def test_s03_store_integrity_batch_constraints(db, batch_id, job_id, step_key, kind, state):
    insert_job(db)
    with pytest.raises(sqlite3.IntegrityError):
        insert_batch(db, batch_id, job_id=job_id, step_key=step_key, kind=kind, state=state)


def test_s03_store_integrity_unique_steps_and_cascade_without_history_fk(db):
    insert_job(db)
    insert_batch(db)
    with pytest.raises(sqlite3.IntegrityError):
        insert_batch(db, "different-id")
    insert_input(db, channel_id=1)
    insert_input(db, channel_id=2)
    db.execute("""INSERT INTO analysis_evidence
        (record_id,batch_id,record_type,payload_json) VALUES ('record','batch','claim','{}')""")
    assert db.execute("SELECT COUNT(*) FROM analysis_inputs").fetchone()[0] == 2
    assert list(db.execute("PRAGMA foreign_key_check")) == []
    db.execute("DELETE FROM analysis_jobs WHERE job_id='job'")
    for table in ("analysis_batches", "analysis_inputs", "analysis_evidence"):
        assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


@pytest.mark.parametrize("start,end,role", [(-1, 4, "selected"), (3, 2, "selected"), (2, 2, "context"), (0, 0, "other"), (None, 0, "selected")])
def test_s03_store_integrity_fragment_constraints(db, start, end, role):
    insert_job(db)
    insert_batch(db)
    with pytest.raises(sqlite3.IntegrityError):
        insert_input(db, start=start, end=end, role=role)


def test_s03_store_integrity_transaction_rolls_back_children(db):
    insert_job(db)
    with pytest.raises(RuntimeError):
        with common_sqlite.write_tx(db, immediate=True):
            insert_batch(db)
            insert_input(db)
            raise RuntimeError("simulated failure before commit")
    assert db.execute("SELECT COUNT(*) FROM analysis_batches").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM analysis_inputs").fetchone()[0] == 0


def test_s03_store_integrity_bounded_json_uses_bytes(db):
    module = store_module()
    value = {"text": "Данные"}
    encoded = module.encode_payload(value, max_bytes=100)
    assert json.loads(encoded) == value
    size = len(encoded.encode("utf-8"))
    assert module.encode_payload(value, max_bytes=size) == encoded
    with pytest.raises(ValueError):
        module.encode_payload(value, max_bytes=size - 1)
    for invalid in ("not an object", {"nan": float("nan")}, {"object": object()}):
        with pytest.raises(ValueError):
            module.encode_payload(invalid, max_bytes=100)


def test_s03_store_integrity_checkpoint_generation_and_atomic_failure(db):
    module = store_module()
    insert_job(db)
    store = module.AnalysisStore(db)
    store.save_checkpoint("job", {"cursor": 42}, generation=0, max_bytes=100)
    assert store.get_job("job")["checkpoint"] == {"cursor": 42}
    with pytest.raises(module.StaleGenerationError):
        store.save_checkpoint("job", {"cursor": 17}, generation=1, max_bytes=100)
    with pytest.raises(ValueError):
        store.save_checkpoint("job", {"cursor": "too large"}, generation=0, max_bytes=1)
    assert store.get_job("job")["checkpoint"] == {"cursor": 42}
    assert store.get_job("missing") is None
    assert not db.in_transaction
    assert list(db.execute("PRAGMA foreign_key_check")) == []


def test_s03_store_integrity_repository_requires_foreign_keys():
    conn = sqlite3.connect(":memory:")
    try:
        with pytest.raises(ValueError, match="foreign"):
            store_module().AnalysisStore(conn)
    finally:
        conn.close()


def test_s03_store_integrity_repository_runtime_asset():
    installer = (history.APP_DIR / "scripts" / "install_launch_agent.sh").read_text()
    assert 'cp "$SOURCE_ROOT/analysis_store.py" "$SERVICE_ROOT/analysis_store.py"' in installer


@pytest.mark.parametrize("corrupt", ["[]", "null", '"text"'])
def test_s03_store_integrity_rejects_corrupt_checkpoint_shape(db, corrupt):
    insert_job(db)
    db.execute("UPDATE analysis_jobs SET checkpoint_json=?", (corrupt,))
    with pytest.raises(ValueError):
        store_module().AnalysisStore(db).get_job("job")


def test_s03_store_integrity_failed_migration_is_atomic():
    conn = common_sqlite.connect_sqlite(":memory:")
    def deny_third_table(action, table, *unused):
        return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_CREATE_TABLE and table == "analysis_evidence" else sqlite3.SQLITE_OK

    try:
        conn.set_authorizer(deny_third_table)
        with pytest.raises(sqlite3.DatabaseError):
            history.init_db(conn)
        conn.set_authorizer(None)
        assert not list(conn.execute("SELECT name FROM sqlite_master WHERE name LIKE 'analysis_%'"))
        assert not conn.in_transaction
        history.init_db(conn)
        assert list(conn.execute("PRAGMA foreign_key_check")) == []
    finally:
        conn.close()


def test_s03_store_integrity_g1_request_settings_roundtrip(db):
    from datetime import datetime, timezone
    from telegram_connector import telegram_analysis as analysis
    from telegram_connector import telegram_bridge as bridge
    import tomllib

    config_dir = history.APP_DIR / "config"
    config = tomllib.loads((config_dir / "runtime.example.toml").read_text())
    settings = analysis.resolve_analysis_settings(config, config_dir=config_dir)
    request = bridge.parse_analysis_request(
        '/search @fixture since=week until=today "банки  Испании" --detail short',
        policy=settings, now=datetime(2026, 10, 15, 12, tzinfo=timezone.utc),
    )
    payload = {"request": request.to_payload(), "settings": settings}
    encoded = store_module().encode_payload(payload, max_bytes=settings["analysis"]["max_intermediate_bytes"])
    # Fixture seeding; real transactional admission is the next step S04.
    insert_job(db, command="search", request_json=encoded,
               accepted_at=request.accepted_at.isoformat(), cutoff_at=request.cutoff_at.isoformat())
    job = store_module().AnalysisStore(db).get_job("job")
    restored = job["request"]
    assert restored == payload
    assert restored["request"]["query"] == "банки  Испании"
    assert restored["request"]["cutoff_at"] == "2026-10-15T12:00:00+00:00"
    assert job["accepted_at"] == restored["request"]["accepted_at"]
    assert job["cutoff_at"] == restored["request"]["cutoff_at"]
    assert restored["settings"]["analysis_prompts"]["hash"] == settings["analysis_prompts"]["hash"]
