from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class CreateCandidate:
    display_name: str


@dataclass(frozen=True, slots=True)
class SetActiveCandidate:
    candidate_id: str


@dataclass(frozen=True, slots=True)
class RegisterCandidateSource:
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
class GenerateCandidateProfileDraftFromSources:
    candidate_id: str
    source_artifact_ids: list[str] | None = None


@dataclass(frozen=True, slots=True)
class BuildCandidateAiExtractionRequest:
    candidate_id: str
    source_artifact_ids: list[str] | None = None


@dataclass(frozen=True, slots=True)
class ImportCandidateAiExtractionDraft:
    candidate_id: str
    response_payload: dict[str, Any]
    source_artifact_ids: list[str] | None = None


@dataclass(frozen=True, slots=True)
class ConfirmCandidateProfileDraft:
    candidate_id: str
    draft_id: str
    accepted_field_values: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class UpdateCandidateTargets:
    candidate_id: str
    target_roles: list[str] = field(default_factory=list)
    target_markets: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class UpdateCandidateCompensation:
    candidate_id: str
    salary_floor: int | None = None
    salary_target: int | None = None
    salary_aspiration: int | None = None
    currency: str | None = None
    compensation_notes: str | None = None


@dataclass(frozen=True, slots=True)
class GenerateResumeMarkdown:
    candidate_id: str
    language: str = "en"
    target_role: str | None = None


@dataclass(frozen=True, slots=True)
class RunResumeQualityGate:
    artifact_id: str


@dataclass(frozen=True, slots=True)
class FinalizeResumeMarkdown:
    artifact_id: str
    allow_warnings: bool = False


@dataclass(frozen=True, slots=True)
class GenerateResumeRoastReport:
    artifact_id: str
    target_role: str | None = None


@dataclass(frozen=True, slots=True)
class GenerateResumePositioningBrief:
    candidate_id: str
    target_role: str
    language: str = "en"


@dataclass(frozen=True, slots=True)
class GenerateCareerPathingLite:
    candidate_id: str
    target_roles: list[str] | None = None


@dataclass(frozen=True, slots=True)
class GenerateCareerPathingFull:
    candidate_id: str
    target_roles: list[str] | None = None
    include_kb: bool = True


@dataclass(frozen=True, slots=True)
class GenerateJobSearchPlaybook:
    candidate_id: str
