#!/usr/bin/env python3

from pathlib import Path
import re
import tomllib


def require_text(data: dict, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing required text field: {key}")
    return value.strip()


def optional_text(data: dict, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        return ""
    return value.strip()


def require_object(data: dict, key: str) -> dict:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"Field '{key}' must be an object")
    return value


def normalize_string_list(value, field_name: str):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"Field '{field_name}' must be a list")
    result = []
    for idx, item in enumerate(value, start=1):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"Field '{field_name}' item #{idx} must be non-empty text")
        result.append(item.strip())
    return result


def normalize_text(value) -> str:
    if not isinstance(value, str):
        return ""
    lines = [line.rstrip() for line in value.strip().splitlines()]
    return "\n".join(lines).strip()


def resolve_skill_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def payload_cleanup_enabled(skill_dir: Path | None = None) -> bool:
    base_dir = skill_dir or resolve_skill_dir()
    config_path = base_dir / "config" / "runtime.local.toml"
    if not config_path.exists():
        return True
    try:
        data = tomllib.loads(config_path.read_text())
    except Exception:
        return True
    section = data.get("payload_cleanup")
    if not isinstance(section, dict):
        return True
    enabled = section.get("enabled")
    return enabled if isinstance(enabled, bool) else True


def sanitize_payload_text(text: str, enabled: bool = True) -> str:
    normalized = normalize_text(text)
    if not enabled or not normalized:
        return normalized

    sanitized = normalized
    sanitized = re.sub(r"```[^\n]*\n(.*?)```", lambda m: m.group(1).strip(), sanitized, flags=re.DOTALL)
    sanitized = re.sub(r"\[\[([^|\]]+)\|([^\]]+)\]\]", r"\2", sanitized)
    sanitized = re.sub(r"\[\[([^\]]+)\]\]", r"\1", sanitized)
    sanitized = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", sanitized)
    sanitized = re.sub(r"(?m)^(#{1,6})\s+", "", sanitized)
    sanitized = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", sanitized)
    sanitized = re.sub(r"__([^_\n]+)__", r"\1", sanitized)
    sanitized = re.sub(r"`([^`\n]+)`", r"\1", sanitized)
    sanitized = re.sub(r"[ \t]+\n", "\n", sanitized)
    sanitized = re.sub(r"\n{3,}", "\n\n", sanitized)
    sanitized = sanitized.strip()
    return sanitized or normalized


def sanitize_required_text(data: dict, key: str, enabled: bool = True) -> str:
    raw = require_text(data, key)
    return sanitize_payload_text(raw, enabled=enabled)


def sanitize_optional_text(data: dict, key: str, enabled: bool = True) -> str:
    raw = optional_text(data, key)
    return sanitize_payload_text(raw, enabled=enabled)


def expand_inline_ordered_list(text: str) -> str:
    normalized = normalize_text(text)
    if not normalized.startswith("1. "):
        return normalized
    return re.sub(r"\s(?=(\d+)\.\s)", "\n", normalized)


def text_lines(text: str):
    normalized = normalize_text(text)
    return normalized.splitlines() if normalized else []


def ordered_text_lines(text: str):
    normalized = expand_inline_ordered_list(text)
    return normalized.splitlines() if normalized else []


def normalize_peer_reviews(value, cleanup_enabled: bool = True):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("Field 'peer_reviews' must be a list")
    reviews = []
    for idx, item in enumerate(value, start=1):
        if isinstance(item, str) and item.strip():
            reviews.append(
                {
                    "reviewer": f"Reviewer {idx}",
                    "response": sanitize_payload_text(item.strip(), enabled=cleanup_enabled),
                }
            )
            continue
        if not isinstance(item, dict):
            raise ValueError(f"Peer review #{idx} must be text or an object")
        reviewer = item.get("reviewer", f"Reviewer {idx}")
        response = sanitize_required_text(item, "response", enabled=cleanup_enabled)
        if not isinstance(reviewer, str) or not reviewer.strip():
            raise ValueError(f"Peer review #{idx} field 'reviewer' must be non-empty text")
        reviews.append({"reviewer": reviewer.strip(), "response": response})
    return reviews


def normalize_anonymization_mapping(value):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("Field 'anonymization_mapping' must be a list")
    mapping = []
    for idx, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Anonymization mapping #{idx} must be an object")
        label = require_text(item, "label")
        advisor = require_text(item, "advisor")
        mapping.append({"label": label, "advisor": advisor})
    return mapping


def validate_anonymization_mapping(mapping, advisors):
    advisor_names = [advisor["name"] for advisor in advisors]
    if len(set(advisor_names)) != len(advisor_names):
        raise ValueError("Advisor names must be unique before anonymization mapping is validated")
    if not advisor_names:
        return mapping
    if not mapping:
        raise ValueError(
            "Field 'anonymization_mapping' must fully cover the advisor set for council payloads"
        )

    labels = [item["label"] for item in mapping]
    mapped_advisors = [item["advisor"] for item in mapping]

    if len(set(labels)) != len(labels):
        raise ValueError("Field 'anonymization_mapping' must not contain duplicate labels")
    if len(set(mapped_advisors)) != len(mapped_advisors):
        raise ValueError("Field 'anonymization_mapping' must not contain duplicate advisor names")

    expected_labels = [f"Response {chr(ord('A') + idx)}" for idx in range(len(advisor_names))]
    if labels != expected_labels:
        raise ValueError(
            "Field 'anonymization_mapping' must use the exact ordered label set "
            + ", ".join(expected_labels)
        )

    advisor_set = set(advisor_names)
    mapped_set = set(mapped_advisors)
    unknown_advisors = sorted(mapped_set - advisor_set)
    if unknown_advisors:
        raise ValueError(
            "Field 'anonymization_mapping' references unknown advisors: "
            + ", ".join(unknown_advisors)
        )

    missing_advisors = sorted(advisor_set - mapped_set)
    if missing_advisors:
        raise ValueError(
            "Field 'anonymization_mapping' is missing advisors: "
            + ", ".join(missing_advisors)
        )

    return mapping


def normalize_verdict(value):
    if not isinstance(value, dict):
        raise ValueError("Field 'verdict' must be an object")
    return {
        "agrees": require_text(value, "agrees"),
        "clashes": require_text(value, "clashes"),
        "blind_spots": require_text(value, "blind_spots"),
        "recommendation": require_text(value, "recommendation"),
        "first_step": require_text(value, "first_step"),
    }


def sanitize_verdict(value, cleanup_enabled: bool = True):
    if not isinstance(value, dict):
        raise ValueError("Field 'verdict' must be an object")
    return {
        "agrees": sanitize_required_text(value, "agrees", enabled=cleanup_enabled),
        "clashes": sanitize_required_text(value, "clashes", enabled=cleanup_enabled),
        "blind_spots": sanitize_required_text(value, "blind_spots", enabled=cleanup_enabled),
        "recommendation": sanitize_required_text(
            value, "recommendation", enabled=cleanup_enabled
        ),
        "first_step": sanitize_required_text(value, "first_step", enabled=cleanup_enabled),
    }


def normalize_run_status(value):
    if value is None:
        return {"status": "full", "details": ""}
    if not isinstance(value, dict):
        raise ValueError("Field 'run_status' must be an object")
    status = optional_text(value, "status").lower() or "full"
    if status not in {"full", "degraded"}:
        raise ValueError("Field 'run_status.status' must be 'full' or 'degraded'")
    details = optional_text(value, "details")
    return {"status": status, "details": details}


def sanitize_run_status(value, cleanup_enabled: bool = True):
    result = normalize_run_status(value)
    result["details"] = sanitize_payload_text(result["details"], enabled=cleanup_enabled)
    return result


def validate_council_run_status(run_status, advisors, peer_reviews):
    advisor_count = len(advisors)
    has_peer_reviews = bool(peer_reviews)
    status = run_status["status"]
    details = run_status["details"]

    if status == "full":
        if advisor_count != 5:
            raise ValueError(
                "Field 'run_status' may be `full` only when exactly 5 advisor responses were completed"
            )
        if not has_peer_reviews:
            raise ValueError(
                "Field 'run_status' may be `full` only when peer review responses are present"
            )
        return run_status

    if status == "degraded" and not details:
        raise ValueError(
            "Field 'run_status.details' must explain why the run was degraded"
        )
    return run_status


def parse_allowed_roots(values):
    roots = []
    for raw in values:
        root = Path(raw).expanduser()
        if not root.is_absolute():
            raise ValueError(f"Allowed root must be an absolute path: {raw}")
        if not root.exists() or not root.is_dir():
            raise ValueError(f"Allowed root does not exist as a directory: {root}")
        roots.append(root.resolve())
    return roots


def validate_output_path(output_path: Path, allowed_roots):
    resolved_output = output_path.expanduser()
    if not resolved_output.is_absolute():
        raise ValueError(f"Output path must be absolute: {resolved_output}")
    parent = resolved_output.parent
    if not parent.exists() or not parent.is_dir():
        raise ValueError(f"Parent output directory must already exist: {parent}")
    resolved_output = resolved_output.resolve(strict=False)
    if allowed_roots:
        if not any(
            resolved_output == root or root in resolved_output.parents for root in allowed_roots
        ):
            allowed = ", ".join(str(root) for root in allowed_roots)
            raise ValueError(
                f"Output path must stay under one of the allowed roots: {allowed}"
            )
    return resolved_output
