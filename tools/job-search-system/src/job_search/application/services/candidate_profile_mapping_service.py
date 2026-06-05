from __future__ import annotations

from typing import Any

from job_search.application.services.evidence_normalization import dedupe_record_list


class CandidateProfileMappingService:
    CORE_FIELDS = {
        "full_name",
        "primary_email",
        "primary_phone",
        "current_location",
        "current_title",
        "summary_text",
    }

    def resolve_confirmed_payload(
        self,
        *,
        draft_payload: dict[str, Any],
        conflicts: dict[str, list[dict[str, Any]]],
        accepted_field_values: dict[str, Any],
    ) -> dict[str, Any]:
        core_profile = dict(draft_payload.get("core_profile", {}))
        resolved_payload = {
            "external_profiles": self._dedupe_draft_records(draft_payload.get("external_profiles", [])),
            "languages": self._dedupe_draft_records(draft_payload.get("languages", [])),
            "work_authorizations": self._dedupe_draft_records(draft_payload.get("work_authorizations", [])),
            "experience_entries": self._dedupe_draft_records(draft_payload.get("experience_entries", [])),
            "achievement_evidence": self._dedupe_draft_records(draft_payload.get("achievement_evidence", [])),
            "education_entries": self._dedupe_draft_records(draft_payload.get("education_entries", [])),
            "skill_signals": self._dedupe_draft_records(draft_payload.get("skill_signals", [])),
            "recommendations": self._dedupe_draft_records(draft_payload.get("recommendations", [])),
            "certifications": self._dedupe_draft_records(draft_payload.get("certifications", [])),
            "publications": self._dedupe_draft_records(draft_payload.get("publications", [])),
            "awards": self._dedupe_draft_records(draft_payload.get("awards", [])),
            "targets": dict(draft_payload.get("targets", {})),
            "compensation": dict(draft_payload.get("compensation", {})),
            "platform_preferences": dict(draft_payload.get("platform_preferences", {})),
            "search_preferences": dict(draft_payload.get("search_preferences", {})),
        }
        for field, value in accepted_field_values.items():
            if field in self.CORE_FIELDS:
                core_profile[field] = value
                continue
            if "." in field:
                section, key = field.split(".", 1)
                target = resolved_payload.get(section)
                if isinstance(target, dict):
                    target[key] = value

        for field in list(conflicts.keys()):
            if field not in accepted_field_values and field in self.CORE_FIELDS:
                default_value = self._default_conflict_value(field, conflicts[field])
                if default_value not in (None, "", []):
                    core_profile[field] = default_value
                else:
                    core_profile.pop(field, None)

        return {
            "core_profile": {k: v for k, v in core_profile.items() if k in self.CORE_FIELDS},
            **resolved_payload,
        }

    def _dedupe_draft_records(self, value: object) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return dedupe_record_list([dict(item) for item in value if isinstance(item, dict)])

    def _default_conflict_value(self, field: str, entries: list[dict[str, Any]]) -> Any | None:
        counts: dict[str, tuple[int, Any]] = {}
        for entry in entries:
            value = entry.get("value")
            if value in (None, "", []):
                continue
            key = str(value).strip()
            count, _ = counts.get(key, (0, value))
            counts[key] = (count + 1, value)
        if counts:
            ranked = sorted(counts.values(), key=lambda item: item[0], reverse=True)
            if len(ranked) == 1 or ranked[0][0] > ranked[1][0]:
                return ranked[0][1]

        preferred_source_order = {
            "full_name": ("resume", "linkedin", "profile"),
            "current_title": ("resume", "linkedin", "profile"),
            "current_location": ("profile", "linkedin", "resume"),
            "summary_text": ("resume", "linkedin", "profile"),
        }
        for source_kind in preferred_source_order.get(field, ()):
            for entry in entries:
                if entry.get("source_kind") == source_kind and entry.get("value") not in (None, "", []):
                    return entry["value"]
        return None
