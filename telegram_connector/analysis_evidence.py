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

EVIDENCE_SCHEMA_VERSION = 3
ADDRESSED_EVIDENCE_FORMATS = {5: 'telegram_evidence_v5'}
# Structured Outputs protocol bounds; not additional operator ceilings.
MODEL_SCHEMA_LIMITS = {'properties': 5000, 'enum_values': 1000,
                       'string_chars': 120000, 'depth': 10}


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
        "topic_key": {"type": ["string", "null"]},
        "source_refs": _array(_object({**_MESSAGE_REF, "content_digest": _STRING,
            "fragment_start": {"type": "integer"}, "fragment_end": {"type": "integer"},
            "quote": {"type": "string", "minLength": 1}})),
        "related_message_refs": _array(_object(_MESSAGE_REF)),
        "assertion_kind": {"type": "string", "enum": ["reported", "experience", "opinion", "inference"]},
        "support_status": {"type": "string", "enum": ["reported", "corroborated_in_corpus", "contradicted", "unresolved"]},
    })),
    "saturated": {"type": "boolean"},
})
EVIDENCE_SCHEMAS = {EVIDENCE_SCHEMA_VERSION: EVIDENCE_SCHEMA}


def record_schema_version(settings):
    return EVIDENCE_SCHEMA_VERSION


QUERY_TERMS_SCHEMA = _object({"terms": _STRINGS})
_RELATION_KIND = {"type": "string", "enum": ["duplicate", "complements", "contradicts", "answers", "unrelated"]}
RELATION_PAIR_SCHEMA = _object({
    "decisions": _array(_object({"pair_id": {"type": "string", "minLength": 1},
        "relation": copy.deepcopy(_RELATION_KIND),
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
        "contradictions": _STRINGS, "evidence_ids": _STRINGS})),
    "limitations": _STRINGS, "insufficient_information": {"type": "boolean"},
    "saturated": {"type": "boolean"},
})


def query_terms_format():
    return {"type": "json_schema", "name": "telegram_query_terms_v1", "strict": True, "schema": copy.deepcopy(QUERY_TERMS_SCHEMA)}


def synthesis_format(*, topic_groups=None):
    schema = copy.deepcopy(SYNTHESIS_SCHEMA)
    item = schema['properties']['items']['items']['properties']
    if topic_groups and sum(map(len, topic_groups)) + _schema_enum_values(schema) <= MODEL_SCHEMA_LIMITS['enum_values']:
        item['evidence_ids'] = {'anyOf': [
            {'type': 'array', 'items': {'type': 'string', 'enum': group}, 'minItems': 1}
            for group in topic_groups]}
    schema['properties']['limitations'] = {**schema['properties']['limitations'], 'maxItems': 0}
    return {"type": "json_schema", "name": "telegram_synthesis_v4", "strict": True, "schema": schema}


def merge_format():
    return {"type": "json_schema", "name": "telegram_merge_v1", "strict": True, "schema": copy.deepcopy(MERGE_SCHEMA)}


def relation_duplicate_error(left, right):
    return duplicate_context_error((_source_subject(left), _source_fact_key(left, claim=False)),
                                   (_source_subject(right), _source_fact_key(right, claim=False)))


def relation_format(pair_map, *, record_payloads=None):
    if not 1 <= len(pair_map) <= RELATION_PAIR_ENUM_LIMIT - (4 if record_payloads is not None else 0):
        raise ValueError("relation_pair_enum_limit")
    schema = copy.deepcopy(RELATION_PAIR_SCHEMA)
    schema["properties"]["decisions"]["items"]["properties"]["pair_id"]["enum"] = list(pair_map)
    if record_payloads is not None:
        groups = {True: [], False: []}
        for key, (left, right) in pair_map.items():
            groups[not relation_duplicate_error(record_payloads[left], record_payloads[right])].append(key)
        variants = []
        for allowed, keys in groups.items():
            if not keys:
                continue
            item = copy.deepcopy(schema['properties']['decisions']['items'])
            item['properties']['pair_id']['enum'] = keys
            if not allowed:
                item['properties']['relation']['enum'].remove('duplicate')
            variants.append(item)
        schema['properties']['decisions']['items'] = {'anyOf': variants}
    return {"type": "json_schema", "name": "telegram_relations_v2", "strict": True, "schema": schema}


def classify_relation_document(document, pair_map, *, max_bytes, record_payloads=None):
    """Validate shape, then retain only unambiguous application-addressed decisions."""
    validate_shape(document, RELATION_PAIR_SCHEMA)
    if len(canonical_json(document).encode("utf-8")) > max_bytes:
        raise ValueError("relation_output_limit")
    reverse = {tuple(sorted(pair)): key for key, pair in pair_map.items()}
    if len(reverse) != len(pair_map) or any(len(pair) != 2 or pair[0] == pair[1] for pair in pair_map.values()):
        raise ValueError("relation_pair_mapping")
    grouped, extra, duplicate = {}, 0, 0
    for item in document["decisions"]:
        identity = item['pair_id']
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
    semantic = {}
    if record_payloads is not None:
        for key, value in list(accepted.items()):
            if value['relation'] != 'duplicate':
                continue
            left, right = (record_payloads[identity] for identity in pair_map[key])
            error = relation_duplicate_error(left, right)
            if error:
                semantic[key] = error
                del accepted[key]
    diagnostics = {"requested": len(pair_map), "accepted": len(accepted), "missing": len(missing),
                   "extra": extra, "duplicate": duplicate, "conflicting": len(conflicts)}
    diagnostics["codes"] = [code for key, code in (("missing", "relation_missing_disposition"),
        ("extra", "relation_pair_scope"), ("duplicate", "relation_duplicate"), ("conflicting", "relation_conflict")) if diagnostics[key]]
    if document["saturated"]:
        diagnostics["codes"].append("relation_saturated")
    if record_payloads is not None:
        diagnostics['semantic_rejected'] = len(semantic)
        diagnostics['codes'] = list(dict.fromkeys([*diagnostics['codes'], *semantic.values()]))
    return {"accepted_decisions": accepted, "unresolved_pair_ids": sorted(set(missing) | set(conflicts) | set(semantic)),
            "diagnostics": diagnostics, "saturated": document["saturated"]}


def _schema_enum_values(value):
    if isinstance(value, dict):
        return len(value.get('enum', [])) + sum(_schema_enum_values(v) for v in value.values())
    return sum(_schema_enum_values(v) for v in value) if isinstance(value, list) else 0


def extraction_input_enum_limit():
    # Structured Outputs permits 1000 enum members across the complete schema.
    # Five decision-enum members and two reserved slots retain the frozen cap.
    return MODEL_SCHEMA_LIMITS['enum_values'] - _schema_enum_values(EVIDENCE_SCHEMA) - 7


def model_schema_usage(schema):
    """Count definitions once; resolve refs only to measure actual nesting."""
    usage = dict.fromkeys(MODEL_SCHEMA_LIMITS, 0)
    def count(value):
        if isinstance(value, dict):
            usage['properties'] += len(value.get('properties', {}))
            usage['enum_values'] += len(value.get('enum', []))
            usage['string_chars'] += sum(len(key) for name in ('properties', '$defs')
                                         for key in value.get(name, {}))
            usage['string_chars'] += sum(len(v) for v in value.get('enum', []) if isinstance(v, str))
            usage['string_chars'] += len(value.get('const', '')) if isinstance(value.get('const'), str) else 0
            for child in value.values():
                count(child)
        elif isinstance(value, list):
            for child in value:
                count(child)
    def depth(value, ancestors=()):
        if 'anyOf' in value:
            return max(depth(option, ancestors) for option in value['anyOf'])
        if '$ref' in value:
            key = value['$ref'].removeprefix('#/$defs/')
            if value['$ref'] != '#/$defs/' + key or key in ancestors or key not in schema.get('$defs', {}):
                raise ValueError('model_schema_reference')
            return depth(schema['$defs'][key], (*ancestors, key))
        children = list(value.get('properties', {}).values())
        if 'items' in value:
            children.append(value['items'])
        return int(value.get('type') in ('object', 'array')) + max(
            (depth(child, ancestors) for child in children), default=0)
    count(schema)
    usage['depth'] = depth(schema)
    return usage


def addressed_evidence_format(scope, version=5):
    if version != 5:
        raise ValueError('extraction_format_retired')
    if len(scope['model_input_ids']) > extraction_input_enum_limit():
        raise ValueError('extraction_input_enum_limit')
    record = copy.deepcopy(EVIDENCE_SCHEMA['properties']['records']['items'])
    for field in ('source_refs', 'related_message_refs'):
        del record['properties'][field]
        record['required'].remove(field)
    for field in ('supporting_input_ids', 'related_input_ids'):
        record['properties'][field] = _array({'$ref': '#/$defs/source_id'})
        record['required'].append(field)
    source_ids = [*scope['model_input_ids'], *scope.get('context_input_ids', [])]
    if not source_ids:
        for field in ('supporting_input_ids', 'related_input_ids'):
            record['properties'][field]['maxItems'] = 0
    base = _object({'status': {'type': 'string'}, 'records': _array({'$ref': '#/$defs/record'}),
                    'retained_record_ids': _STRINGS, 'reason': _STRING})
    represented = copy.deepcopy(base)
    represented['properties']['status']['enum'] = ['represented']
    represented['properties']['records']['minItems'] = 1
    # A decision either creates records or reuses roots. Mixing both can
    # repeat its own slot and invalidate otherwise correct residual repair.
    represented['properties']['retained_record_ids']['maxItems'] = 0
    negative = copy.deepcopy(base)
    negative['properties']['status']['enum'] = ['irrelevant', 'unresolved']
    negative['properties']['records']['maxItems'] = 0
    negative['properties']['retained_record_ids']['maxItems'] = 0
    negative['properties']['reason']['minLength'] = 1
    choices = [represented, negative]
    retained = copy.deepcopy(base)
    retained['properties']['status']['enum'] = ['represented', 'duplicate'] if scope.get('allow_retained') else ['represented']
    retained['properties']['records']['maxItems'] = 0
    retained['properties']['retained_record_ids']['minItems'] = 1
    if not scope.get('allow_retained'):
        retained['properties']['retained_record_ids']['items'] = {'type': 'string', 'pattern': r'^i[0-9]{4}:[0-9]+$'}
    if scope.get('allow_retained'):
        choices.append(retained)
    schema = _object({'saturated': {'type': 'boolean'},
        'input_dispositions': _object({key: {'$ref': '#/$defs/decision'} for key in scope['model_input_ids']})})
    schema['$defs'] = {'decision': {'anyOf': choices}, 'record': record,
        'source_id': {'type': 'string', **({'enum': source_ids} if source_ids else {})}}
    usage = model_schema_usage(schema)
    if any(usage[key] > bound for key, bound in MODEL_SCHEMA_LIMITS.items()):
        raise ValueError('model_schema_limit')
    return {'type': 'json_schema', 'name': ADDRESSED_EVIDENCE_FORMATS[version],
            'strict': True, 'schema': schema}




def _internal_disposition_schema():
    base = _object({'status': {'type': 'string'}, 'record_indices': _array({'type': 'integer'}),
                    'retained_record_ids': _STRINGS, 'reason': _STRING})
    by_index, by_root, negative = (copy.deepcopy(base) for _ in range(3))
    for value in (by_index, by_root):
        value['properties']['status']['enum'] = ['represented', 'duplicate']
    by_index['properties']['record_indices']['minItems'] = 1
    by_root['properties']['retained_record_ids']['minItems'] = 1
    negative['properties']['status']['enum'] = ['irrelevant', 'unresolved']
    negative['properties']['record_indices']['maxItems'] = 0
    negative['properties']['retained_record_ids']['maxItems'] = 0
    negative['properties']['reason']['minLength'] = 1
    return {'anyOf': [by_index, by_root, negative]}


def extraction_context_ids(context):
    return [f'c{index:04}' for index, fragment in enumerate(context)
            if fragment['message']['text'].strip()]


def _materialize_owned_records(document, selected, context, *, fragments, maximum, max_records, allow_retained):
    if set(document) != {'saturated', 'input_dispositions'} or not isinstance(document['input_dispositions'], dict):
        raise ValueError('extraction_envelope_schema')
    scope = extraction_scope(selected)
    sources = dict(zip(scope['inputs'], selected))
    sources.update({f'c{index:04}': fragment for index, fragment in enumerate(context)})
    provided = [key for key, fragment in sources.items() if fragment in fragments]
    wire_scope = {**scope, 'model_input_ids': [key for key in scope['model_input_ids'] if key in provided],
                  'context_input_ids': extraction_context_ids(context), 'allow_retained': allow_retained}
    schema = addressed_evidence_format(wire_scope)['schema']
    records, decisions, owners, local_records = [], {}, {}, {}
    used = len(canonical_json(document).encode())
    if used > maximum:
        raise ValueError('evidence_output_limit')
    order = [key for key in scope['inputs'] if key in document['input_dispositions']]
    order += [key for key in document['input_dispositions'] if key not in scope['inputs']]
    for key in order:
        decision = document['input_dispositions'][key]
        if not isinstance(decision, dict) or not isinstance(decision.get('records'), list):
            decisions[key] = decision
            continue
        indices = []
        for local_index, raw in enumerate(decision['records']):
            value = copy.deepcopy(raw)
            slot = len(records)
            if key in scope['model_input_ids']:
                owners[slot] = key
                local_records[f'{key}:{local_index}'] = slot
            try:
                validate_shape(raw, schema['$defs']['record'], _root=schema)
                if key not in wire_scope['model_input_ids']:
                    raise ValueError('extraction_disposition_scope')
                refs = [sources[name] for name in dict.fromkeys([key, *raw['supporting_input_ids']])]
                primary = sources[key]['input']
                value = {name: item for name, item in raw.items()
                         if name not in {'supporting_input_ids', 'related_input_ids'}}
                value['source_refs'] = [{**{field: fragment['input'][field] for field in
                    ('channel_id', 'message_id', 'content_digest', 'fragment_start', 'fragment_end')},
                    'quote': fragment['message']['text']} for fragment in refs]
                value['related_message_refs'] = [
                    {field: sources[name]['input'][field] for field in ('channel_id', 'message_id')}
                    for name in dict.fromkeys(raw['related_input_ids'])]
            except (ValueError, KeyError, TypeError) as exc:
                # Keep malformed records visible to the shared per-record classifier.
                value = copy.deepcopy(raw) if isinstance(raw, dict) else {'value': raw}
                value['_owned_source_error'] = 'extraction_owned_record_schema'
                if key in sources:
                    fragment = sources[key]
                    value['source_refs'] = [{**{field: fragment['input'][field] for field in
                        ('channel_id', 'message_id', 'content_digest', 'fragment_start', 'fragment_end')},
                        'quote': fragment['message']['text']}]
            used += len(canonical_json(value).encode()) + 32
            if used > maximum or len(records) >= max_records:
                raise ValueError('evidence_output_limit')
            indices.append(slot)
            records.append(value)
        decisions[key] = {name: item for name, item in decision.items() if name != 'records'}
        decisions[key]['record_indices'] = indices
    for key, decision in decisions.items():
        if key not in scope['inputs'] or not isinstance(decision, dict):
            continue
        retained = decision.get('retained_record_ids')
        if not isinstance(retained, list):
            continue
        external = []
        for identity in retained:
            slot = local_records.get(identity) if isinstance(identity, str) else None
            owner = scope['inputs'].get(owners.get(slot))
            source = scope['inputs'][key]
            if slot is not None and all(owner[field] == source[field] for field in ('channel_id','message_id','content_digest')):
                decision['record_indices'].append(slot)
            else:
                external.append(identity)
        decision['retained_record_ids'] = external
    return {'records': records, 'saturated': document['saturated'], 'input_dispositions': decisions}, owners



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


def validate_shape(value, schema=EVIDENCE_SCHEMA, *, _root=None):
    """Validate the small JSON Schema subset defined above, without extra dependencies."""
    root = schema if _root is None else _root
    if 'anyOf' in schema:
        for option in schema['anyOf']:
            try:
                validate_shape(value, option, _root=root)
            except ValueError:
                continue
            return
        raise ValueError('evidence_schema_anyof')
    if '$ref' in schema:
        key = schema['$ref'].removeprefix('#/$defs/')
        if schema['$ref'] != '#/$defs/' + key or key not in root.get('$defs', {}):
            raise ValueError('model_schema_reference')
        return validate_shape(value, root['$defs'][key], _root=root)
    kinds = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
    actual = {dict: "object", list: "array", str: "string", int: "integer", bool: "boolean", type(None): "null"}.get(type(value))
    if actual not in kinds or ("enum" in schema and value not in schema["enum"]):
        raise ValueError("evidence_schema_type")
    if actual == "object":
        if set(value) != set(schema["properties"]):
            raise ValueError("evidence_schema_fields")
        for key, child in schema["properties"].items():
            validate_shape(value[key], child, _root=root)
    elif actual == "array":
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", len(value)):
            raise ValueError("evidence_array_size")
        for item in value:
            validate_shape(item, schema["items"], _root=root)
    elif actual == 'string':
        if len(value.strip()) < schema.get('minLength', 0):
            raise ValueError('evidence_empty_text')
        if 'pattern' in schema and re.search(schema['pattern'], value) is None:
            raise ValueError('evidence_string_pattern')


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


def record_source_eligible(store, job, record):
    """Apply command policy before reduction; durable evidence stays intact."""
    if job['command'] != 'topics':
        return True
    sources = [_verified_analysis_source(store, job, ref, supporting=True) for ref in record['source_refs']]
    return any(not source['is_digest'] and (
        not any(ref['is_digest'] for ref in sources) or _record_supported_by_quote(record, source['quote']))
        for source in sources)


def _record_supported_by_quote(record, quote):
    """Sufficient literal proof for mixed sources; never guess paraphrase support."""
    text = _source_identity_text(quote)
    facts = [record['claim'], *record['details'], *record['entities']]
    return all(value.strip() and re.search(r'(?<!\w)' + re.escape(
        _source_identity_text(value).rstrip('.')) + r'(?!\w)', text) for value in facts)


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


def _detail_equivalence_key(text):
    # Only an explicit copular variant is erased. Bounds, units, negations and
    # remaining wording must still match; this is not general fuzzy equivalence.
    value = re.sub(r'\b(?:составляет|составляют)\b', ' ', _source_identity_text(text))
    value = re.sub(r'\s+[—–]\s+', ' ', value).rstrip('.')
    return ' '.join(value.split())


def _detail_scope(record):
    return (_source_subject(record), _source_identity_text(record['claim']),
            tuple(_source_identity_text(value) for value in record['details']),
            record.get('assertion_kind'), record.get('record_type'))


def equivalent_detail_groups(known, *, relations=()):
    groups = {}
    blocked = {frozenset((edge['left_id'], edge['right_id']))
               for edge in relations if edge['relation'] == 'unrelated'}
    for identity, record in known.items():
        for field in ('details',):
            for index, text in enumerate(record[field]):
                key = (field, _detail_scope(record), _detail_equivalence_key(text),
                       None if _source_subject(record) else identity)
                groups.setdefault(key, []).append({'record_id': identity, 'index': index})
    result = []
    for key, refs in groups.items():
        if len(refs) < 2:
            continue
        identities = {ref['record_id'] for ref in refs}
        if any(pair <= identities for pair in blocked):
            continue
        result.append({'refs': refs})
    return result


def topic_components(identities, relations, *, consistent=False):
    if consistent:
        members = {key:{key} for key in identities}
        blocked = {key:set() for key in members}
        parent = {key:key for key in members}
        def root(key):
            while parent[key] != key:
                parent[key] = parent[parent[key]]
                key = parent[key]
            return key
        edges = [edge for edge in relations if edge['left_id'] in members and edge['right_id'] in members]
        for edge in edges:
            if edge['relation'] == 'unrelated':
                blocked[edge['left_id']].add(edge['right_id'])
                blocked[edge['right_id']].add(edge['left_id'])
        priority = {'duplicate':0,'contradicts':1,'complements':2}
        for edge in sorted(edges, key=lambda edge:(priority.get(edge['relation'],3),
                min(edge['left_id'],edge['right_id']),max(edge['left_id'],edge['right_id']))):
            if edge['relation'] not in {'duplicate','complements','contradicts'}:
                continue
            left,right = root(edge['left_id']),root(edge['right_id'])
            if left == right:
                continue
            if len(members[left]) < len(members[right]):
                left,right = right,left
            if blocked[right] & members[left]:
                continue
            parent[right] = left
            members[left].update(members.pop(right))
            blocked[left].update(blocked.pop(right))
        return sorted((sorted(group) for group in members.values()), key=lambda group:group[0])
    neighbors = {key: set() for key in identities}
    for edge in relations:
        left, right = edge['left_id'], edge['right_id']
        if left in neighbors and right in neighbors and edge['relation'] in {'duplicate', 'complements', 'contradicts'}:
            neighbors[left].add(right)
            neighbors[right].add(left)
    remaining, groups = set(neighbors), []
    while remaining:
        seen, pending = set(), [min(remaining)]
        while pending:
            key = pending.pop()
            if key not in seen:
                seen.add(key)
                pending.extend(neighbors[key] - seen)
        groups.append(sorted(seen))
        remaining -= seen
    return groups




def validate_topic_group(identities, relations, *, scope_identities=None):
    """An event group needs a connected supporting graph, not a shared brand."""
    wanted = set(identities)
    if len(wanted) < 2:
        return
    for edge in relations:
        if (edge['left_id'] in wanted and edge['right_id'] in wanted
                and edge['relation'] == 'unrelated'):
            raise ValueError('topics_group_unrelated')
    groups = topic_components(scope_identities if scope_identities is not None else identities,
                              relations, consistent=scope_identities is not None)
    if not any(wanted <= set(group) for group in groups):
        raise ValueError('topics_group_disconnected')


def _source_fact_key(record, *, claim=True, subject=True):
    values = tuple(tuple(sorted(_source_identity_text(text) for text in record[key]))
                   for key in ('details',))
    constraints = (values, record['assertion_kind'])
    if subject:
        constraints += (_source_subject(record),)
    return (_source_identity_text(record['claim']), constraints) if claim else constraints


def duplicate_context_error(left, right):
    """Shared necessary support for duplicate edges and source substitution."""
    if not left[0] or left[0] != right[0]:
        return 'relation_subject_mismatch'
    if left[1] != right[1]:
        return 'relation_constraints_mismatch'
    return None


def _source_options(store, job, records, relations, *, budget):
    """Prefer complete standalone equivalents within this frozen evidence set."""
    def sources(record):
        return [_verified_analysis_source(store, job, ref, supporting=True) for ref in record['source_refs']]
    options = {key: sources(record) for key, record in records.items()}
    if job['command'] == 'topics':
        return {key: [ref for ref in refs if not ref['is_digest'] and (
            not any(source['is_digest'] for source in refs)
            or _record_supported_by_quote(records[key], ref['quote']))]
            for key, refs in options.items()}, set()
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
            error = duplicate_context_error(left, right)
            if error == 'relation_subject_mismatch':
                metrics['source_subject_rejections'] += 1
                continue
            if (frozenset((edge['left_id'], edge['right_id'])) not in forbidden
                    and error is None):
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
        if job['command'] == 'topics' and any(not options[key] for key in records):
            # A standalone citation from another fact cannot support this text.
            unsupported += 1
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
            selected_pairs = {(ref['channel_id'], ref['message_id']) for ref in selected}
            if any(not selected_pairs.intersection((ref['channel_id'], ref['message_id'])
                    for ref in options[key]) for key in records):
                selection = _select_supporting_sources({key: options[key] for key in records},
                    set(records), limit, budget=budget)
                selected = selection['sources']
                if not selected:
                    unsupported += 1
                    source_limited += selection['outcome'] == 'search_limited'
                    continue
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
        verified.setdefault('limitations', [])
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
            if store.extraction_work_state(job_id)['unknown_range_messages']:
                # Preserve frozen-input interruption before filtering changed sources.
                after = ''
                while batch := store.next_manifest_batch(job_id, after=after):
                    after = batch['step_key']
                    for ref in store.manifest_batch_inputs(batch['batch_id']):
                        _verified_source_snapshot(store, job, ref)
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
        texts.extend([item["title"], item["text"], *item.get("conditions", []), *item.get("exceptions", []), *item["contradictions"]])
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


def validate_evidence_records(store, job_id, batch_id, *, generation, document, fragments, record_offset=0, record_schema_version=3):
    job = store.require_running(job_id, generation=generation)
    batch = store.get_batch(batch_id)
    if batch is None or batch["job_id"] != job_id or batch["kind"] != "extract":
        raise ValueError("Evidence step outside job scope")
    validate_shape(document, EVIDENCE_SCHEMAS[record_schema_version])
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
        for ref in item["source_refs"]:
            source = sources.get((ref["channel_id"], ref["message_id"]))
            if source is None or ref["content_digest"] != source["manifest"]["content_digest"]:
                raise ValueError("evidence_source_scope")
            start, end = ref["fragment_start"], ref["fragment_end"]
            candidates = [f for f in source['fragments'] if f['fragment_start'] <= start < end <= f['fragment_end']]
            parent = min(candidates, key=lambda f:(f['fragment_start'] != start or f['fragment_end'] != end,
                         f['fragment_end']-f['fragment_start'])) if candidates else None
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
        item.update(reported_at=selected["date_utc"], related_record_ids=[], schema_version=record_schema_version)
        identity = uuid.uuid5(uuid.NAMESPACE_URL, f"telegram-analysis:{job_id}:{batch_id}:{index + record_offset}:{canonical_json(item)}").hex
        records.append({"record_id": identity, "record_type": item["record_type"], "topic_key": item["topic_key"], "payload": item})
    return records


def extraction_scope(selected):
    """Address selected fragments; empty text is an application observation."""
    inputs = {f"i{index:04d}": {key: copy.deepcopy(value) for key, value in item['input'].items() if key != 'batch_id'}
              for index, item in enumerate(selected)}
    empty = [key for key, item in zip(inputs, selected)
             if not item['message']['text'].strip()]
    return {'inputs': inputs, 'empty_input_ids': empty,
            'explicit_digest_input_ids': [key for key, item in zip(inputs, selected) if _is_digest_source(item['message'])],
            'model_input_ids': [key for key in inputs if key not in empty],
            'sha256': hashlib.sha256(canonical_json(inputs).encode()).hexdigest()}


def _record_uses_fragment(record, fragment):
    return any(ref['channel_id'] == fragment['channel_id']
               and ref['message_id'] == fragment['message_id']
               and ref['content_digest'] == fragment['content_digest']
               and ref.get('role', 'selected') == fragment.get('role', 'selected')
               and fragment['fragment_start'] <= ref['fragment_start']
               < ref['fragment_end'] <= fragment['fragment_end']
               for ref in record['payload']['source_refs'])










def _classify_extraction_records(store, job_id, batch_id, generation, document, fragments):
    records, errors = {}, {}
    for index, value in enumerate(document['records']):
        if isinstance(value, dict) and '_owned_source_error' in value:
            errors[str(index)] = value['_owned_source_error']
            continue
        try:
            validate_shape(value, EVIDENCE_SCHEMA['properties']['records']['items'])
            accepted = validate_evidence_records(store, job_id, batch_id,
                generation=generation, document={'records': [value], 'saturated': False},
                fragments=fragments, record_offset=index)
            if not accepted:
                raise ValueError('evidence_without_selected_support')
            records[index] = accepted[0]
        except ValueError as exc:
            if str(exc) == 'input_changed':
                raise
            errors[str(index)] = str(exc)
    return records, errors


def _disposition_records(value, records, retained_records):
    if set(value) != {'input_id', 'status', 'record_indices', 'retained_record_ids', 'reason'}:
        return None
    indices, retained = value['record_indices'], value['retained_record_ids']
    if (not isinstance(indices, list) or any(type(i) is not int or i not in records for i in indices)
            or len(set(indices)) != len(indices) or not isinstance(value['reason'], str)
            or not isinstance(retained, list) or any(not isinstance(i, str) or i not in retained_records for i in retained)
            or len(set(retained)) != len(retained)):
        return None
    selected = [records[i] for i in indices] + [retained_records[i] for i in retained]
    return selected if len({r['record_id'] for r in selected}) == len(selected) else None


def _literal_extraction_duplicate(key, records, scope, texts, accepted):
    value = _source_identity_text(texts[key])
    for record in records:
        subjects = _source_subject(record['payload'])
        if not subjects or not all(subject in value for subject in subjects):
            return False
        if not any(other != key and decision['status'] == 'represented'
                   and record['record_id'] in decision['record_ids']
                   and _source_identity_text(texts[other]) == value
                   for other, decision in accepted.items()):
            return False
    return bool(records)


def _validated_retained_dispositions(scope, dispositions, records):
    roots = {}
    for key, decision in (dispositions or {}).items():
        ids = decision.get('record_ids')
        if (key not in scope['model_input_ids'] or decision.get('status') != 'represented'
                or not isinstance(ids, list) or not ids or len(set(ids)) != len(ids)
                or not isinstance(decision.get('reason'), str)
                or any(identity not in records or not _record_uses_fragment(
                    records[identity], scope['inputs'][key]) for identity in ids)):
            continue
        roots[key] = copy.deepcopy(decision)
    return roots


def _classify_input_dispositions(document, scope, records, retained_records, selected, retained_dispositions):
    grouped = {}
    for value in document['input_dispositions']:
        if isinstance(value, dict) and value.get('input_id') in scope['model_input_ids']:
            grouped.setdefault(value['input_id'], []).append(value)
    accepted = {key: {'status': 'empty_text', 'record_ids': [], 'reason': ''}
                for key in scope['empty_input_ids']}
    accepted.update(_validated_retained_dispositions(scope, retained_dispositions, retained_records))
    pending = {}
    for key, values in grouped.items():
        if key in accepted:
            continue  # A fresh response cannot replace an already accepted root.
        unique = {canonical_json(value): value for value in values}
        if len(unique) != 1:
            continue
        value = next(iter(unique.values()))
        selected_records = _disposition_records(value, records, retained_records)
        if selected_records is None:
            continue
        status, reason = value['status'], value['reason']
        if status == 'represented':
            if not selected_records or any(
                    not _record_uses_fragment(record, scope['inputs'][key]) for record in selected_records):
                continue
        elif status == 'irrelevant':
            if selected_records or not reason.strip() or any(_record_uses_fragment(
                    record, scope['inputs'][key]) for record in [*records.values(), *retained_records.values()]):
                continue
        elif status == 'duplicate' and reason.strip():
            pending[key] = (selected_records, reason)
            continue
        else:
            continue
        accepted[key] = {'status': status, 'record_ids': [record['record_id'] for record in selected_records],
                         'reason': reason}
    texts = {key: item['message']['text'] for key, item in zip(scope['inputs'], selected)}
    for key, (values, reason) in pending.items():
        if _literal_extraction_duplicate(key, values, scope, texts, accepted):
            accepted[key] = {'status': 'duplicate', 'record_ids': [r['record_id'] for r in values], 'reason': reason}
    return accepted


def _record_failure_diagnostics(document, errors, scope, fragments):
    """Describe a failure, without repairing source text or guessing support."""
    result = {}
    for slot, code in errors.items():
        proposed = document['records'][int(slot)]
        refs = proposed.get('source_refs', []) if isinstance(proposed, dict) else []
        diagnostics = []
        for index, ref in enumerate(refs if isinstance(refs, list) else []):
            if not isinstance(ref, dict):
                continue
            pair = (ref.get('channel_id'), ref.get('message_id'))
            addressed = [key for key, source in scope['inputs'].items()
                         if (source['channel_id'], source['message_id']) == pair]
            reason, matches = code, set()
            if code in {'evidence_supporting_span', 'evidence_source_scope'}:
                candidates = [fragment for fragment in fragments if
                    (fragment['input']['channel_id'], fragment['input']['message_id']) == pair]
                quote = ref.get('quote')
                if not candidates:
                    reason = 'source_outside_scope'
                elif not isinstance(quote, str) or not quote:
                    reason = 'invalid_quote'
                else:
                    for fragment in candidates:
                        text = fragment['message']['text']
                        start = text.find(quote)
                        while start >= 0 and len(matches) < 2:
                            matches.add(fragment['input']['fragment_start'] + start)
                            start = text.find(quote, start + 1)
                    reason = ('quote_not_found' if not matches else
                              'quote_ambiguous' if len(matches) > 1 else 'source_metadata_or_bounds')
            diagnostics.append({'code': code, 'reason': reason, 'source_ref_index': index,
                'channel_id': pair[0], 'message_id': pair[1], 'input_ids': addressed})
        if diagnostics:
            result[slot] = diagnostics
    return result


def classify_extraction_document(store, job_id, batch_id, *, generation,
                                 document, fragments, selected, retained_records=None, retained_dispositions=None, context=None):
    """Retain independently valid records; a missing decision is not irrelevance."""
    job = store.require_running(job_id, generation=generation)
    maximum = job['request']['settings']['analysis']['max_intermediate_bytes']
    disposition_error = None
    if not isinstance(document, dict) or 'records' in document:
        raise ValueError('extraction_envelope_schema')
    context = context if context is not None else [f for f in fragments if f not in selected]
    document, owned_inputs = _materialize_owned_records(document, selected, context, fragments=fragments,
        maximum=maximum, max_records=job['request']['settings']['analysis']['max_evidence_records'],
        allow_retained=bool(retained_records))
    if not isinstance(document, dict) or not isinstance(document.get('input_dispositions'), dict):
        raise ValueError('extraction_envelope_schema')
    scope = extraction_scope(selected)
    decision_schema = _internal_disposition_schema()
    decisions = []
    for key, value in document['input_dispositions'].items():
        try:
            if key not in scope['model_input_ids']:
                raise ValueError('extraction_disposition_scope')
            validate_shape(value, decision_schema)
        except ValueError as exc:
            disposition_error = str(exc)
            continue
        decisions.append({**value, 'input_id': key})
    document = {**document, 'input_dispositions': decisions}
    arrays = ('records', 'input_dispositions')
    if (not isinstance(document, dict) or set(document) != {'saturated', *arrays}
            or type(document.get('saturated')) is not bool
            or any(not isinstance(document.get(key), list) for key in arrays)):
        raise ValueError('extraction_envelope_schema')
    if (len(canonical_json(document).encode()) > maximum
            or len(document['records']) > job['request']['settings']['analysis']['max_evidence_records']):
        raise ValueError('evidence_output_limit')
    scope = extraction_scope(selected)
    records, errors = _classify_extraction_records(
        store, job_id, batch_id, generation, document, fragments)
    dispositions = _classify_input_dispositions(document, scope, records, retained_records or {}, selected, retained_dispositions)
    for index in errors:
        raw = document['records'][int(index)]
        refs = raw.get('source_refs', []) if isinstance(raw, dict) else []
        pairs = {(r.get('channel_id'), r.get('message_id')) for r in refs
                 if isinstance(r, dict) and type(r.get('channel_id')) is int and type(r.get('message_id')) is int} if isinstance(refs, list) else set()
        # Native owned records address an exact selected range. A failed sibling
        # cannot reopen valid ranges from the same message.
        affected = ([owned_inputs[int(index)]] if int(index) in owned_inputs else
                    [key for key, ref in scope['inputs'].items() if (ref['channel_id'], ref['message_id']) in pairs])
        for key in affected or scope['model_input_ids']:
            dispositions.pop(key, None)
    unresolved = [key for key in scope['inputs'] if key not in dispositions]
    diagnostics = _record_failure_diagnostics(document, errors, scope, fragments)
    rejected = {key: document['records'][int(key)] for key in errors}
    if disposition_error:
        errors['envelope'] = disposition_error
    reasons = ['extraction_coverage_incomplete'] if unresolved or errors or document['saturated'] else []
    return {'scope': scope, 'records': list(records.values()),
            'model_unresolved_input_ids': [key for key in unresolved if sum(
                isinstance(value, dict) and value.get('input_id') == key for value in document['input_dispositions']) == 1
                and any(isinstance(value, dict) and value.get('input_id') == key and value.get('status') == 'unresolved'
                        for value in document['input_dispositions'])],
            'record_slots': {str(i): value for i, value in records.items()},
            'record_errors': errors, 'dispositions': dispositions,
            'record_diagnostics': diagnostics,
            'rejected_records': rejected,
            'local_date_normalizations': 0,
            'unresolved_input_ids': unresolved, 'input_accounting_complete': not reasons,
            'semantic_coverage': 'unknown', 'reasons': reasons,
            'saturated': document['saturated']}


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
