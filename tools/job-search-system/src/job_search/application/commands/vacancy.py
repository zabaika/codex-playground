from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class VacancyImportItem:
    title: str
    company_name: str
    location_text: str | None = None
    source_url: str | None = None
    external_vacancy_id: str | None = None
    source_published_at: str | None = None
    source_updated_at: str | None = None
    raw_text: str | None = None


@dataclass(frozen=True, slots=True)
class ImportVacancyBatch:
    candidate_id: str
    source_kind: str
    items: list[VacancyImportItem] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class CreateVacancyUrlEnrichmentSeed:
    candidate_id: str
    source_url: str
    platform: str | None = None
    source_origin: str = "saved_url"
    notes: str | None = None
    idempotency_key: str | None = None


@dataclass(frozen=True, slots=True)
class PreviewVacancyUrlEnrichmentSeed:
    candidate_id: str
    url_seed_id: str
    content_text: str
    source_origin: str = "manual_page_text"


@dataclass(frozen=True, slots=True)
class ConfirmVacancyUrlEnrichmentImport:
    candidate_id: str
    url_seed_id: str
    source_kind: str | None = None


@dataclass(frozen=True, slots=True)
class RejectVacancyUrlEnrichmentSeed:
    candidate_id: str
    url_seed_id: str
    rejection_reason: str | None = None


@dataclass(frozen=True, slots=True)
class MarkVacancyProcessed:
    candidate_id: str
    canonical_vacancy_id: str


@dataclass(frozen=True, slots=True)
class UpdateVacancyWorkflowStage:
    candidate_id: str
    canonical_vacancy_id: str
    workflow_stage: str


@dataclass(frozen=True, slots=True)
class CreateApplicationDraft:
    candidate_id: str
    canonical_vacancy_id: str
    language: str = "en"
    target_role: str | None = None


@dataclass(frozen=True, slots=True)
class ShortlistVacancy:
    candidate_id: str
    canonical_vacancy_id: str


@dataclass(frozen=True, slots=True)
class PrepareApplicationPayload:
    candidate_id: str
    canonical_vacancy_id: str
    language: str = "en"
    target_role: str | None = None


@dataclass(frozen=True, slots=True)
class GenerateVacancyResume:
    candidate_id: str
    canonical_vacancy_id: str
    language: str = "en"
    source_resume_artifact_id: str | None = None


@dataclass(frozen=True, slots=True)
class FinalizeVacancyResume:
    candidate_id: str
    artifact_id: str
    allow_warnings: bool = False


@dataclass(frozen=True, slots=True)
class CreateTouchpoint:
    candidate_id: str
    canonical_vacancy_id: str
    application_id: str | None = None
    message_artifact_id: str | None = None
    channel: str = "email"
    direction: str = "outgoing"
    touchpoint_state: str = "sent"
    contact_name: str | None = None
    occurred_at: str | None = None
    notes: str | None = None
    follow_up_due_at: str | None = None


@dataclass(frozen=True, slots=True)
class UpdateTouchpointState:
    candidate_id: str
    touchpoint_id: str
    touchpoint_state: str
    replied_at: str | None = None


@dataclass(frozen=True, slots=True)
class ResolveReminder:
    candidate_id: str
    reminder_id: str


@dataclass(frozen=True, slots=True)
class RecordManualBoardAction:
    candidate_id: str
    platform: str
    action_type: str
    action_state: str = "completed"
    canonical_vacancy_id: str | None = None
    application_id: str | None = None
    artifact_id: str | None = None
    external_target: str | None = None
    occurred_at: str | None = None
    notes: str | None = None
    idempotency_key: str | None = None
    external_action_approval_id: str | None = None


@dataclass(frozen=True, slots=True)
class RecordArtifactAcceptance:
    candidate_id: str
    artifact_id: str
    approval_state: str = "accepted"
    actor: str = "operator"
    reason: str | None = None
    notes: str | None = None
    idempotency_key: str | None = None


@dataclass(frozen=True, slots=True)
class RecordExternalActionApproval:
    candidate_id: str
    platform: str
    action_type: str
    approval_state: str = "approved"
    actor: str = "operator"
    artifact_id: str | None = None
    canonical_vacancy_id: str | None = None
    application_id: str | None = None
    external_target: str | None = None
    reason: str | None = None
    notes: str | None = None
    idempotency_key: str | None = None


@dataclass(frozen=True, slots=True)
class CreateInterviewRound:
    candidate_id: str
    application_id: str
    round_type: str
    round_state: str = "scheduled"
    scheduled_at: str | None = None
    interviewer_name: str | None = None
    notes: str | None = None
    idempotency_key: str | None = None


@dataclass(frozen=True, slots=True)
class UpdateInterviewRoundState:
    candidate_id: str
    interview_round_id: str
    round_state: str
    completed_at: str | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class ResolveReconciliationItem:
    candidate_id: str
    reconciliation_item_id: str
    review_status: str = "resolved"
    resolution_notes: str | None = None
