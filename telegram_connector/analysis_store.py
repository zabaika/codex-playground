"""Persistence owner for analytic jobs in the existing Connector database."""

from datetime import datetime, timezone
import copy
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
if __package__:
    from . import analysis_settings
else:
    import analysis_settings


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
PRESENTATION_LIMIT_REASONS = frozenset({'max_chars', 'max_telegram_messages'})
LINK_OBSERVATION_FIELDS = ('input_pairs', 'input_unique_records', 'record_payload_bytes', 'serialized_payload_bytes')
_COUNT_FIELDS = ("downloaded_messages", "llm_calls", "provider_requests", "used_tokens", "reserved_tokens", "evidence_records")
# Fixed worker lifecycle; optional/repeated spans still pass normal accounting.
_LIFECYCLE_PHASES = ("queue", "worker_start", "startup", "sync", "select", "extract", "link", "merge",
                     "synthesize", "verify", "deliver", "finalize")
SOURCE_VERIFICATION_FIELDS = ('excluded_digest_only_items', 'excluded_unsupported_items',
                              'excluded_source_limited_items', 'digest_sources', 'source_subject_rejections',
                              'source_cover_fallbacks', 'source_cover_states', 'source_cover_impossible_items',
                              'source_cover_limited_items', 'source_cover_elapsed_ms')
_CHILD_VALUE_COLUMNS = {
    "b": ("batch_id", "job_id", "step_key", "kind", "state", "input_digest", "cache_key", "payload_json", "attempts_json", "created_at", "updated_at"),
    "e": ("record_id", "batch_id", "record_type", "topic_key", "payload_json"),
    "i": ("batch_id", "channel_id", "message_id", "fragment_start", "fragment_end", "role", "date_utc", "content_digest"),
    "p": ("job_id", "generation", "sequence", "phase", "process_run_id", "implementation_sha256", "started_at",
          "finished_at", "elapsed_ms", "duration_source", "outcome"),
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


def _validate_source_verification(value):
    if not isinstance(value, dict) or set(value) != set(SOURCE_VERIFICATION_FIELDS):
        raise ValueError('Invalid source verification counts')
    for key, count in value.items():
        _budget_integer(count, key)
    return dict(value)


def result_evidence_ids(result):
    return sorted({identity for item in (result or {}).get('items', [])
                   for identity in item.get('evidence_ids', [])})


def record_aliases(identities):
    """Stable wire identities shared by producers and saved-output readers."""
    return {key: f'r{index:03}' for index, key in enumerate(sorted(identities), 1)}


def model_selection_ids(identities, scope):
    """Resolve saved model references without inferring missing observations."""
    if identities is None:
        return None, None
    if scope is None:
        return None, identities
    known = set(scope)
    canonical = {alias: key for key, alias in record_aliases(scope).items() if alias not in known}
    unresolved = sorted(key for key in identities if canonical.get(key, key) not in known)
    selected = sorted({canonical.get(key, key) for key in identities if key not in unresolved})
    return selected, unresolved


def _presented_ids(job, values):
    if (not isinstance(values, list) or any(not isinstance(value, str) for value in values)
            or len(values) != len(set(values)) or not set(values) <= set(result_evidence_ids(job['result']))):
        raise ValueError('Invalid presentation evidence scope')
    return sorted(values)


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
            "tokens": merges * (policy["max_input_tokens"] + analysis_settings.stage_output_limit(settings, "merge"))
                      + final * (policy["max_input_tokens"] + output)}


def canonical_json(value: dict[str, Any] | list[Any]) -> str:
    if not isinstance(value, (dict, list)):
        raise ValueError("Analytic payload must be an object or array")
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid analytic JSON payload") from exc
    return encoded


def _extraction_source_key(ref):
    return tuple(ref[key] for key in ('channel_id', 'message_id', 'content_digest'))


def _extraction_remaining_ranges(lower, upper, covered):
    if lower == upper:
        return [] if any(start <= lower <= end for start, end in covered) else [(lower, upper)]
    remaining, cursor = [], lower
    for start, end in sorted(covered):
        if end <= cursor or start >= upper:
            continue
        if start > cursor:
            remaining.append((cursor, min(start, upper)))
        cursor = max(cursor, end)
        if cursor >= upper:
            break
    if cursor < upper:
        remaining.append((cursor, upper))
    return remaining


def _extraction_child_refs(parent_id, parent, child):
    if (child is None or child['payload'].get('invalidated')
            or child['payload'].get('plan', {}).get('parent_batch_id') != parent_id):
        return None
    refs = child['payload']['plan'].get('inputs', [])
    parents = parent['payload'].get('plan', {}).get('inputs', [])
    if not refs or any(type(ref.get(key)) is not int for ref in refs for key in ('fragment_start', 'fragment_end')):
        return None
    for ref in refs:
        if not any(_extraction_source_key(ref) == _extraction_source_key(root)
                   and 0 <= root['fragment_start'] <= ref['fragment_start'] <= ref['fragment_end'] <= root['fragment_end']
                   for root in parents):
            return None
    return refs


def _extraction_delegated_ranges(parent_id, parent, children):
    payload = parent['payload']
    progress = payload.get('extraction_progress')
    wanted = ([progress['scope']['inputs'][key] for key in progress['unresolved_input_ids']]
              if progress and progress['unresolved_input_ids'] else payload.get('plan', {}).get('inputs', []))
    allowed, delegated = {}, {}
    for ref in wanted:
        allowed.setdefault(_extraction_source_key(ref), []).append((ref['fragment_start'], ref['fragment_end']))
    ids = payload.get('split_children', [])
    if len(set(ids)) != len(ids):
        return None
    for identity in ids:
        refs = _extraction_child_refs(parent_id, parent, children.get(identity))
        if refs is None:
            return None
        for ref in refs:
            source = _extraction_source_key(ref)
            if _extraction_remaining_ranges(ref['fragment_start'], ref['fragment_end'], allowed.get(source, [])):
                return None
            delegated.setdefault(source, []).append((ref['fragment_start'], ref['fragment_end']))
    if any(_extraction_remaining_ranges(lower, upper, delegated.get(source, []))
           for source, intervals in allowed.items() for lower, upper in intervals):
        return None
    return delegated


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
        "source_verification": {key: SQLITE_INTEGER_MAX for key in SOURCE_VERIFICATION_FIELDS},
        "outcome": "confirmed", "error": max(DELIVERY_STOP_REASONS, key=len), "parts": []}
    future['presented_evidence_ids'] = result_evidence_ids(job['result'])
    future['presentation_limit_reason'] = max(PRESENTATION_LIMIT_REASONS, key=len)
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
    if job['kind'] == 'analysis':
        projection['source_verification'] = {key: SQLITE_INTEGER_MAX for key in SOURCE_VERIFICATION_FIELDS}
    result_extra = 0
    artifact_extra = 0
    if job["kind"] == "analysis" and "output" in policy:
        if job["request"].get("request", {}).get("detail") == "full":
            projection["artifact"] = {"result_digest": "f" * 64, "content_hash": "f" * 64,
                "previous_hash": "f" * 64, "partial": False, "restore_missing": False}
            projection['artifact']['presented_evidence_ids'] = result_evidence_ids(job['result'])
            projection['artifact']['presentation_limit_reason'] = max(PRESENTATION_LIMIT_REASONS, key=len)
            relative = str(Path(policy["output"]["results_dir"]) / (job["job_id"] + ".md"))
            artifact_extra = max(0, len(relative.encode("utf-8")) + 64
                - sum(len((job[key] or "").encode("utf-8")) for key in ("full_path", "full_hash")))
        projection["budget_stop"] = {"reason": max(BUDGET_REASONS, key=len), "phase": "synthesize"}
        merge = checkpoint.get('merge', {'frontier': [], 'next_frontier': [], 'cursor': 0,
            'depth': 0, 'reasons': [], 'group_order': 'unranked'})
        # A budget stop must persist its merge checkpoint even at the byte cap.
        projection['merge'] = {**merge, 'state': 'partial', 'partial': True,
            'reasons': sorted(set(merge.get('reasons', [])) | BUDGET_REASONS)}
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


def _telemetry_allowance(conn, job):
    if job['kind'] != 'analysis':
        return 0
    rows = [dict(row) for row in conn.execute('SELECT * FROM analysis_phase_runs WHERE job_id=?', (job['job_id'],))]
    stamp = '9999-12-31T23:59:59.999999+00:00'
    def size(row):
        return sum(len(str(row[key]).encode('utf-8')) for key in _CHILD_VALUE_COLUMNS['p'] if row[key] is not None)
    reserve = 0
    for row in rows:
        if row['finished_at'] is None:
            future = {**row, 'finished_at': stamp, 'elapsed_ms': SQLITE_INTEGER_MAX,
                      'outcome': 'interrupted', 'duration_source': 'utc_estimate'}
            reserve += max(0, size(future) - size(row))
    if job['status'] in {'queued', 'running'}:
        generation = job['generation'] + int(job['status'] == 'queued')
        present = {row['phase'] for row in rows if row['generation'] == generation}
        for phase in set(_LIFECYCLE_PHASES) - present:
            future = dict(zip(_CHILD_VALUE_COLUMNS['p'], (
                job['job_id'], SQLITE_INTEGER_MAX, SQLITE_INTEGER_MAX, phase,
                '9' * len(str(SQLITE_INTEGER_MAX)) + ':' + 'f' * 32, 'f' * 64,
                stamp, stamp, SQLITE_INTEGER_MAX, 'utc_estimate', 'interrupted')))
            reserve += size(future)
    return reserve


def _closed_extraction_payload(batch, *, upper=False):
    payload = copy.deepcopy(batch['payload'])
    progress = payload['extraction_progress']
    marker = progress.get('active_request')
    if marker:
        tail = batch['attempts'][marker['attempt_offset']:]
        infer = [a for a in tail if a['request_kind'] == 'inference']
        last = infer[-1] if infer else tail[-1] if tail else None
        code = ('extraction_preflight_stop' if not infer else
                'extraction_observation_missing' if last['state'] == 'succeeded' else
                'extraction_provider_failure' if last['state'] == 'failed' else 'extraction_outcome_unknown')
        progress['diagnostics'].append({'attempt_id': 'f' * 32 if upper else last['attempt_id'] if last else None,
            'repair_index': marker['repair_index'], 'scope_sha256': marker['scope_sha256'],
            'accepted': 0, 'unresolved': len(progress['unresolved_input_ids']),
            'codes': ['extraction_observation_missing' if upper else code]})
        progress['active_request'] = None
    progress['closed'] = True
    origins = [value['inferred_at'] for value in progress['accepted_records'].values()]
    origins += [attempt['created_at'] for attempt in batch['attempts']
                if attempt['request_kind'] == 'inference' and attempt['state'] == 'succeeded']
    if origins:
        payload['provenance'] = {
            'inferred_at': min(origins),
            'actual_model': None, 'cache_hit': False}
    payload['completion_digest'] = hashlib.sha256(canonical_json(progress).encode()).hexdigest()
    payload['outcome'] = ('partial' if progress['unresolved_input_ids'] or progress['record_errors']
                          or progress.get('saturated') or marker else 'succeeded')
    return payload


def _extraction_closure_allowance(batch):
    progress = batch['payload'].get('extraction_progress')
    if progress is None or progress.get('closed') or batch['payload'].get('invalidated') or batch['payload'].get('outcome') == 'input_split':
        return 0
    future = _closed_extraction_payload(batch, upper=True)
    reserve = max(0, len(canonical_json(future).encode()) - len(canonical_json(batch['payload']).encode()))
    # Both active states (planned/running) occupy seven bytes; publication grows
    # the persisted batch state as well as its JSON and child rows.
    reserve += len('succeeded') - len('running')
    for value in progress['accepted_records'].values():
        record = value['record']
        reserve += sum(len((text or '').encode()) for text in (
            record['record_id'], batch['batch_id'], record['record_type'],
            record['topic_key'], canonical_json(record['payload'])))
    for ref in batch['payload']['plan']['inputs'] + batch['payload']['plan']['context']:
        reserve += len(batch['batch_id'].encode()) + sum(len(str(ref[key]).encode()) for key in (
            'channel_id', 'message_id', 'fragment_start', 'fragment_end', 'role', 'date_utc', 'content_digest'))
    return reserve


def _link_observation_allowance(payload):
    if not payload.get('plan', {}).get('packing') or not payload.get('link_progress', {}).get('active_request'):
        return 0
    # Scalar metrics are known before dispatch; do not spend their space on
    # other progress before the physical observation has been persisted.
    future = {key: SQLITE_INTEGER_MAX for key in LINK_OBSERVATION_FIELDS}
    return len(canonical_json(future).encode()) - 1


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
               "i": "analysis_inputs i JOIN analysis_batches b USING(batch_id)",
               "p": "analysis_phase_runs p JOIN analysis_jobs b USING(job_id)"}
    for alias in _CHILD_VALUE_COLUMNS:
        expression = _value_bytes_sql(alias)
        logical += conn.execute(f"SELECT COALESCE(SUM({expression}),0) FROM {sources[alias]} WHERE b.job_id=?", (job_id,)).fetchone()[0]
    receipts, progress_reserve = 0, 0
    for entry in conn.execute("SELECT attempts_json,batch_id,payload_json FROM analysis_batches WHERE job_id=? AND kind!='select'", (job_id,)):
        receipts += sum(item.get("receipt_allowance", _receipt_allowance(item)) for item in json.loads(entry[0]))
        payload = json.loads(entry[2])
        progress_reserve += _extraction_closure_allowance({'batch_id': entry[1],
            'payload': payload, 'attempts': json.loads(entry[0])})
        progress_reserve += _link_observation_allowance(payload)
    finalization = _finalization_allowance(job)
    telemetry = _telemetry_allowance(conn, job)
    return {"logical_bytes": logical, "receipt_reserve": receipts, "finalization_reserve": finalization,
            "telemetry_reserve": telemetry, "progress_reserve": progress_reserve,
            "allocated_bytes": logical + receipts + finalization + telemetry + progress_reserve}


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
            queued = self.get_job(job_id)
            running = self.conn.execute("SELECT COUNT(*) FROM analysis_jobs WHERE status='running'").fetchone()[0]
            if running >= max_running_jobs:
                return None
            changed = self.conn.execute(
                """UPDATE analysis_jobs SET status='running',generation=generation+1,updated_at=?
                   WHERE job_id=? AND status='queued' AND cancel_requested=0""",
                (now.astimezone(timezone.utc).isoformat(), job_id),
            ).rowcount
            if changed and queued["kind"] == "analysis":
                generation = queued["generation"] + 1
                timestamp = now.astimezone(timezone.utc).isoformat()
                started = queued["created_at"] if queued["generation"] == 0 else queued["updated_at"]
                elapsed = max(0, round((now - datetime.fromisoformat(started)).total_seconds() * 1000))
                self.conn.execute("""INSERT INTO analysis_phase_runs VALUES
                    (?,?,0,'queue','bridge',NULL,?,?,?,'utc_estimate','succeeded')""",
                    (job_id, generation, started, timestamp, elapsed))
                self.conn.execute("""INSERT INTO analysis_phase_runs VALUES
                    (?,?,1,'worker_start','bridge',NULL,?,NULL,NULL,'utc_estimate','running')""",
                    (job_id, generation, timestamp))
                self._sync_intermediate_no_tx(job_id)
            return self.get_job(job_id) if changed else None

    def phase_runs(self, job_id):
        return [dict(row) for row in self.conn.execute(
            "SELECT * FROM analysis_phase_runs WHERE job_id=? ORDER BY generation,sequence", (job_id,))]

    def fail_queued_job(self, job_id, *, generation, reason, now):
        if reason not in BUDGET_REASONS:
            raise ValueError('Invalid queued failure reason')
        with common_sqlite.write_tx(self.conn, immediate=True):
            job = self.get_job(job_id)
            if job is None or job['status'] != 'queued' or job['generation'] != generation or job['cancel_requested']:
                return False
            job['checkpoint']['last_stop'] = {'reason': reason, 'phase': 'worker_start'}
            self.conn.execute("""UPDATE analysis_jobs SET status='failed',finished_at=?,updated_at=?,checkpoint_json=?
                WHERE job_id=? AND generation=? AND status='queued'""",
                (now.isoformat(), now.isoformat(), canonical_json(job['checkpoint']), job_id, generation))
            self._sync_intermediate_no_tx(job_id)
            return True

    def current_phase_sequence(self, job_id, generation):
        row = self.conn.execute("""SELECT sequence FROM analysis_phase_runs
            WHERE job_id=? AND generation=? AND finished_at IS NULL
            ORDER BY sequence DESC LIMIT 1""", (job_id, generation)).fetchone()
        return row[0] if row else None

    def record_provider_metrics(self, job_id, attempt_id, *, generation, phase_sequence,
                                started_at, actual_model, cache_write_30m_tokens, cache_write_12h_tokens,
                                actual_service_tier=None):
        with common_sqlite.write_tx(self.conn):
            self.conn.execute("""UPDATE ai_usage_log SET generation=?,phase_sequence=?,started_at=?,
                actual_model=?,cache_write_30m_tokens=?,cache_write_12h_tokens=?,actual_service_tier=?,
                backoff_ms=0,backoff_requested_ms=0 WHERE job_id=? AND attempt_id=?""",
                (generation, phase_sequence, started_at, actual_model, cache_write_30m_tokens,
                 cache_write_12h_tokens, actual_service_tier, job_id, attempt_id))

    def record_provider_backoff(self, job_id, attempt_id, *, elapsed_ms, requested_ms):
        with common_sqlite.write_tx(self.conn):
            self.conn.execute("""UPDATE ai_usage_log SET backoff_ms=?,backoff_requested_ms=?
                WHERE job_id=? AND attempt_id=?""", (elapsed_ms, requested_ms, job_id, attempt_id))

    def _close_open_phases_no_tx(self, job_id, generation, *, now, outcome):
        for row in self.conn.execute("""SELECT sequence,phase,started_at FROM analysis_phase_runs
            WHERE job_id=? AND generation=? AND finished_at IS NULL""", (job_id, generation)).fetchall():
            elapsed = max(0, round((now - datetime.fromisoformat(row["started_at"])).total_seconds() * 1000))
            self.conn.execute("""UPDATE analysis_phase_runs SET finished_at=?,elapsed_ms=?,
                duration_source='utc_estimate',outcome=? WHERE job_id=? AND generation=? AND sequence=?""",
                (now.isoformat(), elapsed, "succeeded" if row["phase"] == "worker_start" and outcome == "interrupted"
                 else outcome, job_id, generation, row["sequence"]))

    def begin_phase(self, job_id, *, generation, phase, process_run_id, now, implementation_sha256=None):
        with common_sqlite.write_tx(self.conn, immediate=True):
            self.require_running(job_id, generation=generation)
            self._close_open_phases_no_tx(job_id, generation, now=now, outcome="interrupted")
            sequence = self.conn.execute("""SELECT COALESCE(MAX(sequence),-1)+1 FROM analysis_phase_runs
                WHERE job_id=? AND generation=?""", (job_id, generation)).fetchone()[0]
            self.conn.execute("""INSERT INTO analysis_phase_runs VALUES
                (?,?,?,?,?,?,?,NULL,NULL,'monotonic','running')""",
                (job_id, generation, sequence, phase, process_run_id, implementation_sha256, now.isoformat()))
            self._sync_intermediate_no_tx(job_id)
            return sequence

    def end_phase(self, job_id, *, generation, sequence, now, elapsed_ms, outcome):
        _budget_integer(elapsed_ms, "phase elapsed milliseconds")
        with common_sqlite.write_tx(self.conn, immediate=True):
            job = self.get_job(job_id)
            if job is None or job["generation"] != generation:
                raise StaleGenerationError("Phase generation changed")
            self.conn.execute("""UPDATE analysis_phase_runs SET finished_at=?,elapsed_ms=?,outcome=?
                WHERE job_id=? AND generation=? AND sequence=? AND finished_at IS NULL""",
                (now.isoformat(), elapsed_ms, outcome, job_id, generation, sequence))
            self._sync_intermediate_no_tx(job_id)

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

    def extraction_children_pending(self, job_id, batch_id):
        """Published parent facts do not settle delegated child work."""
        pending, seen, loaded, found = [(batch_id, None)], set(), {}, False
        while pending:
            identity, parent = pending.pop()
            if identity in seen:
                raise ValueError('Extraction split cycle')
            seen.add(identity)
            batch = loaded.get(identity) or self.get_batch(identity)
            if batch is None or batch['job_id'] != job_id or batch['kind'] != 'extract':
                raise ValueError('Extraction child outside job scope')
            if parent and _extraction_child_refs(parent['batch_id'], parent, batch) is None:
                raise ValueError('Extraction child outside parent scope')
            if batch['payload'].get('invalidated'):
                continue
            if identity != batch_id and batch['state'] in {'planned', 'running'}:
                found = True
            children = batch['payload'].get('split_children', [])
            loaded.update({key: self.get_batch(key) for key in children})
            if children and _extraction_delegated_ranges(identity, batch, loaded) is None:
                raise ValueError('Extraction child coverage outside residual scope')
            pending.extend((key, batch) for key in children)
        return found

    def extraction_work_state(self, job_id, *, include_processed_sources=False):
        """Read the current range obligations; no second durable coverage ledger."""
        if __package__:
            from .analysis_evidence import _analysis_message_snapshot, analysis_text_view
        else:
            from analysis_evidence import _analysis_message_snapshot, analysis_text_view
        job = self.get_job(job_id)
        if job is None:
            raise StaleGenerationError('Analytic job was removed')
        maximum = job['request']['settings']['analysis']['max_intermediate_bytes']
        batches = {}
        for row in self.conn.execute("SELECT batch_id,state,payload_json FROM analysis_batches WHERE job_id=? AND kind='extract'", (job_id,)):
            payload = json.loads(row['payload_json'])
            if not payload.get('invalidated'):
                batches[row['batch_id']] = {'state': row['state'], 'payload': payload}
        inputs, expected = {}, {}
        for row in self.conn.execute('''SELECT i.*,b.kind FROM analysis_inputs i JOIN analysis_batches b USING(batch_id)
                WHERE b.job_id=? AND b.kind IN ('select','extract') AND i.role='selected' ''', (job_id,)):
            ref = dict(row)
            if row['kind'] == 'select':
                source = _extraction_source_key(ref)
                if source in expected:
                    continue
                try:
                    snapshot, digest = _analysis_message_snapshot(self, ref['channel_id'], ref['message_id'], maximum)
                except ValueError:
                    snapshot, digest = None, None
                expected[source] = (0, len(analysis_text_view(snapshot))) if snapshot is not None and digest == ref['content_digest'] else None
            elif row['batch_id'] in batches:
                inputs.setdefault(row['batch_id'], []).append(ref)
        return self._extraction_ranges_state(batches, inputs, expected,
            include_processed_sources=include_processed_sources)

    def _extraction_obligations(self, batches, inputs):
        observations, pending, observed, errors = {}, 0, False, 0
        priorities = {'represented': 0, 'duplicate': 1, 'irrelevant': 2, 'empty_text': 3,
                      'legacy': 4, 'unresolved': 5, 'unaccounted': 6}
        for identity, batch in batches.items():
            payload = batch['payload']
            refs = payload.get('plan', {}).get('inputs', inputs.get(identity, []))
            progress = payload.get('extraction_progress')
            observed |= bool(progress and refs)
            children = payload.get('split_children', [])
            delegated = _extraction_delegated_ranges(identity, batch, batches) if children else {}
            missing = delegated is None
            delegated = delegated or {}
            errors += missing
            if payload.get('outcome') == 'input_split':
                continue  # Child plans and manifest gaps define the remaining work.
            pending += bool(refs and not children and batch['state'] in {'planned', 'running'})
            errors += bool(progress and not children and (progress.get('record_errors')
                or progress.get('saturated') or progress.get('active_request')))
            scoped = {(_extraction_source_key(ref), ref['fragment_start'], ref['fragment_end']): key
                      for key, ref in progress['scope']['inputs'].items()} if progress else {}
            for ref in refs:
                source = _extraction_source_key(ref)
                key = scoped.get((source, ref['fragment_start'], ref['fragment_end']))
                decision = progress['dispositions'].get(key) if progress else None
                status = decision['status'] if decision else ('unresolved' if progress and key in progress.get(
                    'model_unresolved_input_ids', []) else 'legacy' if not progress and batch['state'] == 'succeeded' else 'unaccounted')
                if missing:
                    status = 'unaccounted'
                for lower, upper in _extraction_remaining_ranges(ref['fragment_start'], ref['fragment_end'], delegated.get(source, [])):
                    unit = (*source, lower, upper)
                    previous = observations.get(unit)
                    if previous is None or priorities[status] > priorities[previous]:
                        observations[unit] = status
        return observations, pending, observed, errors

    def _extraction_ranges_state(self, batches, inputs, expected, *, include_processed_sources=False):
        observations, pending, observed, errors = self._extraction_obligations(batches, inputs)
        processed = {}
        planned, unaccounted = {}, set()
        for unit, status in observations.items():
            planned.setdefault(unit[:3], []).append(unit[3:])
            if status not in {'unaccounted', 'unresolved'}:
                processed.setdefault(unit[:3], []).append(unit[3:])
            else:
                unaccounted.add(unit[:3])
        accounted = {source for source, bounds in expected.items()
                     if bounds is not None and not _extraction_remaining_ranges(*bounds, processed.get(source, []))
                     and source not in unaccounted}
        unknown_ranges = sum(bounds is None for bounds in expected.values())
        for source, bounds in expected.items():
            if bounds is None:
                continue
            lower, upper = bounds
            for start, end in _extraction_remaining_ranges(lower, upper, planned.get(source, [])):
                observations[(*source, start, end)] = 'unaccounted'
        counts = {status + '_fragments': sum(value == status for value in observations.values())
                  for status in ('represented', 'duplicate', 'irrelevant', 'empty_text', 'unresolved', 'unaccounted')}
        unknown = counts['unresolved_fragments'] + counts['unaccounted_fragments']
        legacy = sum(value == 'legacy' for value in observations.values())
        result = {'observed': observed, 'incomplete': bool(errors or pending or unknown or unknown_ranges),
                'processed_messages': len(accounted), 'pending_batches': pending, 'scope_errors': errors,
                'unknown_range_messages': unknown_ranges,
                'input_accounting_complete': not (errors or pending or unknown or legacy or unknown_ranges) if observed else None,
                'accounted_messages': len(accounted) if observed else None,
                'observed_messages': len({unit[:3] for unit in observations}) if observed else None,
                'manifest_selected_messages': len(expected) if expected or observed else None,
                'unplanned_messages': sum(source not in planned for source in expected) if observed else None,
                'selected_fragments': len(observations) if observed else None,
                **{key: value if observed else None for key, value in counts.items()}}
        if include_processed_sources:
            result['processed_sources'] = accounted
        return result

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
        if limit == 0:
            return []
        processed = self.extraction_work_state(job_id, include_processed_sources=True)['processed_sources']
        rows = self.conn.execute("""SELECT i.channel_id,i.message_id,i.content_digest FROM analysis_inputs i
            JOIN analysis_batches b USING(batch_id) WHERE b.job_id=? AND b.kind='select' AND i.role='selected'
            GROUP BY i.channel_id,i.message_id,i.content_digest ORDER BY MIN(i.date_utc),i.channel_id,i.message_id""",
            (job_id,))
        result = []
        for row in rows:
            if (row['channel_id'], row['message_id'], row['content_digest']) in processed:
                result.append(dict(row))
                if len(result) == limit:
                    break
        return result

    def iter_leaf_evidence(self, job_id, *, page_size, identifiers_only=False):
        after = ""
        while True:
            columns = 'e.record_id' if identifiers_only else 'e.*'
            rows = self.conn.execute(f"""SELECT {columns} FROM analysis_evidence e JOIN analysis_batches b USING(batch_id)
                WHERE b.job_id=? AND b.kind='extract' AND b.state='succeeded' AND e.record_id>?
                ORDER BY e.record_id LIMIT ?""", (job_id, after, page_size)).fetchall()
            if not rows:
                return
            for row in rows:
                if identifiers_only:
                    after = row['record_id']
                    yield after
                    continue
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
        rows = self.conn.execute("""SELECT state,payload_json FROM analysis_batches
            WHERE job_id=? AND kind='link' ORDER BY batch_id""", (job_id,))
        for row in rows:
            payload = json.loads(row["payload_json"])
            if payload.get('invalidated'):
                continue
            if row["state"] == "skipped" and not payload.get("link_split"):
                continue
            if "link_progress" in payload:
                yield from payload["link_progress"]["accepted_decisions"].values()
            else:
                yield from payload.get("output", {}).get("decisions", [])

    def _link_progress_batch(self, job_id, batch_id, generation, expected):
        job = self.require_running(job_id, generation=generation)
        batch = self.get_batch(batch_id)
        if batch is None or batch["job_id"] != job_id or batch["kind"] != "link" or batch["state"] not in {"planned", "running"}:
            raise ValueError("Link progress outside active batch")
        if batch["payload"].get("link_progress") != expected:
            raise StaleGenerationError("Link progress changed")
        return job, batch

    def begin_link_request(self, job_id, batch_id, *, generation, expected, progress):
        """Claim one logical repair and its scope before any physical attempt."""
        with common_sqlite.write_tx(self.conn, immediate=True):
            job, batch = self._link_progress_batch(job_id, batch_id, generation, expected)
            if expected is not None and progress != expected:
                raise ValueError("Link request must use saved progress")
            value = copy.deepcopy(progress)
            if value.get("active_request"):
                return batch
            repair_index = batch["payload"].get("repair_attempts", 0)
            if value["diagnostics"]:
                if repair_index >= job["request"]["settings"]["analysis"]["max_repair_attempts"]:
                    return None
                repair_index += 1
                batch["payload"]["repair_attempts"] = repair_index
            pair_ids = value["unresolved_pair_ids"]
            if not pair_ids or not set(pair_ids) <= set(batch["payload"]["plan"]["pair_map"]):
                raise ValueError("Invalid link request scope")
            value["active_request"] = {"pair_ids": pair_ids, "attempt_offset": len(batch["attempts"]),
                "repair_index": repair_index,
                "scope_sha256": hashlib.sha256(canonical_json(pair_ids).encode()).hexdigest()}
            batch["payload"]["link_progress"] = value
            self.conn.execute("UPDATE analysis_batches SET payload_json=? WHERE batch_id=?",
                              (canonical_json(batch["payload"]), batch_id))
            self._sync_intermediate_no_tx(job_id)
            return self.get_batch(batch_id)

    def split_link_progress(self, job_id, batch_id, *, generation, expected):
        with common_sqlite.write_tx(self.conn, immediate=True):
            _, batch = self._link_progress_batch(job_id, batch_id, generation, expected)
            batch["payload"].update(link_split=True, outcome="split")
            self.conn.execute("UPDATE analysis_batches SET state='skipped',payload_json=? WHERE batch_id=?",
                              (canonical_json(batch["payload"]), batch_id))
            self._sync_intermediate_no_tx(job_id)

    def save_link_progress(self, job_id, batch_id, *, generation, expected, progress, attempt_id=None):
        """Commit accepted decisions, remaining scope and terminal output together."""
        with common_sqlite.write_tx(self.conn, immediate=True):
            job, batch = self._link_progress_batch(job_id, batch_id, generation, expected)
            mapping = batch["payload"]["plan"]["pair_map"]
            accepted, pending = progress["accepted_decisions"], progress["unresolved_pair_ids"]
            if any(accepted.get(key) != value for key, value in (expected or {}).get("accepted_decisions", {}).items()):
                raise ValueError("Accepted link decision cannot be overwritten")
            if (set(accepted) & set(pending) or set(accepted) | set(pending) != set(mapping)
                    or len(pending) != len(set(pending))
                    or any([value["left_id"], value["right_id"]] != mapping[key] for key, value in accepted.items())):
                raise ValueError("Invalid accepted link scope")
            if attempt_id is not None:
                marker = (expected or {}).get("active_request")
                attempts = batch["attempts"][marker["attempt_offset"]:] if marker else []
                if not any(a["attempt_id"] == attempt_id and a["request_kind"] == "inference"
                           and a["state"] == "succeeded" for a in attempts):
                    raise ValueError("Link progress requires a settled inference receipt")
            elif any(a["request_kind"] == "inference" for a in batch["attempts"]):
                raise ValueError("Physical link output requires its receipt")
            batch["payload"]["link_progress"] = copy.deepcopy(progress)
            complete = not pending and not progress.get("saturated", False)
            if complete:
                output = {"decisions": [accepted[key] for key in sorted(accepted)], "saturated": False}
                batch["payload"]["output"] = output
                batch["payload"]["completion_digest"] = hashlib.sha256(canonical_json(output).encode()).hexdigest()
            self.conn.execute("UPDATE analysis_batches SET state=?,payload_json=? WHERE batch_id=?",
                ("succeeded" if complete else batch["state"], canonical_json(batch["payload"]), batch_id))
            self._sync_intermediate_no_tx(job_id)
            return self.get_batch(batch_id)

    def _extraction_progress_batch(self, job_id, batch_id, generation, expected):
        job = self.require_running(job_id, generation=generation)
        batch = self.get_batch(batch_id)
        if (batch is None or batch['job_id'] != job_id or batch['kind'] != 'extract'
                or batch['state'] not in {'planned', 'running'} or batch['payload'].get('invalidated')):
            raise ValueError('Extraction progress outside active batch')
        if batch['payload'].get('extraction_progress') != expected:
            raise StaleGenerationError('Extraction progress changed')
        return job, batch

    def extraction_ancestor_records(self, job_id, batch_id):
        """Only active split ancestors may supply immutable retained facts."""
        records, seen = {}, set()
        batch = self.get_batch(batch_id)
        while batch and batch['payload'].get('plan', {}).get('parent_batch_id'):
            parent = batch['payload']['plan']['parent_batch_id']
            if parent in seen:
                raise ValueError('Extraction ancestor cycle')
            seen.add(parent)
            batch = self.get_batch(parent)
            if (not batch or batch['job_id'] != job_id or batch['kind'] != 'extract'
                    or batch['payload'].get('invalidated') or batch['state'] != 'succeeded'):
                raise ValueError('Extraction ancestor outside active scope')
            for row in self.conn.execute('SELECT * FROM analysis_evidence WHERE batch_id=?', (parent,)):
                records[row['record_id']] = {**dict(row), 'payload': json.loads(row['payload_json'])}
        return records

    def begin_extraction_request(self, job_id, batch_id, *, generation, expected, scope):
        """Claim the residual request before dispatch; a receipt alone is not an observation."""
        with common_sqlite.write_tx(self.conn, immediate=True):
            job, batch = self._extraction_progress_batch(job_id, batch_id, generation, expected)
            progress = copy.deepcopy(expected) if expected is not None else {
                'scope': scope, 'accepted_records': {}, 'dispositions': {
                    key: {'status': 'empty_text', 'record_ids': [], 'reason': ''}
                    for key in scope['empty_input_ids']},
                'unresolved_input_ids': list(scope['model_input_ids']), 'record_errors': {},
                'diagnostics': [], 'active_request': None, 'local_date_normalizations': 0}
            if progress['scope'] != scope:
                raise ValueError('Extraction scope changed')
            if progress['active_request']:
                return progress
            repair = batch['payload'].get('repair_attempts', 0)
            if progress['diagnostics']:
                if repair >= job['request']['settings']['analysis']['max_repair_attempts']:
                    return None
                repair += 1
                batch['payload']['repair_attempts'] = repair
            if progress['unresolved_input_ids'] or progress['record_errors']:
                addressed = {'inputs': progress['unresolved_input_ids'],
                             'record_slots': sorted(progress['record_errors'])}
                progress['active_request'] = {**addressed, 'attempt_offset': len(batch['attempts']),
                    'repair_index': repair, 'scope_sha256': hashlib.sha256(canonical_json(addressed).encode()).hexdigest()}
            batch['payload']['extraction_progress'] = progress
            self.conn.execute('UPDATE analysis_batches SET payload_json=? WHERE batch_id=?',
                              (canonical_json(batch['payload']), batch_id))
            self._sync_intermediate_no_tx(job_id)
            return copy.deepcopy(progress)

    def initialize_extraction_scope(self, job_id, batch_id, *, generation, scope):
        """Persist local accounting before count dispatch, without claiming repair."""
        self.require_running(job_id, generation=generation)
        batch = self.get_batch(batch_id)
        if batch is None or batch['job_id'] != job_id or batch['kind'] != 'extract':
            raise ValueError('Extraction initialization outside job scope')
        progress = batch['payload'].get('extraction_progress')
        if progress is not None:
            if progress['scope'] != scope:
                raise ValueError('Extraction scope changed')
            return progress
        # The request claim is only a marker until inference dispatch. Count
        # attempts do not consume repair allowance or become observations.
        return self.begin_extraction_request(job_id, batch_id, generation=generation, expected=None, scope=scope)

    def save_extraction_progress(self, job_id, batch_id, *, generation, expected, classified, attempt_id):
        with common_sqlite.write_tx(self.conn, immediate=True):
            job, batch = self._extraction_progress_batch(job_id, batch_id, generation, expected)
            marker = expected.get('active_request')
            attempts = batch['attempts'][marker['attempt_offset']:] if marker else []
            attempt = next((a for a in attempts if a['attempt_id'] == attempt_id
                            and a['request_kind'] == 'inference' and a['state'] == 'succeeded'), None)
            if attempt is None:
                raise ValueError('Extraction progress requires a settled inference receipt')
            if classified['scope'] != expected['scope']:
                raise ValueError('Extraction observation outside scope')
            progress = copy.deepcopy(expected)
            ancestors = self.extraction_ancestor_records(job_id, batch_id)
            inherited = set(classified.get('inherited_record_ids', []))
            if not inherited <= set(ancestors):
                raise ValueError('Unknown retained ancestor record')
            progress['inherited_record_ids'] = sorted(set(progress.get('inherited_record_ids', [])) | inherited)
            added = 0
            for slot, record in classified['record_slots'].items():
                if not any(ref['channel_id'] == source['channel_id'] and ref['message_id'] == source['message_id']
                           and ref['content_digest'] == source['content_digest']
                           and source['fragment_start'] <= ref['fragment_start'] <= ref['fragment_end'] <= source['fragment_end']
                           for key in marker['inputs'] for source in [expected['scope']['inputs'][key]]
                           for ref in record['payload']['source_refs']):
                    raise ValueError('Extraction record outside residual scope')
                previous = progress['accepted_records'].get(slot)
                if previous and previous['record'] != record:
                    raise ValueError('Accepted extraction record cannot be overwritten')
                if previous is None:
                    added += 1
                    progress['accepted_records'][slot] = {'record': record, 'attempt_id': attempt_id,
                        'inferred_at': attempt['created_at']}
            for key, decision in classified['dispositions'].items():
                if key not in expected['scope']['inputs']:
                    raise ValueError('Extraction decision outside scope')
                known_ids = {value['record']['record_id'] for value in progress['accepted_records'].values()}
                known_ids.update(progress.get('inherited_record_ids', []))
                if not set(decision['record_ids']) <= known_ids:
                    raise ValueError('Extraction disposition references unknown record')
                if key not in marker['inputs'] and key not in progress['dispositions']:
                    raise ValueError('Extraction decision outside residual scope')
                if key in progress['dispositions'] and progress['dispositions'][key] != decision:
                    raise ValueError('Accepted extraction disposition cannot be overwritten')
                progress['dispositions'][key] = decision
            progress['unresolved_input_ids'] = [key for key in progress['scope']['inputs']
                                                if key not in progress['dispositions']]
            count = self.conn.execute('''SELECT COUNT(*) FROM analysis_evidence e
                JOIN analysis_batches b USING(batch_id) WHERE b.job_id=?''', (job_id,)).fetchone()[0]
            if count + len(progress['accepted_records']) > job['request']['settings']['analysis']['max_evidence_records']:
                raise BudgetExceededError('max_evidence_records')
            progress['record_errors'] = dict(classified['record_errors'])
            progress['record_diagnostics'] = copy.deepcopy(classified.get('record_diagnostics', {}))
            progress['rejected_records'] = copy.deepcopy(classified.get('rejected_records', {}))
            progress['model_unresolved_input_ids'] = list(classified.get('model_unresolved_input_ids', []))
            progress['saturated'] = classified.get('saturated', False)
            progress['local_date_normalizations'] += classified.get('local_date_normalizations', 0)
            progress['diagnostics'].append({'attempt_id': attempt_id,
                'repair_index': marker['repair_index'], 'scope_sha256': marker['scope_sha256'],
                'accepted': added,
                'unresolved': len(progress['unresolved_input_ids']), 'codes': classified['reasons']})
            progress['active_request'] = None
            batch['payload']['extraction_progress'] = progress
            self.conn.execute('UPDATE analysis_batches SET payload_json=? WHERE batch_id=?',
                              (canonical_json(batch['payload']), batch_id))
            self._sync_intermediate_no_tx(job_id)
            return copy.deepcopy(progress)

    def finish_extraction_progress(self, job_id, batch_id, *, generation, expected, children=None):
        """Publish the validated subset without changing any physical attempt outcome."""
        with common_sqlite.write_tx(self.conn, immediate=True):
            job, batch = self._extraction_progress_batch(job_id, batch_id, generation, expected)
            progress = copy.deepcopy(expected)
            records = [value['record'] for value in progress['accepted_records'].values()]
            count = self.conn.execute('''SELECT COUNT(*) FROM analysis_evidence e
                JOIN analysis_batches b USING(batch_id) WHERE b.job_id=?''', (job_id,)).fetchone()[0]
            if count + len(records) > job['request']['settings']['analysis']['max_evidence_records']:
                raise BudgetExceededError('max_evidence_records')
            for value in progress['accepted_records'].values():
                receipt = next((a for a in batch['attempts'] if a['attempt_id'] == value['attempt_id']), None)
                if receipt is None or receipt['state'] != 'succeeded':
                    raise ValueError('Published extraction requires its settled receipt')
                record = value['record']
                self.conn.execute('INSERT INTO analysis_evidence VALUES (?,?,?,?,?)',
                    (record['record_id'], batch_id, record['record_type'], record['topic_key'], canonical_json(record['payload'])))
            for ref in batch['payload']['plan']['inputs'] + batch['payload']['plan']['context']:
                self.conn.execute('''INSERT INTO analysis_inputs
                    (batch_id,channel_id,message_id,fragment_start,fragment_end,role,date_utc,content_digest)
                    VALUES (?,?,?,?,?,?,?,?)''', (batch_id, *(ref[key] for key in (
                        'channel_id', 'message_id', 'fragment_start', 'fragment_end', 'role', 'date_utc', 'content_digest'))))
            batch['payload'] = _closed_extraction_payload(batch)
            self.conn.execute("UPDATE analysis_batches SET state='succeeded',payload_json=? WHERE batch_id=?",
                              (canonical_json(batch['payload']), batch_id))
            self.conn.execute('UPDATE analysis_jobs SET evidence_records=? WHERE job_id=?', (count + len(records), job_id))
            if children:
                self._split_extraction_no_tx(job_id, batch_id, generation=generation, children=children,
                                              preserve_records=True)
            self._sync_intermediate_no_tx(job_id)
            return self.get_batch(batch_id)

    def incomplete_evidence_reasons(self, job_id):
        reasons = ["extraction_incomplete" if row[0] == "extract" else "relations_incomplete"
                for row in self.conn.execute("""SELECT DISTINCT kind FROM analysis_batches
                    WHERE job_id=? AND kind IN ('extract','link') AND state='failed'""", (job_id,))]
        for row in self.conn.execute("SELECT payload_json FROM analysis_batches WHERE job_id=? AND kind='extract'", (job_id,)):
            payload = json.loads(row[0])
            progress = payload.get('extraction_progress')
            if (progress and not payload.get('invalidated') and not payload.get('split_children')
                    and payload.get('outcome') != 'input_split' and
                    (progress['unresolved_input_ids'] or progress['record_errors'] or progress['active_request']
                     or progress.get('saturated'))):
                reasons.append('extraction_coverage_incomplete')
                break
        if self.extraction_work_state(job_id)['incomplete']:
            reasons.append('extraction_coverage_incomplete')
        return sorted(set(reasons))

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
            sources = [ref for ref in self.processed_source_refs(job_id, limit=limit)
                       if len(ref['content_digest']) == 64
                       and all(char in "0123456789abcdef" for char in ref['content_digest'])]
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

    def split_extraction_batch(self, job_id, batch_id, *, generation, children, preserve_records=False):
        with common_sqlite.write_tx(self.conn, immediate=True):
            return self._split_extraction_no_tx(job_id, batch_id, generation=generation,
                                                children=children, preserve_records=preserve_records)

    def _split_extraction_no_tx(self, job_id, batch_id, *, generation, children, preserve_records):
        job = self.require_running(job_id, generation=generation)
        parent = self.get_batch(batch_id)
        allowed = {'succeeded'} if preserve_records else {'running'}
        if parent is None or parent["job_id"] != job_id or parent["kind"] != "extract" or parent["state"] not in allowed or len(children) != 2:
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
        self.conn.execute("UPDATE analysis_batches SET state=?,payload_json=? WHERE batch_id=?",
            ('succeeded' if preserve_records else 'skipped', canonical_json(parent["payload"]), batch_id))
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

    def set_batch_outcome(self, job_id, batch_id, *, generation, outcome, diagnostic_code=None):
        if outcome not in {"refused", "invalid_schema", "incomplete", "saturated", "max_split_depth", "provider_error"}:
            raise ValueError("Invalid extraction outcome")
        with common_sqlite.write_tx(self.conn, immediate=True):
            self.require_running(job_id, generation=generation)
            batch = self.get_batch(batch_id)
            if batch is None or batch["job_id"] != job_id or batch["state"] not in {"planned", "running"}:
                raise ValueError("Extraction outcome outside active step")
            progress = batch["payload"].get("link_progress")
            marker = progress.get("active_request") if progress else None
            if marker:
                attempts = batch["attempts"][marker["attempt_offset"]:]
                inference = [attempt for attempt in attempts if attempt["request_kind"] == "inference"]
                last = inference[-1] if inference else attempts[-1] if attempts else None
                code = ("relation_preflight_stop" if not inference else
                        "relation_observation_missing" if last["state"] == "succeeded" else
                        "relation_provider_failure" if last["state"] == "failed" else "relation_outcome_unknown")
                receipt = self.conn.execute("SELECT latency_ms FROM ai_usage_log WHERE job_id=? AND attempt_id=?",
                    (job_id, last["attempt_id"])).fetchone() if last else None
                progress["diagnostics"].append({"requested": len(marker["pair_ids"]), "accepted": 0,
                    "missing": len(marker["pair_ids"]), "extra": 0, "duplicate": 0, "conflicting": 0,
                    "codes": [code, "relation_" + outcome] + ([diagnostic_code] if diagnostic_code else []),
                    "attempt_id": last["attempt_id"] if last else None,
                    "request_kind": last["request_kind"] if last else None,
                    "repair_index": marker["repair_index"], "scope_sha256": marker["scope_sha256"],
                    "provider_ms": receipt[0] if receipt else None})
                progress["active_request"] = None
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
            if batch["kind"] == "link" and batch["payload"].get("link_progress"):
                batch["payload"]["link_progress"]["active_request"] = None
                batch["payload"]["outcome"] = "input_split"
            if batch['kind'] == 'extract' and batch['payload'].get('extraction_progress'):
                batch['payload']['extraction_progress'].update(active_request=None, closed=True)
                batch['payload']['outcome'] = 'input_split' if batch['payload']['plan']['inputs'] else 'local_preflight'
            self.conn.execute("UPDATE analysis_batches SET state='skipped',payload_json=? WHERE batch_id=?",
                              (canonical_json(batch["payload"]), batch_id))
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
        # Verification may persist an input-change interruption. It must run
        # outside the reservation transaction, before any attempt is charged.
        current = self.require_running(job_id, generation=generation)
        planned = self.get_batch(batch_id)
        eligible_roots = (self._eligible_completion_roots(current)
                          if planned and planned['kind'] in {'extract', 'link'} else None)
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
                output_cap = analysis_settings.stage_output_limit(job["request"]["settings"], batch["kind"],
                    detail=job["request"]["request"]["detail"])
                if output_tokens > output_cap:
                    raise BudgetExceededError("stage_output_limit")
            protected = self._completion_reserve_no_tx(job, batch, request_kind=request_kind,
                                                       eligible_roots=eligible_roots)
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

    def _eligible_completion_roots(self, job):
        if job['command'] != 'topics':
            return None
        if __package__:
            from .analysis_evidence import record_source_eligible
        else:
            from analysis_evidence import record_source_eligible
        return sum(not row['payload'].get('source_refs') or record_source_eligible(self, job, row['payload'])
                   for row in self.iter_leaf_evidence(job['job_id'],
                       page_size=max(1, job['request']['settings']['sync']['batch_size'])))

    def _completion_reserve_no_tx(self, job, batch, *, request_kind, eligible_roots=None):
        roots = 0
        if batch["kind"] in {"extract", "link"}:
            # Merge starts from individual evidence records, not one node per
            # extraction request. A request can yield several independent facts.
            if job['command'] == 'topics':
                # Reserve the same eligible frontier used by the reducer. Keep
                # excluded evidence intact; it cannot consume a future merge.
                # Unknown legacy payloads are conservatively included.
                roots = self._eligible_completion_roots(job) if eligible_roots is None else eligible_roots
            else:
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

    def complete_batch(self, job_id, batch_id, *, generation, records, inputs, attempt_id=None, usage=None, provenance=None, json_output=None, merge_transition=None, final_result=None, extraction_progress=None):
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
        if extraction_progress is not None:
            output['extraction_progress'] = extraction_progress
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
                if extraction_progress is not None:
                    batch['payload']['extraction_progress'] = extraction_progress
                    batch['payload']['outcome'] = 'succeeded'
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
            job = self.require_running(job_id, generation=generation)
            job['checkpoint'].pop('source_verification', None)
            self.conn.execute("UPDATE analysis_jobs SET result_json=?,checkpoint_json=? WHERE job_id=?",
                              (canonical_json(result), canonical_json(job['checkpoint']), job_id))
            self._sync_intermediate_no_tx(job_id)

    def record_source_verification(self, job_id, *, generation, counts):
        counts = _validate_source_verification(counts)
        with common_sqlite.write_tx(self.conn, immediate=True):
            job = self.require_running(job_id, generation=generation)
            job['checkpoint']['source_verification'] = counts
            self.conn.execute('UPDATE analysis_jobs SET checkpoint_json=? WHERE job_id=?',
                              (canonical_json(job['checkpoint']), job_id))
            self._sync_intermediate_no_tx(job_id)

    def save_full_artifact(self, job_id, *, generation, relative_path, content_hash, result_digest, partial, ready=True, previous_hash=None,
                           presented_evidence_ids=None, presentation_limit_reason=None):
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
            previous = job['checkpoint'].get('artifact', {})
            metadata = {"result_digest": result_digest, "content_hash": content_hash, "partial": partial}
            if presented_evidence_ids is not None:
                metadata['presented_evidence_ids'] = _presented_ids(job, presented_evidence_ids)
                if presentation_limit_reason is not None and presentation_limit_reason not in PRESENTATION_LIMIT_REASONS:
                    raise ValueError('Invalid presentation limit reason')
                metadata['presentation_limit_reason'] = presentation_limit_reason
            elif previous.get('content_hash') == content_hash and 'presented_evidence_ids' in previous:
                metadata['presented_evidence_ids'] = previous['presented_evidence_ids']
                metadata['presentation_limit_reason'] = previous.get('presentation_limit_reason')
            job['checkpoint']['artifact'] = metadata
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

    def prepare_delivery(self, job_id, *, generation, chunks, parse_mode, detail, analysis_partial, source_verification=None,
                         presented_evidence_ids=None, presentation_limit_reason=None):
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
            if source_verification is not None:
                identity['source_verification'] = _validate_source_verification(source_verification)
            if presented_evidence_ids is not None:
                identity['presented_evidence_ids'] = _presented_ids(job, presented_evidence_ids)
                if presentation_limit_reason is not None and presentation_limit_reason not in PRESENTATION_LIMIT_REASONS:
                    raise ValueError('Invalid presentation limit reason')
                identity['presentation_limit_reason'] = presentation_limit_reason
            digest = hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
            if job["delivery"]:
                if job["delivery"].get("plan_digest") != digest:
                    raise ValueError("delivery_plan_conflict")
                return job["delivery"]
            delivery = {"version": 1, "destination": job["destination"], "detail": detail, "parse_mode": parse_mode,
                "plan_digest": digest, "analysis_partial": analysis_partial, "artifact_hash": identity["artifact_hash"], "outcome": "prepared",
                "parts": [{"text": text, "outcome": "pending", "attempts": []} for text in chunks]}
            if source_verification is not None:
                delivery['source_verification'] = dict(source_verification)
            if presented_evidence_ids is not None:
                delivery['presented_evidence_ids'] = identity['presented_evidence_ids']
                delivery['presentation_limit_reason'] = presentation_limit_reason
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
        return self.extraction_work_state(job_id)['processed_messages']

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
        job['checkpoint'].pop('source_verification', None)
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

    def _expire_reuse_no_tx(self, job_id):
        """Reuse is evictable; the recorded job and its provenance are history."""
        self.conn.execute('UPDATE analysis_batches SET cache_key=NULL WHERE job_id=? AND cache_key IS NOT NULL',
                          (job_id,))
        self._sync_intermediate_no_tx(job_id)

    def maintain_analysis_storage(self, *, now, max_cache_bytes):
        _budget_integer(max_cache_bytes, "cache byte cap")
        if now.tzinfo is None:
            raise ValueError("Cache maintenance requires an aware clock")
        evicted, after = 0, ""
        while True:
            row = self.conn.execute("""SELECT job_id FROM analysis_jobs WHERE kind='analysis'
                AND status NOT IN ('queued','running','stopping') AND job_id>?
                AND EXISTS(SELECT 1 FROM analysis_batches b WHERE b.job_id=analysis_jobs.job_id AND b.cache_key IS NOT NULL)
                ORDER BY job_id LIMIT 1""", (after,)).fetchone()
            if row is None:
                break
            after = row[0]
            with common_sqlite.write_tx(self.conn, immediate=True):
                job = self.get_job(after)
                if job is None or job["status"] in {"queued", "running", "stopping"}:
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
                    self._expire_reuse_no_tx(after)
        remaining = self.cache_storage_bytes()
        while remaining > max_cache_bytes:
            with common_sqlite.write_tx(self.conn, immediate=True):
                row = self.conn.execute(f"""SELECT b.batch_id,b.job_id,{_cache_bytes_sql()} AS bytes
                    FROM analysis_batches b JOIN analysis_jobs j USING(job_id) WHERE {_CACHE_ELIGIBLE}
                    ORDER BY b.updated_at,b.batch_id LIMIT 1""").fetchone()
                if row is None:
                    break
                self.conn.execute("UPDATE analysis_batches SET cache_key=NULL WHERE batch_id=?", (row[0],))
                self._sync_intermediate_no_tx(row[1])
                remaining -= row[2]
                evicted += 1
        return {"pruned_jobs": 0, "evicted_batches": evicted, "cache_bytes": self.cache_storage_bytes()}

    def prune_expired_analysis_jobs(self, *, before: datetime) -> int:
        """Expire reuse at a cutoff; the number of deleted historical jobs is zero."""
        with common_sqlite.write_tx(self.conn, immediate=True):
            rows = self.conn.execute("""SELECT DISTINCT j.job_id FROM analysis_jobs j
                JOIN analysis_batches b USING(job_id) WHERE j.kind='analysis' AND b.cache_key IS NOT NULL
                AND j.status NOT IN ('queued','running','stopping') AND COALESCE(j.finished_at,j.updated_at)<?""",
                (before.astimezone(timezone.utc).isoformat(),)).fetchall()
            for row in rows:
                self._expire_reuse_no_tx(row[0])
            return 0

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
            self._close_open_phases_no_tx(job_id, generation, now=now, outcome=status)
            latest = self.conn.execute("""SELECT sequence,finished_at,process_run_id,implementation_sha256
                FROM analysis_phase_runs WHERE job_id=? AND generation=?
                ORDER BY sequence DESC LIMIT 1""", (job_id, generation)).fetchone()
            if latest and latest["finished_at"] is not None:
                elapsed = max(0, round((now - datetime.fromisoformat(latest["finished_at"])).total_seconds() * 1000))
                self.conn.execute("""INSERT INTO analysis_phase_runs VALUES
                    (?,?,?,'finalize',?,?,?,?,?,'utc_estimate',?)""",
                    (job_id, generation, latest["sequence"] + 1, latest["process_run_id"],
                     latest["implementation_sha256"], latest["finished_at"], timestamp, elapsed, status))
            try:
                self._sync_intermediate_no_tx(job_id)
            except json.JSONDecodeError:
                # Terminal state must survive checkpoint corruption. The existing
                # allocation retains the reserved phase-close/finalization bound.
                pass

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
            self._sync_intermediate_no_tx(job_id)
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
