#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from render_common import (
    normalize_anonymization_mapping,
    normalize_peer_reviews,
    normalize_string_list,
    payload_cleanup_enabled,
    require_object,
    sanitize_optional_text,
    sanitize_required_text,
    sanitize_run_status,
    validate_anonymization_mapping,
    validate_council_run_status,
)


def normalize_advisors(value, cleanup_enabled: bool = True):
    if not isinstance(value, list) or not value:
        raise ValueError("Field 'advisors' must be a non-empty list")
    advisors = []
    for idx, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Advisor #{idx} must be an object")
        advisors.append(
            {
                "name": sanitize_required_text(item, "name", enabled=False),
                "response": sanitize_required_text(item, "response", enabled=cleanup_enabled),
                "headline": sanitize_required_text(item, "headline", enabled=cleanup_enabled),
                "stance": sanitize_required_text(item, "stance", enabled=cleanup_enabled),
            }
        )
    return advisors


def normalize_council_payload(
    data: dict,
    *,
    cleanup_enabled: bool | None = None,
    default_payload_source: str | None = None,
):
    note_type = sanitize_required_text(data, "type", enabled=False)
    if note_type != "council-verdict":
        raise ValueError("Field 'type' must be `council-verdict`")
    verdict = require_object(data, "verdict")
    if cleanup_enabled is None:
        cleanup_enabled = payload_cleanup_enabled()

    payload_source = default_payload_source or sanitize_optional_text(
        data, "payload_source", enabled=cleanup_enabled
    )
    if not payload_source:
        raise ValueError("Missing required text field: payload_source")
    if "peer_reviews" not in data:
        raise ValueError("Missing required field: peer_reviews")

    payload = {
        "type": note_type,
        "title": sanitize_required_text(data, "title", enabled=cleanup_enabled),
        "timestamp": sanitize_required_text(data, "timestamp", enabled=cleanup_enabled),
        "year": sanitize_optional_text(data, "year", enabled=cleanup_enabled),
        "payload_source": payload_source,
        "question": sanitize_required_text(data, "question", enabled=cleanup_enabled),
        "framed_question": sanitize_required_text(
            data, "framed_question", enabled=cleanup_enabled
        ),
        "related_notes": normalize_string_list(data.get("related_notes"), "related_notes"),
        "run_status": sanitize_run_status(data.get("run_status"), cleanup_enabled),
        "verdict": {
            "agrees": sanitize_required_text(verdict, "agrees", enabled=cleanup_enabled),
            "clashes": sanitize_required_text(verdict, "clashes", enabled=cleanup_enabled),
            "blind_spots": sanitize_required_text(
                verdict, "blind_spots", enabled=cleanup_enabled
            ),
            "recommendation": sanitize_required_text(
                verdict, "recommendation", enabled=cleanup_enabled
            ),
            "first_step": sanitize_required_text(
                verdict, "first_step", enabled=cleanup_enabled
            ),
        },
        "advisors": normalize_advisors(data.get("advisors"), cleanup_enabled),
        "peer_reviews": normalize_peer_reviews(data.get("peer_reviews"), cleanup_enabled),
        "anonymization_mapping": normalize_anonymization_mapping(
            data.get("anonymization_mapping")
        ),
    }
    payload["anonymization_mapping"] = validate_anonymization_mapping(
        payload["anonymization_mapping"], payload["advisors"]
    )
    payload["run_status"] = validate_council_run_status(
        payload["run_status"], payload["advisors"], payload["peer_reviews"]
    )
    return payload


def load_council_payload(path: Path, *, cleanup_enabled: bool | None = None):
    data = json.loads(path.read_text(encoding="utf-8"))
    return normalize_council_payload(data, cleanup_enabled=cleanup_enabled)
