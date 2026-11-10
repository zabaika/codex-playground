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
    from .analysis_store import StaleGenerationError, canonical_json, SOURCE_VERIFICATION_FIELDS, result_evidence_ids, model_selection_ids
else:
    from analysis_store import StaleGenerationError, canonical_json, SOURCE_VERIFICATION_FIELDS, result_evidence_ids, model_selection_ids


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
    missing = [row for row in rows if row.get("input_tokens") is None or row.get("output_tokens") is None]
    result = {"usd": None, "usd_min": None, "usd_max": None, "known_usage_usd": None,
              "unknown_components": [], "unpriced_attempts": 0,
              "unknown_attempts": len(missing), "reason": "pricing_not_supplied"}
    if pricing is None:
        return result
    keys = ("input", "cached_input", "cache_write_30m", "cache_write_12h", "output")
    if any(type(pricing.get(key)) not in {int, float} or not math.isfinite(pricing[key])
           or pricing[key] < 0 for key in keys):
        raise ValueError("Expected finite nonnegative per-million token prices")
    result["pricing"] = pricing
    amount, lower, upper, bounded, unknown = 0.0, 0.0, 0.0, True, set()
    for row in rows:
        counts = [row.get(key) for key in ("input_tokens", "output_tokens", "cached_input_tokens", "cache_write_tokens")]
        if any(type(value) is not int or value < 0 for value in counts):
            bounded = False
            unknown.add("missing_or_invalid_token_counts")
            result["unpriced_attempts"] += 1
            continue
        inputs, outputs, reads, writes = counts
        short = row.get("cache_write_30m_tokens")
        long = row.get("cache_write_12h_tokens")
        lifetimes = [value for value in (short, long) if value is not None]
        if (reads + writes > inputs or any(type(value) is not int or value < 0 for value in lifetimes)
                or sum(lifetimes) > writes or (len(lifetimes) == 2 and sum(lifetimes) != writes)):
            bounded = False
            unknown.add("inconsistent_token_counts")
            result["unpriced_attempts"] += 1
            continue
        known = ((inputs - reads - writes) * pricing["input"] + reads * pricing["cached_input"]
                 + (short or 0) * pricing["cache_write_30m"] + (long or 0) * pricing["cache_write_12h"]
                 + outputs * pricing["output"]) / 1000000
        remaining = writes - sum(lifetimes)
        lower += known + remaining * min(pricing["cache_write_30m"], pricing["cache_write_12h"]) / 1000000
        upper += known + remaining * max(pricing["cache_write_30m"], pricing["cache_write_12h"]) / 1000000
        if writes and len(lifetimes) != 2:
            unknown.add("cache_write_lifetime")
        else:
            amount += known
    complete = bounded and not unknown
    result.update(known_usage_usd=amount,
                  usd=amount if complete else None,
                  usd_min=lower if bounded else None, usd_max=upper if bounded else None,
                  unknown_components=sorted(unknown),
                  reason=None if complete else "incomplete_usage_or_cache_writes")
    return result


def _billing(inference, pricing, missing_attempts):
    cost = _cost(inference, pricing)
    warnings = list(cost['unknown_components'])
    assumptions = []
    if pricing is None:
        warnings.append('pricing_not_supplied')
    for field, warning in (('actual_model', 'actual_model'), ('actual_service_tier', 'actual_service_tier')):
        expected = pricing.get('model' if field == 'actual_model' else 'service_tier') if pricing else None
        actual = [row.get(field) for row in inference]
        normalize = lambda value: 'standard' if field == 'actual_service_tier' and value == 'default' else value
        if any(value is None for value in actual):
            warnings.append(warning + '_unavailable')
        if pricing is not None and expected is None:
            warnings.append('pricing_' + ('model' if field == 'actual_model' else 'service_tier') + '_unavailable')
        if any(value is None for value in actual) or expected is None:
            assumptions.append(warning + '_matches_supplied_pricing')
        if inference and expected is None:
            cost.update(usd=None, usd_min=None, usd_max=None)
        if expected is not None and any(value is not None and normalize(value) != normalize(expected) for value in actual):
            warnings.append(warning + '_mismatch')
            cost.update(usd=None, usd_min=None, usd_max=None)
    if missing_attempts:
        warnings.append('missing_inference_attempt_logs')
        cost.update(usd=None, usd_min=None, usd_max=None, reason='missing_inference_attempt_logs')
    if warnings:
        cost['usd'] = None
    if pricing:
        compatible = [row for row in inference if row.get('actual_model') == pricing.get('model')
            and ('standard' if row.get('actual_service_tier') == 'default' else row.get('actual_service_tier'))
            == ('standard' if pricing.get('service_tier') == 'default' else pricing.get('service_tier'))
            and row.get('actual_service_tier') is not None]
        cost['known_usage_usd'] = _cost(compatible, pricing)['known_usage_usd']
    cost['assumptions'] = assumptions
    cost['known_usage_basis'] = 'sum_of_fully_priced_receipts_only'
    cost['known_usage_note'] = 'Zero fully priced usage does not establish free usage.'
    cost['excludes'] = ['invoice_adjustments', 'taxes', 'non_model_services']
    return cost, sorted(set(warnings))


def _validation_rejections(batches):
    """Project rejected input identities; do not expose rejected quote bodies."""
    rejected = {}
    for batch in batches:
        payload = json.loads(batch['payload_json'])
        if batch['kind'] != 'extract' or payload.get('invalidated') or payload.get('split_children'):
            continue
        progress = payload.get('extraction_progress')
        if not progress:
            continue
        for key in progress.get('unresolved_input_ids', []):
            ref = progress['scope']['inputs'][key]
            identity = tuple(ref[name] for name in ('channel_id', 'message_id', 'content_digest', 'fragment_start', 'fragment_end'))
            codes, reasons = set(), set()
            for slot, code in progress.get('record_errors', {}).items():
                diagnostic = progress.get('record_diagnostics', {}).get(slot, [])
                raw = progress.get('rejected_records', {}).get(slot, {})
                related = [entry for entry in diagnostic if key in entry['input_ids']]
                # Older addressed snapshots retained rejected records but no fine diagnostic.
                if related or (not diagnostic and any((source.get('channel_id'), source.get('message_id')) ==
                        (ref['channel_id'], ref['message_id']) for source in
                        (raw.get('source_refs', []) if isinstance(raw, dict) and isinstance(raw.get('source_refs'), list) else [])
                        if isinstance(source, dict))):
                    codes.add(code)
                    reasons.update(entry['reason'] for entry in related)
            if codes:
                value = rejected.setdefault(identity, {'input_id': key, **{name: ref[name] for name in
                    ('channel_id', 'message_id', 'fragment_start', 'fragment_end')}, 'codes': set(), 'reasons': set()})
                value['codes'].update(codes)
                value['reasons'].update(reasons or codes)
    return [{**value, 'codes': sorted(value['codes']), 'reasons': sorted(value['reasons'])}
            for _, value in sorted(rejected.items())]


def extraction_summary(batches, work):
    """Diagnostics supplement the store-owned range projection, never replace it."""
    records, digest_messages, repairs, normalizations = set(), set(), 0, 0
    rejected, splits, invalidated = 0, 0, 0
    validation_errors = defaultdict(int)
    for batch in batches:
        if batch['kind'] != 'extract':
            continue
        payload = json.loads(batch['payload_json'])
        invalidated += bool(payload.get('invalidated'))
        if payload.get('invalidated'):
            continue
        splits += bool(payload.get('split_children')) or payload.get('outcome') == 'input_split'
        progress = payload.get('extraction_progress')
        if not progress:
            continue
        if not payload.get('split_children'):
            for code in progress.get('record_errors', {}).values():
                validation_errors[code] += 1
        repairs += payload.get('repair_attempts', 0)
        normalizations += progress.get('local_date_normalizations', 0)
        records.update(value['record']['record_id'] for value in progress['accepted_records'].values())
        records.update(progress.get('inherited_record_ids', []))
        rejected += len(progress.get('rejected_records', {})) if not payload.get('split_children') else 0
        for key in progress['scope'].get('explicit_digest_input_ids', []):
            ref = progress['scope']['inputs'][key]
            digest_messages.add(tuple(ref[name] for name in ('channel_id', 'message_id', 'content_digest')))
    observed = work['observed']
    projection = {key: value for key, value in work.items() if key not in {'observed', 'incomplete', 'processed_messages'}}
    rejections = _validation_rejections(batches)
    return {**projection, 'semantic_coverage': 'unknown',
            'model_unresolved_fragments': work['unresolved_fragments'],
            'validation_rejected_fragments': len(rejections) if observed else None,
            'validation_rejections': rejections if observed else None,
            'validation_errors': dict(sorted(validation_errors.items())) if observed else None,
            'validation_rejection_basis': 'addressed unresolved inputs; record/envelope errors counted separately',
            'coverage_basis': 'addressed_input_ranges' if observed else 'legacy_unavailable',
            'accepted_records': len(records) if observed else None,
            'rejected_records': rejected if observed else None, 'active_split_batches': splits,
            'invalidated_batches': invalidated, 'explicit_digest_messages': len(digest_messages) if observed else None,
            'nonempty_fragments': work['selected_fragments'] - work['empty_text_fragments'] if observed else None,
            'schema_repairs': repairs if observed else None,
            'local_date_normalizations': normalizations if observed else None}


def selection_summary(store, job, batches):
    """Read saved selection/publication observations; never re-score old facts."""
    extracted = set(store.iter_leaf_evidence(job['job_id'],
        page_size=max(1, job['request']['settings']['sync']['batch_size']), identifiers_only=True))
    succeeded = [json.loads(batch['payload_json']) for batch in batches if batch['kind'] == 'synthesize'
                 and batch['state'] == 'succeeded' and not json.loads(batch['payload_json']).get('invalidated')]
    final = succeeded[-1] if succeeded else None
    passed = (sorted(record['record_id'] for record in final['plan']['evidence'])
              if final and 'evidence' in final.get('plan', {}) else None)
    model = result_evidence_ids(final['output']) if final and isinstance(final.get('output'), dict) else None
    model, unresolved = model_selection_ids(model, passed)
    selected = result_evidence_ids(job['result']) if job['result'] is not None else None
    presented = job['delivery'].get('presented_evidence_ids')
    excluded = job['checkpoint'].get('merge', {}).get('source_excluded_evidence_ids')
    return {'extracted_evidence_records': len(extracted), 'source_excluded_evidence_ids': excluded,
            'eligible_evidence_records': len(extracted - set(excluded)) if excluded is not None else None,
            'passed_evidence_ids': passed, 'model_selected_evidence_ids': model,
            'unresolved_model_evidence_ids': unresolved,
            'not_selected_by_model_ids': sorted(set(passed) - set(model))
                if passed is not None and model is not None and not unresolved else None,
            'result_evidence_ids': selected, 'presented_evidence_ids': presented,
            'presented_evidence_records': len(presented) if presented is not None else None,
            'not_presented_after_selection_ids': sorted(set(selected) - set(presented))
                if selected is not None and presented is not None else None,
            'presentation_observed': presented is not None,
            'presentation_limit_reason': job['delivery'].get('presentation_limit_reason'),
            'presentation_outcome': job['delivery'].get('outcome', 'not_prepared'),
            'basis': 'saved_plan_output_result_delivery; absent observations remain unknown'}


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
    cost, billing_warnings = _billing(inference, pricing, len(inference) != job['llm_calls'])
    request, settings = job["request"]["request"], job["request"]["settings"]
    if pricing and pricing.get("model", settings["processing"]["model"]) != settings["processing"]["model"]:
        raise ValueError("Pricing model differs from the frozen job model")
    batches = [dict(row) for row in store.conn.execute(
        "SELECT batch_id,kind,state,payload_json,attempts_json FROM analysis_batches WHERE job_id=? ORDER BY created_at,batch_id", (job_id,))]
    planned_pairs, accepted_pairs, link_batches, packing_observations = set(), set(), [], []
    link_attempts, link_provider_attempts = set(), set()
    for batch in batches:
        if batch["kind"] != "link":
            continue
        for attempt in json.loads(batch['attempts_json']):
            link_provider_attempts.add(attempt['attempt_id'])
            if attempt['request_kind'] == 'inference':
                link_attempts.add(attempt['attempt_id'])
        payload = json.loads(batch["payload_json"])
        invalidated = bool(payload.get('invalidated'))
        plan, progress = payload.get("plan", {}), payload.get("link_progress")
        pairs = list(plan.get("pair_map", {}).values()) if "pair_map" in plan else plan.get("pairs", [])
        if not invalidated:
            planned_pairs.update(tuple(sorted(pair)) for pair in pairs)
        if progress is not None:
            packing_observations.extend(item for item in progress['diagnostics'] if 'record_payload_bytes' in item)
            link_batches.append({"batch_id": batch["batch_id"], "state": batch["state"],
                "invalidated": invalidated,
                "requested": len(pairs), "accepted": len(progress["accepted_decisions"]),
                "unresolved_pair_ids": progress["unresolved_pair_ids"],
                "repair_attempts": payload.get("repair_attempts", 0),
                "split": bool(payload.get("link_split") or payload.get("outcome") == "input_split"),
                "active_request": progress.get("active_request"), "diagnostics": progress["diagnostics"],
                'packing': plan.get('packing')})
    accepted_pairs.update(tuple(sorted((item["left_id"], item["right_id"]))) for item in store.iter_link_decisions(job_id))
    link_attempts.update(row['attempt_id'] for row in rows if row['stage'] == 'link:inference')
    link_provider_attempts.update(row['attempt_id'] for row in rows if row['stage'].startswith('link:'))
    observed_packing = {item['attempt_id']: item for item in packing_observations if item.get('attempt_id') in link_attempts}
    known_record_bytes = sum(item['record_payload_bytes'] for item in observed_packing.values())
    merge = job["checkpoint"].get("merge", {})
    raw_verification = job['checkpoint'].get('source_verification', job['delivery'].get('source_verification'))
    verification = {key: raw_verification.get(key) for key in SOURCE_VERIFICATION_FIELDS} if raw_verification is not None else None
    reasons = set(job['result'].get('reasons', []) if job['result'] else [])
    if verification and verification['excluded_source_limited_items']:
        reasons.add('source_selection_incomplete')
    return {"version": 2, "job_id": job_id, "command": job["command"],
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
        "extraction": extraction_summary(batches, store.extraction_work_state(job_id)),
        'selection': selection_summary(store, job, batches),
        'synthesis': {'detail_hints': [dict(batch_id=row['batch_id'], state=row['state'],
            invalidated=bool(json.loads(row['payload_json']).get('invalidated')), **hint)
            for row in batches if row['kind'] == 'synthesize'
            if (hint := json.loads(row['payload_json']).get('plan', {}).get('detail_hint')) is not None],
            'basis': 'application_input_metadata_not_model_coverage'},
        "delivery": job["delivery"].get("outcome", "pending"),
        "phase_runs": phases, "stages": stages,
        "relations": {"planned_pairs": len(planned_pairs), "accepted_pairs": len(accepted_pairs),
            "unresolved_pairs": len(planned_pairs - accepted_pairs),
            "partial": job["checkpoint"].get("relations", {}).get("partial"), "batches": link_batches,
            'packing': {'mode': 'adaptive' if settings['analysis'].get('link_pairs_per_request') == 0 else 'fixed',
                'record_payload_bytes': known_record_bytes if len(observed_packing) == len(link_attempts) else None,
                'known_record_payload_bytes': known_record_bytes,
                'observed_inference_attempts': len(observed_packing),
                'unknown_inference_observations': len(link_attempts) - len(observed_packing),
                'provider_attempts': len(link_provider_attempts),
                'basis': 'serialized_record_bytes_proxy_not_tokens'}},
        "merge": {"state": merge.get("state"), "depth": merge.get("depth"),
            "route": merge.get('route'), "direct_skip_reason": merge.get('direct_skip_reason'),
            'direct_reason': merge.get('direct_reason'), 'direct_input_digest': merge.get('direct_input_digest'),
            "frontier_groups": len(merge.get("frontier", [])), "group_order": merge.get("group_order"),
            "reduction_outcome": merge.get("reduction_outcome")},
        "batches": {"split": sum(json.loads(row["payload_json"]).get("outcome") in {"split", "input_split"} for row in batches),
                    "failed": sum(row["state"] == "failed" for row in batches)},
        "usage": {"inference_attempts": job["llm_calls"], "provider_attempts": job["provider_requests"],
            "used_tokens": job["used_tokens"], "reserved_tokens": job["reserved_tokens"],
            "input_tokens": _sum_known(inference, "input_tokens"),
            "output_tokens": _sum_known(inference, "output_tokens"),
            "cached_input_tokens": _sum_known(inference, "cached_input_tokens"),
            **{key: _sum_known(inference, key) for key in ('cache_write_tokens', 'cache_write_30m_tokens',
                                                         'cache_write_12h_tokens', 'reasoning_tokens')},
            'actual_models': sorted({row['actual_model'] for row in inference if row.get('actual_model')}),
            'actual_service_tiers': sorted({row['actual_service_tier'] for row in inference if row.get('actual_service_tier')}),
            'unknown_counts': {key: sum(row.get(key) is None for row in inference) for key in (
                'input_tokens', 'output_tokens', 'cached_input_tokens', 'cache_write_tokens',
                'cache_write_30m_tokens', 'cache_write_12h_tokens', 'reasoning_tokens', 'actual_model', 'actual_service_tier')},
            "missing_inference_receipts": missing},
        "cost": cost,
        "billing_complete": not billing_warnings, "billing_warnings": billing_warnings,
        "telemetry_complete": not warnings, "warnings": warnings}
