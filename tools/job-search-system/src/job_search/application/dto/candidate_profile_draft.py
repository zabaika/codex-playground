from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class CandidateProfileDraftDTO:
    candidate_id: str
    source_set_id: str
    draft_payload: dict[str, Any]
    field_conflicts: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    field_evidence: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    missing_fields: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class CandidateSourceRegistrationDTO:
    candidate_id: str
    source_kind: str
    source_origin: str
    content_text: str | None = None
    file_path: str | None = None
    source_url: str | None = None
    existing_artifact_id: str | None = None
    external_profile_id: str | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class CandidateProfileConfirmRequestDTO:
    candidate_id: str
    draft_id: str
    accepted_field_values: dict[str, Any]
