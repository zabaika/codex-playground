"""Versioned evidence schemas, source snapshots and authoritative support validation."""

import copy
from datetime import datetime
import hashlib
import json
import re
import uuid

if __package__:
    from .analysis_store import canonical_json
    from . import analysis_settings
else:
    from analysis_store import canonical_json
    import analysis_settings

from telegram_shared.formatting import build_message_link

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


def verify_analysis_sources(store, job_id, result, *, generation):
    """Resolve application-owned provenance; this does not prove semantic entailment."""
    job = store.require_running(job_id, generation=generation)
    limit = analysis_settings._synthesis_limits(job)["sources_per_item"]
    verified = copy.deepcopy(result)
    verified.pop("sources", None)
    answer_pairs = _qa_answer_pairs(store.iter_link_decisions(job_id)) if job["command"] == "qa" else set()
    for item in verified.get("items", []):
        sources, records = {}, {}
        for identity in item["evidence_ids"]:
            record = store.leaf_evidence(job_id, identity)
            if record is None or not record["payload"]["source_refs"]:
                raise ValueError("final_evidence_scope")
            records[identity] = record["payload"]
            for ref in record["payload"]["source_refs"]:
                source = _verified_analysis_source(store, job, ref, supporting=True)
                pair = (source["channel_id"], source["message_id"])
                if pair not in sources or len(source["quote"]) > len(sources[pair]["quote"]):
                    sources[pair] = source
        if not sources:
            raise ValueError("final_evidence_without_support")
        if job["command"] == "qa":
            _validate_qa_support(item["evidence_ids"], records, answer_pairs)
        # Keep the latest update; remaining slots retain substantive backing
        # rather than letting a chain of short acknowledgements displace it.
        ordered = sorted(sources.values(), key=lambda s: (
            -datetime.fromisoformat(s["date_utc"]).timestamp() if job["command"] == "topics" else 0,
            -len(s["quote"]), s["channel_id"], s["message_id"]))
        if job["command"] == "topics":
            ordered = ordered[:1] + sorted(ordered[1:], key=lambda s: -len(s["quote"]))
        item["sources"] = ordered[:limit]
    if not verified.get("items"):
        verified["sources"] = [_verified_analysis_source(store, job, ref, supporting=False)
            for ref in store.processed_source_refs(job_id, limit=limit)]
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
