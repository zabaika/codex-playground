from __future__ import annotations

from collections import defaultdict
from typing import Any


class CandidateDraftReviewService:
    def build_review(
        self,
        *,
        draft: dict[str, Any],
        sources: list[dict[str, Any]],
        conflict_groups: dict[str, list[str]],
    ) -> dict[str, object]:
        source_by_artifact_id = {str(source["artifact_id"]): source for source in sources}
        field_conflicts = dict(draft.get("field_conflicts") or {})
        field_evidence = dict(draft.get("field_evidence") or {})
        missing_fields = list(draft.get("missing_fields") or [])
        field_names = sorted({*field_conflicts.keys(), *field_evidence.keys(), *missing_fields})
        return {
            "draft_id": draft.get("candidate_profile_draft_id") or draft.get("draft_id"),
            "candidate_id": draft.get("candidate_id"),
            "source_set_id": draft.get("source_set_id"),
            "draft_artifact_id": draft.get("draft_artifact_id"),
            "created_at": draft.get("created_at"),
            "sources": [self._source_summary(source) for source in sources],
            "conflict_groups": conflict_groups,
            "fields": [
                self._field_review(
                    field_name=field_name,
                    draft_payload=dict(draft.get("draft_payload") or {}),
                    conflict_entries=list(field_conflicts.get(field_name) or []),
                    evidence_entries=list(field_evidence.get(field_name) or []),
                    source_by_artifact_id=source_by_artifact_id,
                    is_missing=field_name in missing_fields,
                )
                for field_name in field_names
            ],
            "missing_fields": missing_fields,
            "intake_quality_issues": self._quality_issues(
                draft_payload=dict(draft.get("draft_payload") or {}),
                field_conflicts=field_conflicts,
                field_evidence=field_evidence,
            ),
            "confirm_contract": {
                "command": "confirm-draft",
                "accepted_field_values_format": "field.path=value",
                "mutates_canonical_profile": True,
            },
        }

    def _field_review(
        self,
        *,
        field_name: str,
        draft_payload: dict[str, Any],
        conflict_entries: list[dict[str, Any]],
        evidence_entries: list[dict[str, Any]],
        source_by_artifact_id: dict[str, dict[str, Any]],
        is_missing: bool,
    ) -> dict[str, object]:
        return {
            "field": field_name,
            "status": "missing" if is_missing else "conflict" if conflict_entries else "ready",
            "draft_value": self._payload_value(draft_payload, field_name),
            "accepted_value": self._payload_value(draft_payload, field_name) if not conflict_entries else None,
            "conflicts": [self._entry_with_source(entry, source_by_artifact_id) for entry in conflict_entries],
            "evidence": [self._entry_with_source(entry, source_by_artifact_id) for entry in evidence_entries],
        }

    def _entry_with_source(self, entry: dict[str, Any], source_by_artifact_id: dict[str, dict[str, Any]]) -> dict[str, object]:
        artifact_id = str(entry.get("artifact_id") or "")
        source = source_by_artifact_id.get(artifact_id, {})
        return {
            **entry,
            "artifact_id": artifact_id or None,
            "source_kind": source.get("source_kind"),
            "source_origin": source.get("source_origin"),
            "artifact_type": source.get("artifact_type"),
            "storage_path": source.get("storage_path"),
        }

    def _source_summary(self, source: dict[str, Any]) -> dict[str, object]:
        return {
            "artifact_id": source.get("artifact_id"),
            "artifact_type": source.get("artifact_type"),
            "source_kind": source.get("source_kind"),
            "source_origin": source.get("source_origin"),
            "storage_path": source.get("storage_path"),
        }

    def _payload_value(self, payload: dict[str, Any], field_name: str) -> object:
        if field_name in payload.get("core_profile", {}):
            return payload["core_profile"][field_name]
        current: object = payload
        for part in field_name.split("."):
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
        return current

    def _quality_issues(
        self,
        *,
        draft_payload: dict[str, Any],
        field_conflicts: dict[str, list[dict[str, Any]]],
        field_evidence: dict[str, list[dict[str, Any]]],
    ) -> list[dict[str, object]]:
        issues = []
        duplicate_fields = self._duplicate_evidence_fields(field_conflicts, field_evidence)
        if duplicate_fields:
            issues.append(
                {
                    "code": "duplicate_evidence_entries",
                    "severity": "warn",
                    "fields": duplicate_fields,
                    "message": "Some review fields contain repeated evidence from the same source artifact.",
                }
            )
        ambiguous_profiles = self._ambiguous_external_profiles(draft_payload)
        if ambiguous_profiles:
            issues.append(
                {
                    "code": "ambiguous_external_profiles",
                    "severity": "warn",
                    "profiles": ambiguous_profiles,
                    "message": "Multiple external profile URLs look ambiguous and should be checked before confirm-draft.",
                }
            )
        return issues

    def _duplicate_evidence_fields(
        self,
        field_conflicts: dict[str, list[dict[str, Any]]],
        field_evidence: dict[str, list[dict[str, Any]]],
    ) -> list[str]:
        duplicate_fields: list[str] = []
        for field, entries in {**field_conflicts, **field_evidence}.items():
            seen: set[tuple[str, str]] = set()
            for entry in entries:
                key = (str(entry.get("artifact_id") or ""), str(entry.get("value") or ""))
                if key in seen:
                    duplicate_fields.append(field)
                    break
                seen.add(key)
        return sorted(set(duplicate_fields))

    def _ambiguous_external_profiles(self, draft_payload: dict[str, Any]) -> list[dict[str, object]]:
        profiles = [profile for profile in draft_payload.get("external_profiles", []) if isinstance(profile, dict)]
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        primary_by_platform: dict[str, int] = defaultdict(int)
        for profile in profiles:
            platform = str(profile.get("platform") or "").strip().lower()
            handle = str(profile.get("handle_or_slug") or "").strip().lower()
            url = str(profile.get("profile_url") or "").strip().lower()
            if profile.get("is_primary"):
                primary_by_platform[platform] += 1
            if platform and handle:
                grouped[(platform, handle)].append(profile)
            elif platform == "other" and url:
                grouped[(platform, url)].append(profile)
        ambiguous: list[dict[str, object]] = []
        for (platform, handle), records in grouped.items():
            urls = sorted({str(record.get("profile_url")) for record in records if record.get("profile_url")})
            if len(urls) > 1:
                ambiguous.append({"platform": platform, "handle_or_url": handle, "urls": urls})
        for platform, count in primary_by_platform.items():
            if platform and count > 1:
                ambiguous.append({"platform": platform, "issue": "multiple_primary_profiles", "count": count})
        return ambiguous
