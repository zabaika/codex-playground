"""Versioned analytical output shapes; application metadata is added after validation."""

import copy
import json


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


def relation_format():
    return {"type": "json_schema", "name": "telegram_relations_v1", "strict": True, "schema": copy.deepcopy(RELATION_SCHEMA)}


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
