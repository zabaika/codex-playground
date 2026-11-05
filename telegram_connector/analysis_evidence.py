"""Versioned evidence schemas, source snapshots and authoritative support validation."""

import copy
from datetime import datetime
import hashlib
import json
import re
import unicodedata
import uuid

if __package__:
    from .analysis_store import canonical_json, SOURCE_VERIFICATION_FIELDS
    from . import analysis_settings
else:
    from analysis_store import canonical_json, SOURCE_VERIFICATION_FIELDS
    import analysis_settings

from telegram_shared.formatting import build_message_link
from common import process as common_process

EVIDENCE_SCHEMA_VERSION = 1


def _object(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def _array(items):
    return {"type": "array", "items": items}


_STRING = {"type": "string"}
_STRINGS = _array(_STRING)
_MESSAGE_REF = {"channel_id": {"type": "integer"}, "message_id": {"type": "integer"}}
EVIDENCE_SCHEMA = _object({
    "records": _array(_object({
        "record_type": {"type": "string", "enum": ["event", "claim", "problem", "question", "answer", "experience", "opinion"]},
        "claim": {"type": "string", "minLength": 1}, "details": _STRINGS, "entities": _STRINGS,
        "topic_key": {"type": ["string", "null"]}, "conditions": _STRINGS, "exceptions": _STRINGS,
        "event_at": {"type": ["string", "null"]},
        "source_refs": _array(_object({**_MESSAGE_REF, "content_digest": _STRING,
            "fragment_start": {"type": "integer"}, "fragment_end": {"type": "integer"},
            "quote": {"type": "string", "minLength": 1}})),
        "related_message_refs": _array(_object(_MESSAGE_REF)),
        "assertion_kind": {"type": "string", "enum": ["reported", "experience", "opinion", "inference"]},
        "support_status": {"type": "string", "enum": ["reported", "corroborated_in_corpus", "contradicted", "unresolved"]},
    })),
    "saturated": {"type": "boolean"},
})
QUERY_TERMS_SCHEMA = _object({"terms": _STRINGS})
RELATION_SCHEMA = _object({
    "decisions": _array(_object({"left_id": _STRING, "right_id": _STRING,
        "relation": {"type": "string", "enum": ["duplicate", "complements", "contradicts", "answers", "unrelated"]},
        "reason": {"type": "string", "minLength": 1}})),
    "saturated": {"type": "boolean"},
})
RELATION_PAIR_SCHEMA = _object({
    "decisions": _array(_object({"pair_id": {"type": "string", "minLength": 1},
        "relation": copy.deepcopy(RELATION_SCHEMA["properties"]["decisions"]["items"]["properties"]["relation"]),
        "reason": {"type": "string", "minLength": 1}})),
    "saturated": {"type": "boolean"},
})
# Structured Outputs permits 1000 enum values across the complete schema.
RELATION_PAIR_ENUM_LIMIT = 1000 - len(RELATION_PAIR_SCHEMA["properties"]["decisions"]["items"]["properties"]["relation"]["enum"])
MERGE_SCHEMA = _object({
    "groups": _array(_object({"title": {"type": "string", "minLength": 1},
        "summary": {"type": "string", "minLength": 1}, "input_ids": _STRINGS})),
    "dispositions": _array(_object({"input_id": _STRING,
        "disposition": {"type": "string", "enum": ["used", "merged", "irrelevant", "duplicate", "excluded"]},
        "reason": {"type": "string", "minLength": 1}})),
    "saturated": {"type": "boolean"},
})
SYNTHESIS_SCHEMA = _object({
    "summary": _STRING,
    "items": _array(_object({"title": {"type": "string", "minLength": 1},
        "text": {"type": "string", "minLength": 1},
        # These display arrays are filled from supporting evidence by the app.
        "conditions": {**_STRINGS, "maxItems": 0}, "exceptions": {**_STRINGS, "maxItems": 0},
        "contradictions": _STRINGS, "evidence_ids": _STRINGS})),
    "limitations": _STRINGS, "insufficient_information": {"type": "boolean"},
    "saturated": {"type": "boolean"},
})


def query_terms_format():
    return {"type": "json_schema", "name": "telegram_query_terms_v1", "strict": True, "schema": copy.deepcopy(QUERY_TERMS_SCHEMA)}


def synthesis_format():
    return {"type": "json_schema", "name": "telegram_synthesis_v1", "strict": True, "schema": copy.deepcopy(SYNTHESIS_SCHEMA)}


def merge_format():
    return {"type": "json_schema", "name": "telegram_merge_v1", "strict": True, "schema": copy.deepcopy(MERGE_SCHEMA)}


def relation_format(pair_map=None):
    if pair_map is None:
        return {"type": "json_schema", "name": "telegram_relations_v1", "strict": True, "schema": copy.deepcopy(RELATION_SCHEMA)}
    if not 1 <= len(pair_map) <= RELATION_PAIR_ENUM_LIMIT:
        raise ValueError("relation_pair_enum_limit")
    schema = copy.deepcopy(RELATION_PAIR_SCHEMA)
    schema["properties"]["decisions"]["items"]["properties"]["pair_id"]["enum"] = list(pair_map)
    return {"type": "json_schema", "name": "telegram_relations_v2", "strict": True, "schema": schema}


def classify_relation_document(document, pair_map, *, max_bytes, legacy=False):
    """Validate shape, then retain only unambiguous application-addressed decisions."""
    validate_shape(document, RELATION_SCHEMA if legacy else RELATION_PAIR_SCHEMA)
    if len(canonical_json(document).encode("utf-8")) > max_bytes:
        raise ValueError("relation_output_limit")
    reverse = {tuple(sorted(pair)): key for key, pair in pair_map.items()}
    if len(reverse) != len(pair_map) or any(len(pair) != 2 or pair[0] == pair[1] for pair in pair_map.values()):
        raise ValueError("relation_pair_mapping")
    grouped, extra, duplicate = {}, 0, 0
    for item in document["decisions"]:
        identity = (reverse.get(tuple(sorted((item["left_id"], item["right_id"]))))
                    if legacy else item["pair_id"])
        if identity not in pair_map:
            extra += 1
            continue
        left, right = pair_map[identity]
        value = {"left_id": left, "right_id": right, "relation": item["relation"], "reason": item["reason"]}
        bucket = grouped.setdefault(identity, {})
        digest = canonical_json(value)
        if digest in bucket:
            duplicate += 1
        bucket[digest] = value
    accepted = {key: next(iter(values.values())) for key, values in sorted(grouped.items()) if len(values) == 1}
    missing = sorted(set(pair_map) - set(grouped))
    conflicts = sorted(key for key, values in grouped.items() if len(values) > 1)
    diagnostics = {"requested": len(pair_map), "accepted": len(accepted), "missing": len(missing),
                   "extra": extra, "duplicate": duplicate, "conflicting": len(conflicts)}
    diagnostics["codes"] = [code for key, code in (("missing", "relation_missing_disposition"),
        ("extra", "relation_pair_scope"), ("duplicate", "relation_duplicate"), ("conflicting", "relation_conflict")) if diagnostics[key]]
    if document["saturated"]:
        diagnostics["codes"].append("relation_saturated")
    return {"accepted_decisions": accepted, "unresolved_pair_ids": sorted(set(missing) | set(conflicts)),
            "diagnostics": diagnostics, "saturated": document["saturated"]}


def evidence_format():
    schema = copy.deepcopy(EVIDENCE_SCHEMA)
    refs = schema["properties"]["records"]["items"]["properties"]["source_refs"]["items"]["properties"]
    # Keep the durable evidence shape; the app binds these placeholders from
    # the selected source and an unambiguous exact quote, never from model math.
    refs["content_digest"] = {"type": "string", "enum": [""]}
    for field in ("fragment_start", "fragment_end"):
        refs[field] = {"type": "integer", "enum": [0]}
    return {"type": "json_schema", "name": "telegram_evidence_v1", "strict": True, "schema": schema}


def response_text(response):
    output = response.get("output")
    if not isinstance(output, list):
        raise ValueError("evidence_response_envelope")
    texts = []
    for item in output:
        if not isinstance(item, dict):
            raise ValueError("evidence_response_envelope")
        if item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            raise ValueError("evidence_response_envelope")
        for part in content:
            if not isinstance(part, dict):
                raise ValueError("evidence_response_envelope")
            if part.get("type") == "refusal":
                return "refused", ""
            if part.get("type") == "output_text":
                if not isinstance(part.get("text"), str):
                    raise ValueError("evidence_response_envelope")
                texts.append(part["text"])
    if response.get("status") != "completed":
        return "incomplete", ""
    return "completed", "".join(texts)


def parse_document(text):
    def unique_fields(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("evidence_duplicate_json_key")
            value[key] = item
        return value
    return json.loads(text, object_pairs_hook=unique_fields)


def validate_shape(value, schema=EVIDENCE_SCHEMA):
    """Validate the small JSON Schema subset defined above, without extra dependencies."""
    kinds = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
    actual = {dict: "object", list: "array", str: "string", int: "integer", bool: "boolean", type(None): "null"}.get(type(value))
    if actual not in kinds or ("enum" in schema and value not in schema["enum"]):
        raise ValueError("evidence_schema_type")
    if actual == "object":
        if set(value) != set(schema["properties"]):
            raise ValueError("evidence_schema_fields")
        for key, child in schema["properties"].items():
            validate_shape(value[key], child)
    elif actual == "array":
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", len(value)):
            raise ValueError("evidence_array_size")
        for item in value:
            validate_shape(item, schema["items"])
    elif actual == "string" and len(value.strip()) < schema.get("minLength", 0):
        raise ValueError("evidence_empty_text")


ANALYSIS_NORMALIZATION_VERSION = 1


def build_analysis_message_link(channel, message_id):
    if channel.get("channel_type") != "Channel" or type(message_id) is not int or message_id <= 0:
        return None
    peer = channel.get("channel_id")
    if type(peer) is not int:
        return None
    if peer < 0:
        peer = -peer - 1000000000000
    # MTProto channel/monoforum ranges: https://core.telegram.org/api/bots/ids
    if not (1 <= peer <= 997852516352 or 1002147483649 <= peer <= 3000000000000):
        return None
    username = channel.get("username")
    username = username.strip().removeprefix("@") if isinstance(username, str) else ""
    if not re.fullmatch(r"[A-Za-z0-9_]+", username):
        username = ""
    return build_message_link({"channel_id": peer, "username": username, "message_id": message_id})


def _is_digest_source(message):
    text = (message.get("text") or "").strip()
    heading = text.split("\n", 1)[0]
    return bool(re.search(r"(?i)(?<!\w)#(?:дайджест|digest)\b", text)
                or re.match(r"(?i)^[^\w]*(?:(?:нейро)?дайджест\b|digest\b|weekly\s+(?:digest|roundup)\b)", heading))


def _verified_analysis_source(store, job, ref, *, supporting):
    pair = (ref["channel_id"], ref["message_id"])
    manifest = store.manifest_reference(job["job_id"], *pair)
    scope = job["checkpoint"].get("history", {}).get("refs", {}).values()
    if pair[0] not in scope or manifest is None or manifest["content_digest"] != ref["content_digest"]:
        raise ValueError("final_source_scope")
    request = job["request"]["request"]
    date = datetime.fromisoformat(manifest["date_utc"])
    if not datetime.fromisoformat(request["since"]) <= date <= min(
            datetime.fromisoformat(request["until"]), datetime.fromisoformat(request["cutoff_at"])):
        raise ValueError("final_source_period")
    message = _verified_source_snapshot(store, job, ref)
    if supporting:
        start, end = ref["fragment_start"], ref["fragment_end"]
        text = analysis_text_view(message)
        if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text)
                or text[start:end] != ref["quote"] or ref["date_utc"] != manifest["date_utc"]):
            raise ValueError("final_source_span")
    channel = store.source_channel(pair[0])
    if channel is None:
        raise ValueError("final_source_channel")
    return {"channel_id": pair[0], "message_id": pair[1], "date_utc": manifest["date_utc"],
            "content_digest": manifest["content_digest"], "title": channel["title"],
            "quote": ref["quote"] if supporting else None,
            "is_digest": _is_digest_source(message),
            "url": build_analysis_message_link(channel, pair[1])}


def _qa_answer_pairs(relations):
    return {frozenset((relation["left_id"], relation["right_id"])) for relation in relations if relation["relation"] == "answers"}


def _validate_qa_support(ids, known, answer_pairs):
    answers = {key for key in ids if known[key]["record_type"] == "answer"}
    if not answers:
        raise ValueError("qa_answer_missing_support")
    questions = {key for key in ids if known[key]["record_type"] in {"question", "problem"}}
    if any(not any(frozenset((question, answer)) in answer_pairs for answer in answers) for question in questions):
        raise ValueError("qa_question_without_answer_link")


def source_verification_counts(result):
    counts = {key: result.get(key, 0) for key in SOURCE_VERIFICATION_FIELDS}
    counts['digest_sources'] = sum(bool(source.get('is_digest')) for item in result.get('items', [])
                                  for source in item.get('sources', []))
    return counts


def _source_identity_text(value):
    return ' '.join(unicodedata.normalize('NFC', value).split()).casefold()


def _source_subject(record):
    return tuple(sorted({_source_identity_text(value) for value in record['entities'] if value.strip()}))


def _source_fact_key(record, *, claim=True, subject=True):
    values = tuple(tuple(sorted(_source_identity_text(text) for text in record[key]))
                   for key in ('details', 'conditions', 'exceptions'))
    constraints = (values, record['assertion_kind'], record.get('event_at'))
    if subject:
        constraints += (_source_subject(record),)
    return (_source_identity_text(record['claim']), constraints) if claim else constraints


def _source_options(store, job, records, relations, *, budget):
    """Prefer complete standalone equivalents within this frozen evidence set."""
    def sources(record):
        return [_verified_analysis_source(store, job, ref, supporting=True) for ref in record['source_refs']]
    options = {key: sources(record) for key, record in records.items()}
    if job['command'] == 'topics':
        return {key: [ref for ref in refs if not ref['is_digest']] for key, refs in options.items()}, set()
    for key, record in records.items():
        if record['record_type'] in {'question', 'problem'}:
            options[key] = [ref for ref in options[key] if not ref['is_digest']]
    targets = {key for key, refs in options.items() if refs and all(ref['is_digest'] for ref in refs)
               and records[key]['record_type'] not in {'question', 'problem', 'opinion'}}
    if not targets:
        return options, set()
    # Equivalence is app-owned exact fact identity or an accepted duplicate edge.
    # Conditions/details remain part of the identity even for duplicate edges.
    metrics, scan_limited = budget.metrics, False
    def expired():
        return budget.deadline is not None and budget.clock() >= budget.deadline
    roots = {}
    cached = {key: (_source_subject(record), _source_fact_key(record, claim=False)) for key, record in records.items()}
    forbidden = {frozenset((edge['left_id'], edge['right_id'])) for edge in relations if edge['relation'] != 'duplicate'}
    def payload(key):
        if key not in cached:
            value = store.leaf_evidence(job['job_id'], key)
            cached[key] = (_source_subject(value['payload']), _source_fact_key(value['payload'], claim=False)) if value else None
        return cached[key]
    def root(key):
        roots.setdefault(key, key)
        while roots[key] != key:
            roots[key] = roots[roots[key]]
            key = roots[key]
        return key
    for edge in relations:
        if expired():
            scan_limited = True
            break
        if edge['relation'] == 'duplicate':
            left, right = payload(edge['left_id']), payload(edge['right_id'])
            if left is None or right is None:
                continue
            if not left[0] or left[0] != right[0]:
                metrics['source_subject_rejections'] += 1
                continue
            if (frozenset((edge['left_id'], edge['right_id'])) not in forbidden
                    and left[1] == right[1]):
                roots[root(edge['left_id'])] = root(edge['right_id'])
    by_fact, by_root = {}, {}
    for key in targets:
        by_fact.setdefault(_source_fact_key(records[key], subject=False), []).append(key)
        by_root.setdefault(root(key), []).append(key)
    alternatives, ambiguous = {key: [] for key in targets}, set()
    for candidate in store.iter_leaf_evidence(job['job_id'], page_size=job['request']['settings']['sync']['batch_size']):
        if expired():
            scan_limited = True
            break
        record = candidate['payload']
        if record['record_type'] in {'question', 'problem', 'opinion'}:
            continue
        potential = set(by_fact.get(_source_fact_key(record, subject=False), []))
        if candidate['record_id'] in roots:
            potential.update(key for key in by_root.get(root(candidate['record_id']), [])
                           if _source_fact_key(records[key], claim=False) == _source_fact_key(record, claim=False))
        matched = set()
        for key in potential:
            if key == candidate['record_id']:
                continue
            target_subject, candidate_subject = _source_subject(records[key]), _source_subject(record)
            if not target_subject or target_subject != candidate_subject:
                metrics['source_subject_rejections'] += 1
                if not target_subject or not candidate_subject:
                    ambiguous.add(key)
                continue
            if frozenset((key, candidate['record_id'])) in forbidden:
                ambiguous.add(key)
                continue
            matched.add(key)
        if matched:
            standalone = [ref for ref in sources(record) if not ref['is_digest']]
            for key in matched:
                alternatives[key].extend(standalone)
    checkpoint = job['checkpoint']
    incomplete = (scan_limited or bool(store.incomplete_evidence_reasons(job['job_id']))
                  or store.processed_analysis_messages(job['job_id']) < checkpoint.get('manifest', {}).get('selected', 0)
                  or checkpoint.get('manifest', {}).get('partial', False)
                  or checkpoint.get('relations', {}).get('partial', False)
                  or checkpoint.get('relations', {}).get('limited', False))
    uncertain = set()
    for key in targets:
        if alternatives[key]:
            options[key] = alternatives[key]
        elif incomplete or key in ambiguous:
            options[key] = []
            uncertain.add(key)
    for key, refs in options.items():
        standalone = [ref for ref in refs if not ref['is_digest']]
        if standalone:
            options[key] = standalone
    return options, uncertain


class _SourceCoverLimited(Exception):
    pass


class SourceCoverBudget:
    """One frozen work budget shared by every item in one verification."""
    def __init__(self, maximum, *, deadline=None, monotonic_func=common_process.continuous_time):
        if type(maximum) is not int or maximum < 1:
            raise ValueError('Invalid source cover state budget')
        self.maximum, self.deadline, self.clock = maximum, deadline, monotonic_func
        self.metrics = {key: 0 for key in SOURCE_VERIFICATION_FIELDS if key.startswith('source_')}

    def check(self):
        if self.deadline is not None and self.clock() >= self.deadline:
            raise _SourceCoverLimited()

    def spend(self):
        self.check()
        if self.metrics['source_cover_states'] >= self.maximum:
            raise _SourceCoverLimited()
        self.metrics['source_cover_states'] += 1


def _select_supporting_sources(options, required, limit, *, budget):
    started = budget.clock()
    try:
        return _find_supporting_sources(options, required, limit, budget=budget)
    except _SourceCoverLimited:
        budget.metrics['source_cover_limited_items'] += 1
        return {'outcome': 'search_limited', 'sources': []}
    finally:
        budget.metrics['source_cover_elapsed_ms'] += max(0, round((budget.clock() - started) * 1000))


def _find_supporting_sources(options, required, limit, *, budget):
    candidates, coverage = {}, {}
    for key, refs in options.items():
        budget.check()
        for source in refs:
            budget.check()
            pair = (source['channel_id'], source['message_id'])
            if pair not in candidates or len(source['quote']) > len(candidates[pair]['quote']):
                candidates[pair] = source
            coverage.setdefault(pair, set()).add(key)
    def priority(pair):
        source = candidates[pair]
        return (source['is_digest'], -len(source['quote']), pair)
    def found(selected):
        budget.check()
        if len(selected) > limit or len(selected) != len(set(selected)) or set(required) - set().union(set(), *(coverage[p] for p in selected)):
            raise ValueError('source_cover_validation')
        selected = list(selected)
        selected.extend(sorted((pair for pair in candidates if pair not in selected), key=priority)[:limit - len(selected)])
        return {'outcome': 'found', 'sources': [candidates[pair] for pair in selected]}

    budget.check()
    missing, selected = set(required), []
    while missing and len(selected) < limit:
        choices = []
        for pair in candidates:
            budget.check()
            if pair not in selected and coverage[pair] & missing:
                choices.append(pair)
        if not choices:
            break
        pair = min(choices, key=lambda pair: (-len(coverage[pair] & missing), priority(pair)))
        selected.append(pair)
        missing -= coverage[pair]
    if not missing:
        return found(selected)
    budget.metrics['source_cover_fallbacks'] += 1
    positions = {key: index for index, key in enumerate(sorted(required))}
    compressed = {}
    for pair in sorted(candidates, key=priority):
        budget.check()
        mask = 0
        for key in coverage[pair] & required:
            budget.check()
            mask |= 1 << positions[key]
        if mask:
            compressed.setdefault(mask, pair)
    masks = list(compressed)

    def choices_for(mask, slots):
        if slots <= 0:
            return None
        union, gain = 0, 0
        for candidate in masks:
            budget.spend()
            covered = candidate & mask
            union |= covered
            gain = max(gain, covered.bit_count())
        if union != mask or (mask.bit_count() + gain - 1) // gain > slots:
            return None
        remaining, best = mask, None
        while remaining:
            bit = remaining & -remaining
            remaining ^= bit
            choices = []
            for candidate in masks:
                budget.spend()
                if candidate & bit:
                    choices.append(candidate)
            if best is None or len(choices) < len(best):
                best = choices
            if len(best) == 1:
                break
        return iter(sorted(best, key=lambda candidate: (-(candidate & mask).bit_count(), priority(compressed[candidate]))))

    wanted = (1 << len(required)) - 1
    choices = choices_for(wanted, limit)
    stack = [(wanted, limit, (), choices)] if choices is not None else []
    while stack:
        remaining, slots, selected, choices = stack[-1]
        candidate = next(choices, None)
        if candidate is None:
            stack.pop()
            continue
        budget.spend()
        pending, chosen = remaining & ~candidate, (*selected, compressed[candidate])
        if not pending:
            return found(chosen)
        following = choices_for(pending, slots - 1)
        if following is not None:
            stack.append((pending, slots - 1, chosen, following))
    budget.metrics['source_cover_impossible_items'] += 1
    return {'outcome': 'proven_impossible', 'sources': []}


def verify_analysis_sources(store, job_id, result, *, generation, deadline=None,
                            monotonic_func=common_process.continuous_time):
    """Resolve application-owned provenance; this does not prove semantic entailment."""
    job = store.require_running(job_id, generation=generation)
    limit = analysis_settings._synthesis_limits(job)["sources_per_item"]
    verified = copy.deepcopy(result)
    verified.pop("sources", None)
    retained = []
    excluded = verified.get("excluded_digest_only_items", 0)
    unsupported = verified.get('excluded_unsupported_items', 0)
    source_limited = verified.get('excluded_source_limited_items', 0)
    relations = list(store.iter_link_decisions(job_id)) if job['command'] != 'topics' else []
    answer_pairs = _qa_answer_pairs(relations) if job["command"] == "qa" else set()
    known = {}
    for item in verified.get('items', []):
        for identity in item['evidence_ids']:
            if identity not in known:
                record = store.leaf_evidence(job_id, identity)
                if record is None or not record['payload']['source_refs']:
                    raise ValueError('final_evidence_scope')
                known[identity] = record['payload']
    output = job['request']['settings']['analysis']['output']
    if 'source_cover_max_states' not in output:
        raise ValueError('analysis_policy_incompatible')
    budget = SourceCoverBudget(output['source_cover_max_states'],
                               deadline=deadline, monotonic_func=monotonic_func)
    options, uncertain = _source_options(store, job, known, relations, budget=budget)
    for item in verified.get("items", []):
        sources, records = {}, {}
        for identity in item["evidence_ids"]:
            records[identity] = known[identity]
            for source in options[identity]:
                pair = (source["channel_id"], source["message_id"])
                if pair not in sources or len(source["quote"]) > len(sources[pair]["quote"]):
                    sources[pair] = source
        if not sources:
            if not records:
                raise ValueError("final_evidence_without_support")
            if job['command'] == 'topics':
                excluded += 1
            else:
                unsupported += 1
                source_limited += bool(set(records) & uncertain)
            continue
        if job["command"] == "qa":
            _validate_qa_support(item["evidence_ids"], records, answer_pairs)
        # Keep the latest update; remaining slots retain substantive backing
        # rather than letting a chain of short acknowledgements displace it.
        ordered = sorted(sources.values(), key=lambda s: (
            -datetime.fromisoformat(s["date_utc"]).timestamp() if job["command"] == "topics" else 0,
            -len(s["quote"]), s["channel_id"], s["message_id"]))
        if job["command"] == "topics":
            ordered = ordered[:1] + sorted(ordered[1:], key=lambda s: -len(s["quote"]))
            selected = ordered[:limit]
        else:
            required = {key for key, record in records.items() if record['record_type'] not in {'question', 'problem'}}
            item_options = {key: options[key] for key in records}
            if job['command'] == 'search' and required:
                # Search has always shown representative standalone references.
                # Unique digest facts are mandatory; a question alone cannot fill
                # the remaining representative factual source slot.
                factual = [source for key in required for source in options[key]]
                required = {key for key in required if key in uncertain or any(ref['is_digest'] for ref in options[key])}
                item_options['__facts__'] = factual
                required.add('__facts__')
            selection = _select_supporting_sources(item_options, required, limit, budget=budget) if required else {'outcome': 'proven_impossible', 'sources': []}
            selected = selection['sources']
            if not selected:
                unsupported += 1
                source_limited += bool(set(records) & uncertain) or selection['outcome'] == 'search_limited'
                continue
        item['sources'] = [{key: value for key, value in source.items() if key != 'is_digest' or value}
                           for source in selected]
        retained.append(item)
    verified["items"] = retained
    verified.update(budget.metrics)
    if excluded:
        verified["excluded_digest_only_items"] = excluded
        notice = f"Не включено пунктов без отдельных постов по теме: {excluded}. Источники есть только в дайджестах."
        limitations = verified.setdefault("limitations", [])
        if notice not in limitations:
            limitations.append(notice)
        if not retained:
            verified["summary"] = ""
            verified["insufficient_information"] = True
    if unsupported:
        verified['excluded_unsupported_items'] = unsupported
        notice = f'Не включено пунктов без допустимого подтверждения ответа/утверждений в пределах лимита источников: {unsupported}.'
        if notice not in verified.setdefault('limitations', []):
            verified['limitations'].append(notice)
        verified['summary'] = ''
        if not retained:
            verified['insufficient_information'] = True
    if source_limited:
        verified['excluded_source_limited_items'] = source_limited
        verified['partial'] = True
        verified['reasons'] = sorted(set(verified.get('reasons', [])) | {'source_selection_incomplete'})
    if budget.metrics['source_cover_limited_items']:
        notice = 'Проверка покрытия источниками ограничена бюджетом поиска или временем; отсутствие полного покрытия не доказано.'
        if notice not in verified.setdefault('limitations', []):
            verified['limitations'].append(notice)
    if any(source.get('is_digest') for item in retained for source in item['sources']):
        notice = ('Дайджестные источники подтверждают сведения, для которых эквивалентный отдельный пост '
                  'не найден в проанализированной выборке. Это не проверка всего архива Telegram.')
        if notice not in verified.setdefault('limitations', []):
            verified['limitations'].append(notice)
    if not verified.get("items"):
        verified["sources"] = []
        if not excluded and not unsupported:
            for ref in store.processed_source_refs(job_id, limit=limit):
                source = _verified_analysis_source(store, job, ref, supporting=False)
                digest = source.pop('is_digest')
                if not digest or job['command'] != 'topics':
                    if digest:
                        source['is_digest'] = True
                    verified['sources'].append(source)
        verified["source_role"] = "processed_inputs"
        verified.pop("source_refs", None)
    return verified


_PRESENTATION_URL = re.compile(r"(?:https?://|tg://|www\.|t\.me/)[^\s<>\"']+", re.IGNORECASE)


def _verify_presentation_urls(result):
    sources = [source for item in result.get("items", []) for source in item["sources"]] + result.get("sources", [])
    allowed = {s["url"] for s in sources if s["url"]}
    allowed.update(url for source in sources for url in _PRESENTATION_URL.findall(source["quote"] or ""))
    texts = [result.get("summary", ""), *result.get("limitations", [])]
    for item in result.get("items", []):
        texts.extend([item["title"], item["text"], *item["conditions"], *item["exceptions"], *item["contradictions"]])
    if any(url not in allowed for text in texts for url in _PRESENTATION_URL.findall(text)):
        raise ValueError("final_unverified_url")


def _verified_source_snapshot(store, job, ref):
    try:
        message, digest = _analysis_message_snapshot(store, ref["channel_id"], ref["message_id"],
            job["request"]["settings"]["analysis"]["max_intermediate_bytes"])
    except ValueError:
        message, digest = None, None
    if digest != ref["content_digest"]:
        store.interrupt_changed_input(job["job_id"], generation=job["generation"], source=ref)
        raise ValueError("input_changed")
    return message


def validate_evidence_records(store, job_id, batch_id, *, generation, document, fragments, bind_model_sources=False):
    job = store.require_running(job_id, generation=generation)
    batch = store.get_batch(batch_id)
    if batch is None or batch["job_id"] != job_id or batch["kind"] != "extract":
        raise ValueError("Evidence step outside job scope")
    validate_shape(document)
    policy = job["request"]["settings"]["analysis"]
    if len(document["records"]) > policy["max_evidence_records"] or len(canonical_json(document).encode("utf-8")) > policy["max_intermediate_bytes"]:
        raise ValueError("evidence_output_limit")
    sources = {}
    for fragment in fragments:
        ref = fragment["input"]
        pair = (ref["channel_id"], ref["message_id"])
        if pair not in sources:
            manifest = store.manifest_reference(job_id, *pair)
            if manifest is None or manifest["content_digest"] != ref["content_digest"]:
                raise ValueError("evidence_source_scope")
            source = _verified_source_snapshot(store, job, ref)
            sources[pair] = {"text": analysis_text_view(source), "manifest": manifest, "fragments": []}
        source = sources[pair]
        start, end = ref["fragment_start"], ref["fragment_end"]
        if (type(start) is not int or type(end) is not int or not 0 <= start <= end <= len(source["text"])
                or source["text"][start:end] != fragment["message"]["text"]):
            raise ValueError("evidence_fragment_mismatch")
        source["fragments"].append(ref)
    records = []
    for index, proposed in enumerate(document["records"]):
        item = copy.deepcopy(proposed)
        if not item["source_refs"]:
            raise ValueError("evidence_without_support")
        if item["event_at"] is not None:
            try:
                datetime.fromisoformat(item["event_at"].replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("evidence_event_date") from exc
        for ref in item["source_refs"]:
            source = sources.get((ref["channel_id"], ref["message_id"]))
            if source is not None and bind_model_sources:
                # Model chooses the source and exact quote; mechanical metadata
                # comes only from the verified manifest and supplied fragments.
                matches = set()
                for fragment in source["fragments"]:
                    lower, upper = fragment["fragment_start"], fragment["fragment_end"]
                    start = source["text"].find(ref["quote"], lower, upper)
                    while start != -1:
                        matches.add((start, start + len(ref["quote"])))
                        start = source["text"].find(ref["quote"], start + 1, upper)
                if len(matches) != 1:
                    raise ValueError("evidence_supporting_span")
                start, end = next(iter(matches))
                ref.update(content_digest=source["manifest"]["content_digest"], fragment_start=start, fragment_end=end)
            if source is None or ref["content_digest"] != source["manifest"]["content_digest"]:
                raise ValueError("evidence_source_scope")
            start, end = ref["fragment_start"], ref["fragment_end"]
            parent = next((f for f in source["fragments"] if f["fragment_start"] <= start < end <= f["fragment_end"]), None)
            if parent is None or source["text"][start:end] != ref["quote"]:
                raise ValueError("evidence_supporting_span")
            ref.update(role=parent["role"], date_utc=source["manifest"]["date_utc"], text_view_version=1)
        if any((r["channel_id"], r["message_id"]) not in sources for r in item["related_message_refs"]):
            raise ValueError("evidence_related_scope")
        selected = next((ref for ref in item["source_refs"] if ref["role"] == "selected"), None)
        if selected is None:
            continue
        if item["topic_key"] is not None:
            item["topic_key"] = item["topic_key"].strip() or None
        item.update(reported_at=selected["date_utc"], related_record_ids=[], schema_version=EVIDENCE_SCHEMA_VERSION)
        identity = uuid.uuid5(uuid.NAMESPACE_URL, f"telegram-analysis:{job_id}:{batch_id}:{index}:{canonical_json(item)}").hex
        records.append({"record_id": identity, "record_type": item["record_type"], "topic_key": item["topic_key"], "payload": item})
    return records


def analysis_text_view(message):
    """Version 1 fragment offsets address text followed by ordinal OCR blocks."""
    parts = [message.get("text") or ""]
    for asset in sorted(message.get("media", []), key=lambda value: value["ordinal"]):
        if asset.get("ocr_text"):
            parts.append(f"\n[OCR {asset['ordinal']}]\n{asset['ocr_text']}")
    return "".join(parts)


def _analysis_message_snapshot(store, channel_id, message_id, maximum):
    payload = store.analysis_source(channel_id, message_id, max_bytes=maximum)
    if payload is None:
        return None, None
    payload["normalization_version"] = ANALYSIS_NORMALIZATION_VERSION
    encoded = canonical_json(payload)
    if len(encoded.encode("utf-8")) > maximum:
        raise ValueError("manifest_byte_cap")
    return payload, hashlib.sha256(encoded.encode("utf-8")).hexdigest()
