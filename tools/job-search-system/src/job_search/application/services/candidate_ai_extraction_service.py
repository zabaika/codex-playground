from __future__ import annotations

from hashlib import sha256
from typing import Any

from job_search.application.dto.candidate_profile_draft import CandidateProfileDraftDTO
from job_search.application.services.evidence_normalization import dedupe_evidence_map
from job_search.domain.enums import FieldStatus


class CandidateAiExtractionService:
    ENVELOPE_KEYS = {"candidate_id", "source_set_id", "draft_payload", "field_conflicts", "field_evidence", "missing_fields"}
    DRAFT_KEYS = {
        "core_profile",
        "languages",
        "external_profiles",
        "work_authorizations",
        "experience_entries",
        "achievement_evidence",
        "education_entries",
        "skill_signals",
        "recommendations",
        "certifications",
        "publications",
        "awards",
        "targets",
        "compensation",
        "platform_preferences",
        "search_preferences",
        "field_statuses",
    }
    CORE_PROFILE_KEYS = {
        "full_name",
        "primary_email",
        "primary_phone",
        "current_location",
        "current_title",
        "summary_text",
    }
    FIELD_STATUS_VALUES = {status.value for status in FieldStatus}

    LIST_FIELDS = {
        "languages",
        "external_profiles",
        "work_authorizations",
        "experience_entries",
        "achievement_evidence",
        "education_entries",
        "skill_signals",
        "recommendations",
        "certifications",
        "publications",
        "awards",
    }
    DICT_FIELDS = {
        "core_profile",
        "targets",
        "compensation",
        "platform_preferences",
        "search_preferences",
        "field_statuses",
    }

    def build_request(self, *, candidate_id: str, sources: list[dict[str, str]]) -> dict[str, object]:
        return {
            "task": "extract_candidate_profile_draft",
            "mode": "draft_only_no_state_mutation",
            "candidate_id": candidate_id,
            "source_set_id": self.source_set_id(sources),
            "source_artifacts": [
                {
                    "artifact_id": source["artifact_id"],
                    "artifact_type": source["artifact_type"],
                    "source_kind": source["source_kind"],
                    "content_text": source["content_text"],
                }
                for source in sources
            ],
            "output_contract": {
                "return_json_only": True,
                "required_envelope_keys": sorted(self.ENVELOPE_KEYS),
                "allowed_draft_payload_keys": sorted(self.DRAFT_KEYS),
                "allowed_core_profile_keys": sorted(self.CORE_PROFILE_KEYS),
                "field_status_values": sorted(self.FIELD_STATUS_VALUES),
                "rules": [
                    "Do not invent facts, metrics, dates, companies, credentials, or profile URLs.",
                    "Use only source_artifacts artifact_id values in field_evidence and source_artifact_id fields.",
                    "Put uncertain values into field_conflicts or missing_fields instead of confirming them.",
                    "Return a draft only; do not claim that canonical candidate state was updated.",
                ],
            },
        }

    def validate_response(
        self,
        *,
        candidate_id: str,
        expected_source_set_id: str,
        allowed_source_artifact_ids: set[str],
        response_payload: dict[str, Any],
    ) -> CandidateProfileDraftDTO:
        self._reject_unknown_keys("ai_extraction_response", response_payload, self.ENVELOPE_KEYS)
        if response_payload.get("candidate_id") != candidate_id:
            raise ValueError("AI extraction response candidate_id does not match requested candidate")
        if response_payload.get("source_set_id") != expected_source_set_id:
            raise ValueError("AI extraction response source_set_id does not match selected source artifacts")

        draft_payload = self._normalize_draft_payload(response_payload.get("draft_payload"))
        field_conflicts = self._require_dict(response_payload.get("field_conflicts", {}), "field_conflicts")
        field_evidence = self._require_dict(response_payload.get("field_evidence", {}), "field_evidence")
        missing_fields = self._require_string_list(response_payload.get("missing_fields", []), "missing_fields")
        self._validate_source_references(draft_payload, field_conflicts, field_evidence, allowed_source_artifact_ids)
        return CandidateProfileDraftDTO(
            candidate_id=candidate_id,
            source_set_id=expected_source_set_id,
            draft_payload=draft_payload,
            field_conflicts=field_conflicts,
            field_evidence=dedupe_evidence_map(field_evidence),
            missing_fields=missing_fields,
        )

    def source_set_id(self, sources: list[dict[str, str]]) -> str:
        return sha256("|".join(sorted(source["artifact_id"] for source in sources)).encode("utf-8")).hexdigest()

    def _normalize_draft_payload(self, raw_payload: object) -> dict[str, Any]:
        draft_payload = self._require_dict(raw_payload, "draft_payload")
        self._reject_unknown_keys("draft_payload", draft_payload, self.DRAFT_KEYS)
        normalized: dict[str, Any] = {}
        for field in self.LIST_FIELDS:
            normalized[field] = self._require_list(draft_payload.get(field, []), field)
        for field in self.DICT_FIELDS:
            normalized[field] = self._require_dict(draft_payload.get(field, {}), field)
        self._reject_unknown_keys("core_profile", normalized["core_profile"], self.CORE_PROFILE_KEYS)
        for field, status in normalized["field_statuses"].items():
            if status not in self.FIELD_STATUS_VALUES:
                raise ValueError(f"Unsupported field_status for {field}: {status}")
        return normalized

    def _validate_source_references(
        self,
        draft_payload: dict[str, Any],
        field_conflicts: dict[str, object],
        field_evidence: dict[str, object],
        allowed_source_artifact_ids: set[str],
    ) -> None:
        for field_name, entries in field_evidence.items():
            for entry in self._require_list(entries, f"field_evidence.{field_name}"):
                self._validate_artifact_id_reference(entry, allowed_source_artifact_ids, f"field_evidence.{field_name}")
        for field_name, entries in field_conflicts.items():
            for entry in self._require_list(entries, f"field_conflicts.{field_name}"):
                self._validate_artifact_id_reference(entry, allowed_source_artifact_ids, f"field_conflicts.{field_name}")
        for field in self.LIST_FIELDS:
            for idx, entry in enumerate(draft_payload[field]):
                if isinstance(entry, dict) and entry.get("source_artifact_id"):
                    self._validate_artifact_id_reference(
                        entry,
                        allowed_source_artifact_ids,
                        f"draft_payload.{field}[{idx}]",
                    )

    def _validate_artifact_id_reference(self, entry: object, allowed_source_artifact_ids: set[str], location: str) -> None:
        if not isinstance(entry, dict):
            raise ValueError(f"{location} entries must be objects")
        artifact_id = entry.get("artifact_id") or entry.get("source_artifact_id")
        if artifact_id is None:
            return
        if str(artifact_id) not in allowed_source_artifact_ids:
            raise ValueError(f"{location} references source artifact outside selected source set: {artifact_id}")

    def _reject_unknown_keys(self, location: str, payload: dict[str, object], allowed_keys: set[str]) -> None:
        unknown = set(payload) - allowed_keys
        if unknown:
            raise ValueError(f"{location} contains unsupported fields: {', '.join(sorted(unknown))}")

    def _require_dict(self, value: object, location: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError(f"{location} must be an object")
        return dict(value)

    def _require_list(self, value: object, location: str) -> list[Any]:
        if not isinstance(value, list):
            raise ValueError(f"{location} must be a list")
        return list(value)

    def _require_string_list(self, value: object, location: str) -> list[str]:
        return [str(item) for item in self._require_list(value, location) if str(item).strip()]
