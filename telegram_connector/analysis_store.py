"""Persistence owner for analytic jobs in the existing Connector database."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any, Iterator
import uuid
from contextlib import contextmanager

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common import sqlite as common_sqlite


class StaleGenerationError(RuntimeError):
    """The caller no longer owns the job generation, or the job was removed."""


class AdmissionConflictError(RuntimeError):
    """A transport identity was replayed with different caller-owned input."""


class QueueFullError(RuntimeError):
    """No new queued job can be accepted under the current operator cap."""


class BudgetExceededError(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


SQLITE_INTEGER_MAX = (1 << 63) - 1
BUDGET_REASONS = frozenset({"max_llm_calls", "max_provider_requests", "max_total_tokens", "max_input_tokens",
    "stage_output_limit", "max_intermediate_bytes", "max_evidence_records", "usage_overrun", "max_merge_depth", "max_split_depth"})
DELIVERY_STOP_REASONS = frozenset({"forbidden", "deadline_exceeded", "artifact_unavailable"})
_COUNT_FIELDS = ("downloaded_messages", "llm_calls", "provider_requests", "used_tokens", "reserved_tokens", "evidence_records")
_CHILD_VALUE_COLUMNS = {
    "b": ("batch_id", "job_id", "step_key", "kind", "state", "input_digest", "cache_key", "payload_json", "attempts_json", "created_at", "updated_at"),
    "e": ("record_id", "batch_id", "record_type", "topic_key", "payload_json"),
    "i": ("batch_id", "channel_id", "message_id", "fragment_start", "fragment_end", "role", "date_utc", "content_digest"),
}
_CACHE_ELIGIBLE = """j.kind='analysis' AND j.status IN ('completed','failed','cancelled')
    AND (j.status!='completed' OR j.result_json IS NOT NULL) AND b.kind IN ('extract','expand_query')
    AND b.state='succeeded' AND b.cache_key IS NOT NULL"""


def _value_bytes_sql(alias):
    return "+".join(f"length(CAST(COALESCE({alias}.{name},'') AS BLOB))" for name in _CHILD_VALUE_COLUMNS[alias])


def _cache_bytes_sql():
    return ("length(CAST(b.cache_key AS BLOB))+length(CAST(b.payload_json AS BLOB))"
            f"+COALESCE((SELECT SUM({_value_bytes_sql('e')}) FROM analysis_evidence e WHERE e.batch_id=b.batch_id),0)"
            f"+COALESCE((SELECT SUM({_value_bytes_sql('i')}) FROM analysis_inputs i WHERE i.batch_id=b.batch_id),0)")


def _budget_integer(value, name):
    if type(value) is not int or not 0 <= value <= SQLITE_INTEGER_MAX:
        raise ValueError(f"Invalid {name}")
    return value


def completion_budget(settings, *, detail, roots):
    """Conservative one-attempt cost of the bounded, consecutive fan-in tree."""
    _budget_integer(roots, "completion roots")
    policy = settings["analysis"]
    merges, depth, nodes = 0, 0, roots
    while nodes > 1:
        depth += 1
        if depth > policy["max_merge_depth"]:
            raise BudgetExceededError("max_merge_depth")
        groups, remainder = divmod(nodes, policy["merge_fan_in"])
        merges += groups + int(remainder > 1)
        nodes = groups + int(remainder > 0)
    final = int(roots > 0)
    output = policy["output"]["profiles"][detail]["max_output_tokens"]
    return {"roots": roots, "merge_calls": merges, "llm_calls": merges + final,
            "provider_requests": 2 * (merges + final),
            "tokens": merges * (policy["max_input_tokens"] + settings["digest_ai"]["max_output_tokens"])
                      + final * (policy["max_input_tokens"] + output)}


def canonical_json(value: dict[str, Any] | list[Any]) -> str:
    if not isinstance(value, (dict, list)):
        raise ValueError("Analytic payload must be an object or array")
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid analytic JSON payload") from exc
    return encoded


def _same_channel_reply(raw, channel_id):
    reply = raw.get("reply_to") if isinstance(raw, dict) else None
    if not isinstance(reply, dict):
        return None
    message_id = reply.get("reply_to_msg_id")
    peer = reply.get("reply_to_peer_id")
    if type(message_id) is not int or message_id <= 0 or (peer is not None and peer != {"channel_id": channel_id}):
        return None
    return message_id


def _leaf_disposition(batch, count):
    if batch["payload"].get("invalidated"):
        return "excluded"
    if batch["state"] == "succeeded" and not count:
        return "irrelevant"
    statuses = batch["payload"].get("record_dispositions", {})
    if len(statuses) < count or batch["state"] not in {"succeeded", "skipped"}:
        return "excluded"
    values = set(statuses.values())
    return next(iter(values)) if len(values) == 1 else "used" if values else "irrelevant"


def encode_payload(value: dict[str, Any] | list[Any], *, max_bytes: int) -> str:
    encoded = canonical_json(value)
    if len(encoded.encode("utf-8")) > max_bytes:
        raise ValueError("Analytic payload exceeds its byte limit")
    return encoded


def _receipt_allowance(attempt):
    if attempt.get("usage") is not None:
        return 0
    current = {**attempt, "receipt_allowance": 0}
    bound = 0 if attempt["request_kind"] == "token_count" else SQLITE_INTEGER_MAX
    future = {**current, "state": "succeeded", "settled_at": "9999-12-31T23:59:59.999999+00:00",
              "usage": {key: bound for key in ("input_tokens", "output_tokens", "cached_input_tokens")}}
    if attempt["request_kind"] == "inference":
        future["overrun"] = True
    return max(0, len(canonical_json(future).encode("utf-8")) - len(canonical_json(current).encode("utf-8")))


def _fallback_document(job, reason, sources, *, upper=False):
    manifest = job["checkpoint"].get("manifest", {})
    counts = {key: SQLITE_INTEGER_MAX if upper else job[key] for key in _COUNT_FIELDS}
    counts["selected_messages"] = SQLITE_INTEGER_MAX if upper else manifest.get("selected", 0)
    phase = job["phase"] if job["phase"] in {"sync", "select", "extract", "link", "merge", "synthesize", "verify", "deliver"} else "unknown"
    return {"kind": "budget_stop", "status": "partial", "reason": reason,
            "phase": "synthesize" if upper else phase, "counts": counts,
            "selection_mode": "all_messages" if upper else manifest.get("selection_mode", "not_selected"),
            "source_refs": sources}


def _delivery_allowance(job):
    settings = job["request"].get("settings", {})
    output = settings.get("analysis", {}).get("output")
    request = job["request"].get("request", {})
    if not output or not request or "bridge" not in settings:
        return 0
    delivery = job["delivery"]
    if delivery.get("outcome") == "confirmed" or any(p["outcome"] == "unknown" for p in delivery.get("parts", [])):
        return 0
    detail = request["detail"]
    parts = 1 if detail == "full" else output["max_messages"]
    attempts = settings["bridge"]["send_message_retry_attempts"]
    stamp = "9999-12-31T23:59:59.999999+00:00"
    future_attempt = {"attempt_id": "f" * 32, "generation": SQLITE_INTEGER_MAX, "outcome": "confirmed",
        "started_at": stamp, "finished_at": stamp, "message_id": SQLITE_INTEGER_MAX, "chat_id": job["destination"],
        "error_code": SQLITE_INTEGER_MAX, "retry_after": SQLITE_INTEGER_MAX}
    future = {"version": 1, "destination": job["destination"], "detail": detail, "parse_mode": None if detail == "full" else "HTML",
        "plan_digest": "f" * 64, "artifact_hash": "f" * 64 if detail == "full" else None, "analysis_partial": False,
        "outcome": "confirmed", "error": max(DELIVERY_STOP_REASONS, key=len), "parts": []}
    part_bytes = len(canonical_json({"text": "", "outcome": "confirmed", "attempts": []}).encode("utf-8"))
    part_bytes += attempts * len(canonical_json(future_attempt).encode("utf-8")) + attempts - 1
    if delivery.get("parts"):
        empty = len(canonical_json({"text": ""}).encode("utf-8"))
        text_bytes = sum(len(canonical_json({"text": p["text"]}).encode("utf-8")) - empty for p in delivery["parts"])
    else:
        if detail == "full":
            text_chars = len(str(Path(output["results_dir"]) / (job["job_id"] + ".md")))
        else:
            reference = {"channel_id": -(1 << 63), "message_id": SQLITE_INTEGER_MAX, "content_digest": "f" * 64}
            count = min(request["sources"], settings["analysis"]["max_messages"])
            fallback = _fallback_document(job, max(BUDGET_REASONS, key=len), [], upper=True)
            fallback_chars = len(canonical_json(fallback)) + count * len(canonical_json(reference)) + max(0, count - 1)
            text_chars = min(output["profiles"][detail]["max_chars"], fallback_chars + len(request.get("query") or ""))
        # JSON can encode a single control character as six UTF-8 bytes (\u0000).
        text_bytes = 6 * text_chars
    # Multiply sizes rather than allocating arrays at operator-configured limits.
    bound = len(canonical_json(future).encode("utf-8")) + parts * part_bytes + parts - 1 + text_bytes
    return max(0, bound - len(canonical_json(delivery).encode("utf-8")))


def _finalization_allowance(job):
    policy = job["request"].get("settings", {}).get("analysis", {})
    if not policy:
        return 0
    checkpoint = job["checkpoint"]
    projection = dict(checkpoint)
    if "progress" in checkpoint:
        projection["progress"] = {"outcome": "confirmed", "message_id": SQLITE_INTEGER_MAX,
            "view_digest": "f" * 64, "updated_at": "9999-12-31T23:59:59.999999+00:00"}
    projection["last_stop"] = {"reason": "worker_exited_without_terminal_state", "phase": "synthesize", "exit_code": SQLITE_INTEGER_MAX}
    result_extra = 0
    artifact_extra = 0
    if job["kind"] == "analysis" and "output" in policy:
        if job["request"].get("request", {}).get("detail") == "full":
            projection["artifact"] = {"result_digest": "f" * 64, "content_hash": "f" * 64,
                "previous_hash": "f" * 64, "partial": False, "restore_missing": False}
            relative = str(Path(policy["output"]["results_dir"]) / (job["job_id"] + ".md"))
            artifact_extra = max(0, len(relative.encode("utf-8")) + 64
                - sum(len((job[key] or "").encode("utf-8")) for key in ("full_path", "full_hash")))
        projection["budget_stop"] = {"reason": max(BUDGET_REASONS, key=len), "phase": "synthesize"}
        if "manifest" in checkpoint:
            manifest = dict(checkpoint["manifest"])
            manifest.update(state="complete", partial=False, known_skipped=SQLITE_INTEGER_MAX,
                input_changed={"batch_id": "f" * 32, "channel_id": -(1 << 63), "message_id": SQLITE_INTEGER_MAX},
                repair_after_step=f"select/{SQLITE_INTEGER_MAX}")
            manifest["reasons"] = list(dict.fromkeys([*manifest.get("reasons", []), "history_partial", "max_messages", "input_removed", "manifest_byte_cap"]))
            projection["manifest"] = manifest
        bound = _fallback_document(job, max(BUDGET_REASONS, key=len), [], upper=True)
        source_count = min(policy["output"]["sources_per_item"], policy["max_messages"])
        reference = {"channel_id": -(1 << 63), "message_id": SQLITE_INTEGER_MAX, "content_digest": "f" * 64}
        result_size = len(canonical_json(bound).encode("utf-8"))
        result_size += source_count * len(canonical_json(reference).encode("utf-8")) + max(0, source_count - 1)
        existing = len(canonical_json(job["result"]).encode("utf-8")) if job["result"] is not None else 0
        result_extra = max(0, result_size - existing)
    elif job["kind"] == "fts_backfill" and "fts" in checkpoint:
        projection["fts"] = {**checkpoint["fts"], "state": "building", "reason": "writer_without_index",
            "cursor": SQLITE_INTEGER_MAX, "upper_rowid": SQLITE_INTEGER_MAX,
            "indexed_messages": SQLITE_INTEGER_MAX, "schema_version": SQLITE_INTEGER_MAX, "needs_clear": False}
    return (max(0, len(canonical_json(projection).encode("utf-8")) - len(canonical_json(checkpoint).encode("utf-8")))
            + result_extra + artifact_extra + (_delivery_allowance(job) if job["kind"] == "analysis" else 0))


def job_storage_usage(conn, job_id):
    """UTF-8 value bytes, excluding SQLite pages/WAL, plus unconsumed reserves."""
    cursor = conn.execute("SELECT * FROM analysis_jobs WHERE job_id=?", (job_id,))
    row = cursor.fetchone()
    if row is None:
        raise StaleGenerationError("Analytic job was removed")
    job = dict(zip((column[0] for column in cursor.description), row))
    logical = sum(len((job[f"{key}_json"] or "").encode("utf-8")) for key in ("request", "checkpoint", "result", "delivery"))
    logical += sum(len((job[key] or "").encode("utf-8")) for key in ("full_path", "full_hash"))
    for key in ("request", "checkpoint", "result", "delivery"):
        value = job.pop(f"{key}_json")
        job[key] = json.loads(value) if value is not None else None
    sources = {"b": "analysis_batches b", "e": "analysis_evidence e JOIN analysis_batches b USING(batch_id)",
               "i": "analysis_inputs i JOIN analysis_batches b USING(batch_id)"}
    for alias in _CHILD_VALUE_COLUMNS:
        expression = _value_bytes_sql(alias)
        logical += conn.execute(f"SELECT COALESCE(SUM({expression}),0) FROM {sources[alias]} WHERE b.job_id=?", (job_id,)).fetchone()[0]
    receipts = 0
    for entry in conn.execute("SELECT attempts_json FROM analysis_batches WHERE job_id=? AND kind!='select'", (job_id,)):
        receipts += sum(item.get("receipt_allowance", _receipt_allowance(item)) for item in json.loads(entry[0]))
    finalization = _finalization_allowance(job)
    return {"logical_bytes": logical, "receipt_reserve": receipts, "finalization_reserve": finalization,
            "allocated_bytes": logical + receipts + finalization}


def account_job_storage_no_tx(conn, job_id):
    if not conn.in_transaction:
        raise ValueError("Storage accounting requires an explicit transaction")
    size = job_storage_usage(conn, job_id)["allocated_bytes"]
    request = json.loads(conn.execute("SELECT request_json FROM analysis_jobs WHERE job_id=?", (job_id,)).fetchone()[0])
    maximum = request.get("settings", {}).get("analysis", {}).get("max_intermediate_bytes")
    if size > SQLITE_INTEGER_MAX or (maximum is not None and size > maximum):
        raise BudgetExceededError("max_intermediate_bytes")
    conn.execute("UPDATE analysis_jobs SET intermediate_bytes=? WHERE job_id=?", (size, job_id))
    return size


class AnalysisStore:
    def __init__(self, conn: sqlite3.Connection):
        if not conn.execute("PRAGMA foreign_keys").fetchone()[0]:
            raise ValueError("Analytic storage requires foreign keys")
        self.conn = conn

    def get_by_admission(self, admission_key: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT job_id FROM analysis_jobs WHERE admission_key=?", (admission_key,),
        ).fetchone()
        return self.get_job(row[0]) if row else None

    def worker_stop_requested(self, job_id: str, *, generation: int) -> bool:
        timeout_ms = self.conn.execute("PRAGMA busy_timeout").fetchone()[0]
        self.conn.execute("PRAGMA busy_timeout=0")
        try:
            row = self.conn.execute(
                "SELECT generation,cancel_requested FROM analysis_jobs WHERE job_id=?", (job_id,),
            ).fetchone()
        except sqlite3.OperationalError as exc:
            code = getattr(exc, "sqlite_errorcode", 0)
            if code & 0xFF in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                # Unknown intent under contention: poll again while the independent
                # process deadline continues, rather than waiting inside SQLite.
                return False
            raise
        finally:
            self.conn.execute(f"PRAGMA busy_timeout={timeout_ms}")
        return row is None or row[0] != generation or bool(row[1])

    def admit_job(self, payload: dict[str, Any], *, command: str, requester_id: str,
                  destination: str, admission_key: str, request_hash: str,
                  accepted_at: str, cutoff_at: str, max_queued_jobs: int,
                  max_bytes: int) -> tuple[dict[str, Any], bool]:
        encoded = encode_payload(payload, max_bytes=max_bytes)
        with common_sqlite.write_tx(self.conn, immediate=True):
            existing = self.get_by_admission(admission_key)
            if existing:
                if existing["request_hash"] != request_hash:
                    raise AdmissionConflictError("Admission payload changed")
                return existing, False
            queued = self.conn.execute("SELECT COUNT(*) FROM analysis_jobs WHERE status='queued'").fetchone()[0]
            if queued >= max_queued_jobs:
                raise QueueFullError("Analysis queue is full")
            job_id = uuid.uuid4().hex
            self.conn.execute(
                """INSERT INTO analysis_jobs
                   (job_id,kind,command,requester_id,destination,admission_key,
                    request_json,request_hash,accepted_at,cutoff_at,created_at,updated_at,intermediate_bytes)
                   VALUES (?,'analysis',?,?,?,?,?,?,?,?,?,?,?)""",
                (job_id, command, requester_id, destination, admission_key, encoded,
                 request_hash, accepted_at, cutoff_at, accepted_at, accepted_at, len(encoded.encode("utf-8"))),
            )
            self._sync_intermediate_no_tx(job_id)
            job = self.get_job(job_id)
        return job, True

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        cursor = self.conn.execute("SELECT * FROM analysis_jobs WHERE job_id=?", (job_id,))
        row = cursor.fetchone()
        if row is None:
            return None
        result = dict(zip((column[0] for column in cursor.description), row))
        for name in ("request", "checkpoint", "result", "delivery"):
            encoded = result.pop(f"{name}_json")
            value = json.loads(encoded) if encoded is not None else None
            if not isinstance(value, dict) and not (name == "result" and value is None):
                raise ValueError(f"Invalid analytic job {name} payload")
            result[name] = value
        return result

    def claim_job(self, job_id: str, *, max_running_jobs: int, now: datetime) -> dict[str, Any] | None:
        if type(max_running_jobs) is not int or max_running_jobs < 1:
            raise ValueError("Running job cap must be a positive integer")
        with common_sqlite.write_tx(self.conn, immediate=True):
            running = self.conn.execute("SELECT COUNT(*) FROM analysis_jobs WHERE status='running'").fetchone()[0]
            if running >= max_running_jobs:
                return None
            changed = self.conn.execute(
                """UPDATE analysis_jobs SET status='running',generation=generation+1,updated_at=?
                   WHERE job_id=? AND status='queued' AND cancel_requested=0""",
                (now.astimezone(timezone.utc).isoformat(), job_id),
            ).rowcount
            return self.get_job(job_id) if changed else None

    def queue_index_job(self, payload, *, index_key, index_version, rebuild, max_queued_jobs, now):
        """Keep one durable service job per index version, outside user admission."""
        with common_sqlite.write_tx(self.conn, immediate=True):
            existing = self.get_by_admission(index_key)
            if existing and (existing["kind"] != "fts_backfill" or existing["command"] is not None
                             or existing["requester_id"] is not None or existing["destination"] is not None):
                raise ValueError("Invalid index service identity")
            cookie = self.conn.execute("PRAGMA schema_version").fetchone()[0]
            if existing and not rebuild:
                state = existing["checkpoint"].get("fts", {})
                if (state.get("version") != index_version or state.get("schema_version") != cookie
                        or state.get("state") == "invalid"):
                    raise ValueError("fts_rebuild_required")
                if existing["status"] in {"queued", "running", "completed"}:
                    return existing
            queued = self.conn.execute("SELECT COUNT(*) FROM analysis_jobs WHERE status='queued'").fetchone()[0]
            if queued >= max_queued_jobs and not (existing and existing["status"] == "queued"):
                raise QueueFullError("Analysis queue is full")
            timestamp = now.astimezone(timezone.utc).isoformat()
            if existing and not rebuild:
                self.conn.execute("""UPDATE analysis_jobs SET status='queued',generation=generation+1,
                    cancel_requested=0,finished_at=NULL,updated_at=? WHERE job_id=?""", (timestamp, existing["job_id"]))
                return self.get_job(existing["job_id"])
            maximum = payload["settings"]["analysis"]["max_intermediate_bytes"]
            encoded = encode_payload(payload, max_bytes=maximum)
            checkpoint = canonical_json({"fts": {"version": index_version, "state": "building", "cursor": 0,
                "upper_rowid": None,
                "indexed_messages": 0, "needs_clear": True, "schema_version": cookie}})
            size = len(encoded.encode("utf-8")) + len(checkpoint.encode("utf-8"))
            if size > maximum:
                raise ValueError("Index metadata exceeds intermediate byte cap")
            job_id = existing["job_id"] if existing else uuid.uuid4().hex
            if existing:
                self.conn.execute("""UPDATE analysis_jobs SET status='queued',phase='select',generation=generation+1,
                    request_json=?,request_hash=?,checkpoint_json=?,cancel_requested=0,finished_at=NULL,updated_at=?,intermediate_bytes=?
                    WHERE job_id=?""", (encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest(), checkpoint, timestamp, size, job_id))
            else:
                self.conn.execute("""INSERT INTO analysis_jobs
                    (job_id,kind,admission_key,request_json,request_hash,accepted_at,cutoff_at,created_at,updated_at,
                     phase,checkpoint_json,intermediate_bytes) VALUES (?,'fts_backfill',?,?,?,?,?,?,?,'select',?,?)""",
                    (job_id, index_key, encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest(), timestamp,
                     timestamp, timestamp, timestamp, checkpoint, size))
            self._sync_intermediate_no_tx(job_id)
            return self.get_job(job_id)

    def require_running(self, job_id: str, *, generation: int) -> dict[str, Any]:
        job = self.get_job(job_id)
        if job is None or job["generation"] != generation or job["status"] != "running":
            raise StaleGenerationError("Analytic job generation or running state changed")
        if job["cancel_requested"]:
            raise InterruptedError("Analytic job cancelled")
        return job

    def set_analysis_phase(self, job_id: str, *, generation: int, phase: str):
        if phase not in {"sync", "select", "extract", "link", "merge", "synthesize", "verify", "deliver"}:
            raise ValueError("Invalid analytic phase")
        with common_sqlite.write_tx(self.conn, immediate=True):
            self.require_running(job_id, generation=generation)
            self.conn.execute("UPDATE analysis_jobs SET phase=?,updated_at=? WHERE job_id=?",
                (phase, datetime.now(timezone.utc).isoformat(), job_id))
            self._sync_intermediate_no_tx(job_id)

    def get_batch(self, batch_id: str):
        row = self.conn.execute("SELECT * FROM analysis_batches WHERE batch_id=?", (batch_id,)).fetchone()
        if row is None:
            return None
        batch = dict(row)
        batch["payload"] = json.loads(batch.pop("payload_json"))
        batch["attempts"] = json.loads(batch.pop("attempts_json"))
        if not isinstance(batch["payload"], dict) or not isinstance(batch["attempts"], list):
            raise ValueError("Invalid batch payload")
        return batch

    def _sync_intermediate_no_tx(self, job_id):
        return account_job_storage_no_tx(self.conn, job_id)

    def storage_usage(self, job_id):
        return job_storage_usage(self.conn, job_id)

    def get_batch_by_step(self, job_id, step_key):
        row = self.conn.execute("SELECT batch_id FROM analysis_batches WHERE job_id=? AND step_key=?", (job_id, step_key)).fetchone()
        return self.get_batch(row[0]) if row else None

    def find_extraction_plan(self, job_id, *, input_digest, plan):
        rows = self.conn.execute("""SELECT batch_id FROM analysis_batches WHERE job_id=? AND kind='extract'
            AND input_digest=? AND step_key NOT LIKE 'invalidated/%'
            ORDER BY CASE state WHEN 'succeeded' THEN 0 ELSE 1 END,batch_id""", (job_id, input_digest))
        for row in rows:
            batch = self.get_batch(row[0])
            if batch["payload"].get("plan") == plan and not batch["payload"].get("invalidated"):
                return batch
        return None

    def reconcile_extraction_batches(self, job_id, *, generation, seen):
        with common_sqlite.write_tx(self.conn, immediate=True):
            job = self.require_running(job_id, generation=generation)
            live = set(seen)
            pending = list(live)
            while pending:
                batch = self.get_batch(pending.pop())
                for child in batch["payload"].get("split_children", []):
                    if child not in live:
                        live.add(child)
                        pending.append(child)
            orphans = {row[0] for row in self.conn.execute("""SELECT batch_id FROM analysis_batches
                WHERE job_id=? AND kind='extract' AND step_key LIKE 'extract/%'""", (job_id,)) if row[0] not in live}
            if orphans:
                self._invalidate_dependencies_no_tx(job, seeds=orphans)
                self._write_checkpoint_no_tx(job_id, canonical_json(job["checkpoint"]), generation=generation)

    def manifest_reference(self, job_id, channel_id, message_id):
        row = self.conn.execute("""SELECT i.* FROM analysis_inputs i JOIN analysis_batches b USING(batch_id)
            WHERE b.job_id=? AND b.kind='select' AND i.channel_id=? AND i.message_id=? AND i.role='selected' LIMIT 1""",
            (job_id, channel_id, message_id)).fetchone()
        return dict(row) if row else None

    def source_channel(self, channel_id):
        row = self.conn.execute("SELECT channel_id,username,title,channel_type FROM channels WHERE channel_id=?", (channel_id,)).fetchone()
        return dict(row) if row else None

    def processed_source_refs(self, job_id, *, limit):
        rows = self.conn.execute("""SELECT i.channel_id,i.message_id,i.content_digest FROM analysis_inputs i
            JOIN analysis_batches b USING(batch_id) WHERE b.job_id=? AND b.kind='extract'
            AND b.state='succeeded' AND i.role='selected'
            GROUP BY i.channel_id,i.message_id,i.content_digest ORDER BY MIN(i.date_utc),i.channel_id,i.message_id LIMIT ?""",
            (job_id, limit))
        return [dict(row) for row in rows]

    def iter_leaf_evidence(self, job_id, *, page_size):
        after = ""
        while True:
            rows = self.conn.execute("""SELECT e.* FROM analysis_evidence e JOIN analysis_batches b USING(batch_id)
                WHERE b.job_id=? AND b.kind='extract' AND b.state='succeeded' AND e.record_id>?
                ORDER BY e.record_id LIMIT ?""", (job_id, after, page_size)).fetchall()
            if not rows:
                return
            for row in rows:
                value = dict(row)
                value["payload"] = json.loads(value.pop("payload_json"))
                after = value["record_id"]
                yield value

    @contextmanager
    def relation_index(self, job_id, *, generation, key_func):
        """Rebuildable connection-local index; evidence remains the durable owner."""
        job = self.require_running(job_id, generation=generation)
        self.conn.execute("""CREATE TEMP TABLE IF NOT EXISTS analysis_relation_keys (
            job_id TEXT,kind TEXT,key TEXT,record_id TEXT,PRIMARY KEY(job_id,kind,key,record_id)
        ) WITHOUT ROWID""")
        self.conn.execute("DELETE FROM temp.analysis_relation_keys WHERE job_id=?", (job_id,))
        try:
            for record in self.iter_leaf_evidence(job_id, page_size=max(1, job["request"]["settings"]["sync"]["batch_size"])):
                self.require_running(job_id, generation=generation)
                with common_sqlite.write_tx(self.conn):
                    self.conn.executemany("INSERT OR IGNORE INTO temp.analysis_relation_keys VALUES (?,?,?,?)",
                        ((job_id, kind, key, record["record_id"]) for kind, key in key_func(record)))
            yield
        finally:
            self.conn.execute("DELETE FROM temp.analysis_relation_keys WHERE job_id=?", (job_id,))

    def relation_candidate_ids(self, job_id, record_id, *, keys, limit):
        candidates = []
        for kind, key in keys:
            rows = self.conn.execute("""SELECT record_id FROM temp.analysis_relation_keys
                WHERE job_id=? AND kind=? AND key=? AND record_id>? ORDER BY record_id LIMIT ?""",
                (job_id, kind, key, record_id, limit + 1))
            candidates = sorted(set(candidates).union(row[0] for row in rows))[:limit + 1]
        return candidates

    def leaf_evidence(self, job_id, record_id):
        row = self.conn.execute("""SELECT e.* FROM analysis_evidence e JOIN analysis_batches b USING(batch_id)
            WHERE b.job_id=? AND b.kind='extract' AND b.state='succeeded' AND e.record_id=?""", (job_id, record_id)).fetchone()
        if row is None:
            return None
        value = dict(row)
        value["payload"] = json.loads(value.pop("payload_json"))
        return value

    def reply_message_id(self, channel_id, message_id):
        row = self.conn.execute("SELECT raw_json FROM messages WHERE channel_id=? AND message_id=?", (channel_id, message_id)).fetchone()
        return _same_channel_reply(json.loads(row[0]), channel_id) if row else None

    def iter_link_decisions(self, job_id):
        rows = self.conn.execute("""SELECT payload_json FROM analysis_batches
            WHERE job_id=? AND kind='link' AND state='succeeded' ORDER BY batch_id""", (job_id,))
        for row in rows:
            yield from json.loads(row[0])["output"]["decisions"]

    def incomplete_evidence_reasons(self, job_id):
        return ["extraction_incomplete" if row[0] == "extract" else "relations_incomplete"
                for row in self.conn.execute("""SELECT DISTINCT kind FROM analysis_batches
                    WHERE job_id=? AND kind IN ('extract','link') AND state='failed'""", (job_id,))]

    def load_leaf_cache(self, job_id, cache_key, *, kind):
        if kind not in {"extract", "expand_query"}:
            raise ValueError("Only validated leaf contracts may be reused across jobs")
        # A bounded snapshot prevents concurrent origin cleanup from turning a
        # non-empty result into an apparent valid empty cache entry.
        with common_sqlite.write_tx(self.conn):
            target = self.get_job(job_id)
            row = self.conn.execute("""SELECT b.batch_id FROM analysis_batches b JOIN analysis_jobs j USING(job_id)
                WHERE b.cache_key=? AND b.kind=? AND b.state='succeeded' AND j.kind='analysis'
                AND j.job_id!=? AND j.requester_id=? AND j.destination=?
                ORDER BY b.updated_at DESC,b.batch_id DESC LIMIT 1""",
                (cache_key, kind, job_id, target["requester_id"], target["destination"])).fetchone()
            if row is None:
                return None
            batch = self.get_batch(row[0])
            records = [dict(row) for row in self.conn.execute("SELECT * FROM analysis_evidence WHERE batch_id=? ORDER BY record_id", (batch["batch_id"],))]
            return {"batch": batch, "job": self.get_job(batch["job_id"]), "records": records}

    def budget_fallback(self, job_id, *, generation, reason):
        if reason not in BUDGET_REASONS:
            raise ValueError("Unknown budget stop reason")
        job = self.require_running(job_id, generation=generation)
        previous = job["result"] or {}
        reasons = set(previous.get("reasons", [])) | {reason}
        if previous.get("kind") == "budget_stop":
            reasons.add(previous["reason"])
        return {**_fallback_document(job, reason, []), "reasons": sorted(reasons)}

    def record_budget_stop(self, job_id, *, generation, reason):
        if reason not in BUDGET_REASONS:
            raise ValueError("Unknown budget stop reason")
        with common_sqlite.write_tx(self.conn, immediate=True):
            job = self.require_running(job_id, generation=generation)
            policy = job["request"]["settings"]["analysis"]
            limit = min(policy["output"]["sources_per_item"], policy["max_messages"])
            rows = self.conn.execute("""SELECT i.channel_id,i.message_id,i.content_digest FROM analysis_inputs i
                JOIN analysis_batches b USING(batch_id) JOIN messages m ON m.channel_id=i.channel_id AND m.message_id=i.message_id
                WHERE b.job_id=? AND b.kind='extract' AND b.state='succeeded' AND i.role='selected'
                GROUP BY i.channel_id,i.message_id,i.content_digest ORDER BY MIN(i.date_utc),i.channel_id,i.message_id LIMIT ?""",
                (job_id, limit))
            sources = [{"channel_id": row[0], "message_id": row[1], "content_digest": row[2]} for row in rows
                       if len(row[2]) == 64 and all(char in "0123456789abcdef" for char in row[2])]
            fallback = _fallback_document(job, reason, sources)
            result = job["result"] if job["result"] is not None else fallback
            job["checkpoint"]["budget_stop"] = {"reason": reason, "phase": fallback["phase"]}
            self.conn.execute("UPDATE analysis_jobs SET checkpoint_json=?,result_json=?,updated_at=? WHERE job_id=?",
                (canonical_json(job["checkpoint"]), canonical_json(result), datetime.now(timezone.utc).isoformat(), job_id))
            self._sync_intermediate_no_tx(job_id)
        return result

    def plan_batch(self, job_id, *, generation, step_key, kind, payload, input_digest=None, cache_key=None):
        if not isinstance(step_key, str) or not step_key.strip() or kind not in {"expand_query", "extract", "link", "merge", "synthesize"}:
            raise ValueError("Invalid provider step identity")
        with common_sqlite.write_tx(self.conn, immediate=True):
            job = self.require_running(job_id, generation=generation)
            if job["kind"] != "analysis":
                raise ValueError("Internal index jobs cannot plan provider requests")
            encoded = encode_payload({"plan": payload}, max_bytes=job["request"]["settings"]["analysis"]["max_intermediate_bytes"])
            existing = self.conn.execute("SELECT batch_id FROM analysis_batches WHERE job_id=? AND step_key=?", (job_id, step_key)).fetchone()
            if existing:
                batch = self.get_batch(existing[0])
                if (batch["kind"] != kind or batch["input_digest"] != input_digest or batch["cache_key"] != cache_key
                        or canonical_json(batch["payload"].get("plan", {})) != canonical_json(payload)):
                    raise ValueError("Provider step identity conflict")
                return batch
            batch_id, now = uuid.uuid4().hex, datetime.now(timezone.utc).isoformat(timespec="microseconds")
            self.conn.execute("""INSERT INTO analysis_batches
                (batch_id,job_id,step_key,kind,payload_json,input_digest,cache_key,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?)""", (batch_id, job_id, step_key, kind, encoded, input_digest, cache_key, now, now))
            batch = self.get_batch(batch_id)
            self._sync_intermediate_no_tx(job_id)
            return batch

    def split_extraction_batch(self, job_id, batch_id, *, generation, children):
        with common_sqlite.write_tx(self.conn, immediate=True):
            job = self.require_running(job_id, generation=generation)
            parent = self.get_batch(batch_id)
            if parent is None or parent["job_id"] != job_id or parent["kind"] != "extract" or parent["state"] != "running" or len(children) != 2:
                raise ValueError("Invalid extraction split")
            planned = []
            now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
            for index, child in enumerate(children):
                child_id = uuid.uuid4().hex
                encoded = encode_payload({"plan": {**child["plan"], "parent_batch_id": batch_id}},
                    max_bytes=job["request"]["settings"]["analysis"]["max_intermediate_bytes"])
                self.conn.execute("""INSERT INTO analysis_batches
                    (batch_id,job_id,step_key,kind,payload_json,input_digest,cache_key,created_at,updated_at)
                    VALUES (?,?,?,'extract',?,?,?,?,?)""", (child_id, job_id, parent["step_key"] + f"/output/{index}",
                    encoded, child["input_digest"], child.get("cache_key"), now, now))
                planned.append(child_id)
            parent["payload"].update(outcome="split", split_children=planned)
            self.conn.execute("UPDATE analysis_batches SET state='skipped',payload_json=? WHERE batch_id=?", (canonical_json(parent["payload"]), batch_id))
            self._sync_intermediate_no_tx(job_id)
            return planned

    def claim_repair(self, job_id, batch_id, *, generation):
        with common_sqlite.write_tx(self.conn, immediate=True):
            job = self.require_running(job_id, generation=generation)
            batch = self.get_batch(batch_id)
            if batch is None or batch["job_id"] != job_id or batch["state"] not in {"planned", "running"}:
                raise ValueError("Repair outside active step")
            count = batch["payload"].get("repair_attempts", 0)
            if count >= job["request"]["settings"]["analysis"]["max_repair_attempts"]:
                return False
            batch["payload"]["repair_attempts"] = count + 1
            self.conn.execute("UPDATE analysis_batches SET payload_json=? WHERE batch_id=?", (canonical_json(batch["payload"]), batch_id))
            self._sync_intermediate_no_tx(job_id)
            return True

    def set_batch_outcome(self, job_id, batch_id, *, generation, outcome):
        if outcome not in {"refused", "invalid_schema", "incomplete", "saturated", "max_split_depth", "provider_error"}:
            raise ValueError("Invalid extraction outcome")
        with common_sqlite.write_tx(self.conn, immediate=True):
            self.require_running(job_id, generation=generation)
            batch = self.get_batch(batch_id)
            if batch is None or batch["job_id"] != job_id or batch["state"] not in {"planned", "running"}:
                raise ValueError("Extraction outcome outside active step")
            batch["payload"]["outcome"] = outcome
            self.conn.execute("UPDATE analysis_batches SET state='failed',payload_json=? WHERE batch_id=?", (canonical_json(batch["payload"]), batch_id))
            self._sync_intermediate_no_tx(job_id)

    def skip_counted_batch(self, job_id, batch_id, *, generation):
        with common_sqlite.write_tx(self.conn, immediate=True):
            self.require_running(job_id, generation=generation)
            batch = self.get_batch(batch_id)
            if (batch is None or batch["job_id"] != job_id or batch["state"] not in {"planned", "running", "skipped"}
                    or any(a["request_kind"] == "inference" for a in batch["attempts"])):
                raise ValueError("Only count-only steps may be skipped for input splitting")
            self.conn.execute("UPDATE analysis_batches SET state='skipped' WHERE batch_id=?", (batch_id,))
            self._sync_intermediate_no_tx(job_id)

    def save_token_count(self, job_id, batch_id, *, generation, payload_digest, input_tokens):
        _budget_integer(input_tokens, "input token count")
        with common_sqlite.write_tx(self.conn, immediate=True):
            self.require_running(job_id, generation=generation)
            batch = self.get_batch(batch_id)
            if batch is None or batch["job_id"] != job_id or batch["state"] not in {"planned", "running"}:
                raise ValueError("Token count step outside active job scope")
            batch["payload"].setdefault("token_counts", {})[payload_digest] = input_tokens
            self.conn.execute("UPDATE analysis_batches SET payload_json=? WHERE batch_id=?", (canonical_json(batch["payload"]), batch_id))
            self._sync_intermediate_no_tx(job_id)

    def reserve_provider_attempt(self, job_id, batch_id, *, generation, attempt_id, request_kind,
                                   input_tokens=0, output_tokens=0):
        if not isinstance(attempt_id, str) or not attempt_id or request_kind not in {"inference", "token_count"}:
            raise ValueError("Invalid provider attempt identity")
        _budget_integer(input_tokens, "input tokens")
        _budget_integer(output_tokens, "output tokens")
        tokens = input_tokens + output_tokens if request_kind == "inference" else 0
        if tokens > SQLITE_INTEGER_MAX or (request_kind == "inference" and output_tokens == 0):
            raise ValueError("Invalid token reservation")
        if request_kind == "token_count" and (input_tokens or output_tokens):
            raise ValueError("Token count does not reserve inference tokens")
        with common_sqlite.write_tx(self.conn, immediate=True):
            job = self.require_running(job_id, generation=generation)
            batch = self.get_batch(batch_id)
            if batch is None or batch["job_id"] != job_id or batch["kind"] == "select":
                raise ValueError("Provider step outside job scope")
            stop = job["checkpoint"].get("budget_stop")
            if stop and (batch["kind"] not in {"merge", "synthesize"} or stop["reason"] == "usage_overrun"):
                raise BudgetExceededError(stop["reason"])
            if batch["state"] not in {"planned", "running"}:
                raise ValueError("Provider step is terminal")
            if any(item["attempt_id"] == attempt_id for item in batch["attempts"]):
                raise ValueError("attempt_already_reserved")
            policy = job["request"]["settings"]["analysis"]
            if request_kind == "token_count" and job["used_tokens"] + job["reserved_tokens"] >= policy["max_total_tokens"]:
                raise BudgetExceededError("max_total_tokens")
            if request_kind == "inference":
                if input_tokens > policy["max_input_tokens"]:
                    raise BudgetExceededError("max_input_tokens")
                output_cap = (policy["output"]["profiles"][job["request"]["request"]["detail"]]["max_output_tokens"]
                              if batch["kind"] == "synthesize" else job["request"]["settings"]["digest_ai"]["max_output_tokens"])
                if output_tokens > output_cap:
                    raise BudgetExceededError("stage_output_limit")
            protected = self._completion_reserve_no_tx(job, batch, request_kind=request_kind)
            calls = int(request_kind == "inference")
            for reason, used, delta in (("max_llm_calls", job["llm_calls"], calls + protected["llm_calls"]),
                                       ("max_provider_requests", job["provider_requests"], 1 + protected["provider_requests"]),
                                       ("max_total_tokens", job["used_tokens"] + job["reserved_tokens"], tokens + protected["tokens"])):
                if used + delta > policy[reason]:
                    raise BudgetExceededError(reason)
            now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
            attempt = {"attempt_id": attempt_id, "generation": generation, "request_kind": request_kind,
                       "input_tokens": input_tokens, "output_limit": output_tokens, "reserved_tokens": tokens,
                       "state": "reserved", "usage": None, "created_at": now, "protected_tail": protected}
            attempt["receipt_allowance"] = _receipt_allowance(attempt)
            batch["attempts"].append(attempt)
            batch.update(state="running", updated_at=now)
            self.conn.execute("UPDATE analysis_batches SET state='running',attempts_json=?,updated_at=? WHERE batch_id=?",
                               (canonical_json(batch["attempts"]), now, batch_id))
            self.conn.execute("""UPDATE analysis_jobs SET llm_calls=llm_calls+?,provider_requests=provider_requests+1,
                reserved_tokens=reserved_tokens+?,updated_at=? WHERE job_id=?""", (calls, tokens, now, job_id))
            self._sync_intermediate_no_tx(job_id)
            return attempt

    def _completion_reserve_no_tx(self, job, batch, *, request_kind):
        roots = 0
        if batch["kind"] in {"extract", "link"}:
            # Merge starts from individual evidence records, not one node per
            # extraction request. A request can yield several independent facts.
            roots = self.conn.execute("""SELECT COUNT(*) FROM analysis_evidence e
                JOIN analysis_batches b ON b.batch_id=e.batch_id
                WHERE b.job_id=? AND b.kind='extract'""", (job["job_id"],)).fetchone()[0]
            roots += int(batch["kind"] == "extract")
        elif batch["kind"] == "merge":
            state = job["checkpoint"].get("merge")
            if state is not None and batch["payload"]["plan"].get("operation") == "merge":
                roots = len(state["frontier"]) - state["cursor"] - len(batch["payload"]["plan"]["inputs"]) + len(state["next_frontier"]) + 1
            else:
                roots = 1  # Global rank leaves one final synthesis authorization.
        settings = job["request"]["settings"]
        if batch["kind"] == "merge" and job["checkpoint"].get("merge", {}).get("fan_in"):
            settings = {**settings, "analysis": {**settings["analysis"], "merge_fan_in": job["checkpoint"]["merge"]["fan_in"]}}
        reserve = completion_budget(settings, detail=job["request"]["request"]["detail"], roots=roots)
        if request_kind == "token_count":
            # Counting must leave an inference authorization for this same step.
            reserve["llm_calls"] += 1
            reserve["provider_requests"] += 1
        return reserve

    def settle_provider_attempt(self, job_id, batch_id, *, generation, attempt_id, outcome, usage=None):
        with common_sqlite.write_tx(self.conn, immediate=True):
            attempt, overrun = self._settle_provider_attempt_no_tx(
                job_id, batch_id, generation=generation, attempt_id=attempt_id, outcome=outcome, usage=usage)
        if overrun:
            # Actual usage is durable even though the caller must stop work.
            raise BudgetExceededError("usage_overrun")
        return attempt

    def _settle_provider_attempt_no_tx(self, job_id, batch_id, *, generation, attempt_id, outcome, usage=None):
        if outcome not in {"succeeded", "failed", "unknown"}:
            raise ValueError("Invalid provider attempt outcome")
        if usage is not None:
            if (not isinstance(usage, dict) or not {"input_tokens", "output_tokens"} <= set(usage)
                    or set(usage) - {"input_tokens", "output_tokens", "cached_input_tokens"}):
                raise ValueError("Invalid provider usage")
            usage = {key: _budget_integer(usage.get(key, 0), key) for key in ("input_tokens", "output_tokens", "cached_input_tokens")}
            if usage["cached_input_tokens"] > usage["input_tokens"] or usage["input_tokens"] + usage["output_tokens"] > SQLITE_INTEGER_MAX:
                raise ValueError("Invalid provider usage")
        job = self.get_job(job_id)
        if job is None or job["generation"] != generation:
            raise StaleGenerationError("Provider receipt generation changed")
        batch = self.get_batch(batch_id)
        if batch is None or batch["job_id"] != job_id:
            raise ValueError("Provider receipt outside job scope")
        attempt = next((item for item in batch["attempts"] if item["attempt_id"] == attempt_id), None)
        if attempt is None:
            raise ValueError("Unknown provider attempt")
        if attempt["state"] == outcome and attempt["usage"] == usage:
            return attempt, bool(attempt.get("overrun"))
        if attempt["usage"] is not None:
            raise ValueError("Provider usage conflict")
        used = usage["input_tokens"] + usage["output_tokens"] if usage is not None else 0
        if attempt["request_kind"] == "token_count" and used:
            raise ValueError("Token count has no inference usage")
        release = attempt["reserved_tokens"] if usage is not None else 0
        overrun = usage is not None and attempt["request_kind"] == "inference" and (
            used > attempt["reserved_tokens"] or usage["output_tokens"] > attempt["output_limit"]
            or usage["input_tokens"] > job["request"]["settings"]["analysis"]["max_input_tokens"])
        attempt.update(state=outcome, usage=usage, settled_at=datetime.now(timezone.utc).isoformat(timespec="microseconds"))
        if overrun:
            attempt["overrun"] = True
        attempt["receipt_allowance"] = _receipt_allowance(attempt)
        self.conn.execute("UPDATE analysis_batches SET attempts_json=?,updated_at=? WHERE batch_id=?",
                           (canonical_json(batch["attempts"]), attempt["settled_at"], batch_id))
        self.conn.execute("UPDATE analysis_jobs SET used_tokens=used_tokens+?,reserved_tokens=reserved_tokens-?,updated_at=? WHERE job_id=?",
                           (used, release, attempt["settled_at"], job_id))
        if overrun:
            job["checkpoint"]["budget_stop"] = {"reason": "usage_overrun", "phase": job["phase"]}
            self.conn.execute("UPDATE analysis_jobs SET checkpoint_json=? WHERE job_id=?", (canonical_json(job["checkpoint"]), job_id))
        self._sync_intermediate_no_tx(job_id)
        return attempt, overrun

    def complete_batch(self, job_id, batch_id, *, generation, records, inputs, attempt_id=None, usage=None, provenance=None, json_output=None, merge_transition=None, final_result=None):
        """Persist validated output atomically; output failure cannot erase incurred cost."""
        output = {"records": records, "inputs": inputs, "attempt_id": attempt_id, "usage": usage}
        if provenance is not None:
            if not isinstance(provenance, dict):
                raise ValueError("Invalid provider provenance")
            output["provenance"] = provenance
        if json_output is not None:
            if not isinstance(json_output, dict):
                raise ValueError("Validated JSON output must be an object")
            output["json_output"] = json_output
        if final_result is not None:
            output["final_result"] = final_result
        digest = hashlib.sha256(canonical_json(output).encode("utf-8")).hexdigest()
        try:
            with common_sqlite.write_tx(self.conn, immediate=True):
                job = self.require_running(job_id, generation=generation)
                batch = self.get_batch(batch_id)
                if batch is None or batch["job_id"] != job_id or batch["kind"] == "select":
                    raise ValueError("Output step outside job scope")
                if json_output is not None and (batch["kind"] not in {"expand_query", "link", "merge", "synthesize"} or records or inputs):
                    raise ValueError("JSON stage output cannot replace evidence or lineage")
                if final_result is not None and (batch["kind"] != "synthesize" or json_output is None or not isinstance(final_result, dict)):
                    raise ValueError("Final result requires validated synthesis output")
                if batch["state"] == "succeeded":
                    if batch["payload"].get("completion_digest") != digest:
                        raise ValueError("Batch output conflict")
                    return batch
                if merge_transition is not None:
                    if batch["kind"] != "merge" or json_output is None:
                        raise ValueError("Frontier transition requires merge output")
                    if job["checkpoint"].get("merge") != merge_transition["expected"]:
                        raise StaleGenerationError("Merge frontier changed")
                if batch["state"] not in {"planned", "running"}:
                    raise ValueError("Output step is terminal")
                if attempt_id is None:
                    if any(a["request_kind"] == "inference" for a in batch["attempts"]) or usage is not None:
                        raise ValueError("Physical output requires an inference receipt")
                else:
                    attempt = next((a for a in batch["attempts"] if a["attempt_id"] == attempt_id), None)
                    if attempt is None or attempt["request_kind"] != "inference":
                        raise ValueError("Output requires an inference attempt")
                    _, overrun = self._settle_provider_attempt_no_tx(job_id, batch_id, generation=generation,
                        attempt_id=attempt_id, outcome="succeeded", usage=usage)
                    if overrun:
                        raise BudgetExceededError("usage_overrun")
                count = self.conn.execute("""SELECT COUNT(*) FROM analysis_evidence e
                    JOIN analysis_batches b USING(batch_id) WHERE b.job_id=?""", (job_id,)).fetchone()[0]
                if count + len(records) > job["request"]["settings"]["analysis"]["max_evidence_records"]:
                    raise BudgetExceededError("max_evidence_records")
                for item in records:
                    self.conn.execute("""INSERT INTO analysis_evidence(record_id,batch_id,record_type,topic_key,payload_json)
                        VALUES (?,?,?,?,?)""", (item["record_id"], batch_id, item["record_type"], item.get("topic_key"), canonical_json(item["payload"])))
                for item in inputs:
                    self.conn.execute("""INSERT INTO analysis_inputs
                        (batch_id,channel_id,message_id,fragment_start,fragment_end,role,date_utc,content_digest)
                        VALUES (?,?,?,?,?,?,?,?)""", (batch_id, *(item[key] for key in
                        ("channel_id", "message_id", "fragment_start", "fragment_end", "role", "date_utc", "content_digest"))))
                batch["payload"]["completion_digest"] = digest
                if provenance is not None:
                    batch["payload"]["provenance"] = provenance
                if json_output is not None:
                    batch["payload"]["output"] = json_output
                self.conn.execute("UPDATE analysis_batches SET state='succeeded',payload_json=? WHERE batch_id=?",
                    (canonical_json(batch["payload"]), batch_id))
                self.conn.execute("UPDATE analysis_jobs SET evidence_records=? WHERE job_id=?", (count + len(records), job_id))
                if merge_transition is not None:
                    self._advance_merge_no_tx(job, batch, json_output, merge_transition["next"])
                if final_result is not None:
                    self.conn.execute("UPDATE analysis_jobs SET result_json=? WHERE job_id=?", (canonical_json(final_result), job_id))
                self._sync_intermediate_no_tx(job_id)
        except Exception:
            current = self.get_batch(batch_id)
            if (attempt_id is not None and current is not None and current["job_id"] == job_id
                    and any(a["attempt_id"] == attempt_id and a["request_kind"] == "inference" for a in current["attempts"])):
                self.settle_provider_attempt(job_id, batch_id, generation=generation,
                    attempt_id=attempt_id, outcome="succeeded", usage=usage)
            raise
        return self.get_batch(batch_id)

    def save_analysis_result(self, job_id, result, *, generation):
        with common_sqlite.write_tx(self.conn, immediate=True):
            self.require_running(job_id, generation=generation)
            self.conn.execute("UPDATE analysis_jobs SET result_json=? WHERE job_id=?", (canonical_json(result), job_id))
            self._sync_intermediate_no_tx(job_id)

    def save_full_artifact(self, job_id, *, generation, relative_path, content_hash, result_digest, partial, ready=True, previous_hash=None):
        with common_sqlite.write_tx(self.conn, immediate=True):
            job = self.require_running(job_id, generation=generation)
            expected = (Path(job["request"]["settings"]["analysis"]["output"]["results_dir"]) / (job_id + ".md")).as_posix()
            if (job["request"]["request"]["detail"] != "full" or job["result"] is None or relative_path != expected
                    or type(partial) is not bool or type(ready) is not bool or not isinstance(content_hash, str) or len(content_hash) != 64
                    or any(c not in "0123456789abcdef" for c in content_hash)):
                raise ValueError("Invalid full artifact metadata")
            actual = hashlib.sha256(canonical_json(job["result"]).encode("utf-8")).hexdigest()
            if result_digest != actual:
                raise ValueError("Artifact result changed")
            job["checkpoint"]["artifact"] = {"result_digest": result_digest, "content_hash": content_hash, "partial": partial}
            if not ready:
                if previous_hash is not None:
                    if not isinstance(previous_hash, str) or len(previous_hash) != 64 or any(c not in "0123456789abcdef" for c in previous_hash):
                        raise ValueError("Invalid previous artifact hash")
                    job["checkpoint"]["artifact"]["previous_hash"] = previous_hash
                self._write_checkpoint_no_tx(job_id, canonical_json(job["checkpoint"]), generation=generation)
                return
            delivery = job["delivery"]
            if delivery.get("outcome") == "failed" and "parts" not in delivery:
                delivery = {}
            self.conn.execute("""UPDATE analysis_jobs SET full_path=?,full_hash=?,checkpoint_json=?,delivery_json=?,
                phase='verify',updated_at=? WHERE job_id=?""", (relative_path, content_hash, canonical_json(job["checkpoint"]),
                canonical_json(delivery), datetime.now(timezone.utc).isoformat(), job_id))
            self._sync_intermediate_no_tx(job_id)

    def _write_delivery_no_tx(self, job_id, delivery):
        if not self.conn.in_transaction:
            raise ValueError("Delivery write requires a transaction")
        self.conn.execute("UPDATE analysis_jobs SET delivery_json=?,phase='deliver',updated_at=? WHERE job_id=?",
            (canonical_json(delivery), datetime.now(timezone.utc).isoformat(), job_id))
        self._sync_intermediate_no_tx(job_id)

    def prepare_delivery(self, job_id, *, generation, chunks, parse_mode, detail, analysis_partial):
        with common_sqlite.write_tx(self.conn, immediate=True):
            job = self.require_running(job_id, generation=generation)
            settings = job["request"]["settings"]
            output = settings["analysis"]["output"]
            if (job["result"] is None or detail != job["request"]["request"]["detail"] or not isinstance(chunks, list)
                    or not 1 <= len(chunks) <= output["max_messages"] or type(analysis_partial) is not bool
                    or any(not isinstance(text, str) or not text.strip() or len(text.encode("utf-16-le")) // 2 >
                           min(4096, settings["bridge"]["text_chunk_size"]) for text in chunks)):
                raise ValueError("Invalid delivery plan")
            if detail == "full":
                if parse_mode is not None or not job["full_hash"] or chunks != [job["full_path"]]:
                    raise ValueError("Full delivery requires the saved artifact path")
            elif parse_mode != "HTML" or sum(map(len, chunks)) > output["profiles"][detail]["max_chars"]:
                raise ValueError("Invalid text delivery bounds")
            identity = {"destination": job["destination"], "detail": detail, "parse_mode": parse_mode, "chunks": chunks,
                        "analysis_partial": analysis_partial, "artifact_hash": job["full_hash"] if detail == "full" else None}
            digest = hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
            if job["delivery"]:
                if job["delivery"].get("plan_digest") != digest:
                    raise ValueError("delivery_plan_conflict")
                return job["delivery"]
            delivery = {"version": 1, "destination": job["destination"], "detail": detail, "parse_mode": parse_mode,
                "plan_digest": digest, "analysis_partial": analysis_partial, "artifact_hash": identity["artifact_hash"], "outcome": "prepared",
                "parts": [{"text": text, "outcome": "pending", "attempts": []} for text in chunks]}
            self._write_delivery_no_tx(job_id, delivery)
            return delivery

    def begin_delivery_attempt(self, job_id, *, generation, part_index):
        with common_sqlite.write_tx(self.conn, immediate=True):
            job = self.require_running(job_id, generation=generation)
            delivery = job["delivery"]
            if delivery.get("outcome") == "unknown":
                raise ValueError("unknown_delivery")
            parts = delivery.get("parts", [])
            if (type(part_index) is not int or not 0 <= part_index < len(parts)
                    or any(part["outcome"] != "confirmed" for part in parts[:part_index])):
                raise ValueError("Invalid delivery order")
            part = parts[part_index]
            if part["outcome"] not in {"pending", "refused"}:
                raise ValueError("Delivery part cannot be sent again")
            if len(part["attempts"]) >= job["request"]["settings"]["bridge"]["send_message_retry_attempts"]:
                raise ValueError("delivery_attempts_exhausted")
            attempt = {"attempt_id": uuid.uuid4().hex, "generation": generation, "outcome": "intent",
                "started_at": datetime.now(timezone.utc).isoformat(), "message_id": None, "chat_id": None,
                "error_code": None, "retry_after": None}
            part["attempts"].append(attempt)
            part["outcome"] = "intent"
            delivery.pop("error", None)
            delivery["outcome"] = "unknown"
            self._write_delivery_no_tx(job_id, delivery)
            return attempt

    def settle_delivery_attempt(self, job_id, *, generation, part_index, attempt_id, outcome,
                                message_id=None, chat_id=None, error_code=None, retry_after=None):
        with common_sqlite.write_tx(self.conn, immediate=True):
            job = self.get_job(job_id)
            if job is None or job["generation"] != generation or job["status"] != "running":
                raise StaleGenerationError("Delivery receipt generation changed")
            parts = job["delivery"].get("parts", [])
            if type(part_index) is not int or not 0 <= part_index < len(parts) or not parts[part_index]["attempts"]:
                raise ValueError("Delivery receipt outside plan")
            attempt = parts[part_index]["attempts"][-1]
            if attempt["attempt_id"] != attempt_id or attempt["generation"] != generation:
                raise ValueError("Delivery receipt outside attempt")
            if outcome not in {"confirmed", "refused", "unknown"}:
                raise ValueError("Invalid delivery outcome")
            if outcome == "confirmed":
                if type(message_id) is not int or not 1 <= message_id <= SQLITE_INTEGER_MAX or str(chat_id) != job["destination"]:
                    raise ValueError("Invalid Telegram receipt")
                chat_id = str(chat_id)
            elif message_id is not None or chat_id is not None:
                raise ValueError("Unconfirmed send cannot carry a receipt")
            for value in (error_code, retry_after):
                if value is not None and (type(value) is not int or not 0 <= value <= SQLITE_INTEGER_MAX):
                    raise ValueError("Invalid Telegram error metadata")
            receipt = {"outcome": outcome, "message_id": message_id, "chat_id": chat_id,
                       "error_code": error_code, "retry_after": retry_after}
            if attempt["outcome"] != "intent":
                if any(attempt[key] != value for key, value in receipt.items()):
                    raise ValueError("Conflicting delivery receipt")
                return job["delivery"]
            attempt.update(receipt, finished_at=datetime.now(timezone.utc).isoformat())
            parts[part_index]["outcome"] = outcome
            states = {part["outcome"] for part in parts}
            job["delivery"]["outcome"] = ("unknown" if states & {"intent", "unknown"} else "confirmed" if states == {"confirmed"}
                else "partial" if "confirmed" in states else "failed")
            self._write_delivery_no_tx(job_id, job["delivery"])
            return job["delivery"]

    def stop_delivery(self, job_id, *, generation, reason):
        if reason not in DELIVERY_STOP_REASONS:
            raise ValueError("Unknown delivery stop reason")
        with common_sqlite.write_tx(self.conn, immediate=True):
            job = self.require_running(job_id, generation=generation)
            delivery = job["delivery"]
            if not delivery:
                if reason != "artifact_unavailable":
                    raise ValueError("Delivery is not prepared")
                delivery = {"outcome": "failed", "error": reason}
            if delivery["outcome"] not in {"unknown", "confirmed"}:
                delivery["outcome"] = "partial" if any(p["outcome"] == "confirmed" for p in delivery.get("parts", [])) else "failed"
                delivery["error"] = reason
                self._write_delivery_no_tx(job_id, delivery)
            return delivery

    def processed_analysis_messages(self, job_id):
        return self.conn.execute("""SELECT COUNT(*) FROM (SELECT i.channel_id,i.message_id FROM analysis_inputs i
            JOIN analysis_batches b USING(batch_id) WHERE b.job_id=? AND b.kind='extract' AND b.state='succeeded'
            AND i.role='selected' GROUP BY i.channel_id,i.message_id)""", (job_id,)).fetchone()[0]

    def complete_merge_batch(self, job_id, batch_id, *, generation, expected_state, next_state, output, attempt_id, usage):
        if attempt_id is None:
            with common_sqlite.write_tx(self.conn, immediate=True):
                job = self.require_running(job_id, generation=generation)
                batch = self.get_batch(batch_id)
                if (batch is None or batch["job_id"] != job_id or batch["kind"] != "merge" or batch["state"] != "succeeded"
                        or batch["payload"].get("output") != output):
                    raise ValueError("Only validated same-job merge output may be adopted")
                if job["checkpoint"].get("merge") != expected_state:
                    raise StaleGenerationError("Merge frontier changed")
                self._advance_merge_no_tx(job, batch, output, next_state)
                self._sync_intermediate_no_tx(job_id)
            return self.get_batch(batch_id)
        return self.complete_batch(job_id, batch_id, generation=generation, records=[], inputs=[],
            json_output=output, attempt_id=attempt_id, usage=usage,
            merge_transition={"expected": expected_state, "next": next_state})

    def _advance_merge_no_tx(self, job, batch, output, next_state):
        for ref in batch["payload"]["plan"]["inputs"]:
            if ref["kind"] != "evidence":
                continue
            source = self.get_batch(ref["batch_id"])
            status = next(value["disposition"] for value in output["dispositions"] if value["input_id"] == ref["node_id"])
            source["payload"].setdefault("record_dispositions", {})[ref["node_id"]] = status
            self.conn.execute("UPDATE analysis_batches SET payload_json=? WHERE batch_id=?", (canonical_json(source["payload"]), ref["batch_id"]))
        checkpoint = {**job["checkpoint"], "merge": next_state}
        self.conn.execute("UPDATE analysis_jobs SET checkpoint_json=? WHERE job_id=?", (canonical_json(checkpoint), job["job_id"]))

    def finalize_leaf_dispositions(self, job_id, *, generation):
        job = self.require_running(job_id, generation=generation)
        state = job["checkpoint"].get("merge", {})
        direct = {ref["node_id"] for ref in state.get("frontier", []) if ref["kind"] == "evidence"} if state.get("state") == "ready" else set()
        after = ""
        while True:
            row = self.conn.execute("""SELECT b.batch_id FROM analysis_batches b WHERE b.job_id=? AND b.kind='extract'
                AND b.batch_id>? ORDER BY b.batch_id LIMIT 1""", (job_id, after)).fetchone()
            if row is None:
                return
            after = row[0]
            with common_sqlite.write_tx(self.conn, immediate=True):
                self.require_running(job_id, generation=generation)
                batch = self.get_batch(after)
                count = self.conn.execute("SELECT COUNT(*) FROM analysis_evidence WHERE batch_id=?", (after,)).fetchone()[0]
                for row in self.conn.execute("SELECT record_id FROM analysis_evidence WHERE batch_id=?", (after,)):
                    if row[0] in direct:
                        batch["payload"].setdefault("record_dispositions", {})[row[0]] = "used"
                batch["payload"]["disposition"] = _leaf_disposition(batch, count)
                self.conn.execute("UPDATE analysis_batches SET payload_json=? WHERE batch_id=?", (canonical_json(batch["payload"]), after))
                self._sync_intermediate_no_tx(job_id)

    def iter_leaf_dispositions(self, job_id):
        # This read path remains available when the global byte cap has stopped
        # materialization of optional per-batch status metadata.
        rows = self.conn.execute("""SELECT b.batch_id,b.state,b.payload_json,
            (SELECT COUNT(*) FROM analysis_evidence e WHERE e.batch_id=b.batch_id) AS record_count
            FROM analysis_batches b WHERE b.job_id=? AND b.kind='extract' ORDER BY b.batch_id""", (job_id,))
        for row in rows:
            batch = {"state": row[1], "payload": json.loads(row[2])}
            yield {"batch_id": row[0], "disposition": _leaf_disposition(batch, row[3])}

    def invalidate_batch_dependencies(self, job_id, batch_id, *, generation):
        with common_sqlite.write_tx(self.conn, immediate=True):
            job = self.require_running(job_id, generation=generation)
            batch = self.get_batch(batch_id)
            if batch is None or batch["job_id"] != job_id or batch["kind"] == "select":
                raise ValueError("Invalidation outside analytic dependency scope")
            affected = self._invalidate_dependencies_no_tx(job, seeds={batch_id})
            self._write_checkpoint_no_tx(job_id, canonical_json(job["checkpoint"]), generation=generation)
            return affected

    def _invalidate_dependencies_no_tx(self, job, *, seeds=(), changed_sources=()):
        affected, record_ids = set(seeds), set()
        changed_sources = set(changed_sources)
        grew = True
        while grew:
            previous = (len(affected), len(record_ids))
            rows = self.conn.execute("SELECT batch_id,kind,payload_json FROM analysis_batches WHERE job_id=? AND kind!='select'", (job["job_id"],))
            for row in rows:
                batch_id, kind = row[0], row[1]
                payload = json.loads(row[2])
                if payload.get("invalidated"):
                    continue
                plan = payload.get("plan", {})
                if kind == "extract":
                    refs = plan.get("inputs", []) + plan.get("context", [])
                    stored = self.conn.execute("SELECT channel_id,message_id FROM analysis_inputs WHERE batch_id=?", (batch_id,))
                    sources = {(ref["channel_id"], ref["message_id"]) for ref in refs} | {tuple(ref) for ref in stored}
                    if sources & changed_sources or set(payload.get("split_children", [])) & affected:
                        affected.add(batch_id)
                    if batch_id in affected:
                        affected.update(payload.get("split_children", []))
                elif kind == "link" and set(plan.get("record_digests", {})) & record_ids:
                    affected.add(batch_id)
                elif kind == "merge" and any(ref["batch_id"] in affected for ref in plan.get("inputs", [])):
                    affected.add(batch_id)
                elif kind == "synthesize" and affected:
                    affected.add(batch_id)
                if batch_id in affected:
                    record_ids.update(ref[0] for ref in self.conn.execute("SELECT record_id FROM analysis_evidence WHERE batch_id=?", (batch_id,)))
            grew = (len(affected), len(record_ids)) != previous
        if not affected:
            return affected
        # A changed answer cannot reuse rendered parts from the old result.
        # Once a send may have reached Telegram, retain its receipts and stop
        # for inspection instead of resetting the plan and risking a duplicate.
        if (job["delivery"].get("outcome") in {"confirmed", "unknown"}
                or any(part.get("outcome") in {"confirmed", "unknown"}
                       or any(attempt.get("outcome") in {"confirmed", "unknown"}
                              for attempt in part.get("attempts", []))
                       for part in job["delivery"].get("parts", []))):
            raise ValueError("delivery_source_changed")
        for batch_id in affected:
            batch = self.get_batch(batch_id)
            if batch is None or batch["job_id"] != job["job_id"]:
                raise ValueError("Dependency outside job scope")
            batch["payload"]["invalidated"] = True
            self.conn.execute("DELETE FROM analysis_evidence WHERE batch_id=?", (batch_id,))
            self.conn.execute("DELETE FROM analysis_inputs WHERE batch_id=?", (batch_id,))
            self.conn.execute("UPDATE analysis_batches SET state='skipped',step_key=?,cache_key=NULL,payload_json=? WHERE batch_id=?",
                (f"invalidated/{batch_id}", canonical_json(batch["payload"]), batch_id))
        job["checkpoint"].pop("merge", None)
        job["checkpoint"].pop("relations", None)
        self.conn.execute("""UPDATE analysis_jobs SET result_json=NULL,full_path=NULL,full_hash=NULL,delivery_json='{}',
            evidence_records=(SELECT COUNT(*) FROM analysis_evidence e JOIN analysis_batches b USING(batch_id) WHERE b.job_id=?)
            WHERE job_id=?""", (job["job_id"], job["job_id"]))
        return affected

    def history_page(self, channel_id: int, *, since: str, until: str, after, limit: int, max_message_id: int):
        if type(limit) is not int or limit < 1:
            raise ValueError("Invalid history read limit")
        cursor = after or (since, 0)
        return self.conn.execute("""SELECT message_id,date_utc FROM messages
            WHERE channel_id=? AND date_utc>=? AND date_utc<=? AND message_id<=?
              AND (date_utc,message_id)>(?,?) ORDER BY date_utc,message_id LIMIT ?""",
            (channel_id, since, until, max_message_id, *cursor, limit)).fetchall()

    def search_candidates(self, expression, *, since, until, ceilings, limit):
        if not expression or not ceilings:
            return []
        if type(limit) is not int or limit < 1:
            raise ValueError("Invalid candidate read limit")
        scope = " OR ".join("(m.channel_id=? AND m.message_id<=?)" for _ in ceilings)
        parameters = [value for peer, ceiling in ceilings.items() for value in (int(peer), ceiling)]
        return self.conn.execute("""SELECT m.channel_id,m.message_id FROM messages_fts
            JOIN messages m ON m.rowid=messages_fts.rowid WHERE messages_fts MATCH ?
            AND (""" + scope + """) AND m.date_utc>=? AND m.date_utc<=?
            ORDER BY messages_fts.rank,m.date_utc,m.channel_id,m.message_id LIMIT ?""",
            (expression, *parameters, since, until, limit)).fetchall()

    def search_neighbors(self, channel_id, *, at, since, until, ceiling, limit, before):
        comparison, order = ("<", "DESC") if before else (">", "ASC")
        return self.conn.execute("""SELECT channel_id,message_id,date_utc FROM messages
            WHERE channel_id=? AND date_utc>=? AND date_utc<=? AND message_id<=?
            AND (date_utc,message_id) """ + comparison + """ (?,?)
            ORDER BY date_utc """ + order + ",message_id " + order + " LIMIT ?",
            (channel_id, since, until, ceiling, *at, limit)).fetchall()

    def analysis_source(self, channel_id: int, message_id: int, *, max_bytes: int):
        size = self.conn.execute("""SELECT length(CAST(text AS BLOB))+length(CAST(raw_json AS BLOB))+
            COALESCE((SELECT SUM(length(CAST(COALESCE(ocr_text,'') AS BLOB))) FROM media_assets a
                      WHERE a.channel_id=m.channel_id AND a.message_id=m.message_id),0)
            FROM messages m WHERE channel_id=? AND message_id=?""", (channel_id, message_id)).fetchone()
        if size is None:
            return None
        if size[0] > max_bytes:
            raise ValueError("manifest_byte_cap")
        row = self.conn.execute("""SELECT channel_id,message_id,date_utc,grouped_id,sender_id,
            sender_username,sender_display_name,text,has_media,media_kind,raw_json
            FROM messages WHERE channel_id=? AND message_id=?""", (channel_id, message_id)).fetchone()
        if row is None:
            return None
        value = dict(row)
        raw = json.loads(value.pop("raw_json"))
        value["reply_to_message_id"] = _same_channel_reply(raw, channel_id)
        value["media"] = [dict(asset) for asset in self.conn.execute("""SELECT ordinal,media_kind,ocr_status,ocr_text
            FROM media_assets WHERE channel_id=? AND message_id=? ORDER BY ordinal""", (channel_id, message_id))]
        return value

    def commit_manifest_page(self, job_id: str, *, generation: int, expected_page: int, manifest, inputs):
        with common_sqlite.write_tx(self.conn, immediate=True):
            job = self.require_running(job_id, generation=generation)
            previous = job["checkpoint"].get("manifest", {})
            if previous.get("page", 0) != expected_page or previous.get("state") == "complete":
                raise StaleGenerationError("Manifest cursor changed")
            if manifest["page"] != expected_page + 1:
                raise ValueError("Invalid manifest page")
            settings = job["request"]["settings"]
            if len(inputs) > max(1, settings["sync"]["batch_size"]):
                raise ValueError("Manifest page exceeds its flush limit")
            if any(item["channel_id"] not in previous["channels"] for item in inputs):
                raise ValueError("Manifest input outside source scope")
            job["checkpoint"]["manifest"] = manifest
            maximum = settings["analysis"]["max_intermediate_bytes"]
            checkpoint = encode_payload(job["checkpoint"], max_bytes=maximum)
            step_key = f"select/{expected_page:08d}"
            payload = canonical_json({"page": expected_page, "count": len(inputs)})
            encoded_inputs = canonical_json(inputs)
            batch_id, now = uuid.uuid4().hex, datetime.now(timezone.utc).isoformat()
            self.conn.execute("""INSERT INTO analysis_batches
                (batch_id,job_id,step_key,kind,state,input_digest,payload_json,created_at,updated_at)
                VALUES (?,?,?,'select','succeeded',?,?,?,?)""",
                (batch_id, job_id, step_key, hashlib.sha256(encoded_inputs.encode("utf-8")).hexdigest(), payload, now, now))
            self.conn.executemany("""INSERT INTO analysis_inputs
                (batch_id,channel_id,message_id,fragment_start,fragment_end,role,date_utc,content_digest)
                VALUES (?,?,?,0,0,'selected',?,?)""",
                [(batch_id, item["channel_id"], item["message_id"], item["date_utc"], item["content_digest"]) for item in inputs])
            self._write_checkpoint_no_tx(job_id, checkpoint, generation=generation)
            self.conn.execute("UPDATE analysis_jobs SET phase='select' WHERE job_id=?", (job_id,))

    @staticmethod
    def _limited_manifest(manifest):
        return {**manifest, "state": "complete", "partial": True,
                "reasons": [*manifest["reasons"], "manifest_byte_cap"],
                "known_skipped": max(0, manifest["stored_in_window"] - manifest["selected"])}

    def stop_manifest_at_byte_cap(self, job_id: str, *, generation: int):
        with common_sqlite.write_tx(self.conn, immediate=True):
            job = self.require_running(job_id, generation=generation)
            manifest = self._limited_manifest(job["checkpoint"]["manifest"])
            job["checkpoint"]["manifest"] = manifest
            maximum = job["request"]["settings"]["analysis"]["max_intermediate_bytes"]
            checkpoint = encode_payload(job["checkpoint"], max_bytes=maximum)
            self._write_checkpoint_no_tx(job_id, checkpoint, generation=generation)
            self.conn.execute("UPDATE analysis_jobs SET phase='select' WHERE job_id=?", (job_id,))
        return manifest

    def manifest_inputs_page(self, job_id: str, *, channel_id: int, after=None, limit: int):
        if type(limit) is not int or limit < 1:
            raise ValueError("Invalid manifest read limit")
        cursor = after or ("", 0)
        return self.conn.execute("""SELECT b.step_key,i.* FROM analysis_batches b
            JOIN analysis_inputs i USING(batch_id) WHERE b.job_id=? AND b.kind='select' AND b.state='succeeded'
              AND i.channel_id=? AND (i.date_utc,i.message_id)>(?,?)
            ORDER BY i.date_utc,i.message_id LIMIT ?""", (job_id, channel_id, *cursor, limit)).fetchall()

    def next_manifest_batch(self, job_id, *, after=""):
        return self.conn.execute("""SELECT * FROM analysis_batches WHERE job_id=? AND kind='select'
            AND step_key>? ORDER BY step_key LIMIT 1""", (job_id, after)).fetchone()

    def manifest_batch_inputs(self, batch_id):
        return [dict(row) for row in self.conn.execute("""SELECT channel_id,message_id,date_utc,content_digest
            FROM analysis_inputs WHERE batch_id=? ORDER BY date_utc,message_id""", (batch_id,))]

    def repair_manifest_batch(self, job_id, *, generation, batch, inputs):
        with common_sqlite.write_tx(self.conn, immediate=True):
            job = self.require_running(job_id, generation=generation)
            previous = self.conn.execute("SELECT * FROM analysis_batches WHERE batch_id=? AND job_id=? AND kind='select'",
                                          (batch["batch_id"], job_id)).fetchone()
            if previous is None or previous["input_digest"] != batch["input_digest"]:
                raise StaleGenerationError("Manifest page changed during repair")
            old = self.manifest_batch_inputs(batch["batch_id"])
            if len(inputs) > len(old) or not {(x["channel_id"], x["message_id"]) for x in inputs} <= {
                    (x["channel_id"], x["message_id"]) for x in old}:
                raise ValueError("Manifest repair cannot add new inputs")
            manifest = job["checkpoint"]["manifest"]
            manifest["repair_after_step"] = batch["step_key"]
            removed = len(old) - len(inputs)
            if removed:
                manifest["selected"] -= removed
                manifest["known_skipped"] += removed
                manifest["partial"] = True
                if "input_removed" not in manifest["reasons"]:
                    manifest["reasons"].append("input_removed")
            encoded_inputs = canonical_json(inputs)
            changed = encoded_inputs != canonical_json(old)
            payload = json.loads(previous["payload_json"])
            if changed:
                payload.update(count=len(inputs), invalidated=True)
            encoded_payload = canonical_json(payload)
            maximum = job["request"]["settings"]["analysis"]["max_intermediate_bytes"]
            if changed:
                updated = {(x["channel_id"], x["message_id"]): x for x in inputs}
                changed_sources = {(x["channel_id"], x["message_id"]) for x in old
                                   if updated.get((x["channel_id"], x["message_id"])) != x}
                self.conn.execute("DELETE FROM analysis_inputs WHERE batch_id=?", (batch["batch_id"],))
                self.conn.executemany("""INSERT INTO analysis_inputs
                    (batch_id,channel_id,message_id,fragment_start,fragment_end,role,date_utc,content_digest)
                    VALUES (?,?,?,0,0,'selected',?,?)""",
                    [(batch["batch_id"], x["channel_id"], x["message_id"], x["date_utc"], x["content_digest"]) for x in inputs])
                self.conn.execute("UPDATE analysis_batches SET input_digest=?,payload_json=?,updated_at=? WHERE batch_id=?",
                    (hashlib.sha256(encoded_inputs.encode("utf-8")).hexdigest(), encoded_payload,
                     datetime.now(timezone.utc).isoformat(), batch["batch_id"]))
                self._invalidate_dependencies_no_tx(job, changed_sources=changed_sources)
            checkpoint = encode_payload(job["checkpoint"], max_bytes=maximum)
            self._write_checkpoint_no_tx(job_id, checkpoint, generation=generation)

    def interrupt_changed_input(self, job_id: str, *, generation: int, source):
        with common_sqlite.write_tx(self.conn, immediate=True):
            job = self.require_running(job_id, generation=generation)
            identity = {key: source.get(key) for key in ("batch_id", "channel_id", "message_id")}
            job["checkpoint"]["manifest"]["input_changed"] = identity
            job["checkpoint"]["last_stop"] = {"reason": "input_changed", "phase": job["phase"]}
            encoded = encode_payload(job["checkpoint"], max_bytes=job["request"]["settings"]["analysis"]["max_intermediate_bytes"])
            self._write_checkpoint_no_tx(job_id, encoded, generation=generation)
            now = datetime.now(timezone.utc).isoformat()
            self.conn.execute("UPDATE analysis_jobs SET status='interrupted',finished_at=? WHERE job_id=?", (now, job_id))

    def iter_progress_jobs(self):
        """Stream retained UI anchors once when a supervisor restarts."""
        after = ""
        while True:
            row = self.conn.execute("""SELECT job_id FROM analysis_jobs WHERE job_id>? AND kind='analysis'
                AND json_type(checkpoint_json,'$.progress')='object' ORDER BY job_id LIMIT 1""", (after,)).fetchone()
            if row is None:
                return
            after = row[0]
            job = self.get_job(after)
            if job is not None:
                yield job

    def iter_jobs(self, status: str) -> Iterator[dict[str, Any]]:
        last_id = ""
        while True:
            row = self.conn.execute(
                "SELECT job_id FROM analysis_jobs WHERE status=? AND job_id>? ORDER BY job_id LIMIT 1",
                (status, last_id),
            ).fetchone()
            if row is None:
                return
            last_id = row[0]
            job = self.get_job(last_id)
            if job is not None and job["status"] == status:
                yield job

    def cache_storage_bytes(self):
        return self.conn.execute(f"""SELECT COALESCE(SUM({_cache_bytes_sql()}),0)
            FROM analysis_batches b JOIN analysis_jobs j USING(job_id) WHERE {_CACHE_ELIGIBLE}""").fetchone()[0]

    def maintain_analysis_storage(self, *, now, max_cache_bytes):
        _budget_integer(max_cache_bytes, "cache byte cap")
        if now.tzinfo is None:
            raise ValueError("Cache maintenance requires an aware clock")
        pruned, evicted, after = 0, 0, ""
        while True:
            row = self.conn.execute("""SELECT job_id FROM analysis_jobs WHERE kind='analysis'
                AND status NOT IN ('queued','running') AND job_id>? ORDER BY job_id LIMIT 1""", (after,)).fetchone()
            if row is None:
                break
            after = row[0]
            with common_sqlite.write_tx(self.conn, immediate=True):
                job = self.get_job(after)
                if job is None or job["status"] in {"queued", "running"}:
                    continue
                try:
                    retention = job["request"]["settings"]["analysis"]["retention_days"]
                    _budget_integer(retention, "retention days")
                    if retention == 0:
                        continue
                    finished = datetime.fromisoformat(job["finished_at"] or job["updated_at"])
                except (KeyError, TypeError, ValueError):
                    continue  # Legacy rows without policy are not silently expired.
                if finished.tzinfo is not None and (now - finished).total_seconds() > retention * 24 * 60 * 60:
                    self.conn.execute("DELETE FROM analysis_jobs WHERE job_id=?", (after,))
                    pruned += 1
        remaining = self.cache_storage_bytes()
        while remaining > max_cache_bytes:
            with common_sqlite.write_tx(self.conn, immediate=True):
                row = self.conn.execute(f"""SELECT b.batch_id,b.job_id,{_cache_bytes_sql()} AS bytes
                    FROM analysis_batches b JOIN analysis_jobs j USING(job_id) WHERE {_CACHE_ELIGIBLE}
                    ORDER BY b.updated_at,b.batch_id LIMIT 1""").fetchone()
                if row is None:
                    break
                batch = self.get_batch(row[0])
                job = self.get_job(row[1])
                # Reuse is evictable; a retained result and failed-job resume
                # still own their evidence, input hashes and batch lineage.
                if job["result"] is not None or job["status"] == "failed":
                    self.conn.execute("UPDATE analysis_batches SET cache_key=NULL WHERE batch_id=?", (row[0],))
                else:
                    payload = {"evicted": True}
                    if "provenance" in batch["payload"]:
                        payload["provenance"] = batch["payload"]["provenance"]
                    self.conn.execute("DELETE FROM analysis_evidence WHERE batch_id=?", (row[0],))
                    self.conn.execute("DELETE FROM analysis_inputs WHERE batch_id=?", (row[0],))
                    self.conn.execute("UPDATE analysis_batches SET cache_key=NULL,payload_json=? WHERE batch_id=?", (canonical_json(payload), row[0]))
                self.conn.execute("""UPDATE analysis_jobs SET evidence_records=(SELECT COUNT(*) FROM analysis_evidence e
                    JOIN analysis_batches b USING(batch_id) WHERE b.job_id=?) WHERE job_id=?""", (row[1], row[1]))
                self._sync_intermediate_no_tx(row[1])
                remaining -= row[2]
                evicted += 1
        return {"pruned_jobs": pruned, "evicted_batches": evicted, "cache_bytes": self.cache_storage_bytes()}

    def prune_expired_analysis_jobs(self, *, before: datetime) -> int:
        with common_sqlite.write_tx(self.conn, immediate=True):
            return self.conn.execute("""DELETE FROM analysis_jobs WHERE kind='analysis'
                AND status NOT IN ('queued','running') AND COALESCE(finished_at,updated_at)<?""",
                (before.astimezone(timezone.utc).isoformat(),)).rowcount

    def finish_job(self, job_id: str, *, generation: int, status: str, now: datetime) -> None:
        if status not in {"completed", "partial", "failed", "timed_out", "cancelled", "interrupted"}:
            raise ValueError("Invalid terminal analytic status")
        timestamp = now.astimezone(timezone.utc).isoformat()
        with common_sqlite.write_tx(self.conn, immediate=True):
            changed = self.conn.execute(
                """UPDATE analysis_jobs SET
                   status=CASE WHEN cancel_requested=1 AND ? IN ('interrupted','failed','timed_out')
                               THEN 'cancelled' ELSE ? END,finished_at=?,updated_at=?
                   WHERE job_id=? AND generation=? AND status='running'""",
                (status, status, timestamp, timestamp, job_id, generation),
            ).rowcount
            if changed != 1:
                raise StaleGenerationError("Analytic job generation or running state changed")

    def request_cancel(self, job_id: str, *, now: datetime) -> dict[str, Any]:
        timestamp = now.astimezone(timezone.utc).isoformat()
        with common_sqlite.write_tx(self.conn, immediate=True):
            job = self.get_job(job_id)
            if job is None:
                raise StaleGenerationError("Analytic job was removed")
            if job["status"] in {"queued", "running"}:
                queued = job["status"] == "queued"
                self.conn.execute(
                    """UPDATE analysis_jobs SET cancel_requested=1,updated_at=?,status=?,finished_at=? WHERE job_id=?""",
                    (timestamp, "cancelled" if queued else "running", timestamp if queued else None, job_id),
                )
            return self.get_job(job_id)

    def resume_job(self, job_id: str, *, generation: int, phase: str, max_queued_jobs: int,
                   now: datetime, restore_artifact: bool = False) -> dict[str, Any]:
        with common_sqlite.write_tx(self.conn, immediate=True):
            current = self.get_job(job_id)
            if current is None:
                raise StaleGenerationError("Analytic job was removed")
            if current["status"] == "queued":
                return current
            if (current["generation"] != generation or current["status"] not in {"interrupted", "timed_out", "partial", "completed", "failed"}
                    or (current["status"] == "failed" and not current["checkpoint"])):
                raise StaleGenerationError("Analytic resume state changed")
            queued = self.conn.execute("SELECT COUNT(*) FROM analysis_jobs WHERE status='queued'").fetchone()[0]
            if queued >= max_queued_jobs:
                raise QueueFullError("Analysis queue is full")
            if restore_artifact:
                if current["full_path"] is None or current["result"] is None:
                    raise ValueError("Full artifact is not prepared")
                current["checkpoint"]["artifact"]["restore_missing"] = True
                self.conn.execute("UPDATE analysis_jobs SET checkpoint_json=? WHERE job_id=?",
                    (canonical_json(current["checkpoint"]), job_id))
                self._sync_intermediate_no_tx(job_id)
            self.conn.execute(
                """UPDATE analysis_jobs SET status='queued',phase=?,generation=generation+1,
                   cancel_requested=0,finished_at=NULL,updated_at=? WHERE job_id=?""",
                (phase, now.astimezone(timezone.utc).isoformat(), job_id),
            )
            return self.get_job(job_id)

    def update_progress(self, job_id, *, generation, expected, value):
        """CAS only the derived UI state; worker-owned checkpoint fields stay current."""
        if not isinstance(value, dict) or set(value) != {"outcome", "message_id", "view_digest", "updated_at"}:
            raise ValueError("Invalid progress metadata")
        outcome, identity, digest, stamp = (value[key] for key in ("outcome", "message_id", "view_digest", "updated_at"))
        if (not isinstance(outcome, str) or outcome not in {"pending", "confirmed", "unknown", "disabled"}
                or (identity is not None and (type(identity) is not int or not 0 < identity <= SQLITE_INTEGER_MAX))
                or (outcome == "confirmed" and identity is None)
                or (digest is not None and (not isinstance(digest, str) or len(digest) != 64 or set(digest) - set("0123456789abcdef")))
                or not isinstance(stamp, str) or len(stamp) > 32):
            raise ValueError("Invalid progress metadata")
        if datetime.fromisoformat(stamp).tzinfo is None:
            raise ValueError("Progress requires an aware timestamp")
        with common_sqlite.write_tx(self.conn, immediate=True):
            job = self.get_job(job_id)
            if (job is None or job["kind"] != "analysis" or job["generation"] != generation
                    or job["checkpoint"].get("progress") != expected):
                return False
            job["checkpoint"]["progress"] = dict(value)
            self.conn.execute("UPDATE analysis_jobs SET checkpoint_json=? WHERE job_id=?",
                (canonical_json(job["checkpoint"]), job_id))
            self._sync_intermediate_no_tx(job_id)
        return True

    def save_checkpoint(self, job_id: str, checkpoint: dict[str, Any], *, generation: int, max_bytes: int) -> None:
        if not isinstance(checkpoint, dict):
            raise ValueError("Checkpoint must be an object")
        encoded = encode_payload(checkpoint, max_bytes=max_bytes)
        with common_sqlite.write_tx(self.conn, immediate=True):
            self._write_checkpoint_no_tx(job_id, encoded, generation=generation)

    def _write_checkpoint_no_tx(self, job_id: str, encoded: str, *, generation: int,
                                downloaded_delta: int = 0, downloaded_release: int = 0) -> None:
        """Join a caller-owned transaction after payload and budget validation."""
        if not self.conn.in_transaction:
            raise ValueError("Checkpoint write requires an explicit transaction")
        if any(type(value) is not int or value < 0 for value in (downloaded_delta, downloaded_release)):
            raise ValueError("Invalid downloaded message delta")
        current = self.get_job(job_id)
        checkpoint = json.loads(encoded)
        checkpoint.pop("progress", None)
        if current is not None and "progress" in current["checkpoint"]:
            checkpoint["progress"] = current["checkpoint"]["progress"]
        encoded = canonical_json(checkpoint)
        changed = self.conn.execute(
            """UPDATE analysis_jobs SET checkpoint_json=?,downloaded_messages=downloaded_messages+?-?,updated_at=?
               WHERE job_id=? AND generation=? AND status IN ('queued','running') AND downloaded_messages+?>=?""",
            (encoded, downloaded_delta, downloaded_release, datetime.now(timezone.utc).isoformat(),
             job_id, generation, downloaded_delta, downloaded_release),
        ).rowcount
        if changed != 1:
            raise StaleGenerationError("Analytic job generation changed")
        self._sync_intermediate_no_tx(job_id)
