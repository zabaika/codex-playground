"""Bounded selection, provider attempts, extraction, linking, merge and synthesis."""

import copy
from datetime import datetime, timezone
import hashlib
import json
import re
import sys
import time
import uuid
import unicodedata

if __package__:
    from .analysis_store import AnalysisStore, BudgetExceededError, canonical_json, LINK_OBSERVATION_FIELDS
    from . import analysis_store
    from . import telegram_history_client as history_client
    from . import analysis_settings
    from . import analysis_evidence
    from . import analysis_telemetry
else:
    from analysis_store import AnalysisStore, BudgetExceededError, canonical_json, LINK_OBSERVATION_FIELDS
    import analysis_store
    import telegram_history_client as history_client
    import analysis_settings
    import analysis_evidence
    import analysis_telemetry

from telegram_shared import openai_api
from telegram_shared.openai_usage import build_prompt_cache_info, log_openai_usage


def request_analysis_json(store, job_id, batch_id, *, generation, endpoint, payload, api_key,
                          counted_input_tokens, deadline, urlopen_func=None, sleep_func=time.sleep,
                          monotonic_func=time.monotonic):
    """Bind the shared physical-attempt owner to this job's frozen policy."""
    if endpoint not in openai_api.OPENAI_JSON_ENDPOINTS:
        raise ValueError("Unsupported OpenAI endpoint")
    job = store.require_running(job_id, generation=generation)
    batch = store.get_batch(batch_id)
    if batch is None or batch["job_id"] != job_id:
        raise ValueError("Provider step outside job scope")
    settings = job["request"]["settings"]
    if not analysis_settings.supported_analysis_policy(settings):
        raise ValueError('analysis_policy_incompatible')
    if payload.get("model") != settings["processing"]["model"]:
        raise ValueError("Provider model differs from frozen job settings")
    ai = analysis_settings.analysis_ai(settings)
    request_kind = "inference" if endpoint == "responses" else "token_count"
    serialized = canonical_json(payload)
    cache_info = build_prompt_cache_info(cache_key=f"analysis:{batch_id}:{endpoint}",
        system_instructions=payload.get("instructions", ""), prompt_text=serialized, shared_prefix="")
    attempts = {}
    last_attempt = None

    def before_attempt(_index):
        nonlocal last_attempt
        identity = uuid.uuid4().hex
        store.reserve_provider_attempt(job_id, batch_id, generation=generation, attempt_id=identity,
            request_kind=request_kind, input_tokens=counted_input_tokens if request_kind == "inference" else 0,
            output_tokens=payload.get("max_output_tokens", 0) if request_kind == "inference" else 0)
        attempts[identity] = (datetime.now(timezone.utc).isoformat(), store.current_phase_sequence(job_id, generation))
        last_attempt = identity
        return identity

    def after_attempt(identity, response, exc, latency):
        usage = None
        if request_kind == "inference" and response is not None:
            raw = response.get("usage")
            if isinstance(raw, dict):
                details = raw.get("input_tokens_details") or {}
                cached = details.get("cached_tokens", 0) if isinstance(details, dict) else None
                values = (raw.get("input_tokens"), raw.get("output_tokens"), cached)
                if all(type(value) is int and value >= 0 for value in values) and cached <= values[0]:
                    usage = dict(zip(("input_tokens", "output_tokens", "cached_input_tokens"), values))
        outcome = "succeeded" if exc is None else "unknown" if exc.error_type in {"network_error", "invalid_response"} else "failed"
        try:
            store.settle_provider_attempt(job_id, batch_id, generation=generation, attempt_id=identity,
                                          outcome=outcome, usage=usage)
        finally:
            current = store.get_job(job_id)
            log_openai_usage(store.conn, feature=job["command"], created_at=datetime.now(timezone.utc).isoformat(),
                stage=f"{batch['kind']}:{request_kind}", channel="", since=None, until=None,
                model=settings["processing"]["model"], request_index=current["provider_requests"], message_count=0,
                status=outcome, cache_info=cache_info, prompt_text=serialized,
                usage=analysis_telemetry.reported_usage(response, usage, latency),
                response_id=response.get("id") if response is not None else None,
                error=(f"http_{exc.status_code}" if exc.status_code is not None else exc.error_type) if exc else None,
                store_prompt_text=ai["store_prompt_text"], correlation=(job_id, batch_id, identity), latency_ms=latency)
            raw_usage = (response or {}).get("usage")
            details = raw_usage.get("input_tokens_details", {}) if isinstance(raw_usage, dict) else {}
            writes = details.get("cache_write_tokens_details", {}) if isinstance(details, dict) else {}
            def counter(key):
                value = writes.get(key) if isinstance(writes, dict) else None
                return value if type(value) is int and value >= 0 else None
            started, phase_sequence = attempts[identity]
            def native_text(key):
                value = (response or {}).get(key)
                return value if isinstance(value, str) and value else None
            store.record_provider_metrics(job_id, identity, generation=generation, phase_sequence=phase_sequence,
                started_at=started, actual_model=native_text('model'),
                actual_service_tier=native_text('service_tier'),
                cache_write_30m_tokens=counter("ttl_30m_tokens"), cache_write_12h_tokens=counter("ttl_12h_tokens"))

    def measured_sleep(delay):
        started = monotonic_func()
        try:
            sleep_func(delay)
        finally:
            if last_attempt is not None:
                store.record_provider_backoff(job_id, last_attempt,
                    elapsed_ms=max(0, round((monotonic_func() - started) * 1000)), requested_ms=round(delay * 1000))

    kwargs = {"urlopen_func": urlopen_func} if urlopen_func is not None else {}
    return openai_api.post_json(payload, api_key, endpoint=endpoint,
        timeout_seconds=ai["request_timeout_seconds"] if endpoint == "responses" else ai["token_count_timeout_seconds"],
        retry_attempts=ai["retry_attempts"], retry_backoff_seconds=ai["retry_backoff_seconds"], before_attempt=before_attempt,
        after_attempt=after_attempt, deadline=deadline, monotonic_func=monotonic_func, sleep_func=measured_sleep, **kwargs)


def prepare_analysis_payload(store, job_id, batch_id, *, generation, payload, capabilities, **transport):
    """Count exact input; capability metadata must be supplied by its verified owner."""
    job = store.require_running(job_id, generation=generation)
    batch = store.get_batch(batch_id)
    if batch is None or batch["job_id"] != job_id:
        raise ValueError("Preflight step outside active job scope")
    settings = job["request"]["settings"]
    model = settings["processing"]["model"]
    if (not isinstance(capabilities, dict) or capabilities.get("model") != model or not capabilities.get("source")
            or any(type(capabilities.get(k)) is not int or capabilities[k] <= 0 for k in ("context_window", "max_output_tokens"))):
        raise ValueError("model_capability_unverified")
    model_input = capabilities.get("max_input_tokens", capabilities["context_window"])
    if type(model_input) is not int or model_input <= 0:
        raise ValueError("model_capability_unverified")
    data = copy.deepcopy(payload)
    if data.get("model") != model:
        raise ValueError("Provider model differs from frozen job settings")
    # Versioned protocol subset used by this fixed workflow. Unknown input-affecting
    # fields must be supported explicitly rather than silently omitted from count.
    input_fields = {"model", "input", "instructions", "text", "reasoning"}
    if set(data) - input_fields - {"max_output_tokens", "store", "metadata"}:
        raise ValueError("unsupported_preflight_field")
    output = data.get("max_output_tokens")
    cap = analysis_settings.stage_output_limit(settings, batch["kind"], detail=job["request"]["request"]["detail"])
    if type(output) is not int or output <= 0 or output > min(cap, capabilities["max_output_tokens"]):
        raise ValueError("model_output_limit")
    count_payload = {key: value for key, value in data.items() if key in input_fields}
    digest = hashlib.sha256(canonical_json(count_payload).encode("utf-8")).hexdigest()
    count = batch["payload"].get("token_counts", {}).get(digest)
    if count is None:
        response = request_analysis_json(store, job_id, batch_id, generation=generation,
            endpoint="input_tokens", payload=count_payload, counted_input_tokens=0, **transport)
        count = response.response.get("input_tokens")
        if type(count) is not int or count < 0:
            raise ValueError("input_token_count_unavailable")
        store.save_token_count(job_id, batch_id, generation=generation, payload_digest=digest, input_tokens=count)
    if count > min(settings["analysis"]["max_input_tokens"], model_input):
        raise ValueError("max_input_tokens")
    if count + output > capabilities["context_window"]:
        raise ValueError("model_context_limit")
    return {"payload": data, "input_tokens": count, "input_digest": digest}


def request_counted_analysis_payload(store, job_id, batch_id, *, generation, payload, capabilities, **transport):
    prepared = prepare_analysis_payload(store, job_id, batch_id, generation=generation,
                                        payload=payload, capabilities=capabilities, **transport)
    response = request_analysis_json(store, job_id, batch_id, generation=generation,
        endpoint="responses", payload=prepared["payload"], counted_input_tokens=prepared["input_tokens"], **transport)
    return {**prepared, "response": response.response, "latency_ms": response.latency_ms, "attempt_id": response.attempt_id}


def _extraction_fragments(fragments):
    omitted = {'date_utc', 'content_digest', 'fragment_start', 'fragment_end', 'step_key'}
    message_omitted = {'date_utc', 'normalization_version', 'text_view_version'}
    return [{'message': {key:value for key,value in fragment['message'].items() if key not in message_omitted},
             'input': {key:value for key,value in fragment['input'].items() if key != 'batch_id' and key not in omitted}}
            for fragment in fragments]


def _extraction_readonly_record(record):
    return {'record_id': record['record_id'], 'claim': record['payload']['claim'],
            'source_refs': [{key:value for key,value in ref.items() if key != 'date_utc'}
                            for ref in record['payload']['source_refs']]}


def extraction_cache_key(job, batch):
    settings = job["request"]["settings"]
    request = job["request"]["request"]
    identity = {"principal": {"namespace": job["request"]["principal"]["namespace"],
        "requester": job["requester_id"], "destination": job["destination"], "reader": settings.get("auth", {})},
        "scope": {key: request.get(key) for key in ("since", "until")},
        "schema_version": settings["schema_version"], "algorithm_version": settings["algorithm_version"],
        "normalization_version": analysis_evidence.ANALYSIS_NORMALIZATION_VERSION,
        "prompt_hash": settings["analysis_prompts"]["hash"],
        "payload": build_extraction_payload(job, batch["messages"], batch["context"])}
    return hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()


def _cache_provenance(job, snapshot, now):
    source = snapshot["job"]
    settings, original = job["request"]["settings"], source["request"]["settings"]
    if (not original["analysis"]["cache"]["enabled"]
            or job["request"]["principal"]["namespace"] != source["request"]["principal"]["namespace"]
            or settings["processing"]["model"] != original["processing"]["model"]
            or settings["analysis_prompts"]["hash"] != original["analysis_prompts"]["hash"]
            or settings.get("auth", {}) != original.get("auth", {})
            or any(settings[key] != original[key] for key in ("schema_version", "algorithm_version"))):
        return None
    provenance = snapshot["batch"]["payload"].get("provenance")
    try:
        inferred_at = datetime.fromisoformat(provenance["inferred_at"])
    except (KeyError, TypeError, ValueError):
        return None
    retention = min(settings["analysis"]["retention_days"], original["analysis"]["retention_days"])
    if inferred_at.tzinfo is None or not 0 <= (now - inferred_at).total_seconds() <= retention * 24 * 60 * 60:
        return None
    return provenance


def try_reuse_query_expansion(store, job_id, batch_id, *, generation, validator, now):
    job = store.require_running(job_id, generation=generation)
    batch = store.get_batch(batch_id)
    if (not job["request"]["settings"]["analysis"]["cache"]["enabled"] or batch is None
            or batch["job_id"] != job_id or batch["kind"] != "expand_query" or not batch["cache_key"]
            or any(a["request_kind"] == "inference" for a in batch["attempts"])):
        return False
    snapshot = store.load_leaf_cache(job_id, batch["cache_key"], kind="expand_query")
    if snapshot is None or snapshot["batch"]["payload"]["plan"] != batch["payload"]["plan"]:
        return False
    provenance = _cache_provenance(job, snapshot, now)
    if provenance is None:
        return False
    try:
        output = validator(copy.deepcopy(snapshot["batch"]["payload"]["output"]))
    except BudgetExceededError:
        raise
    except (KeyError, TypeError, ValueError):
        return False
    store.complete_batch(job_id, batch_id, generation=generation, records=[], inputs=[],
                          json_output=output, provenance={**provenance, "cache_hit": True})
    return True


def try_reuse_extraction(store, job_id, batch, *, generation, now):
    job = store.require_running(job_id, generation=generation)
    if not job["request"]["settings"]["analysis"]["cache"]["enabled"]:
        return False
    target = store.get_batch(batch["batch_id"])
    if target is None or target["job_id"] != job_id or any(a["request_kind"] == "inference" for a in target["attempts"]):
        return False
    identity = extraction_cache_key(job, batch)
    snapshot = store.load_leaf_cache(job_id, identity, kind="extract")
    if snapshot is None:
        return False
    provenance = _cache_provenance(job, snapshot, now)
    if provenance is None:
        return False
    progress = snapshot['batch']['payload'].get('extraction_progress')
    if (
            not progress or snapshot['batch']['payload'].get('outcome') != 'succeeded'
            or not progress.get('closed') or progress['unresolved_input_ids']
            or progress['record_errors'] or progress.get('saturated')):
        return False
    record_version = analysis_evidence.record_schema_version(job['request']['settings'])
    properties = analysis_evidence.EVIDENCE_SCHEMA['properties']['records']['items']['properties']
    ref_fields = properties["source_refs"]["items"]["properties"]
    try:
        values = []
        saved_payloads = []
        for row in snapshot["records"]:
            value = json.loads(row["payload_json"])
            saved_payloads.append(value)
            item = {key: copy.deepcopy(value[key]) for key in properties}
            item["source_refs"] = [{key: ref[key] for key in ref_fields} for ref in item["source_refs"]]
            values.append(item)
        records = analysis_evidence.validate_evidence_records(store, job_id, batch["batch_id"], generation=generation,
            document={"records": values, "saturated": False}, fragments=batch["messages"] + batch["context"],
            record_schema_version=record_version)
        if len(records) != len(snapshot["records"]):
            return False
        mapping = {old["record_id"]: new["record_id"] for old, new in zip(snapshot["records"], records)}
        for value, record in zip(saved_payloads, records):
            record["payload"]["related_record_ids"] = [mapping[key] for key in value["related_record_ids"] if key in mapping]
            if 'event_date_support' in value:
                record['payload']['event_date_support'] = copy.deepcopy(value['event_date_support'])
        if progress:
            progress = copy.deepcopy(progress)
            progress['scope'] = analysis_evidence.extraction_scope(batch['messages'])
            progress['accepted_records'] = {record['record_id']: {
                'record': record, 'attempt_id': None, 'inferred_at': provenance['inferred_at']}
                for record in records}
            for decision in progress['dispositions'].values():
                decision['record_ids'] = [mapping[key] for key in decision['record_ids']]
            progress['diagnostics'] = []
    except (KeyError, TypeError, ValueError) as exc:
        if str(exc) == "input_changed" or isinstance(exc, BudgetExceededError):
            raise
        return False
    store.complete_batch(job_id, batch["batch_id"], generation=generation, records=records,
                          inputs=[f["input"] for f in batch["messages"] + batch["context"]],
                          provenance={**provenance, "cache_hit": True}, extraction_progress=progress)
    return True


def normalize_search_query(query, policy):
    if not isinstance(query, str) or not query.strip() or len(query) > policy["max_query_chars"]:
        raise ValueError("invalid_search_query")
    normalized = " ".join(unicodedata.normalize("NFC", query).split())
    phrases = [next(value for value in match if value) for match in re.findall(r'"([^"\n]+)"|«([^»]+)»|“([^”]+)”', normalized)]
    tokens = re.findall(r"[+-]?[^\W_]+(?:[’'./:-][^\W_]+)*", normalized)
    terms, seen, limited = [], set(), False
    for value in [*phrases, *tokens]:
        value = value.strip()
        identity = value.casefold()
        if identity in seen:
            continue
        seen.add(identity)
        if len(value) > policy["max_term_chars"] or len(terms) >= policy["max_query_terms"]:
            limited = True
        else:
            terms.append(value)
    return {"query": query, "normalized_query": normalized, "original_terms": terms, "expanded_terms": [],
            "expansion_status": "not_requested", "reasons": ["original_terms_limited"] if limited else []}


def _analysis_query(job):
    query = job["request"]["request"].get("query") or ""
    if job["command"] == "search":
        return normalize_search_query(query, job["request"]["settings"]["analysis"]["search"])["normalized_query"]
    return query


def validate_search_terms(value, policy, original_terms):
    analysis_evidence.validate_shape(value, analysis_evidence.QUERY_TERMS_SCHEMA)
    if len(value["terms"]) > policy["max_query_terms"]:
        raise ValueError("query_terms_limit")
    terms, seen = [], {term.casefold() for term in original_terms}
    for term in value["terms"]:
        term = " ".join(unicodedata.normalize("NFC", term).split())
        if not term or len(term) > policy["max_term_chars"]:
            raise ValueError("query_term_limit")
        if term.casefold() not in seen:
            terms.append(term)
            seen.add(term.casefold())
    return {"terms": terms}


def prepare_search_query(store, job_id, *, generation, **transport):
    job = store.require_running(job_id, generation=generation)
    settings, request = job["request"]["settings"], job["request"]["request"]
    if job["command"] != "search":
        raise ValueError("Query preparation requires search")
    policy, bundle = settings["analysis"]["search"], settings["analysis_prompts"]
    prepared = normalize_search_query(request["query"], policy)
    if request["mode"] == "full":
        return prepared
    payload = {"model": settings["processing"]["model"],
        "instructions": bundle["system_instructions"] + "\n" + bundle["shared_prefix"],
        "input": analysis_settings.render_analysis_prompt(bundle, "prepare_query", query=prepared["normalized_query"],
            max_terms=policy["max_query_terms"], max_term_chars=policy["max_term_chars"]),
        "text": {"format": analysis_evidence.query_terms_format()}, "reasoning": analysis_settings._analysis_reasoning(settings),
        "max_output_tokens": analysis_settings.stage_output_limit(settings, "expand_query"), "store": False}
    digest = hashlib.sha256(canonical_json({"query": request["query"], "payload": payload,
        "prompt_hash": bundle["hash"], "schema_version": settings["schema_version"],
        "algorithm_version": settings["algorithm_version"]}).encode("utf-8")).hexdigest()
    plan = {"query": prepared["normalized_query"], "original_terms": prepared["original_terms"],
        "max_terms": policy["max_query_terms"], "max_term_chars": policy["max_term_chars"]}
    batch = None
    validator = lambda value: validate_search_terms(value, policy, prepared["original_terms"])
    def fallback(outcome):
        if batch["state"] != "failed":
            store.set_batch_outcome(job_id, batch["batch_id"], generation=generation, outcome=outcome)
        return {**prepared, "expansion_status": "fallback", "reasons": [*prepared["reasons"], "query_expansion_" + outcome]}
    try:
        batch = store.plan_batch(job_id, generation=generation, step_key="expand_query/" + digest,
            kind="expand_query", payload=plan, input_digest=digest,
            cache_key=digest if settings["analysis"]["cache"]["enabled"] else None)
        if batch["state"] not in {"succeeded", "failed"} and try_reuse_query_expansion(
                store, job_id, batch["batch_id"], generation=generation, validator=validator, now=datetime.now(timezone.utc)):
            batch = store.get_batch(batch["batch_id"])
        if batch["state"] == "succeeded":
            output = validator(batch["payload"]["output"])
        elif batch["state"] == "failed":
            return fallback(batch["payload"]["outcome"])
        else:
            try:
                result = request_counted_analysis_payload(store, job_id, batch["batch_id"], generation=generation,
                    payload=payload, **transport)
            except openai_api.OpenAIRequestError:
                return fallback("provider_error")
            except ValueError as exc:
                if str(exc) in {"max_input_tokens", "model_context_limit"}:
                    return fallback("saturated")
                if str(exc) == "input_token_count_unavailable":
                    return fallback("provider_error")
                raise
            try:
                outcome, text = analysis_evidence.response_text(result["response"])
                output = validator(analysis_evidence.parse_document(text)) if outcome == "completed" else None
            except ValueError:
                return fallback("invalid_schema")
            if outcome != "completed":
                return fallback(outcome)
            attempt = next(a for a in store.get_batch(batch["batch_id"])["attempts"] if a["attempt_id"] == result["attempt_id"])
            store.complete_batch(job_id, batch["batch_id"], generation=generation, records=[], inputs=[], json_output=output,
                attempt_id=result["attempt_id"], usage=attempt["usage"], provenance={
                    "actual_model": result["response"].get("model"), "inferred_at": attempt["created_at"], "cache_hit": False})
    except BudgetExceededError as exc:
        store.record_budget_stop(job_id, generation=generation, reason=exc.reason)
        prepared.update(expansion_status="fallback")
        prepared["reasons"].append(exc.reason)
        return prepared
    prepared.update(expanded_terms=output["terms"], expansion_status="completed")
    return prepared



def _analysis_period(job):
    request = job['request']['request']
    return {'since': request['since'], 'until': min(request['until'], request['cutoff_at'])}


def _extraction_context(context):
    values = _extraction_fragments(context)
    return [{'input_id': f'c{index:04}', **value} for index, value in enumerate(values)
            if value['message']['text'].strip()]

def build_extraction_payload(job, messages, context):
    settings = job['request']['settings']
    bundle = settings['analysis_prompts']
    scope = analysis_evidence.extraction_scope(messages)
    scope['context_input_ids'] = analysis_evidence.extraction_context_ids(context)
    selected = {'scope_sha256': scope['sha256'], 'inputs': [
        {'input_id': key, **value} for key, value in zip(scope['inputs'], _extraction_fragments(messages))
        if key in scope['model_input_ids']],
        'analysis_period': _analysis_period(job),
        'context_scope_sha256': analysis_evidence.extraction_scope(context)['sha256']}
    return {'model': settings['processing']['model'],
            'instructions': bundle['system_instructions'] + "\n" + bundle['shared_prefix'],
            'input': analysis_settings.render_analysis_prompt(bundle, 'extract', command_rubric=bundle['commands'][job['command']],
                query=_analysis_query(job), messages=canonical_json(selected),
                context=canonical_json(_extraction_context(context))),
            'reasoning': analysis_settings._analysis_reasoning(settings),
            'text': {'format': analysis_evidence.addressed_evidence_format(scope)},
            'max_output_tokens': analysis_settings.stage_output_limit(settings, 'extract'), 'store': False}



def _relation_keys(record, store, *, digest_messages=frozenset(), for_index=False):
    payload = record["payload"]
    keys = {("entity", " ".join(value.casefold().split())) for value in payload["entities"] if value.strip()}
    if payload["topic_key"]:
        keys.add(("topic", " ".join(payload["topic_key"].casefold().split())))
    sources = {(ref['channel_id'], ref['message_id']) for ref in payload['source_refs']}
    for ref in payload["source_refs"] + payload["related_message_refs"]:
        pair = (ref['channel_id'], ref['message_id'])
        if pair not in digest_messages:
            keys.add(("message", f"{pair[0]}:{pair[1]}"))
        elif pair in sources:
            # Match a digest's facts to explicit replies, not to each other.
            keys.add(('digest_source' if for_index else 'digest_reply', f'{pair[0]}:{pair[1]}'))
    if sources & digest_messages:
        keys.add(('exact_record', hashlib.sha256(_technical_record_identity(record).encode()).hexdigest()))
    for ref in payload["source_refs"]:
        reply = store.reply_message_id(ref["channel_id"], ref["message_id"])
        if reply is not None:
            value = f"{ref['channel_id']}:{reply}"
            if (ref['channel_id'], reply) in digest_messages:
                keys.add(('digest_reply' if for_index else 'digest_source', value))
                keys.add(('reply_context', value))
            else:
                keys.add(("message", value))
    return sorted(keys)


def _relation_digest_messages(store, job, eligible):
    checked, digests = set(), set()
    maximum = job['request']['settings']['analysis']['max_intermediate_bytes']
    for record in store.iter_leaf_evidence(job['job_id'],
            page_size=max(1, job['request']['settings']['sync']['batch_size'])):
        if record['record_id'] not in eligible:
            continue
        store.require_running(job['job_id'], generation=job['generation'])
        for ref in record['payload']['source_refs'] + record['payload']['related_message_refs']:
            pair = (ref['channel_id'], ref['message_id'])
            if pair in checked:
                continue
            checked.add(pair)
            frozen = store.manifest_reference(job['job_id'], *pair)
            if frozen is None:
                continue
            try:
                source, digest = analysis_evidence._analysis_message_snapshot(store, *pair, maximum)
            except ValueError as exc:
                if str(exc) != 'manifest_byte_cap':
                    raise
                continue
            if digest == frozen['content_digest'] and analysis_evidence._is_digest_source(source):
                digests.add(pair)
    return digests


def iter_relation_candidates(store, job_id, *, generation):
    job = store.require_running(job_id, generation=generation)
    if not analysis_settings.supported_analysis_policy(job['request']['settings']):
        raise ValueError('analysis_policy_incompatible')
    policy = job["request"]["settings"]["analysis"]
    count, limited = 0, False
    # Source verification can interrupt a changed job. Do it before the index
    # transaction; its key callback must only read this frozen membership.
    eligible = {row['record_id'] for row in store.iter_leaf_evidence(job_id,
                page_size=max(1, job['request']['settings']['sync']['batch_size']))
                if analysis_evidence.record_source_eligible(store, job, row['payload'])}
    digest_messages = _relation_digest_messages(store, job, eligible)
    def in_scope(record):
        return record['record_id'] in eligible
    def qa_role(record):
        if job["command"] == "qa":
            if record["record_type"] in {"question", "problem"}:
                return "question"
            if record["record_type"] == "answer":
                return "answer"
        return None
    def index_keys(record):
        if not in_scope(record):
            return []
        keys = _relation_keys(record, store, digest_messages=digest_messages, for_index=True)
        role = qa_role(record)
        return keys + ([("qa_role", role)] if role else [])
    with store.relation_index(job_id, generation=generation, key_func=index_keys):
        for left in store.iter_leaf_evidence(job_id, page_size=max(1, job["request"]["settings"]["sync"]["batch_size"])):
            if not in_scope(left):
                continue
            store.require_running(job_id, generation=generation)
            ids = store.relation_candidate_ids(job_id, left["record_id"],
                                               keys=_relation_keys(left, store, digest_messages=digest_messages),
                                               limit=policy["relation_candidates_per_record"])
            role = qa_role(left)
            if role and len(ids) <= policy["relation_candidates_per_record"]:
                # Keep indexed hints first, then propose cross-language/wording
                # pairs without requiring identical extracted entity/topic keys.
                extra = store.relation_candidate_ids(job_id, left["record_id"],
                    keys=[("qa_role", "answer" if role == "question" else "question")],
                    limit=policy["relation_candidates_per_record"])
                ids += [record_id for record_id in extra if record_id not in ids]
            limited |= len(ids) > policy["relation_candidates_per_record"]
            for record_id in ids[:policy["relation_candidates_per_record"]]:
                if count >= policy["max_relation_pairs"]:
                    limited = True
                    break
                right = store.leaf_evidence(job_id, record_id)
                if right is not None:
                    count += 1
                    yield {"left": left, "right": right}
            if count >= policy["max_relation_pairs"] and limited:
                break
    current = store.require_running(job_id, generation=generation)
    checkpoint = {**current["checkpoint"], "relations": {**current["checkpoint"].get("relations", {}), "candidate_pairs": count, "limited": limited}}
    store.save_checkpoint(job_id, checkpoint, generation=generation, max_bytes=policy["max_intermediate_bytes"])


def _technical_record_identity(record):
    properties = analysis_evidence.EVIDENCE_SCHEMA['properties']['records']['items']['properties']
    value = {key: copy.deepcopy(record["payload"][key]) for key in properties}
    ref_keys = properties["source_refs"]["items"]["properties"]
    value["source_refs"] = [{key: ref[key] for key in ref_keys} for ref in value["source_refs"]]
    return canonical_json(value)


def _verify_relation_sources(store, job, records):
    checked = set()
    for record in records.values():
        for ref in record["payload"]["source_refs"]:
            identity = (ref["channel_id"], ref["message_id"], ref["content_digest"])
            if identity not in checked:
                analysis_evidence._verified_source_snapshot(store, job, ref)
                checked.add(identity)


def _record_aliases(identities):
    return analysis_store.record_aliases(identities)



def _model_fact_record(record, *, synthesis=False):
    """Keep factual/source identity fields; mechanical provenance stays canonical."""
    payload = {key:value for key,value in record['payload'].items()
               if key not in {'schema_version', 'reported_at', 'related_record_ids'}
               and (not synthesis or key not in {'related_message_refs', 'topic_key'})}
    payload['source_refs'] = [{key:value for key,value in ref.items()
        if key not in {'content_digest', 'fragment_start', 'fragment_end', 'text_view_version'}}
        for ref in payload['source_refs']]
    return {**record, 'payload':payload}


def _compact_record_quotes(records, *, synthesis=False):
    records = [_model_fact_record(record, synthesis=synthesis) for record in records]
    quotes = {ref['quote'] for record in records for ref in record['payload']['source_refs']}
    aliases = {quote: f'q{index:03}' for index, quote in enumerate(sorted(quotes), 1)}
    wire = [{**record, 'payload': {**record['payload'], 'source_refs': [
        {**{key: value for key, value in ref.items() if key != 'quote'}, 'quote_ref': aliases[ref['quote']]}
        for ref in record['payload']['source_refs']]}} for record in records]
    return wire, {alias: quote for quote, alias in aliases.items()}


def _synthesis_source_metadata(ref):
    value = {key:item for key,item in ref.items() if key not in {'quote','quote_ref'}}
    if 'date_utc' in value:
        value['published_at'] = value.pop('date_utc')
    return value


def _compact_synthesis_sources(records, *, synthesis=True):
    """Transmit each publication identity once; exact quotes remain addressable."""
    records = [_model_fact_record(record, synthesis=synthesis) for record in records]
    metadata = {canonical_json(_synthesis_source_metadata(ref)): _synthesis_source_metadata(ref)
                for record in records for ref in record['payload']['source_refs']}
    aliases = {key:f's{index:03}' for index,key in enumerate(sorted(metadata),1)}
    values = [{**record, 'payload': {**record['payload'], 'source_refs': [
        {'source_id': aliases[canonical_json(_synthesis_source_metadata(ref))], 'quote_ref': ref['quote_ref']}
        for ref in record['payload']['source_refs']]}} for record in records]
    return values, {aliases[key]:value for key,value in metadata.items()}


def build_relation_data(pair_map, record_payloads, *, repair_index=0, validation_errors=None):
    """Aliases derive from the whole frozen group, including on residual repair."""
    aliases = _record_aliases(record_payloads)
    identities = {key for pair in pair_map.values() for key in pair}
    data = {'pairs': [{'pair_id': key, 'left_id': aliases[pair[0]], 'right_id': aliases[pair[1]]}
                      for key, pair in pair_map.items()],
            'records': [{'record_id': aliases[key], 'payload': record_payloads[key]} for key in sorted(identities)],
            'repair_index': repair_index, 'validation_errors': validation_errors or []}
    data['records'], data['quote_texts'] = _compact_record_quotes(data['records'])
    data['records'], data['source_table'] = _compact_synthesis_sources(data['records'], synthesis=False)
    for item, pair in zip(data['pairs'], pair_map.values()):
        item['duplicate_allowed'] = not analysis_evidence.relation_duplicate_error(*(record_payloads[key] for key in pair))
        item['left_claim'], item['right_claim'] = (record_payloads[key]['claim'] for key in pair)
    return data


def _link_pair_group(store, job_id, pairs, *, generation, depth=0, packing_reason='end', **transport):
    job = store.require_running(job_id, generation=generation)
    if not analysis_settings.supported_analysis_policy(job['request']['settings']):
        raise ValueError("analysis_policy_incompatible")
    settings, policy = job["request"]["settings"], job["request"]["settings"]["analysis"]
    records = {record["record_id"]: record for pair in pairs for record in (pair["left"], pair["right"])}
    _verify_relation_sources(store, job, records)
    mapping = {f"p{index:03}": [pair["left"]["record_id"], pair["right"]["record_id"]]
               for index, pair in enumerate(pairs, 1)}
    plan = {"pair_map": mapping, "record_digests": {key: hashlib.sha256(canonical_json(value["payload"]).encode()).hexdigest()
                                                  for key, value in sorted(records.items())}}
    plan['packing'] = {'mode': 'adaptive' if policy['link_pairs_per_request'] == 0 else 'fixed',
        'pairs': len(pairs), 'unique_records': len(records),
        'reason': 'split' if depth else packing_reason}
    digest = hashlib.sha256(canonical_json(plan).encode()).hexdigest()
    batch = store.plan_batch(job_id, generation=generation, step_key=f"link/v2/{digest}", kind="link", payload=plan, input_digest=digest)
    if batch["state"] in {"succeeded", "failed"}:
        return batch["state"] == "failed"
    if batch["state"] == "skipped":
        if batch["payload"].get("link_split"):
            pending = set(batch["payload"]["link_progress"]["unresolved_pair_ids"])
            pairs = [pair for key, pair in zip(mapping, pairs) if key in pending]
        return _split_link_group(store, job_id, pairs, generation=generation, depth=depth, **transport)
    technical = {key: {"left_id": mapping[key][0], "right_id": mapping[key][1], "relation": "duplicate",
                      "reason": "Идентичное извлечение из тех же версий и фрагментов источников."}
                 for key, pair in zip(mapping, pairs)
                 if _technical_record_identity(pair["left"]) == _technical_record_identity(pair["right"])}
    progress = batch["payload"].get("link_progress")
    if progress is None:
        progress = {"accepted_decisions": technical, "unresolved_pair_ids": sorted(set(mapping) - set(technical)),
                    "diagnostics": [], "active_request": None, "saturated": False}
        if not progress["unresolved_pair_ids"]:
            store.save_link_progress(job_id, batch["batch_id"], generation=generation, expected=None, progress=progress)
            return False
    bundle = settings["analysis_prompts"]
    while True:
        expected = batch["payload"].get("link_progress")
        claimed = store.begin_link_request(job_id, batch["batch_id"], generation=generation, expected=expected, progress=progress)
        if claimed is None:
            store.set_batch_outcome(job_id, batch["batch_id"], generation=generation,
                                    outcome="saturated" if progress["saturated"] else "invalid_schema")
            return True
        batch = claimed
        progress = batch["payload"]["link_progress"]
        marker = progress["active_request"]
        if any(a["request_kind"] == "inference" for a in batch["attempts"][marker["attempt_offset"]:]):
            # The physical receipt survived but its observation did not: do not replay it.
            store.set_batch_outcome(job_id, batch["batch_id"], generation=generation, outcome="incomplete")
            return True
        pending = {key: mapping[key] for key in marker["pair_ids"]}
        ids = {key for pair in pending.values() for key in pair}
        data = build_relation_data(pending, {key: record['payload'] for key, record in records.items()},
            repair_index=marker['repair_index'],
            validation_errors=progress['diagnostics'][-1]['codes'] if progress['diagnostics'] else [])
        payload = {"model": settings["processing"]["model"], "instructions": bundle["system_instructions"] + "\n" + bundle["shared_prefix"],
            "input": analysis_settings.render_analysis_prompt(bundle, "link", command_rubric=bundle["commands"][job["command"]],
                query=_analysis_query(job), records=canonical_json(data)),
            "reasoning": analysis_settings._analysis_reasoning(settings), "text": {"format": analysis_evidence.relation_format(pending,
                record_payloads={key: record['payload'] for key, record in records.items()})},
            "max_output_tokens": analysis_settings.stage_output_limit(settings, "link"), "store": False}
        _verify_relation_sources(store, job, records)
        try:
            result = request_counted_analysis_payload(store, job_id, batch["batch_id"], generation=generation, payload=payload, **transport)
        except ValueError as exc:
            if str(exc) not in {"max_input_tokens", "model_context_limit"}:
                raise
            if len(pairs) > 1 and depth < policy["max_split_depth"] and not any(a["request_kind"] == "inference" for a in store.get_batch(batch["batch_id"])["attempts"]):
                store.skip_counted_batch(job_id, batch["batch_id"], generation=generation)
                return _split_link_group(store, job_id, pairs, generation=generation, depth=depth, **transport)
            store.set_batch_outcome(job_id, batch["batch_id"], generation=generation,
                                    outcome="max_split_depth", diagnostic_code=str(exc))
            return True
        outcome = "completed"
        try:
            outcome, text = analysis_evidence.response_text(result["response"])
            if outcome != "completed":
                raise ValueError("relation_" + outcome)
            classified = analysis_evidence.classify_relation_document(analysis_evidence.parse_document(text), pending,
                max_bytes=policy["max_intermediate_bytes"],
                record_payloads={key: record['payload'] for key, record in records.items()}
                   )
        except ValueError as exc:
            classified = {"accepted_decisions": {}, "unresolved_pair_ids": list(pending), "saturated": False,
                "diagnostics": {"requested": len(pending), "accepted": 0, "missing": len(pending), "extra": 0,
                    "duplicate": 0, "conflicting": 0, "codes": [str(exc)]}}
        _verify_relation_sources(store, job, records)
        accepted = {**progress["accepted_decisions"], **classified["accepted_decisions"]}
        diagnostic = {**classified["diagnostics"], "attempt_id": result["attempt_id"],
            "repair_index": marker["repair_index"], "scope_sha256": marker["scope_sha256"],
            "request_kind": "inference", "provider_ms": result["latency_ms"]}
        diagnostic.update(zip(LINK_OBSERVATION_FIELDS, (
            len(pending), len(ids), sum(len(canonical_json(record).encode()) for record in data['records']),
            len(canonical_json(payload).encode())), strict=True))
        progress = {"accepted_decisions": accepted, "unresolved_pair_ids": sorted(set(mapping) - set(accepted)),
                    "diagnostics": [*progress["diagnostics"], diagnostic], "active_request": None,
                    "saturated": classified["saturated"]}
        batch = store.save_link_progress(job_id, batch["batch_id"], generation=generation,
            expected=batch["payload"]["link_progress"], progress=progress, attempt_id=result["attempt_id"])
        if outcome != "completed":
            attempt = next(a for a in batch["attempts"] if a["attempt_id"] == result["attempt_id"])
            output_limited = outcome == "incomplete" and attempt.get("usage") and attempt["usage"]["output_tokens"] >= attempt["output_limit"]
            if output_limited and len(pending) > 1 and depth < policy["max_split_depth"]:
                store.split_link_progress(job_id, batch["batch_id"], generation=generation, expected=progress)
                remaining = [pair for key, pair in zip(mapping, pairs) if key in pending]
                return _split_link_group(store, job_id, remaining, generation=generation, depth=depth, **transport)
            store.set_batch_outcome(job_id, batch["batch_id"], generation=generation, outcome=outcome)
            return True
        if batch["state"] == "succeeded":
            return False
        if progress["saturated"] and len(progress["unresolved_pair_ids"]) > 1 and depth < policy["max_split_depth"]:
            store.split_link_progress(job_id, batch["batch_id"], generation=generation, expected=progress)
            remaining = [pair for key, pair in zip(mapping, pairs) if key in progress["unresolved_pair_ids"]]
            return _split_link_group(store, job_id, remaining, generation=generation, depth=depth, **transport)
        if not progress["unresolved_pair_ids"]:
            store.set_batch_outcome(job_id, batch["batch_id"], generation=generation, outcome="saturated")
            return True


def _split_link_group(store, job_id, pairs, *, generation, depth, **transport):
    middle = len(pairs) // 2
    left = _link_pair_group(store, job_id, pairs[:middle], generation=generation, depth=depth + 1, **transport)
    right = _link_pair_group(store, job_id, pairs[middle:], generation=generation, depth=depth + 1, **transport)
    return left or right


def link_analysis_records(store, job_id, *, generation, **transport):
    job = store.require_running(job_id, generation=generation)
    policy = job["request"]["settings"]["analysis"]
    partial, pending = False, []
    fixed = policy.get('link_pairs_per_request', policy['relation_candidates_per_record'])
    adaptive = fixed == 0
    # c29 measured 6003 output tokens/121 pairs (~50). 64 adds 29% headroom.
    # This is an estimate, not a guarantee; exact count/saturation/split remain authoritative.
    target = min(analysis_evidence.RELATION_PAIR_ENUM_LIMIT - 4,
                 max(1, analysis_settings.stage_output_limit(job['request']['settings'], 'link') // 64) if adaptive else max(1, fixed))
    records, record_chars = {}, 0
    guard = analysis_settings.analysis_ai(job['request']['settings'])['message_block_max_chars']
    try:
        for pair in iter_relation_candidates(store, job_id, generation=generation):
            additions = {record['record_id']: record for record in (pair['left'], pair['right'])
                         if record['record_id'] not in records}
            extra = sum(len(canonical_json(record['payload'])) for record in additions.values())
            if adaptive and pending and record_chars + extra > guard:
                partial |= _link_pair_group(store, job_id, pending, generation=generation,
                    packing_reason='record_chars', **transport)
                pending, records, record_chars = [], {}, 0
                additions = {record['record_id']: record for record in (pair['left'], pair['right'])}
                extra = sum(len(canonical_json(record['payload'])) for record in additions.values())
            records.update(additions)
            record_chars += extra
            pending.append({key: records[pair[key]['record_id']] for key in ('left', 'right')})
            if len(pending) >= target:
                partial |= _link_pair_group(store, job_id, pending, generation=generation,
                    packing_reason='pair_target', **transport)
                pending = []
                records, record_chars = {}, 0
        if pending:
            partial |= _link_pair_group(store, job_id, pending, generation=generation, **transport)
    except BudgetExceededError as exc:
        store.record_budget_stop(job_id, generation=generation, reason=exc.reason)
        partial = True
    current = store.require_running(job_id, generation=generation)
    partial |= current["checkpoint"].get("relations", {}).get("limited", False)
    checkpoint = {**current["checkpoint"], "relations": {**current["checkpoint"].get("relations", {}), "partial": partial}}
    store.save_checkpoint(job_id, checkpoint, generation=generation, max_bytes=policy["max_intermediate_bytes"])
    return {"outcome": "partial" if partial else "succeeded", "partial": partial}


def _merge_reference(kind, batch_id, node_id, payload):
    return {"kind": kind, "batch_id": batch_id, "node_id": node_id,
            "digest": hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()}


def _synthesis_wire_evidence(job, evidence):
    settings = job["request"]["settings"]
    # Keep classifier explanations in the ledger. The final model needs the
    # verified edges and source facts, without repeating each link diagnostic.
    wire_evidence = {**evidence, "relations": [
        {key: relation[key] for key in ("left_id", "right_id", "relation")}
        for relation in evidence.get("relations", [])]}
    topic_groups, equivalent_details = None, None
    wire_evidence['analysis_period'] = _analysis_period(job)
    aliases = _record_aliases(record['record_id'] for record in evidence['records'])
    wire_evidence['records'] = [{**record, 'record_id': aliases[record['record_id']]}
                                for record in evidence['records']]
    wire_evidence['relations'] = [{**edge, 'left_id': aliases.get(edge['left_id'], edge['left_id']),
        'right_id': aliases.get(edge['right_id'], edge['right_id'])} for edge in wire_evidence['relations']]
    wire_evidence['groups'] = [{'evidence_ids': [aliases.get(key, key) for key in group['evidence_ids']]}
                              for group in evidence.get('groups', [])]
    wire_evidence['records'], wire_evidence['quote_texts'] = _compact_record_quotes(wire_evidence['records'], synthesis=True)
    wire_evidence['records'], wire_evidence['source_table'] = _compact_synthesis_sources(wire_evidence['records'])
    wire_evidence['records'] = [{**record['payload'], 'record_id':record['record_id']}
                                for record in wire_evidence['records']]
    equivalent_details = analysis_evidence.equivalent_detail_groups(
        {aliases[r['record_id']]: r['payload'] for r in evidence['records']},
        relations=wire_evidence['relations'])
    wire_evidence['equivalent_detail_groups'] = equivalent_details
    if job['command'] == 'topics':
        topic_groups = [[aliases[key] for key in group] for group in
            analysis_evidence.topic_components(aliases, evidence.get('relations', []), consistent=True)]
        wire_evidence['topic_components'] = topic_groups
        # The consistent partition supersedes unranked reducer wrappers.
        # Keep canonical groups for resume/reporting, not duplicated on wire.
        wire_evidence.pop('groups', None)
        component = {key:index for index,group in enumerate(topic_groups) for key in group}
        wire_evidence['relations'] = [edge for edge in wire_evidence['relations']
            if component[edge['left_id']] == component[edge['right_id']]]
    if len(canonical_json(wire_evidence)) > analysis_settings.analysis_ai(settings)['message_block_max_chars']:
        wire_evidence['equivalent_detail_groups'] = []
    return wire_evidence, topic_groups, equivalent_details


def build_synthesis_payload(job, evidence):
    settings, request = job["request"]["settings"], job["request"]["request"]
    bundle = settings["analysis_prompts"]
    profile = settings["analysis"]["output"]["profiles"][request["detail"]]
    wire_evidence, topic_groups, equivalent_details = _synthesis_wire_evidence(job, evidence)
    return {"model": settings["processing"]["model"], "instructions": bundle["system_instructions"] + "\n" + bundle["shared_prefix"],
        "input": analysis_settings.render_analysis_prompt(bundle, "synthesize", command_rubric=bundle["commands"][job["command"]],
            query=_analysis_query(job), detail_instructions=bundle["details"][request["detail"]],
            limits=canonical_json(analysis_settings._synthesis_limits(job)), evidence=canonical_json(wire_evidence)),
        "reasoning": analysis_settings._analysis_reasoning(settings),
        "text": {"format": analysis_evidence.synthesis_format(topic_groups=topic_groups)},
        "max_output_tokens": profile["max_output_tokens"], "store": False}


def _synthesis_evidence(store, job):
    state = job["checkpoint"].get("merge")
    if state is None or state["state"] not in {"ready", "partial"}:
        raise ValueError("synthesis_merge_not_ready")
    for _ in iter_analysis_manifest_messages(store, job["job_id"], generation=job["generation"]):
        pass
    groups, records, seen, included = [], [], set(), set()
    excluded, excluded_groups = set(), set()
    reasons = (set(state['reasons']) | set(job['checkpoint']['manifest'].get('reasons', []))
               | set(store.incomplete_evidence_reasons(job['job_id'])))
    stop = job["checkpoint"].get("budget_stop")
    if stop:
        reasons.add(stop["reason"])
    for ref in state["frontier"]:
        node = _resolve_merge_node(store, job["job_id"], ref)
        value = node["payload"]
        groups.append({"node_id": ref["node_id"], "title": value.get("title", value.get("topic_key")),
                       "summary": value.get("summary", value.get("claim")), "evidence_ids": []})
    size = (0 if job['command'] == 'topics' else
            len(canonical_json([{'evidence_ids': g['evidence_ids']} for g in groups])))
    source_keys, quote_keys = set(), set()
    alias_bound = 's' + '9' * max(3, len(str(job['request']['settings']['analysis']['max_intermediate_bytes'])))
    ref_bound = len(canonical_json({'source_id': alias_bound, 'quote_ref': alias_bound}))
    entry_overhead = len(alias_bound) + 4
    size += 64  # Keys/brackets for the factual packet's four tables/arrays.
    guard = analysis_settings.analysis_ai(job["request"]["settings"])["message_block_max_chars"]
    def members():
        # Give each ranked group a representative before filling from the first
        # group. Only one lineage iterator is live; do not materialize all facts.
        for first_pass in (True, False):
            for ref, group in zip(state["frontier"], groups):
                for index, record in enumerate(iter_merge_leaf_records(store, job["job_id"], ref, generation=job["generation"])):
                    if first_pass:
                        yield group, record
                        break
                    if index:
                        yield group, record

    for group, record in members():
        identity = record["record_id"]
        if not analysis_evidence.record_source_eligible(store, job, record['payload']):
            excluded.add(identity)
            excluded_groups.add(group['node_id'])
            continue
        seen.add(identity)
        value = {key: record[key] for key in ("record_id", "record_type", "payload")}
        budget_value = _model_fact_record(value, synthesis=True)
        sources, quotes = set(), set()
        record_size = len(canonical_json(budget_value))
        flat = {**budget_value['payload'], 'record_id':identity}
        envelope_size = len(canonical_json(budget_value)) - len(canonical_json(flat))
        refs = budget_value['payload']['source_refs']
        sources = {canonical_json(_synthesis_source_metadata(ref)) for ref in refs}
        quotes = {ref['quote'] for ref in refs}
        bare = {**budget_value, 'payload': {**budget_value['payload'], 'source_refs': [{} for _ in refs]}}
        # Every distinct source/quote consumes stored bytes, so the frozen
        # storage ceiling bounds alias width. Charge shared table entries once.
        record_size = (len(canonical_json(bare)) - envelope_size + (ref_bound-2)*len(refs) + 1
            + sum(len(key)+entry_overhead for key in sources-source_keys)
            + sum(len(canonical_json([quote]))-2+entry_overhead for quote in quotes-quote_keys))
        length = (0 if identity in included else record_size) + len(canonical_json([identity])) - 1
        if size + length <= guard:
            source_keys.update(sources)
            quote_keys.update(quotes)
            if identity not in included:
                records.append(value)
                included.add(identity)
            group["evidence_ids"].append(identity)
            size += length
        else:
            reasons.add("synthesis_evidence_limited")
    selected = {record["record_id"] for record in records}
    relations = [value for value in store.iter_link_decisions(job["job_id"])
                 if value["left_id"] in selected and value["right_id"] in selected]
    manifest = job["checkpoint"]["manifest"]
    passed_groups = [group for group in groups if group["evidence_ids"]]
    for group in passed_groups:
        if group['node_id'] in excluded_groups:
            # Excluded facts must not survive in reduced prose.
            group['title'] = None
            group['summary'] = '\n'.join(record['payload']['claim'] for record in records
                if record['record_id'] in group['evidence_ids'])
    order = state.get("group_order")
    if order not in {"global", "unranked"}:
        raise ValueError("synthesis_group_order")
    return {"groups": passed_groups, "records": records, "relations": relations, "group_order": order,
        "partial": state["partial"] or bool(reasons) or manifest.get("partial", False), "reasons": sorted(reasons),
        "coverage": {"selection_mode": manifest["selection_mode"], "selected_messages": manifest["selected"],
            "processed_messages": store.processed_analysis_messages(job["job_id"]), "known_skipped": manifest.get("known_skipped"),
            "ranked_evidence_records": len(seen), "synthesis_evidence_records": len(records),
            "group_order": order, "ranking_scope": "complete" if len(passed_groups) == len(groups) and len(records) == len(seen) else "limited",
            "ranking_scope_basis": "extracted_evidence",
            "eligible_groups": len(groups), "passed_groups": len(passed_groups), "eligible_evidence_records": len(seen),
            "source_excluded_evidence_records": state.get('source_excluded_evidence_records', 0) + len(excluded),
            "extracted_evidence_records": state.get('extracted_evidence_records', len(seen) + len(excluded)),
            **({"candidate_messages": manifest["selected_candidates"], "context_messages": manifest["context_messages"]}
               if manifest["selection_mode"] == "candidates" else {})}}


def _empty_synthesis_document(*, insufficient=False):
    return {"summary": "", "items": [], "limitations": [], "insufficient_information": insufficient, "saturated": False}


def _plan_synthesis_batch(store, job, evidence, payload, *, generation):
    digest = hashlib.sha256(canonical_json(payload).encode('utf-8')).hexdigest()
    plan = {'evidence': [{'record_id': value['record_id'],
                         'digest': hashlib.sha256(canonical_json(value['payload']).encode()).hexdigest()}
                        for value in evidence['records']],
            'detail': job['request']['request']['detail'], 'input_digest': digest}
    data, _, candidates = _synthesis_wire_evidence(job, evidence)
    hints = data['equivalent_detail_groups']
    plan['detail_hint'] = {'candidate_groups': len(candidates), 'sent_groups': len(hints),
        'chars': len(canonical_json(hints)), 'omitted_reason': 'packing' if len(hints) < len(candidates) else None}
    return store.plan_batch(job['job_id'], generation=generation,
        step_key=f'synthesize/{digest}', kind='synthesize', payload=plan, input_digest=digest)




def _synthesis_result(job, evidence, document, *, reason=None):
    schema = analysis_evidence.synthesis_format()['schema']
    analysis_evidence.validate_shape(document, schema)
    limits = analysis_settings._synthesis_limits(job)
    known = {value["record_id"]: value["payload"] for value in evidence["records"]}
    topic_edges = evidence.get('relations', [])
    if job['command'] == 'topics':
        groups = analysis_evidence.topic_components(known, topic_edges, consistent=True)
        membership = {key:index for index,group in enumerate(groups) for key in group}
        topic_edges = [edge for edge in topic_edges if edge['left_id'] in membership and edge['right_id'] in membership
                       and membership[edge['left_id']] == membership[edge['right_id']]]
    answer_pairs = analysis_evidence._qa_answer_pairs(evidence.get("relations", [])) if job["command"] == "qa" else set()
    value = copy.deepcopy(document)
    canonical = {alias: key for key, alias in _record_aliases(known).items()}
    for item in value['items']:
        item['evidence_ids'] = list(dict.fromkeys(canonical.get(key, key) for key in item['evidence_ids']))
    errors = []
    for item_index, item in enumerate(value["items"]):
        try:
            ids = item["evidence_ids"]
            if not ids or len(set(ids)) != len(ids) or not set(ids) <= set(known):
                raise ValueError("synthesis_evidence_scope")
            original_ids = set(ids)
            for relation in topic_edges:
                pair = [relation["left_id"], relation["right_id"]]
                if relation["relation"] == "contradicts" and original_ids.intersection(pair) and set(pair) <= set(known):
                    ids.extend(key for key in pair if key not in ids)
                    item["contradictions"] = list(dict.fromkeys([*item["contradictions"], *(known[key]["claim"] for key in pair)]))
            if job["command"] == "qa":
                analysis_evidence._validate_qa_support(ids, known, answer_pairs)
            if job['command'] == 'topics':
                analysis_evidence.validate_topic_group(ids, evidence.get('relations', []), scope_identities=known)
        except ValueError as exc:
            errors.append(f'items[{item_index}] {exc}')
    if errors:
        raise ValueError('; '.join(errors))
    value["items"] = value["items"][:limits["max_items"]]
    if job["command"] == "search" and value["items"] and not value["summary"].strip():
        raise ValueError("search_answer_missing_summary")
    reasons = set(evidence["reasons"])
    if reason:
        reasons.add(reason)
    if value["saturated"]:
        reasons.add("output_saturated")
    def text_size():
        return len(value["summary"]) + sum(map(len, value["limitations"])) + sum(
            len(item["title"]) + len(item["text"]) + sum(len(text) for field in ("contradictions",) for text in item.get(field, []))
            for item in value["items"])
    while text_size() > limits["max_chars"] and value["items"]:
        value["items"].pop()
        value["summary"] = ""
        reasons.add("max_chars")
    if text_size() > limits["max_chars"]:
        value.update(summary="", limitations=[])
        reasons.add("max_chars")
    if not value["items"]:
        value["summary"] = ""
        if job["command"] in {"search", "qa"}:
            value["insufficient_information"] = True
    elif job['command'] == 'topics':
        value['insufficient_information'] = False
    return {key: value[key] for key in ("summary", "items", "limitations", "insufficient_information")} | {
        "command": job["command"], "query": job["request"]["request"].get("query"), "detail": job["request"]["request"]["detail"],
        "partial": evidence["partial"] or bool(reasons), "reasons": sorted(reasons), "coverage": copy.deepcopy(evidence["coverage"])}


def synthesize_analysis_result(store, job_id, *, generation, **transport):
    try:
        return _synthesize_analysis_result(store, job_id, generation=generation, **transport)
    except BudgetExceededError as exc:
        return store.record_budget_stop(job_id, generation=generation, reason=exc.reason)


def _synthesize_analysis_result(store, job_id, *, generation, **transport):
    job = store.require_running(job_id, generation=generation)
    evidence = _synthesis_evidence(store, job)
    if job["result"] is not None and job["result"].get("detail") == job["request"]["request"]["detail"]:
        return job["result"]
    policy = job["request"]["settings"]["analysis"]
    split_depth = 0
    while evidence["records"]:
        payload = build_synthesis_payload(job, evidence)
        route = job['checkpoint'].get('merge', {})
        if route.get('route') == 'direct' and split_depth == 0:
            counted = {key: value for key, value in payload.items() if key in {'model', 'input', 'instructions', 'text', 'reasoning'}}
            if (hashlib.sha256(canonical_json(counted).encode()).hexdigest() != route['direct_input_digest']
                    or hashlib.sha256(canonical_json(job['request']['settings']).encode()).hexdigest() != route['direct_settings_sha256']):
                raise ValueError('analysis_policy_incompatible')
        original_task = payload["input"]
        batch = _plan_synthesis_batch(store, job, evidence, payload, generation=generation)
        if batch["state"] == "failed":
            final = _synthesis_result(job, evidence, _empty_synthesis_document(), reason=batch["payload"]["outcome"])
            store.save_analysis_result(job_id, final, generation=generation)
            return final
        try:
            prepared = prepare_analysis_payload(store, job_id, batch["batch_id"], generation=generation, payload=payload, **transport)
        except BudgetExceededError:
            raise
        except ValueError as exc:
            if str(exc) not in {"max_input_tokens", "model_context_limit"}:
                raise
            store.skip_counted_batch(job_id, batch["batch_id"], generation=generation)
            if len(evidence["records"]) <= 1 or split_depth >= policy["max_split_depth"]:
                evidence["partial"] = True
                evidence["reasons"] = sorted(set(evidence["reasons"]) | {str(exc)})
                break
            evidence["records"] = evidence["records"][:len(evidence["records"]) // 2]
            selected = {value["record_id"] for value in evidence["records"]}
            for group in evidence["groups"]:
                group["evidence_ids"] = [key for key in group["evidence_ids"] if key in selected]
            evidence["groups"] = [group for group in evidence["groups"] if group["evidence_ids"]]
            evidence["relations"] = [value for value in evidence["relations"] if value["left_id"] in selected and value["right_id"] in selected]
            evidence["coverage"]["synthesis_evidence_records"] = len(selected)
            evidence["coverage"].update(ranking_scope="limited", passed_groups=len(evidence["groups"]))
            evidence.update(partial=True, reasons=sorted(set(evidence["reasons"]) | {"synthesis_evidence_limited"}))
            split_depth += 1
            continue
        while True:
            result = request_counted_analysis_payload(store, job_id, batch["batch_id"], generation=generation, payload=prepared["payload"], **transport)
            for _ in iter_analysis_manifest_messages(store, job_id, generation=generation):
                pass
            invalid = ""
            reason = None
            try:
                outcome, invalid = analysis_evidence.response_text(result["response"])
                if outcome != "completed":
                    reason = outcome
                    final = _synthesis_result(job, evidence, _empty_synthesis_document(), reason=reason)
                else:
                    document = analysis_evidence.parse_document(invalid)
                    final = _synthesis_result(job, evidence, document)
            except ValueError as exc:
                diagnostic = str(exc)
                if not diagnostic.startswith("items[") and diagnostic not in {"synthesis_evidence_scope", "qa_answer_missing_support",
                                      "qa_question_without_answer_link", "search_answer_missing_summary"}:
                    diagnostic = "invalid_schema"
                print(f"Analytic synthesis rejected: job={job_id} code={diagnostic}", file=sys.stderr)
                reason = "invalid_schema"
                if store.claim_repair(job_id, batch["batch_id"], generation=generation):
                    payload = {**payload, "input": analysis_settings.render_analysis_prompt(job["request"]["settings"]["analysis_prompts"], "repair",
                        invalid_output=canonical_json({"invalid_output": invalid, "original_task": original_task}), validation_errors=str(exc))}
                    try:
                        prepared = prepare_analysis_payload(store, job_id, batch["batch_id"], generation=generation, payload=payload, **transport)
                    except BudgetExceededError:
                        raise
                    except ValueError as limit:
                        if str(limit) not in {"max_input_tokens", "model_context_limit"}:
                            raise
                        evidence["reasons"] = sorted(set(evidence["reasons"]) | {str(limit)})
                        reason = "incomplete"
                    else:
                        continue
                final = _synthesis_result(job, evidence, _empty_synthesis_document(), reason=reason)
            if reason:
                store.set_batch_outcome(job_id, batch["batch_id"], generation=generation, outcome=reason)
                store.save_analysis_result(job_id, final, generation=generation)
            else:
                for record in evidence["records"]:
                    _verify_relation_sources(store, job, {record["record_id"]: record})
                attempt = next(a for a in store.get_batch(batch["batch_id"])["attempts"] if a["attempt_id"] == result["attempt_id"])
                store.complete_batch(job_id, batch["batch_id"], generation=generation, records=[], inputs=[], json_output=document,
                    attempt_id=result["attempt_id"], usage=attempt["usage"], final_result=final)
            return final
    final = _synthesis_result(job, evidence, _empty_synthesis_document(insufficient=evidence["coverage"]["ranked_evidence_records"] == 0))
    store.save_analysis_result(job_id, final, generation=generation)
    return final


def _resolve_merge_node(store, job_id, reference):
    if reference["kind"] == "evidence":
        record = store.leaf_evidence(job_id, reference["node_id"])
        if record is None or record["batch_id"] != reference["batch_id"]:
            raise ValueError("merge_lineage_scope")
        payload = record["payload"]
    elif reference["kind"] == "merge":
        batch = store.get_batch(reference["batch_id"])
        if batch is None or batch["job_id"] != job_id or batch["kind"] != "merge" or batch["state"] != "succeeded":
            raise ValueError("merge_lineage_scope")
        payload = next((node for node in batch["payload"]["output"]["groups"] if node["node_id"] == reference["node_id"]), None)
        if payload is None:
            raise ValueError("merge_lineage_scope")
    else:
        raise ValueError("merge_lineage_kind")
    if _merge_reference(reference["kind"], reference["batch_id"], reference["node_id"], payload) != reference:
        raise ValueError("merge_lineage_digest")
    return {"node_id": reference["node_id"], "kind": reference["kind"], "payload": payload}


def iter_merge_leaf_records(store, job_id, reference, *, generation):
    job = store.require_running(job_id, generation=generation)
    emitted = set()
    stack = [(reference, ())]
    while stack:
        ref, path = stack.pop()
        store.require_running(job_id, generation=generation)
        if ref["node_id"] in path or len(path) > job["request"]["settings"]["analysis"]["max_merge_depth"]:
            raise ValueError("merge_lineage_cycle_or_depth")
        node = _resolve_merge_node(store, job_id, ref)
        if ref["kind"] == "evidence":
            if ref["node_id"] not in emitted:
                record = store.leaf_evidence(job_id, ref["node_id"])
                _verify_relation_sources(store, job, {ref["node_id"]: record})
                emitted.add(ref["node_id"])
                yield record
        else:
            batch = store.get_batch(ref["batch_id"])
            parents = {value["node_id"]: value for value in batch["payload"]["plan"]["inputs"]}
            for key in reversed(node["payload"]["input_ids"]):
                if key not in parents:
                    raise ValueError("merge_lineage_scope")
                stack.append((parents[key], (*path, ref["node_id"])))


def _validate_merge_document(value, references, *, max_bytes):
    analysis_evidence.validate_shape(value, analysis_evidence.MERGE_SCHEMA)
    if len(canonical_json(value).encode("utf-8")) > max_bytes:
        raise ValueError("merge_output_limit")
    wanted = {ref["node_id"] for ref in references}
    used = set()
    for group in value["groups"]:
        if not group["input_ids"] or len(set(group["input_ids"])) != len(group["input_ids"]) or not set(group["input_ids"]) <= wanted:
            raise ValueError("merge_lineage_scope")
        used.update(group["input_ids"])
    dispositions = {item["input_id"]: item["disposition"] for item in value["dispositions"]}
    if set(dispositions) != wanted or len(dispositions) != len(value["dispositions"]):
        raise ValueError("merge_missing_disposition")
    if any((key in used) != (status in {"used", "merged", "duplicate"}) for key, status in dispositions.items()):
        raise ValueError("merge_disposition_lineage")
    return value


def _save_merge_state(store, job, state):
    current = store.require_running(job["job_id"], generation=job["generation"])
    store.save_checkpoint(job["job_id"], {**current["checkpoint"], "merge": state}, generation=job["generation"],
        max_bytes=job["request"]["settings"]["analysis"]["max_intermediate_bytes"])


def _advance_merge_frontier(state, references, produced, *, terminal):
    advanced = copy.deepcopy(state)
    advanced["cursor"] += len(references)
    advanced["next_frontier"].extend(produced)
    if advanced["cursor"] == len(state["frontier"]):
        advanced.update(frontier=advanced["next_frontier"], next_frontier=[], cursor=0, depth=state["depth"] + 1)
        if terminal or not advanced["frontier"]:
            advanced["state"] = "ready"
            advanced["group_order"] = "global"
        elif len(advanced["frontier"]) >= len(state["frontier"]):
            advanced.update(state="ready", group_order="unranked",
                reduction_outcome="no_reduction" if len(advanced["frontier"]) == len(state["frontier"]) else "expanded")
        else:
            advanced.update(group_order="unranked", reduction_outcome="reduced")
    return advanced


def _stop_merge_state(state, reason):
    return {**state, "state": "partial", "partial": True, "reasons": sorted(set(state["reasons"]) | {reason}),
            "frontier": state["next_frontier"] + state["frontier"][state["cursor"]:], "cursor": 0, "next_frontier": []}


def _merge_group(store, job_id, state, references, *, generation, **transport):
    job = store.require_running(job_id, generation=generation)
    settings, policy = job["request"]["settings"], job["request"]["settings"]["analysis"]
    operation = "rank" if state["cursor"] == 0 and len(references) == len(state["frontier"]) else "merge"
    plan = {"inputs": references, "depth": state["depth"] + 1, "operation": operation}
    digest = hashlib.sha256(canonical_json(plan).encode("utf-8")).hexdigest()
    batch = store.plan_batch(job_id, generation=generation, step_key=f"merge/{digest}", kind="merge", payload=plan, input_digest=digest)
    if batch["state"] == "failed":
        return None, batch["payload"]["outcome"]
    nodes = [_resolve_merge_node(store, job_id, ref) for ref in references]
    leaf_roots = {}
    for ref in references:
        for record in iter_merge_leaf_records(store, job_id, ref, generation=generation):
            leaf_roots.setdefault(record["record_id"], []).append(ref["node_id"])
    relations = []
    for decision in store.iter_link_decisions(job_id):
        for left in leaf_roots.get(decision["left_id"], []):
            for right in leaf_roots.get(decision["right_id"], []):
                relations.append({**decision, "left_id": left, "right_id": right,
                                  "leaf_ids": [decision["left_id"], decision["right_id"]]})
    bundle = settings["analysis_prompts"]
    payload = {"model": settings["processing"]["model"], "instructions": bundle["system_instructions"] + "\n" + bundle["shared_prefix"],
        "input": analysis_settings.render_analysis_prompt(bundle, "merge", command_rubric=bundle["commands"][job["command"]],
            query=_analysis_query(job), records=canonical_json({"operation": operation, "inputs": nodes, "relations": relations})),
        "reasoning": analysis_settings._analysis_reasoning(settings),
        "text": {"format": analysis_evidence.merge_format()}, "max_output_tokens": analysis_settings.stage_output_limit(settings, "merge"), "store": False}
    result = None
    if batch["state"] == "succeeded":
        cached = batch["payload"]["output"]
        properties = analysis_evidence.MERGE_SCHEMA["properties"]["groups"]["items"]["properties"]
        value = {**cached, "groups": [{key: group[key] for key in properties} for group in cached["groups"]]}
        _validate_merge_document(value, references, max_bytes=policy["max_intermediate_bytes"])
    while batch["state"] != "succeeded":
        try:
            result = request_counted_analysis_payload(store, job_id, batch["batch_id"], generation=generation, payload=payload, **transport)
        except BudgetExceededError:
            raise
        except ValueError as exc:
            if str(exc) not in {"max_input_tokens", "model_context_limit"}:
                raise
            attempted = store.get_batch(batch["batch_id"])["attempts"]
            if (len(references) > 2 and state.get("input_splits", 0) < policy["max_split_depth"]
                    and not any(a["request_kind"] == "inference" for a in attempted)):
                store.skip_counted_batch(job_id, batch["batch_id"], generation=generation)
                adaptive = {**state, "fan_in": max(2, len(references) // 2), "input_splits": state.get("input_splits", 0) + 1}
                _save_merge_state(store, job, adaptive)
                return adaptive, None
            store.set_batch_outcome(job_id, batch["batch_id"], generation=generation, outcome="incomplete")
            return None, str(exc)
        invalid = ""
        try:
            outcome, invalid = analysis_evidence.response_text(result["response"])
            if outcome != "completed":
                store.set_batch_outcome(job_id, batch["batch_id"], generation=generation, outcome=outcome)
                return None, outcome
            value = _validate_merge_document(analysis_evidence.parse_document(invalid), references, max_bytes=policy["max_intermediate_bytes"])
        except ValueError as exc:
            if store.claim_repair(job_id, batch["batch_id"], generation=generation):
                payload = {**payload, "input": analysis_settings.render_analysis_prompt(bundle, "repair", invalid_output=canonical_json({"invalid_output": invalid, "inputs": nodes}), validation_errors=str(exc))}
                continue
            store.set_batch_outcome(job_id, batch["batch_id"], generation=generation, outcome="invalid_schema")
            return None, "invalid_schema"
        break
    if value["saturated"]:
        store.set_batch_outcome(job_id, batch["batch_id"], generation=generation, outcome="saturated")
        return None, "saturated"
    output = copy.deepcopy(value)
    for index, group in enumerate(output["groups"]):
        group["node_id"] = uuid.uuid5(uuid.NAMESPACE_URL, f"telegram-analysis-merge:{job_id}:{batch['batch_id']}:{index}").hex
    advanced = _advance_merge_frontier(state, references,
        [_merge_reference("merge", batch["batch_id"], group["node_id"], group) for group in output["groups"]], terminal=operation == "rank")
    if any(item["disposition"] == "excluded" for item in value["dispositions"]):
        advanced["partial"] = True
        advanced["reasons"] = sorted(set(advanced["reasons"]) | {"merge_excluded"})
    for ref in references:
        for _ in iter_merge_leaf_records(store, job_id, ref, generation=generation):
            pass
    attempt = next(a for a in store.get_batch(batch["batch_id"])["attempts"] if a["attempt_id"] == result["attempt_id"]) if result else None
    store.complete_merge_batch(job_id, batch["batch_id"], generation=generation, expected_state=state,
        next_state=advanced, output=output, attempt_id=result["attempt_id"] if result else None, usage=attempt["usage"] if attempt else None)
    return advanced, None


def merge_analysis_records(store, job_id, *, generation, **transport):
    try:
        return _merge_analysis_records(store, job_id, generation=generation, **transport)
    except BudgetExceededError as exc:
        store.record_budget_stop(job_id, generation=generation, reason=exc.reason)
        current = store.require_running(job_id, generation=generation)
        state = current["checkpoint"].get("merge", {"frontier": [], "next_frontier": [], "cursor": 0,
            "depth": 0, "reasons": [], "group_order": "unranked"})
        state = _stop_merge_state(state, exc.reason)
        _save_merge_state(store, current, state)
        return state


def _direct_synthesis_route(store, job, state, *, generation, **transport):
    candidate = {**state, 'state': 'ready', 'route': 'direct', 'group_order': 'unranked',
                 'reduction_outcome': 'not_required'}
    view = {**job, 'checkpoint': {**job['checkpoint'], 'merge': candidate}}
    evidence = _synthesis_evidence(store, view)
    reason = None
    if evidence['coverage']['ranking_scope'] != 'complete':
        reason = 'synthesis_evidence_limited'
    else:
        payload = build_synthesis_payload(job, evidence)
        if len(canonical_json(payload).encode()) > job['request']['settings']['analysis']['max_intermediate_bytes']:
            reason = 'max_intermediate_bytes'
        else:
            batch = _plan_synthesis_batch(store, job, evidence, payload, generation=generation)
            try:
                prepared = prepare_analysis_payload(store, job['job_id'], batch['batch_id'],
                    generation=generation, payload=payload, **transport)
            except BudgetExceededError:
                raise
            except ValueError as exc:
                if str(exc) not in {'max_input_tokens', 'model_context_limit'}:
                    raise
                store.skip_counted_batch(job['job_id'], batch['batch_id'], generation=generation)
                reason = str(exc)
            else:
                candidate.update(direct_preflight_batch_id=batch['batch_id'],
                                 direct_input_digest=prepared['input_digest'], direct_reason='all_evidence_fits',
                                 direct_settings_sha256=hashlib.sha256(canonical_json(job['request']['settings']).encode()).hexdigest())
                _save_merge_state(store, job, candidate)
                store.finalize_leaf_dispositions(job['job_id'], generation=generation)
                return candidate
    fallback = {**state, 'route': 'hierarchical', 'direct_skip_reason': reason}
    _save_merge_state(store, job, fallback)
    return fallback


def _merge_analysis_records(store, job_id, *, generation, **transport):
    job = store.require_running(job_id, generation=generation)
    policy = job["request"]["settings"]["analysis"]
    state = job["checkpoint"].get("merge")
    if state is None:
        frontier, extracted, excluded = [], 0, 0
        excluded_ids = []
        for row in store.iter_leaf_evidence(job_id, page_size=max(1, job['request']['settings']['sync']['batch_size'])):
            extracted += 1
            if analysis_evidence.record_source_eligible(store, job, row['payload']):
                frontier.append(_merge_reference('evidence', row['batch_id'], row['record_id'], row['payload']))
            else:
                excluded += 1
                excluded_ids.append(row['record_id'])
        reasons = store.incomplete_evidence_reasons(job_id)
        if job["checkpoint"].get("relations", {}).get("partial") and "relations_incomplete" not in reasons:
            reasons.append("relations_incomplete")
        state = {"state": "running" if frontier else "ready", "frontier": frontier, "next_frontier": [],
                 "cursor": 0, "depth": 0, "partial": bool(reasons), "reasons": reasons,
                 "algorithm_version": job["request"]["settings"]["algorithm_version"], "group_order": "unranked"}
        state.update(source_excluded_evidence_records=excluded, extracted_evidence_records=extracted)
        state['source_excluded_evidence_ids'] = sorted(excluded_ids)
        _save_merge_state(store, job, state)
    if (len(state['frontier']) > 1 and state['state'] == 'running'
            and state['depth'] == 0 and state['cursor'] == 0 and not state['next_frontier']
            and 'route' not in state):
        state = _direct_synthesis_route(store, job, state, generation=generation, **transport)
    for ref in state["frontier"] + state["next_frontier"]:
        for _ in iter_merge_leaf_records(store, job_id, ref, generation=generation):
            pass
    while state["state"] == "running":
        if len(state["frontier"]) == 1 and state["cursor"] == 0 and not state["next_frontier"]:
            state = {**state, "state": "ready", "group_order": "global"}
            _save_merge_state(store, job, state)
            break
        if state["depth"] >= policy["max_merge_depth"]:
            state = _stop_merge_state(state, "max_merge_depth")
            _save_merge_state(store, job, state)
            break
        references = state["frontier"][state["cursor"]:state["cursor"] + state.get("fan_in", policy["merge_fan_in"])]
        if len(references) == 1 and len(state["frontier"]) > 1:
            state = _advance_merge_frontier(state, references, references, terminal=False)
            _save_merge_state(store, job, state)
            continue
        try:
            advanced, reason = _merge_group(store, job_id, state, references, generation=generation, **transport)
        except BudgetExceededError as exc:
            store.record_budget_stop(job_id, generation=generation, reason=exc.reason)
            advanced, reason = None, exc.reason
        if advanced is None:
            state = _stop_merge_state(state, reason)
            _save_merge_state(store, job, state)
            break
        state = advanced
    store.finalize_leaf_dispositions(job_id, generation=generation)
    return state


def _extraction_repair_payload(job, batch, progress, retained_records=None):
    pending = set(progress['unresolved_input_ids'])
    scope = progress['scope']
    retained = retained_records if retained_records is not None else {
        value['record']['record_id']: value['record'] for value in progress['accepted_records'].values()}
    roots = analysis_evidence._validated_retained_dispositions(scope, progress['dispositions'], retained)
    texts = {key: fragment['message']['text'] for key, fragment in zip(scope['inputs'], batch['messages'])}
    useful_roots = {}
    for input_id in sorted(pending):
        for key in sorted(roots):
            decision = roots[key]
            if analysis_evidence._literal_extraction_duplicate(input_id,
                    [retained[identity] for identity in decision['record_ids']], scope, texts, {key: decision}):
                useful_roots[key] = decision
                break
    useful_ids = {identity for decision in useful_roots.values() for identity in decision['record_ids']}
    fragments = [{'input_id': key, **_extraction_fragments([fragment])[0]}
                 for key, fragment in zip(scope['inputs'], batch['messages']) if key in pending]
    base = build_extraction_payload(job, batch['messages'], batch['context'])
    original_task = analysis_settings.render_analysis_prompt(
        job['request']['settings']['analysis_prompts'], 'extract',
        command_rubric=job['request']['settings']['analysis_prompts']['commands'][job['command']],
        query=_analysis_query(job), messages='[]', context='[]')
    repair_scope = {**scope, 'model_input_ids': [key for key in scope['model_input_ids'] if key in pending]}
    data = {'original_task': original_task, 'scope_sha256': scope['sha256'],
            'inputs': fragments, 'record_errors': progress['record_errors'],
            'record_diagnostics': progress.get('record_diagnostics', {}),
            'context': _extraction_context(batch['context']),
            'read_only_dispositions': useful_roots,
            'read_only_inputs': [{'input_id': key, **_extraction_fragments([fragment])[0]}
                for key, fragment in zip(scope['inputs'], batch['messages']) if key in useful_roots],
            'rejected_records': progress.get('rejected_records', {}),
            'read_only_records': [
                _extraction_readonly_record(record)
                for record in retained.values()
                if record['record_id'] in useful_ids or any(analysis_evidence._record_uses_fragment(
                    record, scope['inputs'][key]) for key in pending)]}
    data['analysis_period'] = _analysis_period(job)
    base['input'] = analysis_settings.render_analysis_prompt(
        job['request']['settings']['analysis_prompts'], 'repair',
        invalid_output=canonical_json(data), validation_errors='extraction_coverage_incomplete')
    repair_scope.update(context_input_ids=analysis_evidence.extraction_context_ids(batch['context']),
                        allow_retained=bool(data['read_only_records']))
    base['text']['format'] = analysis_evidence.addressed_evidence_format(
        repair_scope, job['request']['settings']['schema_version'])
    return base


def _finish_addressed_extraction(store, job, batch, *, generation, expected, children=None):
    _verify_extraction_batch_sources(store, job, batch, generation=generation)
    return store.finish_extraction_progress(job['job_id'], batch['batch_id'], generation=generation,
                                             expected=expected, children=children)


def _verify_extraction_batch_sources(store, job, batch, *, generation):
    analysis_evidence.validate_evidence_records(store, job['job_id'], batch['batch_id'], generation=generation,
        document={'records': [], 'saturated': False}, fragments=batch['messages'] + batch['context'])
    _verify_relation_sources(store, job, store.extraction_ancestor_records(job['job_id'], batch['batch_id']))


def _extract_addressed_batch(store, job, batch, *, generation, **transport):
    job_id, batch_id = job['job_id'], batch['batch_id']
    scope = analysis_evidence.extraction_scope(batch['messages'])
    current = store.get_batch(batch_id)
    ancestors = store.extraction_ancestor_records(job_id, batch_id)
    _verify_relation_sources(store, job, ancestors)
    if current['state'] == 'succeeded':
        return {'outcome': current['payload'].get('outcome', 'succeeded'),
                'partial': current['payload'].get('outcome') == 'partial', 'batch_id': batch_id}
    analysis_evidence.validate_evidence_records(store, job_id, batch_id, generation=generation,
        document={'records': [], 'saturated': False}, fragments=batch['messages'] + batch['context'])
    while True:
        previous = store.get_batch(batch_id)['payload'].get('extraction_progress')
        progress = store.begin_extraction_request(job_id, batch_id, generation=generation,
                                                   expected=previous, scope=scope)
        if progress is None:
            progress = previous
            break
        marker = progress['active_request']
        if marker is None:
            break
        attempts = store.get_batch(batch_id)['attempts'][marker['attempt_offset']:]
        if any(a['request_kind'] == 'inference' for a in attempts):
            # An observation did not survive its physical request; never replay it.
            break
        _verify_extraction_batch_sources(store, job, batch, generation=generation)
        retained = {**{key: ancestors[key] for key in progress.get('inherited_record_ids', [])},
                    **{value['record']['record_id']: value['record'] for value in progress['accepted_records'].values()}}
        payload = (_extraction_repair_payload(job, batch, progress, retained) if marker['repair_index']
                   else build_extraction_payload(job, batch['messages'], batch['context']))
        relevant_ancestors = {key: record for key, record in ancestors.items()
            if any(analysis_evidence._record_uses_fragment(record, scope['inputs'][input_id]) for input_id in marker['inputs'])}
        if relevant_ancestors:
            payload['input'] = 'read_only_records: ' + canonical_json([
                _extraction_readonly_record(record)
                for key, record in relevant_ancestors.items()]) + '\n' + payload['input']
        try:
            result = request_counted_analysis_payload(store, job_id, batch_id,
                generation=generation, payload=payload, **transport)
        except (BudgetExceededError, openai_api.OpenAIRequestError, TimeoutError):
            _finish_addressed_extraction(store, job, batch, generation=generation, expected=progress)
            raise
        except ValueError as exc:
            if str(exc) not in {'max_input_tokens', 'model_context_limit'}:
                raise
            return _split_extraction_output(store, job, batch, generation=generation,
                                            progress=progress, **transport)
        try:
            outcome, text = analysis_evidence.response_text(result['response'])
            if outcome != 'completed':
                if (outcome == 'incomplete' and result['response'].get('incomplete_details', {}).get('reason')
                        == 'max_output_tokens'):
                    return _split_extraction_output(store, job, batch, generation=generation,
                                                    progress=progress, **transport)
                _finish_addressed_extraction(store, job, batch, generation=generation, expected=progress)
                return {'outcome': outcome, 'partial': True, 'batch_id': batch_id}
            analysis_evidence.validate_evidence_records(store, job_id, batch_id, generation=generation,
                document={'records': [], 'saturated': False}, fragments=batch['messages'] + batch['context'])
            classified = analysis_evidence.classify_extraction_document(
                store, job_id, batch_id, generation=generation,
                document=analysis_evidence.parse_document(text),
                fragments=[fragment for key, fragment in zip(scope['inputs'], batch['messages'])
                           if key in marker['inputs']] + batch['context'], selected=batch['messages'],
                retained_records={**relevant_ancestors, **retained}, retained_dispositions=progress['dispositions'], context=batch['context'])
            # A repair cannot introduce records or decisions outside its residual inputs.
            for record in classified['records']:
                if not any(analysis_evidence._record_uses_fragment(record, scope['inputs'][key])
                           for key in marker['inputs']):
                    raise ValueError('extraction_repair_scope')
            exact = {canonical_json(record['payload']): key for key, record in relevant_ancestors.items()}
            exact.update({canonical_json(value['record']['payload']): value['record']['record_id']
                          for value in progress['accepted_records'].values()})
            remap = {record['record_id']: exact.get(canonical_json(record['payload']), record['record_id'])
                     for record in classified['records']}
            classified['record_slots'] = {r['record_id']: r for r in classified['records'] if remap[r['record_id']] == r['record_id']}
            classified['inherited_record_ids'] = sorted(relevant_ancestors)
            for decision in classified['dispositions'].values():
                decision['record_ids'] = list(dict.fromkeys(remap.get(key, key) for key in decision['record_ids']))
            classified['dispositions'] = {key: value for key, value in classified['dispositions'].items()
                if key in marker['inputs'] or key in scope['empty_input_ids']}
        except ValueError as exc:
            if str(exc) == 'input_changed':
                raise
            classified = {'scope': scope, 'record_slots': {}, 'dispositions': {},
                'record_errors': {'envelope': str(exc)}, 'reasons': [str(exc)]}
        try:
            progress = store.save_extraction_progress(job_id, batch_id, generation=generation,
                expected=progress, classified=classified, attempt_id=result['attempt_id'])
        except BudgetExceededError:
            _finish_addressed_extraction(store, job, batch, generation=generation, expected=progress)
            raise
        if transport.get('monotonic_func', time.monotonic)() >= transport['deadline']:
            _finish_addressed_extraction(store, job, batch, generation=generation, expected=progress)
            raise TimeoutError('Analysis extraction deadline exceeded')
        if progress.get('saturated'):
            return _split_extraction_output(store, job, batch, generation=generation,
                                            progress=progress, **transport)
        if not progress['unresolved_input_ids'] and not progress['record_errors']:
            break
    analysis_evidence.validate_evidence_records(store, job_id, batch_id, generation=generation,
        document={'records': [], 'saturated': False}, fragments=batch['messages'] + batch['context'])
    closed = _finish_addressed_extraction(store, job, batch, generation=generation, expected=progress)
    return {'outcome': closed['payload']['outcome'],
            'partial': closed['payload']['outcome'] == 'partial', 'batch_id': batch_id}


def extract_analysis_batch(store, job_id, batch, *, generation, **transport):
    try:
        return _extract_analysis_batch(store, job_id, batch, generation=generation, **transport)
    except BudgetExceededError as exc:
        store.record_budget_stop(job_id, generation=generation, reason=exc.reason)
        return {"outcome": "budget_exhausted", "partial": True, "batch_id": batch["batch_id"]}


def _restore_extraction_batch(store, job, batch_id):
    current = store.get_batch(batch_id)
    if current is None or current["job_id"] != job["job_id"] or current["kind"] != "extract":
        raise ValueError("Split child outside job scope")
    restored = {"batch_id": batch_id}
    sources = {}
    for name, key in (("messages", "inputs"), ("context", "context")):
        fragments = []
        for ref in current["payload"]["plan"][key]:
            pair = (ref["channel_id"], ref["message_id"])
            if pair not in sources:
                sources[pair] = analysis_evidence._verified_source_snapshot(store, job, ref)
            message = sources[pair]
            text = analysis_evidence.analysis_text_view(message)[ref["fragment_start"]:ref["fragment_end"]]
            fields = {key: value for key, value in message.items() if key not in {"text", "media"}}
            fragments.append({"message": {**fields, "text": text, "text_view_version": 1}, "input": ref})
        restored[name] = fragments
    return restored


def _continue_extraction_split(store, job, parent, *, generation, **transport):
    store.extraction_children_pending(job['job_id'], parent['batch_id'])  # Validate the active lineage before dispatch.
    results = [extract_analysis_batch(store, job["job_id"], _restore_extraction_batch(store, job, child),
               generation=generation, **transport) for child in parent["payload"]["split_children"]]
    return {"outcome": "split", "partial": any(result["partial"] for result in results), "batch_id": parent["batch_id"]}


def _split_extraction_output(store, job, batch, *, generation, progress=None, **transport):
    current = store.get_batch(batch["batch_id"])
    depth = current["payload"]["plan"].get("split_depth", 0)
    messages = batch["messages"]
    if progress is not None:
        pending = set(progress['unresolved_input_ids'])
        messages = [fragment for key, fragment in zip(progress['scope']['inputs'], messages)
                    if not pending or key in pending]
    if depth >= job["request"]["settings"]["analysis"]["max_split_depth"] or not messages or (len(messages) == 1 and len(messages[0]["message"]["text"]) <= 1):
        if progress is None:
            store.set_batch_outcome(job["job_id"], batch["batch_id"], generation=generation, outcome="max_split_depth")
        else:
            _finish_addressed_extraction(store, job, batch, generation=generation, expected=progress)
        return {"outcome": "max_split_depth", "partial": True, "batch_id": batch["batch_id"]}
    if len(messages) > 1:
        middle = len(messages) // 2
        parts = [messages[:middle], messages[middle:]]
    else:
        item = messages[0]
        text = item["message"]["text"]
        middle, offset = len(text) // 2, item["input"]["fragment_start"]
        parts = [[{"message": {**item["message"], "text": text[start:end]},
                   "input": {**item["input"], "fragment_start": offset + start, "fragment_end": offset + end}}]
                 for start, end in ((0, middle), (middle, len(text)))]
    children = [{"plan": {"inputs": [f["input"] for f in part], "context": [f["input"] for f in batch["context"]], "split_depth": depth + 1},
                 "cache_key": extraction_cache_key(job, {"messages": part, "context": batch["context"]}) if job["request"]["settings"]["analysis"]["cache"]["enabled"] else None,
                 "input_digest": hashlib.sha256(canonical_json(build_extraction_payload(job, part, batch["context"])).encode("utf-8")).hexdigest()}
                for part in parts]
    if progress is None:
        store.split_extraction_batch(job["job_id"], batch["batch_id"], generation=generation, children=children)
    else:
        analysis_evidence.validate_evidence_records(store, job['job_id'], batch['batch_id'], generation=generation,
            document={'records': [], 'saturated': False}, fragments=batch['messages'] + batch['context'])
        try:
            _finish_addressed_extraction(store, job, batch, generation=generation, expected=progress, children=children)
        except BudgetExceededError:
            _finish_addressed_extraction(store, job, batch, generation=generation, expected=progress)
            raise
    return _continue_extraction_split(store, job, store.get_batch(batch["batch_id"]), generation=generation, **transport)


def _extract_analysis_batch(store, job_id, batch, *, generation, **transport):
    job = store.require_running(job_id, generation=generation)
    batch_id = batch["batch_id"]
    current = store.get_batch(batch_id)
    if current is None or current["job_id"] != job_id or current["kind"] != "extract":
        raise ValueError("Extraction step outside job scope")
    if current["payload"].get("split_children"):
        return _continue_extraction_split(store, job, current, generation=generation, **transport)
    if current["state"] == "succeeded":
        return {"outcome": current['payload'].get('outcome', 'succeeded'),
                "partial": current['payload'].get('outcome') == 'partial', "batch_id": batch_id}
    if current["state"] == "failed":
        return {"outcome": current["payload"]["outcome"], "partial": True, "batch_id": batch_id}
    if try_reuse_extraction(store, job_id, batch, generation=generation, now=datetime.now(timezone.utc)):
        return {'outcome': 'succeeded', 'partial': False, 'batch_id': batch_id}
    return _extract_addressed_batch(store, job, batch, generation=generation, **transport)





def iter_analysis_batches(store, job_id, *, generation, build_payload, capabilities, **transport):
    try:
        yield from _iter_analysis_batches(store, job_id, generation=generation, build_payload=build_payload,
                                         capabilities=capabilities, **transport)
    except BudgetExceededError as exc:
        store.record_budget_stop(job_id, generation=generation, reason=exc.reason)


def _iter_analysis_batches(store, job_id, *, generation, build_payload, capabilities, **transport):
    """Stream the fixed manifest; recursively recount splits and bounded context."""
    job = store.require_running(job_id, generation=generation)
    settings = job["request"]["settings"]
    policy, ai = settings["analysis"], analysis_settings.analysis_ai(settings)
    maximum, guard = ai["messages_per_ai_pass"], ai["message_block_max_chars"]
    maximum = min(maximum, analysis_evidence.extraction_input_enum_limit())
    overlap = min(policy["overlap_messages"], maximum - 1)
    previous, stopped, seen = [], False, set()

    def counted(selected, context, key, depth=0):
        data = build_payload(selected, context)
        plan = {"inputs": [item["input"] for item in selected], "context": [item["input"] for item in context], "split_depth": depth}
        digest = hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()
        existing = store.find_extraction_plan(job_id, input_digest=digest, plan=plan)
        if existing is None and store.get_batch_by_step(job_id, key) is not None:
            key += f"/input/{digest}"
        cache_key = (existing["cache_key"] if existing else extraction_cache_key(job, {"messages": selected, "context": context})
                     if policy["cache"]["enabled"] else None)
        batch = existing or store.plan_batch(job_id, generation=generation, step_key=key, kind="extract",
            payload=plan, input_digest=digest, cache_key=cache_key)
        seen.add(batch["batch_id"])
        scope = analysis_evidence.extraction_scope(selected)
        if batch['state'] in {'planned', 'running'}:
            store.initialize_extraction_scope(job_id, batch['batch_id'], generation=generation, scope=scope)
        ready = ({'local_only': True} if not scope['model_input_ids'] else
                 prepare_analysis_payload(store, job_id, batch['batch_id'], generation=generation,
                                          payload=data, capabilities=capabilities, **transport))
        return {**ready, "batch_id": batch["batch_id"], "messages": selected, "context": context}

    try:
        minimal = counted([], [], "extract/preflight")
    except ValueError as exc:
        if str(exc) in {"max_input_tokens", "model_context_limit"}:
            raise ValueError("minimal_payload_does_not_fit") from exc
        raise
    store.skip_counted_batch(job_id, minimal["batch_id"], generation=generation)

    def emit(selected, key, depth=0, *, use_context=True):
        nonlocal previous, stopped
        if stopped:
            return
        optional = [{"message": item["message"], "input": {**item["input"], "role": "context"}}
                    for item in previous[-overlap:]] if use_context and overlap else []
        if optional and optional[-1]['input']['channel_id'] != selected[0]['input']['channel_id']:
            optional = []
        if sum(len(item['message']['text']) for item in selected + optional) > guard:
            optional = []
        context = optional
        try:
            ready = counted(selected, context, key, depth)
        except ValueError as exc:
            if str(exc) not in {"max_input_tokens", "model_context_limit", "model_schema_limit", "extraction_input_enum_limit"}:
                raise
            batch = store.get_batch_by_step(job_id, key)
            if batch is not None:
                store.skip_counted_batch(job_id, batch["batch_id"], generation=generation)
            if optional:
                yield from emit(selected, key + "/without-context", depth, use_context=False)
            elif depth < policy["max_split_depth"] and len(selected) > 1:
                middle = len(selected) // 2
                yield from emit(selected[:middle], key + "/0", depth + 1)
                yield from emit(selected[middle:], key + "/1", depth + 1)
            elif depth < policy["max_split_depth"] and len(selected[0]["message"]["text"]) > 1:
                item = selected[0]
                middle = len(item["message"]["text"]) // 2
                offset = item["input"]["fragment_start"]
                for index, (start, end) in enumerate(((0, middle), (middle, len(item["message"]["text"])))):
                    fragment = {**item, "message": {**item["message"], "text": item["message"]["text"][start:end]},
                                "input": {**item["input"], "fragment_start": offset + start, "fragment_end": offset + end}}
                    yield from emit([fragment], key + f"/{index}", depth + 1)
            else:
                stopped = True
                store.record_budget_stop(job_id, generation=generation, reason="max_split_depth")
            return
        previous = selected[-overlap:] if overlap else []
        current = store.get_batch(ready['batch_id'])
        if current['state'] != 'succeeded' or (current['payload'].get('split_children')
                and store.extraction_children_pending(job_id, current['batch_id'])):
            yield ready

    pending, size, page = [], 0, 0
    for source in iter_analysis_manifest_messages(store, job_id, generation=generation):
        if stopped:
            return
        text = analysis_evidence.analysis_text_view(source['message'])
        fields = {key:value for key,value in source['message'].items() if key not in {'text','media'}}
        fragments = ({'message': {**fields, 'text': text[start:min(start+guard, len(text))], 'text_view_version': 1},
            'input': {**source['input'], 'fragment_start': start, 'fragment_end': min(start+guard, len(text))}}
            for start in range(0, max(1, len(text)), guard))
        for fragment in fragments:
            store.require_running(job_id, generation=generation)
            cost = len(fragment['message']['text'])
            if pending and (len(pending) >= maximum - min(overlap, len(previous)) or size + cost > guard
                            or pending[0]['input']['channel_id'] != fragment['input']['channel_id']):
                yield from emit(pending, f'extract/{page:08d}')
                if stopped:
                    return
                page += 1
                pending, size = [], 0
                cost = len(fragment['message']['text'])
            pending.append(fragment)
            size += cost
    if pending and not stopped:
        yield from emit(pending, f"extract/{page:08d}")
    if not stopped:
        store.reconcile_extraction_batches(job_id, generation=generation, seen=seen)


def compile_search_fts(terms):
    # FTS strings use doubled embedded quotes; model text never supplies grammar.
    return " OR ".join('"' + term.replace("\x00", " ").replace('"', '""') + '"'
        for term in terms if re.search(r"[^\W_]", term))


def _search_candidate_refs(store, job, scope, prepared):
    request, settings = job["request"]["request"], job["request"]["settings"]
    cap = min(settings["analysis"]["search"]["max_candidates"], settings["analysis"]["max_messages"])
    original, expanded = (compile_search_fts(prepared[key]) for key in ("original_terms", "expanded_terms"))
    expressions = [original, f"({expanded}) NOT ({original})" if expanded and original else expanded]
    refs, limited = [], False
    for expression in expressions:
        remaining = cap - len(refs)
        rows = store.search_candidates(expression, since=request["since"], until=min(request["until"], request["cutoff_at"]),
            ceilings=scope["message_ceilings"], limit=remaining + 1)
        refs.extend([[row["channel_id"], row["message_id"]] for row in rows[:remaining]])
        limited |= len(rows) > remaining
        if limited:
            break
    return refs, limited


def _search_context_refs(store, job, scope, candidate):
    settings, request = job["request"]["settings"], job["request"]["request"]
    cap = settings["analysis"]["search"]["context_messages"]
    if not cap:
        return [], False
    peer, identity = candidate
    maximum = settings["analysis"]["max_intermediate_bytes"]
    anchor = store.analysis_source(peer, identity, max_bytes=maximum)
    if anchor is None:
        return [], False
    upper, ceiling = min(request["until"], request["cutoff_at"]), scope["message_ceilings"][str(peer)]
    found, seen, source = [], {identity}, anchor
    while len(found) <= cap:
        parent = source["reply_to_message_id"]
        if parent is None or parent in seen or parent > ceiling:
            break
        source = store.analysis_source(peer, parent, max_bytes=maximum)
        if source is None or not request["since"] <= source["date_utc"] <= upper:
            break
        found.append([peer, parent])
        seen.add(parent)
    neighbors = [row for before in (True, False) for row in store.search_neighbors(peer,
        at=(anchor["date_utc"], identity), since=request["since"], until=upper,
        ceiling=ceiling, limit=cap + 1, before=before)]
    stamp = datetime.fromisoformat(anchor["date_utc"])
    neighbors.sort(key=lambda row: (abs((datetime.fromisoformat(row["date_utc"]) - stamp).total_seconds()),
                                    abs(row["message_id"] - identity), row["message_id"]))
    for row in neighbors:
        if row["message_id"] not in seen:
            found.append([peer, row["message_id"]])
            seen.add(row["message_id"])
    return found[:cap], len(found) > cap


def build_search_manifest(store, job_id, *, generation, **transport):
    job = store.require_running(job_id, generation=generation)
    settings, request = job["request"]["settings"], job["request"]["request"]
    policy = settings["analysis"]
    manifest = job["checkpoint"].get("manifest")
    if manifest is None:
        history_client.require_fts_ready(store.conn)
        scope = _analysis_manifest_scope(store, job)
        prepared = {"original_terms": [], "expanded_terms": [], "expansion_status": "not_requested", "reasons": []}
        if scope["stored_in_window"]:
            prepared = prepare_search_query(store, job_id, generation=generation, **transport)
        refs, limited = _search_candidate_refs(store, job, scope, prepared)
        history_client.require_fts_ready(store.conn)
        manifest = {**scope, "selection_mode": "candidates", "candidate_refs": refs,
            "candidate_count": len(refs), "selected_candidates": 0, "context_messages": 0,
            "candidate_cursor": 0, "context_cursor": 0, "pending_context": [],
            "query_expansion_status": prepared["expansion_status"]}
        manifest["reasons"].extend(prepared["reasons"])
        if limited:
            manifest["reasons"].append("max_messages" if policy["max_messages"] < policy["search"]["max_candidates"] else "max_candidates")
        manifest["partial"] |= bool(manifest["reasons"])
        while True:
            try:
                current = store.require_running(job_id, generation=generation)
                store.save_checkpoint(job_id, {**current["checkpoint"], "manifest": manifest},
                    generation=generation, max_bytes=policy["max_intermediate_bytes"])
                break
            except BudgetExceededError as exc:
                if exc.reason != "max_intermediate_bytes" or not manifest["candidate_refs"]:
                    raise
                manifest["candidate_refs"] = manifest["candidate_refs"][:len(manifest["candidate_refs"]) // 2]
                manifest["candidate_count"] = len(manifest["candidate_refs"])
                manifest.update(partial=True, reasons=list(dict.fromkeys([*manifest["reasons"], "candidate_metadata_limited"])))
    if manifest.get("input_changed"):
        manifest = _repair_analysis_manifest(store, job_id, generation=generation)
        manifest["selected_candidates"] = sum(store.manifest_reference(job_id, *ref) is not None for ref in manifest["candidate_refs"])
        manifest["context_messages"] = manifest["selected"] - manifest["selected_candidates"]
        current = store.require_running(job_id, generation=generation)
        store.save_checkpoint(job_id, {**current["checkpoint"], "manifest": manifest}, generation=generation,
            max_bytes=policy["max_intermediate_bytes"])
    flush, upper = max(1, settings["sync"]["batch_size"]), min(request["until"], request["cutoff_at"])
    while manifest["state"] != "complete":
        store.require_running(job_id, generation=generation)
        next_state, inputs, pending = copy.deepcopy(manifest), [], set()
        try:
            while len(inputs) < flush and next_state["state"] != "complete":
                store.require_running(job_id, generation=generation)
                candidate = next_state["candidate_cursor"] < len(next_state["candidate_refs"])
                if candidate:
                    ref = next_state["candidate_refs"][next_state["candidate_cursor"]]
                    next_state["candidate_cursor"] += 1
                else:
                    if not next_state["pending_context"]:
                        if next_state["context_cursor"] == len(next_state["candidate_refs"]):
                            next_state["state"] = "complete"
                            break
                        context, limited = _search_context_refs(store, job, manifest,
                            next_state["candidate_refs"][next_state["context_cursor"]])
                        next_state["context_cursor"] += 1
                        next_state["pending_context"] = context
                        if limited:
                            next_state.update(partial=True, reasons=list(dict.fromkeys([*next_state["reasons"], "context_limited"])))
                        if not context:
                            continue
                    ref = next_state["pending_context"].pop(0)
                pair = tuple(ref)
                if pair in pending or store.manifest_reference(job_id, *pair) is not None:
                    continue
                if next_state["selected"] >= policy["max_messages"]:
                    next_state.update(state="complete", partial=True, reasons=list(dict.fromkeys([*next_state["reasons"], "max_messages"])))
                    break
                value, digest = analysis_evidence._analysis_message_snapshot(store, *pair, policy["max_intermediate_bytes"])
                if value is None or not request["since"] <= value["date_utc"] <= upper:
                    next_state.update(partial=True, reasons=list(dict.fromkeys([*next_state["reasons"], "input_removed"])))
                    continue
                inputs.append({"channel_id": pair[0], "message_id": pair[1], "date_utc": value["date_utc"], "content_digest": digest})
                pending.add(pair)
                next_state["selected"] += 1
                next_state["selected_candidates" if candidate else "context_messages"] += 1
        except ValueError as exc:
            if str(exc) not in {"manifest_byte_cap", "max_intermediate_bytes"}:
                raise
            return store.stop_manifest_at_byte_cap(job_id, generation=generation)
        next_state["page"] += 1
        next_state["known_skipped"] = max(0, next_state["stored_in_window"] - next_state["selected"])
        if next_state["state"] == "complete" and next_state["known_skipped"] == 0:
            next_state["reasons"] = [reason for reason in next_state["reasons"] if reason != "context_limited"]
            next_state["partial"] = bool(next_state["reasons"])
        try:
            store.commit_manifest_page(job_id, generation=generation, expected_page=manifest["page"], manifest=next_state, inputs=inputs)
        except ValueError as exc:
            if str(exc) not in {"manifest_byte_cap", "max_intermediate_bytes"}:
                raise
            return store.stop_manifest_at_byte_cap(job_id, generation=generation)
        manifest = next_state
    return manifest


def _analysis_manifest_scope(store, job):
    history = job["checkpoint"].get("history")
    if history is None:
        raise ValueError("History must be prepared before selection")
    request = job["request"]["request"]
    channels, incomplete = [], bool(history.get("failed_refs"))
    for reference in request["channels"]:
        peer = history.get("refs", {}).get(reference)
        source = history.get("sources", {}).get(str(peer))
        if source is None or source.get("state") == "failed":
            incomplete = True
            continue
        if source.get("state") not in {"complete", "partial"}:
            raise ValueError("History selection cannot start during synchronization")
        incomplete = incomplete or source["state"] != "complete" or bool(source.get("pending_windows"))
        if peer not in channels:
            channels.append(peer)
    stats = {str(peer): store.conn.execute("""SELECT COUNT(*),COALESCE(MAX(message_id),0) FROM messages
        WHERE channel_id=? AND date_utc>=? AND date_utc<=?""",
        (peer, request["since"], min(request["until"], request["cutoff_at"]))).fetchone() for peer in channels}
    reasons = (["history_partial"] if incomplete else []) + (["ocr_partial"] if job["checkpoint"].get("ocr", {}).get("partial") else [])
    return {"state": "building", "channels": channels, "message_ceilings": {peer: row[1] for peer, row in stats.items()},
        "stored_in_window": sum(row[0] for row in stats.values()), "selected": 0, "known_skipped": 0,
        "partial": bool(reasons), "reasons": reasons, "page": 0}


def build_all_messages_manifest(store: AnalysisStore, job_id: str, *, generation: int):
    """Freeze paged IDs/digests before extraction; retain only one input page in RAM."""
    job = store.require_running(job_id, generation=generation)
    settings, request = job["request"]["settings"], job["request"]["request"]
    policy = settings["analysis"]
    manifest = job["checkpoint"].get("manifest")
    if manifest is None:
        manifest = {**_analysis_manifest_scope(store, job), "selection_mode": "all_messages", "source_index": 0, "after": None}
        checkpoint = {**job["checkpoint"], "manifest": manifest}
        store.save_checkpoint(job_id, checkpoint, generation=generation, max_bytes=policy["max_intermediate_bytes"])
    if manifest.get("input_changed"):
        manifest = _repair_analysis_manifest(store, job_id, generation=generation)
    if manifest["state"] == "complete":
        return manifest
    upper = min(request["until"], request["cutoff_at"])
    flush = max(1, settings["sync"]["batch_size"])
    while manifest["state"] != "complete":
        store.require_running(job_id, generation=generation)
        next_state = copy.deepcopy(manifest)
        inputs = []
        if manifest["source_index"] >= len(manifest["channels"]):
            next_state["state"] = "complete"
        else:
            peer = manifest["channels"][manifest["source_index"]]
            remaining = policy["max_messages"] - manifest["selected"]
            rows = store.history_page(peer, since=request["since"], until=upper, after=manifest["after"],
                                      limit=min(flush, remaining + 1), max_message_id=manifest["message_ceilings"][str(peer)])
            for row in rows[:remaining]:
                try:
                    payload, digest = analysis_evidence._analysis_message_snapshot(store, peer, row["message_id"], policy["max_intermediate_bytes"])
                except ValueError as exc:
                    if str(exc) not in {"manifest_byte_cap", "max_intermediate_bytes"}:
                        raise
                    return store.stop_manifest_at_byte_cap(job_id, generation=generation)
                if payload is None or payload["date_utc"] != row["date_utc"]:
                    store.interrupt_changed_input(job_id, generation=generation,
                                                   source={"channel_id": peer, "message_id": row["message_id"]})
                    raise ValueError("input_changed")
                inputs.append({"channel_id": peer, "message_id": row["message_id"],
                               "date_utc": row["date_utc"], "content_digest": digest})
            next_state["selected"] += len(inputs)
            if inputs:
                last = inputs[-1]
                next_state["after"] = [last["date_utc"], last["message_id"]]
            if len(rows) > remaining:
                next_state.update(state="complete", partial=True,
                                  known_skipped=max(0, manifest["stored_in_window"] - next_state["selected"]))
                next_state["reasons"].append("max_messages")
            elif len(rows) < min(flush, remaining + 1):
                next_state.update(source_index=manifest["source_index"] + 1, after=None)
                if next_state["source_index"] == len(manifest["channels"]):
                    next_state["state"] = "complete"
        next_state["page"] += 1
        if next_state["state"] == "complete" and next_state["selected"] < next_state["stored_in_window"]:
            next_state["known_skipped"] = next_state["stored_in_window"] - next_state["selected"]
            next_state["partial"] = True
            if "max_messages" not in next_state["reasons"] and "input_removed" not in next_state["reasons"]:
                next_state["reasons"].append("input_removed")
        try:
            store.commit_manifest_page(job_id, generation=generation, expected_page=manifest["page"],
                                       manifest=next_state, inputs=inputs)
        except ValueError as exc:
            if str(exc) not in {"manifest_byte_cap", "max_intermediate_bytes"}:
                raise
            return store.stop_manifest_at_byte_cap(job_id, generation=generation)
        manifest = next_state
    return manifest


def _repair_analysis_manifest(store, job_id, *, generation):
    job = store.require_running(job_id, generation=generation)
    request, settings = job["request"]["request"], job["request"]["settings"]
    manifest = job["checkpoint"]["manifest"]
    upper = min(request["until"], request["cutoff_at"])
    after = manifest.get("repair_after_step", "")
    while True:
        store.require_running(job_id, generation=generation)
        batch = store.next_manifest_batch(job_id, after=after)
        if batch is None:
            break
        inputs = []
        for source in store.manifest_batch_inputs(batch["batch_id"]):
            payload, digest = analysis_evidence._analysis_message_snapshot(store, source["channel_id"], source["message_id"],
                                                         settings["analysis"]["max_intermediate_bytes"])
            if payload is not None and request["since"] <= payload["date_utc"] <= upper:
                inputs.append({**source, "date_utc": payload["date_utc"], "content_digest": digest})
        inputs.sort(key=lambda item: (item["date_utc"], item["message_id"]))
        store.repair_manifest_batch(job_id, generation=generation, batch=batch, inputs=inputs)
        after = batch["step_key"]
    job = store.require_running(job_id, generation=generation)
    manifest = job["checkpoint"]["manifest"]
    manifest.pop("input_changed", None)
    manifest.pop("repair_after_step", None)
    store.save_checkpoint(job_id, job["checkpoint"], generation=generation,
                          max_bytes=settings["analysis"]["max_intermediate_bytes"])
    return manifest


def iter_analysis_manifest_messages(store: AnalysisStore, job_id: str, *, generation: int):
    job = store.require_running(job_id, generation=generation)
    manifest = job["checkpoint"].get("manifest", {})
    if manifest.get("state") != "complete" or manifest.get("input_changed"):
        raise ValueError("Input manifest is not ready for extraction")
    settings = job["request"]["settings"]
    for peer in manifest["channels"]:
        after = None
        while True:
            store.require_running(job_id, generation=generation)
            refs = store.manifest_inputs_page(job_id, channel_id=peer, after=after,
                                              limit=max(1, settings["sync"]["batch_size"]))
            if not refs:
                break
            for row in refs:
                store.require_running(job_id, generation=generation)
                source = dict(row)
                try:
                    payload, digest = analysis_evidence._analysis_message_snapshot(store, source["channel_id"], source["message_id"],
                                                                 settings["analysis"]["max_intermediate_bytes"])
                except ValueError:
                    payload, digest = None, None
                if digest != source["content_digest"]:
                    store.interrupt_changed_input(job_id, generation=generation, source=source)
                    raise ValueError("input_changed")
                yield {"message": payload, "input": source}
            last = refs[-1]
            after = (last["date_utc"], last["message_id"])
