from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ListVacancies:
    candidate_id: str
    processed: bool | None = None
    workflow_stage: str | None = None


@dataclass(frozen=True, slots=True)
class ListVacancyUrlEnrichmentSeeds:
    candidate_id: str
    seed_status: str | None = None
    platform: str | None = None


@dataclass(frozen=True, slots=True)
class GetVacancy:
    candidate_id: str
    canonical_vacancy_id: str


@dataclass(frozen=True, slots=True)
class ListRankedVacancies:
    candidate_id: str
    processed: bool | None = False


@dataclass(frozen=True, slots=True)
class ListDailyActions:
    candidate_id: str


@dataclass(frozen=True, slots=True)
class ListMaterialChangeReview:
    candidate_id: str


@dataclass(frozen=True, slots=True)
class ListTouchpoints:
    candidate_id: str
    canonical_vacancy_id: str | None = None
    application_id: str | None = None


@dataclass(frozen=True, slots=True)
class GetPipelineReport:
    candidate_id: str


@dataclass(frozen=True, slots=True)
class ListManualBoardActions:
    candidate_id: str
    platform: str | None = None
    canonical_vacancy_id: str | None = None


@dataclass(frozen=True, slots=True)
class ListReconciliationItems:
    candidate_id: str
    review_status: str | None = None
    outcome: str | None = None
    platform: str | None = None


@dataclass(frozen=True, slots=True)
class ListApprovals:
    candidate_id: str
    approval_type: str | None = None
    artifact_id: str | None = None


@dataclass(frozen=True, slots=True)
class ListInterviewRounds:
    candidate_id: str
    application_id: str | None = None
    canonical_vacancy_id: str | None = None


@dataclass(frozen=True, slots=True)
class GetBoardChecklist:
    candidate_id: str
    platform: str
    canonical_vacancy_id: str | None = None
