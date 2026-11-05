"""Phase clocks and read-only projections of the canonical analytics ledgers."""

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import time
import uuid

from telegram_shared.openai_usage import extract_usage

if __package__:
    from .analysis_store import StaleGenerationError, canonical_json, SOURCE_VERIFICATION_FIELDS
else:
    from analysis_store import StaleGenerationError, canonical_json, SOURCE_VERIFICATION_FIELDS


def utc_now():
    return datetime.now(timezone.utc)


def implementation_sha256():
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).resolve().parent.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def reported_usage(response, receipt, latency_ms):
    if receipt is None:
        return None
    usage = {**response["usage"], "total_tokens": receipt["input_tokens"] + receipt["output_tokens"]}
    for section, key in (("input_tokens_details", "cache_write_tokens"),
                         ("output_tokens_details", "reasoning_tokens")):
        details = usage.get(section)
        details = dict(details) if isinstance(details, dict) else {}
        value = details.get(key)
        if type(value) is not int or value < 0:
            details.pop(key, None)
        usage[section] = details
    return extract_usage({**response, "usage": usage}, latency_ms=latency_ms)


class PhaseRecorder:
    def __init__(self, store, job, *, monotonic_func=time.monotonic, utc_now=utc_now):
        self.store, self.job = store, job
        self.clock, self.utc_now = monotonic_func, utc_now
        self.process_run_id = f"{os.getpid()}:{uuid.uuid4().hex}"
        self.sequence = None
        self.implementation_sha256 = implementation_sha256()

    def __enter__(self):
        self.enter("startup")
        return self

    def enter(self, phase):
        self.close("succeeded")
        self.sequence = self.store.begin_phase(self.job["job_id"],
            generation=self.job["generation"], phase=phase,
            process_run_id=self.process_run_id, now=self.utc_now(),
            implementation_sha256=self.implementation_sha256)
        self.started = self.clock()

    def close(self, outcome):
        if self.sequence is None:
            return
        sequence, self.sequence = self.sequence, None
        self.store.end_phase(self.job["job_id"], generation=self.job["generation"],
            sequence=sequence, now=self.utc_now(),
            elapsed_ms=max(0, round((self.clock() - self.started) * 1000)),
            outcome=outcome)

    def __exit__(self, kind, value, traceback):
        outcome = ("succeeded" if kind is None else "timed_out"
                   if issubclass(kind, TimeoutError) else "interrupted"
                   if issubclass(kind, InterruptedError) or not issubclass(kind, Exception)
                   else "failed")
        try:
            self.close(outcome)
        except StaleGenerationError:
            if kind is None:
                raise


def _sum_known(rows, key):
    values = [row.get(key) for row in rows]
    return sum(value for value in values if value is not None) if any(
        value is not None for value in values) else None


def _cost(rows, pricing):
    """Unknown receipts or cache-write splits never become a zero invoice."""
    missing = [row for row in rows if row["input_tokens"] is None or row["output_tokens"] is None]
    result = {"usd": None, "known_usage_usd": None,
              "unknown_attempts": len(missing), "reason": "pricing_not_supplied"}
    if pricing is None:
        return result
    keys = ("input", "cached_input", "cache_write_30m", "cache_write_12h", "output")
    if any(type(pricing.get(key)) not in {int, float} or not math.isfinite(pricing[key])
           or pricing[key] < 0 for key in keys):
        raise ValueError("Expected finite nonnegative per-million token prices")
    result["pricing"] = pricing
    amount, complete = 0.0, not missing
    for row in rows:
        if row["input_tokens"] is None or row["output_tokens"] is None:
            continue
        reads = row.get("cached_input_tokens")
        writes = row.get("cache_write_tokens")
        short = row.get("cache_write_30m_tokens")
        long = row.get("cache_write_12h_tokens")
        if reads is None or writes is None or (writes and (short is None or long is None)):
            complete = False
            continue
        short, long = short or 0, long or 0
        ordinary = row["input_tokens"] - reads - writes
        if ordinary < 0 or short + long != writes:
            complete = False
            continue
        amount += (ordinary * pricing["input"] + reads * pricing["cached_input"]
                   + short * pricing["cache_write_30m"] + long * pricing["cache_write_12h"]
                   + row["output_tokens"] * pricing["output"]) / 1000000
    result.update(known_usage_usd=amount,
                  usd=amount if complete else None,
                  reason=None if complete else "incomplete_usage_or_cache_writes")
    return result


def summarize_job(store, job_id, *, pricing=None):
    job = store.get_job(job_id)
    if job is None or job["kind"] != "analysis":
        raise ValueError("Unknown analytic job")
    rows = [dict(row) for row in store.conn.execute(
        "SELECT * FROM ai_usage_log WHERE job_id=? ORDER BY id", (job_id,))]
    has_phases = store.conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='analysis_phase_runs'").fetchone()
    phases = store.phase_runs(job_id) if has_phases else []
    inference = [row for row in rows if row["stage"].endswith(":inference")]
    missing = sum(row["input_tokens"] is None or row["output_tokens"] is None for row in inference)
    missing += max(0, job["llm_calls"] - len(inference))
    warnings = []
    if missing:
        warnings.append("missing_inference_usage")
    if len(rows) != job["provider_requests"]:
        warnings.append("provider_attempt_log_mismatch")
    if len(inference) != job["llm_calls"]:
        warnings.append("inference_attempt_log_mismatch")
    if not phases:
        warnings.append("phase_timings_unavailable")
    if any(row["finished_at"] is None for row in phases):
        warnings.append("open_phase_spans")
    if any(row.get("generation") is None or row.get("started_at") is None for row in rows):
        warnings.append("attempt_timing_metadata_unavailable")
    if any(row.get("phase_sequence") is None for row in rows):
        warnings.append("attempt_phase_correlation_unavailable")
    if any(row["latency_ms"] is None for row in rows):
        warnings.append("provider_duration_unavailable")
    if any(row.get("cache_write_tokens") is None for row in inference):
        warnings.append("cache_write_usage_unavailable")
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["stage"]].append(row)
    stages = {key: {"attempts": len(values),
        "provider_ms": _sum_known(values, "latency_ms"),
        "backoff_ms": _sum_known(values, "backoff_ms"),
        "input_tokens": _sum_known(values, "input_tokens"),
        "output_tokens": _sum_known(values, "output_tokens"),
        "unknown_outcomes": sum(row["status"] == "unknown" for row in values)}
        for key, values in grouped.items()}
    for phase in phases:
        matched = [row for row in rows if row.get("generation") == phase["generation"]
                   and row.get("phase_sequence") == phase["sequence"]]
        phase["provider_ms"] = _sum_known(matched, "latency_ms") if matched else 0
        phase["backoff_ms"] = _sum_known(matched, "backoff_ms") if matched else 0
        phase["other_phase_ms"] = (max(0, phase["elapsed_ms"] - phase["provider_ms"] - phase["backoff_ms"])
            if all(phase[key] is not None for key in ("elapsed_ms", "provider_ms", "backoff_ms")) else None)
        phase["wall_elapsed_ms"] = (round((datetime.fromisoformat(phase["finished_at"])
            - datetime.fromisoformat(phase["started_at"])).total_seconds() * 1000)
            if phase["finished_at"] is not None else None)
        if phase["wall_elapsed_ms"] is not None and phase["wall_elapsed_ms"] < 0:
            warnings.append("phase_wall_clock_regression")
    elapsed = None
    if job["finished_at"] is not None:
        elapsed = max(0, round((datetime.fromisoformat(job["finished_at"])
            - datetime.fromisoformat(job["accepted_at"])).total_seconds() * 1000))
    phase_ms = _sum_known(phases, "elapsed_ms")
    wall_phase_ms = _sum_known(phases, "wall_elapsed_ms")
    transition_ms, inactive_ms, overlap_ms = 0, 0, 0
    for previous, following in zip(phases, phases[1:]):
        if previous["finished_at"] is None:
            continue
        gap = round((datetime.fromisoformat(following["started_at"])
                     - datetime.fromisoformat(previous["finished_at"])).total_seconds() * 1000)
        if gap < 0:
            overlap_ms -= gap
        elif previous["generation"] == following["generation"]:
            transition_ms += gap
        else:
            inactive_ms += gap
    if overlap_ms:
        warnings.append("overlapping_phase_spans")
    cost = _cost(inference, pricing)
    if len(inference) != job["llm_calls"]:
        cost.update(usd=None, reason="missing_inference_attempt_logs")
    request, settings = job["request"]["request"], job["request"]["settings"]
    if pricing and pricing.get("model", settings["processing"]["model"]) != settings["processing"]["model"]:
        raise ValueError("Pricing model differs from the frozen job model")
    batches = [dict(row) for row in store.conn.execute(
        "SELECT batch_id,kind,state,payload_json FROM analysis_batches WHERE job_id=?", (job_id,))]
    planned_pairs, accepted_pairs, link_batches = set(), set(), []
    for batch in batches:
        if batch["kind"] != "link":
            continue
        payload = json.loads(batch["payload_json"])
        invalidated = bool(payload.get('invalidated'))
        plan, progress = payload.get("plan", {}), payload.get("link_progress")
        pairs = list(plan.get("pair_map", {}).values()) if "pair_map" in plan else plan.get("pairs", [])
        if not invalidated:
            planned_pairs.update(tuple(sorted(pair)) for pair in pairs)
        if progress is not None:
            link_batches.append({"batch_id": batch["batch_id"], "state": batch["state"],
                "invalidated": invalidated,
                "requested": len(pairs), "accepted": len(progress["accepted_decisions"]),
                "unresolved_pair_ids": progress["unresolved_pair_ids"],
                "repair_attempts": payload.get("repair_attempts", 0),
                "split": bool(payload.get("link_split") or payload.get("outcome") == "input_split"),
                "active_request": progress.get("active_request"), "diagnostics": progress["diagnostics"]})
    accepted_pairs.update(tuple(sorted((item["left_id"], item["right_id"]))) for item in store.iter_link_decisions(job_id))
    merge = job["checkpoint"].get("merge", {})
    raw_verification = job['checkpoint'].get('source_verification', job['delivery'].get('source_verification'))
    verification = {key: raw_verification.get(key) for key in SOURCE_VERIFICATION_FIELDS} if raw_verification is not None else None
    reasons = set(job['result'].get('reasons', []) if job['result'] else [])
    if verification and verification['excluded_source_limited_items']:
        reasons.add('source_selection_incomplete')
    return {"version": 1, "job_id": job_id, "command": job["command"],
        "status": job["status"], "generation": job["generation"],
        "period": {key: request[key] for key in ("since", "until", "cutoff_at")},
        "accepted_at": job["accepted_at"], "finished_at": job["finished_at"],
        "elapsed_ms": elapsed, "accounted_phase_ms": phase_ms, "accounted_wall_phase_ms": wall_phase_ms,
        "phase_transition_ms": transition_ms, "inactive_between_generations_ms": inactive_ms,
        "phase_overlap_ms": overlap_ms,
        "unattributed_ms": max(0, elapsed - wall_phase_ms - transition_ms - inactive_ms)
            if elapsed is not None and wall_phase_ms is not None else None,
        "model": settings["processing"]["model"], "settings": settings["analysis"],
        "settings_sha256": hashlib.sha256(canonical_json(settings).encode()).hexdigest(),
        "implementation_versions": sorted({row["implementation_sha256"] for row in phases
                                            if row.get("implementation_sha256")}),
        "coverage": job["result"].get("coverage") if job["result"] else job["checkpoint"].get("manifest"),
        "result_reasons": sorted(reasons), "source_verification": verification,
        "delivery": job["delivery"].get("outcome", "pending"),
        "phase_runs": phases, "stages": stages,
        "relations": {"planned_pairs": len(planned_pairs), "accepted_pairs": len(accepted_pairs),
            "unresolved_pairs": len(planned_pairs - accepted_pairs),
            "partial": job["checkpoint"].get("relations", {}).get("partial"), "batches": link_batches},
        "merge": {"state": merge.get("state"), "depth": merge.get("depth"),
            "frontier_groups": len(merge.get("frontier", [])), "group_order": merge.get("group_order"),
            "reduction_outcome": merge.get("reduction_outcome")},
        "batches": {"split": sum(json.loads(row["payload_json"]).get("outcome") in {"split", "input_split"} for row in batches),
                    "failed": sum(row["state"] == "failed" for row in batches)},
        "usage": {"inference_attempts": job["llm_calls"], "provider_attempts": job["provider_requests"],
            "used_tokens": job["used_tokens"], "reserved_tokens": job["reserved_tokens"],
            "input_tokens": _sum_known(inference, "input_tokens"),
            "output_tokens": _sum_known(inference, "output_tokens"),
            "cached_input_tokens": _sum_known(inference, "cached_input_tokens"),
            "missing_inference_receipts": missing},
        "cost": cost,
        "telemetry_complete": not warnings, "warnings": warnings}
