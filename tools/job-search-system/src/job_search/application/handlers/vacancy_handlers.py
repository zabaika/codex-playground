from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import json
import uuid

from job_search.application.commands.vacancy import (
    ConfirmVacancyUrlEnrichmentImport,
    CreateApplicationDraft,
    CreateInterviewRound,
    CreateTouchpoint,
    CreateVacancyUrlEnrichmentSeed,
    FinalizeVacancyResume,
    GenerateVacancyResume,
    ImportVacancyBatch,
    MarkVacancyProcessed,
    PrepareApplicationPayload,
    PreviewVacancyUrlEnrichmentSeed,
    RecordArtifactAcceptance,
    RecordExternalActionApproval,
    RecordManualBoardAction,
    RejectVacancyUrlEnrichmentSeed,
    ResolveReconciliationItem,
    ResolveReminder,
    ShortlistVacancy,
    UpdateInterviewRoundState,
    UpdateTouchpointState,
    UpdateVacancyWorkflowStage,
    VacancyImportItem,
)
from job_search.application.commands.candidate import RunResumeQualityGate
from job_search.application.handlers.candidate_handlers import CandidateHandlers
from job_search.application.queries.vacancy import (
    GetBoardChecklist,
    GetPipelineReport,
    GetVacancy,
    ListApprovals,
    ListDailyActions,
    ListInterviewRounds,
    ListManualBoardActions,
    ListMaterialChangeReview,
    ListReconciliationItems,
    ListRankedVacancies,
    ListTouchpoints,
    ListVacancyUrlEnrichmentSeeds,
    ListVacancies,
)
from job_search.application.services.artifact_path_service import ArtifactPathService
from job_search.application.services.application_draft_service import ApplicationDraftService
from job_search.application.services.input_validation_service import InputValidationService
from job_search.application.services.job_board_operations_service import JobBoardOperationsService
from job_search.application.services.resume_assembly_service import ResumeAssemblyService
from job_search.application.services.resume_quality_gate_service import ResumeQualityGateService
from job_search.application.services.vacancy_normalization_service import VacancyNormalizationService
from job_search.application.services.vacancy_ranking_service import VacancyRankingService
from job_search.application.services.vacancy_url_enrichment_service import VacancyUrlEnrichmentService
from job_search.application.services.vacancy_resume_service import VacancyResumeService
from job_search.domain.enums import ApplicationState, ArtifactType, InterviewRoundState, TouchpointDirection, TouchpointState, VacancyWorkflowStage
from job_search.infrastructure.db.connection import write_tx
from job_search.infrastructure.repositories.artifact_repository import ArtifactRepository
from job_search.infrastructure.repositories.artifact_usage_repository import ArtifactUsageRepository
from job_search.infrastructure.repositories.audit_repository import AuditRepository
from job_search.infrastructure.repositories.approval_repository import ApprovalRepository
from job_search.infrastructure.repositories.candidate_evidence_repository import CandidateEvidenceRepository
from job_search.infrastructure.repositories.candidate_repository import CandidateRepository
from job_search.infrastructure.repositories.interview_repository import InterviewRepository
from job_search.infrastructure.repositories.manual_board_action_repository import ManualBoardActionRepository
from job_search.infrastructure.repositories.quality_gate_repository import QualityGateRepository
from job_search.infrastructure.repositories.reconciliation_repository import ReconciliationRepository
from job_search.infrastructure.repositories.touchpoint_repository import TouchpointRepository
from job_search.infrastructure.repositories.vacancy_url_enrichment_repository import VacancyUrlEnrichmentRepository
from job_search.infrastructure.repositories.vacancy_repository import VacancyRepository


class VacancyHandlers:
    _BOARD_ACTION_TYPES = {
        "saved_search_configured",
        "vacancy_opened",
        "application_submitted",
        "message_sent",
        "profile_updated",
        "visibility_checked",
        "vacancy_hidden",
        "manual_note",
    }
    _BOARD_ACTION_STATES = {"planned", "completed", "needs_review"}
    _ARTIFACT_REQUIRED_ACTIONS = {"application_submitted", "message_sent", "profile_updated"}
    _EXTERNAL_ACTION_APPROVAL_REQUIRED_ACTIONS = {"application_submitted", "message_sent", "profile_updated"}
    _ARTIFACT_ACCEPTANCE_STATES = {"accepted", "rejected", "revoked"}
    _EXTERNAL_ACTION_APPROVAL_STATES = {"approved", "rejected", "revoked"}
    _RECONCILIATION_REVIEW_STATUSES = {"open", "resolved", "rejected"}

    def __init__(
        self,
        *,
        vacancy_repository: VacancyRepository,
        candidate_repository: CandidateRepository,
        evidence_repository: CandidateEvidenceRepository,
        artifact_repository: ArtifactRepository,
        artifact_usage_repository: ArtifactUsageRepository,
        audit_repository: AuditRepository,
        approval_repository: ApprovalRepository,
        quality_gate_repository: QualityGateRepository,
        reconciliation_repository: ReconciliationRepository,
        touchpoint_repository: TouchpointRepository,
        interview_repository: InterviewRepository,
        manual_board_action_repository: ManualBoardActionRepository,
        url_enrichment_repository: VacancyUrlEnrichmentRepository,
        normalization_service: VacancyNormalizationService,
        ranking_service: VacancyRankingService,
        url_enrichment_service: VacancyUrlEnrichmentService,
        job_board_operations_service: JobBoardOperationsService,
        resume_assembly_service: ResumeAssemblyService,
        application_draft_service: ApplicationDraftService,
        resume_quality_gate_service: ResumeQualityGateService,
        vacancy_resume_service: VacancyResumeService,
        artifact_root: Path,
        candidate_handlers: CandidateHandlers | None,
        tx_connection,
    ) -> None:
        self._vacancy_repository = vacancy_repository
        self._candidate_repository = candidate_repository
        self._evidence_repository = evidence_repository
        self._artifact_repository = artifact_repository
        self._artifact_usage_repository = artifact_usage_repository
        self._audit_repository = audit_repository
        self._approval_repository = approval_repository
        self._quality_gate_repository = quality_gate_repository
        self._reconciliation_repository = reconciliation_repository
        self._touchpoint_repository = touchpoint_repository
        self._interview_repository = interview_repository
        self._manual_board_action_repository = manual_board_action_repository
        self._url_enrichment_repository = url_enrichment_repository
        self._normalization_service = normalization_service
        self._ranking_service = ranking_service
        self._url_enrichment_service = url_enrichment_service
        self._job_board_operations_service = job_board_operations_service
        self._resume_assembly_service = resume_assembly_service
        self._application_draft_service = application_draft_service
        self._resume_quality_gate_service = resume_quality_gate_service
        self._vacancy_resume_service = vacancy_resume_service
        self._artifact_root = artifact_root
        self._candidate_handlers = candidate_handlers
        self._conn = tx_connection

    def close(self) -> None:
        if self._candidate_handlers is not None:
            self._candidate_handlers.close()
        self._conn.close()

    def import_vacancy_batch(self, command: ImportVacancyBatch) -> dict[str, object]:
        if not self._candidate_repository.get_candidate(command.candidate_id):
            raise KeyError(f"Unknown candidate_id: {command.candidate_id}")
        if not command.items:
            raise ValueError("items must contain at least one vacancy")
        imported: list[dict[str, object]] = []
        with write_tx(self._conn, immediate=True):
            for item in command.items:
                normalized = self._normalization_service.normalize_item(asdict(item))
                result = self._vacancy_repository.import_occurrence(
                    candidate_id=command.candidate_id,
                    source_kind=command.source_kind,
                    normalized_item=normalized,
                )
                imported.append(result)
                self._audit_repository.record_event(
                    command_name=type(command).__name__,
                    actor="system",
                    entity_type="canonical_vacancy",
                    entity_id=str(result["canonical_vacancy_id"]),
                    previous_state=None,
                    new_state={
                        "candidate_id": command.candidate_id,
                        "source_kind": command.source_kind,
                        "title": normalized["title"],
                        "company_name": normalized["company_name"],
                        "location_text": normalized["location_text"],
                    },
                )
        return {"imported": imported}

    def create_vacancy_url_enrichment_seed(self, command: CreateVacancyUrlEnrichmentSeed) -> dict[str, object]:
        if not self._candidate_repository.get_candidate(command.candidate_id):
            raise KeyError(f"Unknown candidate_id: {command.candidate_id}")
        source_url = self._url_enrichment_service.normalize_url(command.source_url)
        platform = (command.platform or self._url_enrichment_service.infer_platform(source_url)).strip().lower()
        if not platform:
            raise ValueError("platform is required")
        source_origin = command.source_origin.strip().lower() or "saved_url"
        idempotency_key = command.idempotency_key or str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"url_seed|{command.candidate_id}|{source_url}")
        )
        with write_tx(self._conn, immediate=True):
            seed, reused = self._url_enrichment_repository.record_seed(
                candidate_id=command.candidate_id,
                platform=platform,
                source_url=source_url,
                source_origin=source_origin,
                notes=command.notes,
                idempotency_key=idempotency_key,
            )
            if not reused:
                self._audit_repository.record_event(
                    command_name=type(command).__name__,
                    actor="operator",
                    entity_type="vacancy_url_enrichment_seed",
                    entity_id=str(seed["url_seed_id"]),
                    previous_state=None,
                    new_state=seed,
                )
        return {"seed": seed, "reused": reused}

    def list_vacancy_url_enrichment_seeds(self, query: ListVacancyUrlEnrichmentSeeds) -> list[dict[str, object]]:
        return self._url_enrichment_repository.list_seeds(
            candidate_id=query.candidate_id,
            seed_status=query.seed_status.strip().lower() if query.seed_status else None,
            platform=query.platform.strip().lower() if query.platform else None,
        )

    def preview_vacancy_url_enrichment_seed(self, command: PreviewVacancyUrlEnrichmentSeed) -> dict[str, object]:
        seed = self._url_enrichment_repository.get_seed(
            candidate_id=command.candidate_id,
            url_seed_id=command.url_seed_id,
        )
        if seed is None:
            raise KeyError(f"Unknown url_seed_id: {command.url_seed_id}")
        if str(seed["seed_status"]) in {"imported", "rejected"}:
            raise ValueError(f"Cannot preview seed with status {seed['seed_status']}")
        preview = self._url_enrichment_service.build_preview(
            seed=seed,
            content_text=command.content_text,
            source_origin=command.source_origin.strip().lower() or "manual_page_text",
        )
        with write_tx(self._conn, immediate=True):
            updated_seed = self._url_enrichment_repository.update_preview(
                candidate_id=command.candidate_id,
                url_seed_id=command.url_seed_id,
                latest_preview_json=json.dumps(preview, ensure_ascii=False),
            )
            self._audit_repository.record_event(
                command_name=type(command).__name__,
                actor="operator",
                entity_type="vacancy_url_enrichment_seed",
                entity_id=command.url_seed_id,
                previous_state=seed,
                new_state=updated_seed,
            )
        return {"seed": updated_seed, "preview": preview}

    def confirm_vacancy_url_enrichment_import(self, command: ConfirmVacancyUrlEnrichmentImport) -> dict[str, object]:
        seed = self._url_enrichment_repository.get_seed(
            candidate_id=command.candidate_id,
            url_seed_id=command.url_seed_id,
        )
        if seed is None:
            raise KeyError(f"Unknown url_seed_id: {command.url_seed_id}")
        if str(seed["seed_status"]) == "imported":
            return {
                "seed": seed,
                "imported": [
                    {
                        "canonical_vacancy_id": seed.get("imported_canonical_vacancy_id"),
                        "source_occurrence_id": seed.get("imported_source_occurrence_id"),
                    }
                ],
                "reused": True,
            }
        if str(seed["seed_status"]) == "rejected":
            raise ValueError("Rejected URL seed cannot be imported")
        preview_json = seed.get("latest_preview_json")
        if not preview_json:
            raise ValueError("URL seed must be previewed before confirm import")
        preview = json.loads(str(preview_json))
        items = preview.get("items") or []
        if len(items) != 1:
            raise ValueError("URL seed confirm import requires exactly one preview item")
        item = VacancyImportItem(**items[0])
        source_kind = (command.source_kind or str(seed["platform"])).strip().lower()
        normalized = self._normalization_service.normalize_item(asdict(item))
        with write_tx(self._conn, immediate=True):
            result = self._vacancy_repository.import_occurrence(
                candidate_id=command.candidate_id,
                source_kind=source_kind,
                normalized_item=normalized,
            )
            updated_seed = self._url_enrichment_repository.mark_imported(
                candidate_id=command.candidate_id,
                url_seed_id=command.url_seed_id,
                canonical_vacancy_id=str(result["canonical_vacancy_id"]),
                source_occurrence_id=str(result["source_occurrence_id"]),
            )
            self._audit_repository.record_event(
                command_name=type(command).__name__,
                actor="operator",
                entity_type="canonical_vacancy",
                entity_id=str(result["canonical_vacancy_id"]),
                previous_state=None,
                new_state={
                    "candidate_id": command.candidate_id,
                    "source_kind": source_kind,
                    "title": normalized["title"],
                    "company_name": normalized["company_name"],
                    "url_seed_id": command.url_seed_id,
                },
            )
            self._audit_repository.record_event(
                command_name=type(command).__name__,
                actor="operator",
                entity_type="vacancy_url_enrichment_seed",
                entity_id=command.url_seed_id,
                previous_state=seed,
                new_state=updated_seed,
            )
        return {"seed": updated_seed, "imported": [result], "reused": False}

    def reject_vacancy_url_enrichment_seed(self, command: RejectVacancyUrlEnrichmentSeed) -> dict[str, object]:
        seed = self._url_enrichment_repository.get_seed(
            candidate_id=command.candidate_id,
            url_seed_id=command.url_seed_id,
        )
        if seed is None:
            raise KeyError(f"Unknown url_seed_id: {command.url_seed_id}")
        with write_tx(self._conn, immediate=True):
            updated_seed = self._url_enrichment_repository.reject_seed(
                candidate_id=command.candidate_id,
                url_seed_id=command.url_seed_id,
                rejection_reason=command.rejection_reason,
            )
            self._audit_repository.record_event(
                command_name=type(command).__name__,
                actor="operator",
                entity_type="vacancy_url_enrichment_seed",
                entity_id=command.url_seed_id,
                previous_state=seed,
                new_state=updated_seed,
            )
        return updated_seed

    def mark_vacancy_processed(self, command: MarkVacancyProcessed) -> dict[str, object]:
        previous = self._vacancy_repository.get_vacancy(command.candidate_id, command.canonical_vacancy_id)
        if previous is None:
            raise KeyError(f"Unknown canonical_vacancy_id: {command.canonical_vacancy_id}")
        with write_tx(self._conn, immediate=True):
            self._vacancy_repository.mark_processed(command.candidate_id, command.canonical_vacancy_id)
            current = self._vacancy_repository.get_vacancy(command.candidate_id, command.canonical_vacancy_id)
            self._audit_repository.record_event(
                command_name=type(command).__name__,
                actor="system",
                entity_type="canonical_vacancy",
                entity_id=command.canonical_vacancy_id,
                previous_state=previous,
                new_state=current,
            )
        return {"canonical_vacancy_id": command.canonical_vacancy_id, "processed": True}

    def update_vacancy_workflow_stage(self, command: UpdateVacancyWorkflowStage) -> dict[str, object]:
        InputValidationService.enum_value(VacancyWorkflowStage, command.workflow_stage, "workflow_stage")
        previous = self._vacancy_repository.get_vacancy(command.candidate_id, command.canonical_vacancy_id)
        if previous is None:
            raise KeyError(f"Unknown canonical_vacancy_id: {command.canonical_vacancy_id}")
        with write_tx(self._conn, immediate=True):
            self._vacancy_repository.update_workflow_stage(
                command.candidate_id,
                command.canonical_vacancy_id,
                command.workflow_stage,
            )
            current = self._vacancy_repository.get_vacancy(command.candidate_id, command.canonical_vacancy_id)
            self._audit_repository.record_event(
                command_name=type(command).__name__,
                actor="system",
                entity_type="canonical_vacancy",
                entity_id=command.canonical_vacancy_id,
                previous_state=previous,
                new_state=current,
            )
        return {"canonical_vacancy_id": command.canonical_vacancy_id, "workflow_stage": command.workflow_stage}

    def shortlist_vacancy(self, command: ShortlistVacancy) -> dict[str, object]:
        return self.update_vacancy_workflow_stage(
            UpdateVacancyWorkflowStage(
                candidate_id=command.candidate_id,
                canonical_vacancy_id=command.canonical_vacancy_id,
                workflow_stage="shortlisted",
            )
        )

    def list_vacancies(self, query: ListVacancies) -> list[dict[str, object]]:
        return self._vacancy_repository.list_vacancies_for_candidate(
            candidate_id=query.candidate_id,
            processed=query.processed,
            workflow_stage=query.workflow_stage,
        )

    def get_vacancy(self, query: GetVacancy) -> dict[str, object] | None:
        return self._vacancy_repository.get_vacancy(query.candidate_id, query.canonical_vacancy_id)

    def list_ranked_vacancies(self, query: ListRankedVacancies) -> list[dict[str, object]]:
        profile = self._candidate_repository.get_candidate_profile_view(query.candidate_id)
        if profile is None:
            raise KeyError(f"Unknown candidate_id: {query.candidate_id}")
        evidence = self._evidence_repository.get_resume_evidence(query.candidate_id)
        ranking_inputs = self._vacancy_repository.list_vacancy_ranking_inputs(
            candidate_id=query.candidate_id,
            processed=query.processed,
        )
        return self._ranking_service.rank(
            candidate_profile={**asdict(profile), "skill_signals": evidence.get("skill_signals", [])},
            vacancies=ranking_inputs,
        )

    def list_daily_actions(self, query: ListDailyActions) -> list[dict[str, object]]:
        actions = self._vacancy_repository.list_daily_action_items(query.candidate_id)
        actions.extend(self._reconciliation_daily_actions(query.candidate_id))
        ranked = self.list_ranked_vacancies(ListRankedVacancies(candidate_id=query.candidate_id, processed=False))
        ranking_by_id = {str(item["canonical_vacancy_id"]): item for item in ranked}
        filtered: list[dict[str, object]] = []
        for action in actions:
            if action.get("action_type") != "review_new_vacancy":
                filtered.append(action)
                continue
            vacancy_id = str(action.get("canonical_vacancy_id") or "")
            ranking = ranking_by_id.get(vacancy_id)
            if not ranking:
                filtered.append(action)
                continue
            if ranking.get("fit_label") == "skip" or ranking.get("dealbreakers_hit"):
                continue
            if ranking.get("fit_label") == "low":
                action = {
                    **action,
                    "action_type": "review_low_fit_vacancy",
                    "action_group": "vacancy_review",
                    "priority": 35,
                    "fit_label": ranking.get("fit_label"),
                    "score_reasons": ranking.get("score_reasons", []),
                }
            filtered.append(action)
        filtered.sort(key=lambda item: (int(item["priority"]), str(item.get("updated_at") or "")), reverse=True)
        return filtered

    def list_material_change_review(self, query: ListMaterialChangeReview) -> list[dict[str, object]]:
        vacancies = self.list_vacancies(ListVacancies(candidate_id=query.candidate_id, processed=True))
        return [
            {
                **vacancy,
                "review_bucket": "material_change",
                "review_reason": "processed_vacancy_changed_after_processing",
            }
            for vacancy in vacancies
            if bool(vacancy.get("material_change_detected"))
        ]

    def get_pipeline_report(self, query: GetPipelineReport) -> dict[str, object]:
        profile = self._candidate_repository.get_candidate_profile_view(query.candidate_id)
        if profile is None:
            raise KeyError(f"Unknown candidate_id: {query.candidate_id}")
        vacancies = self.list_vacancies(ListVacancies(candidate_id=query.candidate_id))
        ranked = self.list_ranked_vacancies(ListRankedVacancies(candidate_id=query.candidate_id, processed=False))
        applications = self._vacancy_repository.list_applications_for_candidate(query.candidate_id)
        daily_actions = self.list_daily_actions(ListDailyActions(candidate_id=query.candidate_id))
        board_actions = self.list_manual_board_actions(ListManualBoardActions(candidate_id=query.candidate_id))
        material_change_review = self.list_material_change_review(ListMaterialChangeReview(candidate_id=query.candidate_id))
        workflow_counts = self._count_by(vacancies, "workflow_stage")
        fit_counts = self._count_by(ranked, "fit_label")
        application_counts = self._count_by(applications, "application_state")
        board_action_counts = self._count_by(board_actions, "action_type")
        daily_action_groups = self._count_by(daily_actions, "action_group")
        return {
            "candidate_id": query.candidate_id,
            "summary": {
                "total_vacancies": len(vacancies),
                "active_vacancies": len([item for item in vacancies if not bool(item.get("processed"))]),
                "processed_vacancies": len([item for item in vacancies if bool(item.get("processed"))]),
                "applications": len(applications),
                "daily_actions": len(daily_actions),
                "material_change_review": len(material_change_review),
                "manual_board_actions": len(board_actions),
            },
            "workflow_counts": workflow_counts,
            "fit_label_counts": fit_counts,
            "application_state_counts": application_counts,
            "board_action_counts": board_action_counts,
            "daily_action_group_counts": daily_action_groups,
            "review_buckets": {
                "new_vacancies": workflow_counts.get(VacancyWorkflowStage.NEW.value, 0),
                "shortlisted": workflow_counts.get(VacancyWorkflowStage.SHORTLISTED.value, 0),
                "ranking_needs_review": len([item for item in ranked if bool(item.get("needs_review"))]),
                "material_change": len(material_change_review),
            },
            "top_ranked": ranked[:5],
            "daily_actions": daily_actions,
            "material_change_review": material_change_review,
            "recent_board_actions": board_actions[:5],
            "recommendations": self._pipeline_recommendations(ranked, daily_actions, applications),
        }

    def create_touchpoint(self, command: CreateTouchpoint) -> dict[str, object]:
        InputValidationService.enum_value(TouchpointDirection, command.direction, "direction")
        InputValidationService.enum_value(TouchpointState, command.touchpoint_state, "touchpoint_state")
        occurred_at = InputValidationService.optional_iso_datetime(command.occurred_at, "occurred_at")
        follow_up_due_at = InputValidationService.optional_iso_datetime(command.follow_up_due_at, "follow_up_due_at")
        vacancy = self._vacancy_repository.get_vacancy(command.candidate_id, command.canonical_vacancy_id)
        if vacancy is None:
            raise KeyError(f"Unknown canonical_vacancy_id: {command.canonical_vacancy_id}")
        if command.application_id:
            self._assert_application_belongs_to_vacancy(
                candidate_id=command.candidate_id,
                canonical_vacancy_id=command.canonical_vacancy_id,
                application_id=command.application_id,
            )
        if command.message_artifact_id:
            self._assert_artifact_belongs_to_candidate(command.message_artifact_id, command.candidate_id)
        with write_tx(self._conn, immediate=True):
            touchpoint = self._touchpoint_repository.create_touchpoint(
                candidate_id=command.candidate_id,
                canonical_vacancy_id=command.canonical_vacancy_id,
                application_id=command.application_id,
                message_artifact_id=command.message_artifact_id,
                channel=command.channel,
                direction=command.direction,
                touchpoint_state=command.touchpoint_state,
                contact_name=command.contact_name,
                occurred_at=occurred_at,
                notes=command.notes,
            )
            reminder = None
            if follow_up_due_at:
                reminder = self._touchpoint_repository.create_follow_up_reminder(
                    candidate_id=command.candidate_id,
                    touchpoint_id=str(touchpoint["touchpoint_id"]),
                    canonical_vacancy_id=command.canonical_vacancy_id,
                    application_id=command.application_id,
                    due_at=follow_up_due_at,
                    notes=command.notes,
                )
            if command.message_artifact_id:
                self._artifact_usage_repository.record_usage(
                    artifact_id=command.message_artifact_id,
                    candidate_id=command.candidate_id,
                    usage_type="touchpoint_message_used",
                    target_entity_type="touchpoint",
                    target_entity_id=str(touchpoint["touchpoint_id"]),
                    notes=None,
                )
            self._audit_repository.record_event(
                command_name=type(command).__name__,
                actor="system",
                entity_type="touchpoint",
                entity_id=str(touchpoint["touchpoint_id"]),
                previous_state=None,
                new_state=touchpoint,
            )
        return {"touchpoint": touchpoint, "reminder": reminder}

    def update_touchpoint_state(self, command: UpdateTouchpointState) -> dict[str, object]:
        InputValidationService.enum_value(TouchpointState, command.touchpoint_state, "touchpoint_state")
        replied_at = InputValidationService.optional_iso_datetime(command.replied_at, "replied_at")
        previous = self._touchpoint_repository.get_touchpoint(command.candidate_id, command.touchpoint_id)
        if previous is None:
            raise KeyError(f"Unknown touchpoint_id: {command.touchpoint_id}")
        with write_tx(self._conn, immediate=True):
            current = self._touchpoint_repository.update_touchpoint_state(
                candidate_id=command.candidate_id,
                touchpoint_id=command.touchpoint_id,
                touchpoint_state=command.touchpoint_state,
                replied_at=replied_at,
            )
            self._audit_repository.record_event(
                command_name=type(command).__name__,
                actor="system",
                entity_type="touchpoint",
                entity_id=command.touchpoint_id,
                previous_state=previous,
                new_state=current,
            )
        return current

    def resolve_reminder(self, command: ResolveReminder) -> dict[str, object]:
        with write_tx(self._conn, immediate=True):
            current = self._touchpoint_repository.resolve_reminder(
                candidate_id=command.candidate_id,
                reminder_id=command.reminder_id,
            )
            if current is None:
                raise KeyError(f"Unknown reminder_id: {command.reminder_id}")
            self._audit_repository.record_event(
                command_name=type(command).__name__,
                actor="system",
                entity_type="follow_up_reminder",
                entity_id=command.reminder_id,
                previous_state=None,
                new_state=current,
            )
        return current

    def list_touchpoints(self, query: ListTouchpoints) -> list[dict[str, object]]:
        return self._touchpoint_repository.list_touchpoints(
            candidate_id=query.candidate_id,
            canonical_vacancy_id=query.canonical_vacancy_id,
            application_id=query.application_id,
        )

    def create_interview_round(self, command: CreateInterviewRound) -> dict[str, object]:
        round_type = command.round_type.strip().lower()
        if not round_type:
            raise ValueError("round_type is required")
        InputValidationService.enum_value(InterviewRoundState, command.round_state, "round_state")
        scheduled_at = InputValidationService.optional_iso_datetime(command.scheduled_at, "scheduled_at")
        application = self._vacancy_repository.get_application_by_id(command.candidate_id, command.application_id)
        if application is None:
            raise KeyError(f"Unknown application_id: {command.application_id}")
        canonical_vacancy_id = str(application["canonical_vacancy_id"])
        idempotency_key = command.idempotency_key or self._interview_round_idempotency_key(
            candidate_id=command.candidate_id,
            application_id=command.application_id,
            round_type=round_type,
            scheduled_at=scheduled_at,
            interviewer_name=command.interviewer_name,
        )
        with write_tx(self._conn, immediate=True):
            interview_round, reused = self._interview_repository.create_round(
                candidate_id=command.candidate_id,
                application_id=command.application_id,
                canonical_vacancy_id=canonical_vacancy_id,
                round_type=round_type,
                round_state=command.round_state,
                scheduled_at=scheduled_at,
                interviewer_name=command.interviewer_name,
                notes=command.notes,
                idempotency_key=idempotency_key,
            )
            updated_application = self._vacancy_repository.update_application_state(
                candidate_id=command.candidate_id,
                application_id=command.application_id,
                application_state=ApplicationState.INTERVIEWING.value,
            )
            if not reused:
                self._audit_repository.record_event(
                    command_name=type(command).__name__,
                    actor="operator",
                    entity_type="interview_round",
                    entity_id=str(interview_round["interview_round_id"]),
                    previous_state=None,
                    new_state=interview_round,
                )
                self._audit_repository.record_event(
                    command_name=type(command).__name__,
                    actor="system",
                    entity_type="application",
                    entity_id=command.application_id,
                    previous_state=application,
                    new_state=updated_application,
                )
        return {"interview_round": interview_round, "application": updated_application, "reused": reused}

    def update_interview_round_state(self, command: UpdateInterviewRoundState) -> dict[str, object]:
        InputValidationService.enum_value(InterviewRoundState, command.round_state, "round_state")
        completed_at = InputValidationService.optional_iso_datetime(command.completed_at, "completed_at")
        previous = self._interview_repository.get_round(
            candidate_id=command.candidate_id,
            interview_round_id=command.interview_round_id,
        )
        if previous is None:
            raise KeyError(f"Unknown interview_round_id: {command.interview_round_id}")
        with write_tx(self._conn, immediate=True):
            current = self._interview_repository.update_round_state(
                candidate_id=command.candidate_id,
                interview_round_id=command.interview_round_id,
                round_state=command.round_state,
                completed_at=completed_at,
                notes=command.notes,
            )
            self._audit_repository.record_event(
                command_name=type(command).__name__,
                actor="operator",
                entity_type="interview_round",
                entity_id=command.interview_round_id,
                previous_state=previous,
                new_state=current,
            )
        return current

    def list_interview_rounds(self, query: ListInterviewRounds) -> list[dict[str, object]]:
        return self._interview_repository.list_rounds(
            candidate_id=query.candidate_id,
            application_id=query.application_id,
            canonical_vacancy_id=query.canonical_vacancy_id,
        )

    def record_manual_board_action(self, command: RecordManualBoardAction) -> dict[str, object]:
        platform = command.platform.strip().lower()
        action_type = command.action_type.strip().lower()
        action_state = command.action_state.strip().lower()
        if not platform:
            raise ValueError("platform is required")
        if action_type not in self._BOARD_ACTION_TYPES:
            raise ValueError(f"action_type must be one of: {', '.join(sorted(self._BOARD_ACTION_TYPES))}")
        if action_state not in self._BOARD_ACTION_STATES:
            raise ValueError(f"action_state must be one of: {', '.join(sorted(self._BOARD_ACTION_STATES))}")
        occurred_at = (
            InputValidationService.optional_iso_datetime(command.occurred_at, "occurred_at")
            or datetime.now(timezone.utc).isoformat()
        )
        if command.canonical_vacancy_id:
            vacancy = self._vacancy_repository.get_vacancy(command.candidate_id, command.canonical_vacancy_id)
            if vacancy is None:
                raise KeyError(f"Unknown canonical_vacancy_id: {command.canonical_vacancy_id}")
        if command.application_id and command.canonical_vacancy_id:
            self._assert_application_belongs_to_vacancy(
                candidate_id=command.candidate_id,
                canonical_vacancy_id=command.canonical_vacancy_id,
                application_id=command.application_id,
            )
        if action_type in self._ARTIFACT_REQUIRED_ACTIONS and not command.artifact_id:
            raise ValueError(f"artifact_id is required for {action_type}")
        if command.artifact_id:
            self._assert_artifact_belongs_to_candidate(command.artifact_id, command.candidate_id)
        approval = None
        if action_state == "completed" and action_type in self._EXTERNAL_ACTION_APPROVAL_REQUIRED_ACTIONS:
            if not command.external_action_approval_id:
                raise ValueError(f"external_action_approval_id is required for completed {action_type}")
            approval = self._assert_external_action_approval(
                candidate_id=command.candidate_id,
                approval_id=command.external_action_approval_id,
                platform=platform,
                action_type=action_type,
                artifact_id=command.artifact_id,
            )
        idempotency_key = command.idempotency_key or self._manual_board_action_idempotency_key(
            candidate_id=command.candidate_id,
            platform=platform,
            action_type=action_type,
            canonical_vacancy_id=command.canonical_vacancy_id,
            application_id=command.application_id,
            artifact_id=command.artifact_id,
            external_target=command.external_target,
            occurred_at=occurred_at,
        )
        with write_tx(self._conn, immediate=True):
            action, reused = self._manual_board_action_repository.record_action(
                candidate_id=command.candidate_id,
                platform=platform,
                action_type=action_type,
                action_state=action_state,
                canonical_vacancy_id=command.canonical_vacancy_id,
                application_id=command.application_id,
                artifact_id=command.artifact_id,
                external_target=command.external_target,
                occurred_at=occurred_at,
                notes=command.notes,
                idempotency_key=idempotency_key,
                external_action_approval_id=str(approval["approval_id"]) if approval else None,
            )
            if command.artifact_id:
                self._artifact_usage_repository.record_usage(
                    artifact_id=command.artifact_id,
                    candidate_id=command.candidate_id,
                    usage_type="manual_board_action_artifact_used",
                    target_entity_type="manual_board_action",
                    target_entity_id=str(action["board_action_id"]),
                    external_target=command.external_target or platform,
                    notes=command.notes,
                )
            if not reused:
                self._audit_repository.record_event(
                    command_name=type(command).__name__,
                    actor="operator",
                    entity_type="manual_board_action",
                    entity_id=str(action["board_action_id"]),
                    previous_state=None,
                    new_state=action,
                )
            reconciliation_item, reconciliation_reused = self._record_reconciliation_for_board_action(action)
        return {
            "board_action": action,
            "reused": reused,
            "reconciliation_item": reconciliation_item,
            "reconciliation_reused": reconciliation_reused,
        }

    def record_artifact_acceptance(self, command: RecordArtifactAcceptance) -> dict[str, object]:
        approval_state = command.approval_state.strip().lower()
        actor = command.actor.strip() or "operator"
        if approval_state not in self._ARTIFACT_ACCEPTANCE_STATES:
            raise ValueError(
                f"approval_state must be one of: {', '.join(sorted(self._ARTIFACT_ACCEPTANCE_STATES))}"
            )
        self._assert_artifact_belongs_to_candidate(command.artifact_id, command.candidate_id)
        idempotency_key = command.idempotency_key or ":".join(
            ["artifact_acceptance", command.artifact_id, approval_state, actor]
        )
        with write_tx(self._conn, immediate=True):
            approval, reused = self._approval_repository.record_approval(
                candidate_id=command.candidate_id,
                approval_type="artifact_acceptance",
                approval_state=approval_state,
                actor=actor,
                artifact_id=command.artifact_id,
                target_entity_type="artifact",
                target_entity_id=command.artifact_id,
                action_type=None,
                platform=None,
                external_target=None,
                reason=command.reason,
                notes=command.notes,
                idempotency_key=idempotency_key,
            )
            if not reused:
                self._audit_repository.record_event(
                    command_name=type(command).__name__,
                    actor=actor,
                    entity_type="approval_record",
                    entity_id=str(approval["approval_id"]),
                    previous_state=None,
                    new_state=approval,
                )
        return {"approval": approval, "reused": reused}

    def record_external_action_approval(self, command: RecordExternalActionApproval) -> dict[str, object]:
        platform = command.platform.strip().lower()
        action_type = command.action_type.strip().lower()
        approval_state = command.approval_state.strip().lower()
        actor = command.actor.strip() or "operator"
        if action_type not in self._BOARD_ACTION_TYPES:
            raise ValueError(f"action_type must be one of: {', '.join(sorted(self._BOARD_ACTION_TYPES))}")
        if approval_state not in self._EXTERNAL_ACTION_APPROVAL_STATES:
            raise ValueError(
                f"approval_state must be one of: {', '.join(sorted(self._EXTERNAL_ACTION_APPROVAL_STATES))}"
            )
        if action_type in self._ARTIFACT_REQUIRED_ACTIONS and not command.artifact_id:
            raise ValueError(f"artifact_id is required for {action_type}")
        if command.artifact_id:
            self._assert_artifact_belongs_to_candidate(command.artifact_id, command.candidate_id)
        if command.canonical_vacancy_id:
            vacancy = self._vacancy_repository.get_vacancy(command.candidate_id, command.canonical_vacancy_id)
            if vacancy is None:
                raise KeyError(f"Unknown canonical_vacancy_id: {command.canonical_vacancy_id}")
        if command.application_id and command.canonical_vacancy_id:
            self._assert_application_belongs_to_vacancy(
                candidate_id=command.candidate_id,
                canonical_vacancy_id=command.canonical_vacancy_id,
                application_id=command.application_id,
            )
        target_entity_type = "application" if command.application_id else "vacancy" if command.canonical_vacancy_id else None
        target_entity_id = command.application_id or command.canonical_vacancy_id
        idempotency_key = command.idempotency_key or self._external_action_approval_idempotency_key(
            platform=platform,
            action_type=action_type,
            artifact_id=command.artifact_id,
            target_entity_id=target_entity_id,
            external_target=command.external_target,
        )
        with write_tx(self._conn, immediate=True):
            approval, reused = self._approval_repository.record_approval(
                candidate_id=command.candidate_id,
                approval_type="external_action_approval",
                approval_state=approval_state,
                actor=actor,
                artifact_id=command.artifact_id,
                target_entity_type=target_entity_type,
                target_entity_id=target_entity_id,
                action_type=action_type,
                platform=platform,
                external_target=command.external_target,
                reason=command.reason,
                notes=command.notes,
                idempotency_key=idempotency_key,
            )
            if not reused:
                self._audit_repository.record_event(
                    command_name=type(command).__name__,
                    actor=actor,
                    entity_type="approval_record",
                    entity_id=str(approval["approval_id"]),
                    previous_state=None,
                    new_state=approval,
                )
        return {"approval": approval, "reused": reused}

    def list_approvals(self, query: ListApprovals) -> list[dict[str, object]]:
        return self._approval_repository.list_approvals(
            candidate_id=query.candidate_id,
            approval_type=query.approval_type,
            artifact_id=query.artifact_id,
        )

    def list_manual_board_actions(self, query: ListManualBoardActions) -> list[dict[str, object]]:
        platform = query.platform.strip().lower() if query.platform else None
        return self._manual_board_action_repository.list_actions(
            candidate_id=query.candidate_id,
            platform=platform,
            canonical_vacancy_id=query.canonical_vacancy_id,
        )

    def list_reconciliation_items(self, query: ListReconciliationItems) -> list[dict[str, object]]:
        return self._reconciliation_repository.list_items(
            candidate_id=query.candidate_id,
            review_status=query.review_status.strip().lower() if query.review_status else None,
            outcome=query.outcome.strip().lower() if query.outcome else None,
            platform=query.platform.strip().lower() if query.platform else None,
        )

    def resolve_reconciliation_item(self, command: ResolveReconciliationItem) -> dict[str, object]:
        review_status = command.review_status.strip().lower()
        if review_status not in self._RECONCILIATION_REVIEW_STATUSES - {"open"}:
            raise ValueError("review_status must be one of: resolved, rejected")
        previous = self._reconciliation_repository.get_item(
            candidate_id=command.candidate_id,
            reconciliation_item_id=command.reconciliation_item_id,
        )
        if previous is None:
            raise KeyError(f"Unknown reconciliation_item_id: {command.reconciliation_item_id}")
        with write_tx(self._conn, immediate=True):
            current = self._reconciliation_repository.resolve_item(
                candidate_id=command.candidate_id,
                reconciliation_item_id=command.reconciliation_item_id,
                review_status=review_status,
                resolution_notes=command.resolution_notes,
            )
            self._audit_repository.record_event(
                command_name=type(command).__name__,
                actor="operator",
                entity_type="reconciliation_item",
                entity_id=command.reconciliation_item_id,
                previous_state=previous,
                new_state=current,
            )
        return current

    def get_board_checklist(self, query: GetBoardChecklist) -> dict[str, object]:
        profile = self._candidate_repository.get_candidate_profile_view(query.candidate_id)
        if profile is None:
            raise KeyError(f"Unknown candidate_id: {query.candidate_id}")
        vacancy = None
        if query.canonical_vacancy_id:
            vacancy = self._vacancy_repository.get_vacancy(query.candidate_id, query.canonical_vacancy_id)
            if vacancy is None:
                raise KeyError(f"Unknown canonical_vacancy_id: {query.canonical_vacancy_id}")
        return self._job_board_operations_service.build_manual_checklist(
            platform=query.platform,
            candidate_profile=asdict(profile),
            vacancy=vacancy,
        )

    def prepare_application_payload(self, command: PrepareApplicationPayload) -> dict[str, object]:
        if self._candidate_handlers is None:
            raise RuntimeError("Candidate handlers are required for application payload preparation")
        vacancy = self._vacancy_repository.get_vacancy(command.candidate_id, command.canonical_vacancy_id)
        if vacancy is None:
            raise KeyError(f"Unknown canonical_vacancy_id: {command.canonical_vacancy_id}")
        resolved_target_role = command.target_role or str(vacancy.get("role_title") or "")
        profile = self._candidate_repository.get_candidate_profile_view(command.candidate_id)
        if profile is None:
            raise KeyError(f"Unknown candidate_id: {command.candidate_id}")
        evidence = self._evidence_repository.get_resume_evidence(command.candidate_id)
        resume_markdown = self._resume_assembly_service.assemble_markdown(
            profile=asdict(profile),
            evidence=evidence,
            language=command.language,
            target_role=resolved_target_role,
        )
        message_markdown = self._application_draft_service.build_message_artifact(
            candidate_profile={**asdict(profile), **evidence},
            vacancy=vacancy,
            language=command.language,
            target_role=resolved_target_role,
        )
        resume_content_hash = str(uuid.uuid5(uuid.NAMESPACE_URL, resume_markdown))
        message_content_hash = str(uuid.uuid5(uuid.NAMESPACE_URL, message_markdown))
        resume_existing = self._artifact_repository.find_reusable_artifact(
            candidate_id=command.candidate_id,
            artifact_type=ArtifactType.RESUME_MARKDOWN.value,
            content_hash=resume_content_hash,
        )
        message_existing = self._artifact_repository.find_reusable_artifact(
            candidate_id=command.candidate_id,
            artifact_type="message_artifact",
            content_hash=message_content_hash,
        )
        created_paths: list[Path] = []
        candidate_label = self._candidate_label_from_profile(asdict(profile))
        resume_artifact_id = str(resume_existing["artifact_id"]) if resume_existing else str(uuid.uuid4())
        resume_storage_path = (
            Path(str(resume_existing["storage_path"]))
            if resume_existing
            else self._candidate_artifact_path(
                candidate_id=command.candidate_id,
                artifact_id=resume_artifact_id,
                artifact_type=ArtifactType.RESUME_MARKDOWN.value,
                candidate_label=candidate_label,
                artifact_label=f"{resolved_target_role}-{command.language}",
            )
        )
        message_artifact_id = str(message_existing["artifact_id"]) if message_existing else str(uuid.uuid4())
        message_storage_path = (
            Path(str(message_existing["storage_path"]))
            if message_existing
            else self._candidate_artifact_path(
                candidate_id=command.candidate_id,
                artifact_id=message_artifact_id,
                artifact_type="message_artifact",
                candidate_label=candidate_label,
                artifact_label=f"{vacancy.get('company_name') or 'company'}-{resolved_target_role}",
            )
        )
        if resume_existing is None:
            resume_storage_path.parent.mkdir(parents=True, exist_ok=True)
            resume_storage_path.write_text(resume_markdown, encoding="utf-8")
            created_paths.append(resume_storage_path)
        if message_existing is None:
            message_storage_path.parent.mkdir(parents=True, exist_ok=True)
            message_storage_path.write_text(message_markdown, encoding="utf-8")
            created_paths.append(message_storage_path)
        try:
            previous_application = self._vacancy_repository.get_application(command.candidate_id, command.canonical_vacancy_id)
            with write_tx(self._conn, immediate=True):
                if resume_existing is None:
                    self._artifact_repository.create_artifact(
                        artifact_id=resume_artifact_id,
                        artifact_type=ArtifactType.RESUME_MARKDOWN.value,
                        candidate_id=command.candidate_id,
                        storage_path=str(resume_storage_path),
                        content_hash=resume_content_hash,
                        notes=json.dumps(
                            {"language": command.language, "target_role": resolved_target_role},
                            ensure_ascii=False,
                        ),
                    )
                    self._audit_repository.record_event(
                        command_name=type(command).__name__,
                        actor="system",
                        entity_type="artifact",
                        entity_id=resume_artifact_id,
                        previous_state=None,
                        new_state={
                            "artifact_type": ArtifactType.RESUME_MARKDOWN.value,
                            "language": command.language,
                            "target_role": resolved_target_role,
                            "storage_path": str(resume_storage_path),
                        },
                    )
                if message_existing is None:
                    self._artifact_repository.create_artifact(
                        artifact_id=message_artifact_id,
                        artifact_type="message_artifact",
                        candidate_id=command.candidate_id,
                        storage_path=str(message_storage_path),
                        content_hash=message_content_hash,
                        notes=json.dumps(
                            {
                                "language": command.language,
                                "target_role": resolved_target_role,
                                "canonical_vacancy_id": command.canonical_vacancy_id,
                            },
                            ensure_ascii=False,
                        ),
                    )
                application = self._vacancy_repository.attach_application_message_artifact(
                    candidate_id=command.candidate_id,
                    canonical_vacancy_id=command.canonical_vacancy_id,
                    message_artifact_id=message_artifact_id,
                )
                self._record_application_artifact_usage(
                    artifact_id=message_artifact_id,
                    candidate_id=command.candidate_id,
                    application_id=str(application["application_id"]),
                    usage_type="application_draft_attached",
                )
                self._record_application_artifact_usage(
                    artifact_id=resume_artifact_id,
                    candidate_id=command.candidate_id,
                    application_id=str(application["application_id"]),
                    usage_type="application_resume_attached",
                )
                self._audit_repository.record_event(
                    command_name=type(command).__name__,
                    actor="system",
                    entity_type="application",
                    entity_id=str(application["application_id"]),
                    previous_state=previous_application,
                    new_state=application,
                )
        except Exception:
            for path in created_paths:
                self._cleanup_created_file(path)
            raise
        resume_quality_gate = self._candidate_handlers.run_resume_quality_gate(
            RunResumeQualityGate(artifact_id=resume_artifact_id)
        )
        message_quality_gate = self._run_message_quality_gate(
            artifact_id=message_artifact_id,
            candidate_id=command.candidate_id,
            target_role=resolved_target_role,
            target_company=str(vacancy.get("company_name") or ""),
        )
        application_payload_quality_gate = self._run_application_payload_quality_gate(
            candidate_id=command.candidate_id,
            application_id=str(application["application_id"]),
            resume_quality_gate=resume_quality_gate,
            message_quality_gate=message_quality_gate,
        )
        return {
            "candidate_id": command.candidate_id,
            "canonical_vacancy_id": command.canonical_vacancy_id,
            "resume_artifact_id": resume_artifact_id,
            "resume_quality_gate": resume_quality_gate,
            "message_artifact_id": message_artifact_id,
            "message_quality_gate": message_quality_gate,
            "application_payload_quality_gate": application_payload_quality_gate,
            "application_id": application["application_id"],
        }

    def generate_vacancy_resume(self, command: GenerateVacancyResume) -> dict[str, object]:
        if self._candidate_handlers is None:
            raise RuntimeError("Candidate handlers are required for vacancy resume generation")
        vacancy = self._vacancy_repository.get_vacancy(command.candidate_id, command.canonical_vacancy_id)
        if vacancy is None:
            raise KeyError(f"Unknown canonical_vacancy_id: {command.canonical_vacancy_id}")
        source_or_selection = self._select_vacancy_resume_source(
            candidate_id=command.candidate_id,
            target_role=str(vacancy.get("role_title") or ""),
            source_resume_artifact_id=command.source_resume_artifact_id,
        )
        if "status" in source_or_selection:
            return source_or_selection
        source_artifact = source_or_selection
        source_markdown = Path(str(source_artifact["storage_path"])).read_text(encoding="utf-8")
        markdown = self._vacancy_resume_service.build_resume(
            source_markdown=source_markdown,
            source_artifact=source_artifact,
            vacancy=vacancy,
            language=command.language,
        )
        content_hash = str(uuid.uuid5(uuid.NAMESPACE_URL, markdown))
        existing = self._find_existing_vacancy_artifact(
            candidate_id=command.candidate_id,
            artifact_type=ArtifactType.RESUME_VACANCY.value,
            canonical_vacancy_id=command.canonical_vacancy_id,
        )
        notes = {
            "canonical_vacancy_id": command.canonical_vacancy_id,
            "company_name": vacancy.get("company_name"),
            "target_role": vacancy.get("role_title"),
            "language": command.language,
            "source_resume_artifact_id": source_artifact["artifact_id"],
            "source_resume_artifact_type": source_artifact["artifact_type"],
            "ai_runtime": "deferred_to_stage3_group_12",
        }
        if existing is not None:
            storage_path = Path(str(existing["storage_path"]))
            previous_state = dict(existing)
            storage_path.parent.mkdir(parents=True, exist_ok=True)
            storage_path.write_text(markdown, encoding="utf-8")
            with write_tx(self._conn, immediate=True):
                self._artifact_repository.update_artifact_content(
                    artifact_id=str(existing["artifact_id"]),
                    storage_path=str(storage_path),
                    content_hash=content_hash,
                    notes=json.dumps(notes, ensure_ascii=False),
                    derived_from_artifact_id=str(source_artifact["artifact_id"]),
                )
                current = self._artifact_repository.get_artifact(str(existing["artifact_id"]))
                self._audit_repository.record_event(
                    command_name=type(command).__name__,
                    actor="system",
                    entity_type="artifact",
                    entity_id=str(existing["artifact_id"]),
                    previous_state=previous_state,
                    new_state=current,
                )
            artifact_id = str(existing["artifact_id"])
            overwritten = True
        else:
            artifact_id = str(uuid.uuid4())
            storage_path = self._candidate_artifact_path(
                candidate_id=command.candidate_id,
                artifact_id=artifact_id,
                artifact_type=ArtifactType.RESUME_VACANCY.value,
                candidate_label=self._candidate_label_for_id(command.candidate_id),
                artifact_label=f"{vacancy.get('company_name') or 'company'}-{vacancy.get('role_title') or 'role'}",
            )
            storage_path.parent.mkdir(parents=True, exist_ok=True)
            storage_path.write_text(markdown, encoding="utf-8")
            try:
                with write_tx(self._conn, immediate=True):
                    self._artifact_repository.create_artifact(
                        artifact_id=artifact_id,
                        artifact_type=ArtifactType.RESUME_VACANCY.value,
                        candidate_id=command.candidate_id,
                        storage_path=str(storage_path),
                        content_hash=content_hash,
                        notes=json.dumps(notes, ensure_ascii=False),
                        derived_from_artifact_id=str(source_artifact["artifact_id"]),
                    )
                    self._audit_repository.record_event(
                        command_name=type(command).__name__,
                        actor="system",
                        entity_type="artifact",
                        entity_id=artifact_id,
                        previous_state=None,
                        new_state={
                            "artifact_type": ArtifactType.RESUME_VACANCY.value,
                            "storage_path": str(storage_path),
                            "derived_from_artifact_id": source_artifact["artifact_id"],
                            "canonical_vacancy_id": command.canonical_vacancy_id,
                        },
                    )
            except Exception:
                self._cleanup_created_file(storage_path)
                raise
            overwritten = False
        quality_gate = self._candidate_handlers.run_resume_quality_gate(RunResumeQualityGate(artifact_id=artifact_id))
        return {
            "artifact_id": artifact_id,
            "artifact_type": ArtifactType.RESUME_VACANCY.value,
            "storage_path": str(storage_path),
            "canonical_vacancy_id": command.canonical_vacancy_id,
            "derived_from_artifact_id": str(source_artifact["artifact_id"]),
            "source_resume_artifact_id": str(source_artifact["artifact_id"]),
            "source_resume_artifact_type": str(source_artifact["artifact_type"]),
            "overwritten": overwritten,
            "quality_gate": quality_gate,
        }

    def finalize_vacancy_resume(self, command: FinalizeVacancyResume) -> dict[str, object]:
        if self._candidate_handlers is None:
            raise RuntimeError("Candidate handlers are required for resume finalization")
        source_artifact = self._artifact_repository.get_artifact(command.artifact_id)
        if source_artifact is None:
            raise KeyError(f"Unknown artifact_id: {command.artifact_id}")
        if str(source_artifact["candidate_id"]) != command.candidate_id:
            raise PermissionError("artifact_id does not belong to the requested candidate")
        if str(source_artifact["artifact_type"]) != ArtifactType.RESUME_VACANCY.value:
            raise ValueError("Only resume_vacancy artifacts can be finalized as resume_vacancy_final")
        source_quality_gate = self._candidate_handlers.run_resume_quality_gate(RunResumeQualityGate(artifact_id=command.artifact_id))
        if source_quality_gate["status"] == "fail":
            raise ValueError("Cannot finalize vacancy resume artifact with failing quality gate")
        if source_quality_gate["status"] == "warn" and not command.allow_warnings:
            raise ValueError("Cannot finalize vacancy resume artifact with warnings unless allow_warnings is true")
        markdown = Path(str(source_artifact["storage_path"])).read_text(encoding="utf-8")
        content_hash = str(uuid.uuid5(uuid.NAMESPACE_URL, markdown))
        existing = self._artifact_repository.get_derived_artifact(
            candidate_id=command.candidate_id,
            artifact_type=ArtifactType.RESUME_VACANCY_FINAL.value,
            derived_from_artifact_id=command.artifact_id,
        )
        notes = self._artifact_notes(source_artifact)
        final_notes = {
            **notes,
            "finalized_from_artifact_id": command.artifact_id,
            "allow_warnings": command.allow_warnings,
        }
        if existing is not None:
            storage_path = Path(str(existing["storage_path"]))
            previous_state = dict(existing)
            storage_path.parent.mkdir(parents=True, exist_ok=True)
            storage_path.write_text(markdown, encoding="utf-8")
            with write_tx(self._conn, immediate=True):
                self._artifact_repository.update_artifact_content(
                    artifact_id=str(existing["artifact_id"]),
                    storage_path=str(storage_path),
                    content_hash=content_hash,
                    notes=json.dumps(final_notes, ensure_ascii=False),
                    derived_from_artifact_id=command.artifact_id,
                )
                current = self._artifact_repository.get_artifact(str(existing["artifact_id"]))
                self._audit_repository.record_event(
                    command_name=type(command).__name__,
                    actor="system",
                    entity_type="artifact",
                    entity_id=str(existing["artifact_id"]),
                    previous_state=previous_state,
                    new_state=current,
                )
            final_quality_gate = self._candidate_handlers.run_resume_quality_gate(RunResumeQualityGate(artifact_id=str(existing["artifact_id"])))
            return {
                "artifact_id": str(existing["artifact_id"]),
                "artifact_type": str(existing["artifact_type"]),
                "storage_path": str(storage_path),
                "derived_from_artifact_id": command.artifact_id,
                "overwritten": True,
                "quality_gate": final_quality_gate,
            }
        artifact_id = str(uuid.uuid4())
        storage_path = self._candidate_artifact_path(
            candidate_id=command.candidate_id,
            artifact_id=artifact_id,
            artifact_type=ArtifactType.RESUME_VACANCY_FINAL.value,
            candidate_label=self._candidate_label_for_id(command.candidate_id),
            artifact_label=f"{notes.get('company_name') or 'company'}-{notes.get('target_role') or 'role'}",
        )
        storage_path.parent.mkdir(parents=True, exist_ok=True)
        storage_path.write_text(markdown, encoding="utf-8")
        try:
            with write_tx(self._conn, immediate=True):
                self._artifact_repository.create_artifact(
                    artifact_id=artifact_id,
                    artifact_type=ArtifactType.RESUME_VACANCY_FINAL.value,
                    candidate_id=command.candidate_id,
                    storage_path=str(storage_path),
                    content_hash=content_hash,
                    notes=json.dumps(final_notes, ensure_ascii=False),
                    derived_from_artifact_id=command.artifact_id,
                )
                self._audit_repository.record_event(
                    command_name=type(command).__name__,
                    actor="system",
                    entity_type="artifact",
                    entity_id=artifact_id,
                    previous_state=source_artifact,
                    new_state={
                        "artifact_type": ArtifactType.RESUME_VACANCY_FINAL.value,
                        "storage_path": str(storage_path),
                        "derived_from_artifact_id": command.artifact_id,
                        "quality_gate_status": source_quality_gate["status"],
                    },
                )
        except Exception:
            self._cleanup_created_file(storage_path)
            raise
        final_quality_gate = self._candidate_handlers.run_resume_quality_gate(RunResumeQualityGate(artifact_id=artifact_id))
        return {
            "artifact_id": artifact_id,
            "artifact_type": ArtifactType.RESUME_VACANCY_FINAL.value,
            "storage_path": str(storage_path),
            "derived_from_artifact_id": command.artifact_id,
            "overwritten": False,
            "quality_gate": final_quality_gate,
        }

    def create_application_draft(self, command: CreateApplicationDraft) -> dict[str, object]:
        profile = self._candidate_repository.get_candidate_profile_view(command.candidate_id)
        if profile is None:
            raise KeyError(f"Unknown candidate_id: {command.candidate_id}")
        vacancy = self._vacancy_repository.get_vacancy(command.candidate_id, command.canonical_vacancy_id)
        if vacancy is None:
            raise KeyError(f"Unknown canonical_vacancy_id: {command.canonical_vacancy_id}")
        evidence = self._evidence_repository.get_resume_evidence(command.candidate_id)
        markdown = self._application_draft_service.build_message_artifact(
            candidate_profile={**asdict(profile), **evidence},
            vacancy=vacancy,
            language=command.language,
            target_role=command.target_role,
        )
        content_hash = str(uuid.uuid5(uuid.NAMESPACE_URL, markdown))
        existing = self._artifact_repository.find_reusable_artifact(
            candidate_id=command.candidate_id,
            artifact_type="message_artifact",
            content_hash=content_hash,
        )
        if existing is not None:
            previous_application = self._vacancy_repository.get_application(command.candidate_id, command.canonical_vacancy_id)
            with write_tx(self._conn, immediate=True):
                application = self._vacancy_repository.attach_application_message_artifact(
                    candidate_id=command.candidate_id,
                    canonical_vacancy_id=command.canonical_vacancy_id,
                    message_artifact_id=str(existing["artifact_id"]),
                )
                self._record_application_artifact_usage(
                    artifact_id=str(existing["artifact_id"]),
                    candidate_id=command.candidate_id,
                    application_id=str(application["application_id"]),
                )
                self._audit_repository.record_event(
                    command_name=type(command).__name__,
                    actor="system",
                    entity_type="application",
                    entity_id=str(application["application_id"]),
                    previous_state=previous_application,
                    new_state=application,
                )
            gate_result = self._run_message_quality_gate(
                artifact_id=str(existing["artifact_id"]),
                candidate_id=command.candidate_id,
                target_role=command.target_role or str(vacancy.get("role_title") or ""),
                target_company=str(vacancy.get("company_name") or ""),
            )
            return {
                "application_id": application["application_id"],
                "artifact_id": str(existing["artifact_id"]),
                "storage_path": str(existing["storage_path"]),
                "reused": True,
                "quality_gate": gate_result,
            }

        artifact_id = str(uuid.uuid4())
        storage_path = self._candidate_artifact_path(
            candidate_id=command.candidate_id,
            artifact_id=artifact_id,
            artifact_type="message_artifact",
            candidate_label=self._candidate_label_from_profile(asdict(profile)),
        )
        storage_path.parent.mkdir(parents=True, exist_ok=True)
        storage_path.write_text(markdown, encoding="utf-8")
        try:
            with write_tx(self._conn, immediate=True):
                self._artifact_repository.create_artifact(
                    artifact_id=artifact_id,
                    artifact_type="message_artifact",
                    candidate_id=command.candidate_id,
                    storage_path=str(storage_path),
                    content_hash=content_hash,
                    notes=json.dumps(
                        {
                            "language": command.language,
                            "target_role": command.target_role,
                            "canonical_vacancy_id": command.canonical_vacancy_id,
                        },
                        ensure_ascii=False,
                    ),
                )
                application = self._vacancy_repository.attach_application_message_artifact(
                    candidate_id=command.candidate_id,
                    canonical_vacancy_id=command.canonical_vacancy_id,
                    message_artifact_id=artifact_id,
                )
                self._record_application_artifact_usage(
                    artifact_id=artifact_id,
                    candidate_id=command.candidate_id,
                    application_id=str(application["application_id"]),
                )
                self._audit_repository.record_event(
                    command_name=type(command).__name__,
                    actor="system",
                    entity_type="application",
                    entity_id=str(application["application_id"]),
                    previous_state=None,
                    new_state=application,
                )
        except Exception:
            self._cleanup_created_file(storage_path)
            raise
        gate_result = self._run_message_quality_gate(
            artifact_id=artifact_id,
            candidate_id=command.candidate_id,
            target_role=command.target_role or str(vacancy.get("role_title") or ""),
            target_company=str(vacancy.get("company_name") or ""),
        )
        return {
            "application_id": application["application_id"],
            "artifact_id": artifact_id,
            "storage_path": str(storage_path),
            "reused": False,
            "quality_gate": gate_result,
        }

    def _run_message_quality_gate(
        self,
        *,
        artifact_id: str,
        candidate_id: str,
        target_role: str | None,
        target_company: str | None,
    ) -> dict[str, object]:
        artifact = self._artifact_repository.get_artifact(artifact_id)
        if artifact is None:
            raise KeyError(f"Unknown artifact_id: {artifact_id}")
        if str(artifact.get("candidate_id")) != candidate_id:
            raise PermissionError("artifact_id does not belong to the requested candidate")
        markdown = Path(str(artifact["storage_path"])).read_text(encoding="utf-8")
        gate_result = self._resume_quality_gate_service.check_application_message(
            markdown=markdown,
            target_role=target_role,
            target_company=target_company,
        )
        with write_tx(self._conn, immediate=True):
            quality_gate_run_id = self._quality_gate_repository.record_run(
                gate_name="application_message_quality_gate",
                subject_type="artifact",
                subject_id=artifact_id,
                candidate_id=candidate_id,
                status=str(gate_result["status"]),
                issues=list(gate_result["issues"]),
            )
        return {
            "quality_gate_run_id": quality_gate_run_id,
            "status": gate_result["status"],
            "issues": gate_result["issues"],
        }

    def _run_application_payload_quality_gate(
        self,
        *,
        candidate_id: str,
        application_id: str,
        resume_quality_gate: dict[str, object],
        message_quality_gate: dict[str, object],
    ) -> dict[str, object]:
        statuses = [str(resume_quality_gate["status"]), str(message_quality_gate["status"])]
        if "fail" in statuses:
            status = "fail"
        elif "warn" in statuses:
            status = "warn"
        else:
            status = "pass"
        issues = [
            {
                "component": "resume",
                "status": resume_quality_gate["status"],
                "quality_gate_run_id": resume_quality_gate.get("quality_gate_run_id"),
                "issues": resume_quality_gate.get("issues", []),
            },
            {
                "component": "message",
                "status": message_quality_gate["status"],
                "quality_gate_run_id": message_quality_gate.get("quality_gate_run_id"),
                "issues": message_quality_gate.get("issues", []),
            },
        ]
        with write_tx(self._conn, immediate=True):
            quality_gate_run_id = self._quality_gate_repository.record_run(
                gate_name="application_payload_quality_gate",
                subject_type="application",
                subject_id=application_id,
                candidate_id=candidate_id,
                status=status,
                issues=issues,
            )
        return {
            "quality_gate_run_id": quality_gate_run_id,
            "status": status,
            "issues": issues,
        }

    def _record_application_artifact_usage(
        self,
        *,
        artifact_id: str,
        candidate_id: str,
        application_id: str,
        usage_type: str = "application_draft_attached",
    ) -> None:
        self._artifact_usage_repository.record_usage(
            artifact_id=artifact_id,
            candidate_id=candidate_id,
            usage_type=usage_type,
            target_entity_type="application",
            target_entity_id=application_id,
            notes=None,
        )

    def _record_reconciliation_for_board_action(self, action: dict[str, object]) -> tuple[dict[str, object], bool]:
        classification = self._classify_board_action_drift(action)
        item, reused = self._reconciliation_repository.record_item(
            candidate_id=str(action["candidate_id"]),
            board_action_id=str(action["board_action_id"]),
            canonical_vacancy_id=str(action["canonical_vacancy_id"]) if action.get("canonical_vacancy_id") else None,
            application_id=str(action["application_id"]) if action.get("application_id") else None,
            platform=str(action["platform"]) if action.get("platform") else None,
            external_target=str(action["external_target"]) if action.get("external_target") else None,
            drift_type=classification["drift_type"],
            outcome=classification["outcome"],
            review_status=classification["review_status"],
            reason=classification["reason"],
            recommended_action=classification["recommended_action"],
            idempotency_key=f"board_action:{action['board_action_id']}",
        )
        if not reused:
            self._audit_repository.record_event(
                command_name="RecordBoardActionReconciliation",
                actor="system",
                entity_type="reconciliation_item",
                entity_id=str(item["reconciliation_item_id"]),
                previous_state=None,
                new_state=item,
            )
        return item, reused

    def _classify_board_action_drift(self, action: dict[str, object]) -> dict[str, str]:
        action_type = str(action.get("action_type") or "")
        action_state = str(action.get("action_state") or "")
        canonical_vacancy_id = str(action.get("canonical_vacancy_id") or "")
        application_id = str(action.get("application_id") or "")
        artifact_id = str(action.get("artifact_id") or "")
        if action_state == "planned":
            return self._reconciliation_classification(
                drift_type="informational_drift",
                outcome="reject_as_invalid",
                reason="Planned board action is not an external drift signal yet.",
                recommended_action="Keep the planned action as an operator task; reconcile only after an external/manual event occurs.",
            )
        if action_state == "needs_review":
            return self._reconciliation_classification(
                drift_type="conflict_drift",
                outcome="needs_review",
                reason="Manual board action was explicitly recorded as needs_review.",
                recommended_action="Review the external action and decide whether to update internal state.",
            )
        if action_type in {"manual_note", "visibility_checked", "vacancy_opened", "saved_search_configured"}:
            return self._reconciliation_classification(
                drift_type="informational_drift",
                outcome="record_only",
                reason="External signal is informational and does not require lifecycle mutation.",
                recommended_action="Keep as audit context unless repeated signals reveal a real workflow mismatch.",
            )
        if not canonical_vacancy_id and action_type != "profile_updated":
            return self._reconciliation_classification(
                drift_type="conflict_drift",
                outcome="needs_review",
                reason="Board-side action is not confidently linked to a canonical vacancy.",
                recommended_action="Link the action to a vacancy or reject it as invalid.",
            )
        if action_type in {"application_submitted", "message_sent"} and not application_id:
            return self._reconciliation_classification(
                drift_type="conflict_drift",
                outcome="needs_review",
                reason="Board-side application/message action has no internal application context.",
                recommended_action="Create or link an application before syncing the external action.",
            )
        if action_type in {"application_submitted", "message_sent", "profile_updated"} and not artifact_id:
            return self._reconciliation_classification(
                drift_type="conflict_drift",
                outcome="needs_review",
                reason="External-facing action is missing an artifact reference.",
                recommended_action="Attach the artifact used externally or reject the signal.",
            )
        if action_type == "vacancy_hidden":
            return self._reconciliation_classification(
                drift_type="operational_drift",
                outcome="needs_review",
                reason="Board-side hidden state can conflict with internal workflow state.",
                recommended_action="Decide explicitly whether to hide, reject, or keep the internal vacancy active.",
            )
        return self._reconciliation_classification(
            drift_type="operational_drift",
            outcome="auto_accept",
            reason="External action has enough internal context to be accepted as a synced operational fact.",
            recommended_action="No automatic lifecycle mutation was performed; use explicit commands if internal state should change.",
        )

    def _reconciliation_classification(
        self,
        *,
        drift_type: str,
        outcome: str,
        reason: str,
        recommended_action: str,
    ) -> dict[str, str]:
        return {
            "drift_type": drift_type,
            "outcome": outcome,
            "review_status": "open" if outcome == "needs_review" else "resolved",
            "reason": reason,
            "recommended_action": recommended_action,
        }

    def _reconciliation_daily_actions(self, candidate_id: str) -> list[dict[str, object]]:
        actions = []
        for item in self._reconciliation_repository.list_items(candidate_id=candidate_id, review_status="open"):
            actions.append(
                {
                    "action_type": "review_reconciliation_item",
                    "action_group": "reconciliation",
                    "priority": 94,
                    "reconciliation_item_id": item["reconciliation_item_id"],
                    "board_action_id": item["board_action_id"],
                    "canonical_vacancy_id": item["canonical_vacancy_id"],
                    "application_id": item["application_id"],
                    "label": item["reason"],
                    "updated_at": item["updated_at"],
                    "outcome": item["outcome"],
                    "drift_type": item["drift_type"],
                }
            )
        return actions

    def _manual_board_action_idempotency_key(
        self,
        *,
        candidate_id: str,
        platform: str,
        action_type: str,
        canonical_vacancy_id: str | None,
        application_id: str | None,
        artifact_id: str | None,
        external_target: str | None,
        occurred_at: str,
    ) -> str:
        parts = [
            candidate_id,
            platform,
            action_type,
            canonical_vacancy_id or "",
            application_id or "",
            artifact_id or "",
            external_target or "",
            occurred_at,
        ]
        return str(uuid.uuid5(uuid.NAMESPACE_URL, "|".join(parts)))

    def _external_action_approval_idempotency_key(
        self,
        *,
        platform: str,
        action_type: str,
        artifact_id: str | None,
        target_entity_id: str | None,
        external_target: str | None,
    ) -> str:
        parts = [
            "external_action_approval",
            platform,
            action_type,
            artifact_id or "",
            target_entity_id or "",
            external_target or "",
        ]
        return str(uuid.uuid5(uuid.NAMESPACE_URL, "|".join(parts)))

    def _interview_round_idempotency_key(
        self,
        *,
        candidate_id: str,
        application_id: str,
        round_type: str,
        scheduled_at: str | None,
        interviewer_name: str | None,
    ) -> str:
        parts = [
            "interview_round",
            candidate_id,
            application_id,
            round_type,
            scheduled_at or "",
            interviewer_name or "",
        ]
        return str(uuid.uuid5(uuid.NAMESPACE_URL, "|".join(parts)))

    def _assert_external_action_approval(
        self,
        *,
        candidate_id: str,
        approval_id: str,
        platform: str,
        action_type: str,
        artifact_id: str | None,
    ) -> dict[str, object]:
        approval = self._approval_repository.get_approval(candidate_id=candidate_id, approval_id=approval_id)
        if approval is None:
            raise KeyError(f"Unknown external_action_approval_id: {approval_id}")
        if approval.get("approval_type") != "external_action_approval":
            raise PermissionError("approval_id is not an external_action_approval")
        if approval.get("approval_state") != "approved":
            raise PermissionError("external_action_approval must be approved")
        if str(approval.get("platform") or "") != platform:
            raise PermissionError("external_action_approval platform does not match board action")
        if str(approval.get("action_type") or "") != action_type:
            raise PermissionError("external_action_approval action_type does not match board action")
        if artifact_id and str(approval.get("artifact_id") or "") != artifact_id:
            raise PermissionError("external_action_approval artifact_id does not match board action")
        return approval

    def _assert_artifact_belongs_to_candidate(self, artifact_id: str, candidate_id: str) -> None:
        artifact = self._artifact_repository.get_artifact(artifact_id)
        if artifact is None:
            raise KeyError(f"Unknown artifact_id: {artifact_id}")
        if str(artifact.get("candidate_id")) != candidate_id:
            raise PermissionError("artifact_id does not belong to the requested candidate")

    def _assert_application_belongs_to_vacancy(
        self,
        *,
        candidate_id: str,
        canonical_vacancy_id: str,
        application_id: str,
    ) -> None:
        application = self._vacancy_repository.get_application(candidate_id, canonical_vacancy_id)
        if application is None or str(application["application_id"]) != application_id:
            raise PermissionError("application_id does not belong to the requested candidate vacancy")

    def _count_by(self, items: list[dict[str, object]], key: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in items:
            value = str(item.get(key) or "unknown")
            counts[value] = counts.get(value, 0) + 1
        return counts

    def _pipeline_recommendations(
        self,
        ranked: list[dict[str, object]],
        daily_actions: list[dict[str, object]],
        applications: list[dict[str, object]],
    ) -> list[str]:
        recommendations: list[str] = []
        high_fit = [item for item in ranked if item.get("fit_label") == "high"]
        if high_fit:
            recommendations.append("Review high-fit vacancies first and shortlist only explicit matches.")
        if any(item.get("action_type") == "review_application_draft" for item in daily_actions):
            recommendations.append("Review drafted application messages before any external action.")
        if not applications and high_fit:
            recommendations.append("Prepare application payload for the best high-fit vacancy.")
        if not ranked:
            recommendations.append("Import a small batch of 5-10 vacancies before strategy adjustment.")
        return recommendations

    def _select_vacancy_resume_source(
        self,
        *,
        candidate_id: str,
        target_role: str,
        source_resume_artifact_id: str | None,
    ) -> dict[str, object]:
        allowed_types = {ArtifactType.RESUME_MARKDOWN.value, ArtifactType.RESUME_MARKDOWN_FINAL.value}
        if source_resume_artifact_id:
            artifact = self._artifact_repository.get_artifact(source_resume_artifact_id)
            if artifact is None:
                raise KeyError(f"Unknown source_resume_artifact_id: {source_resume_artifact_id}")
            if str(artifact["candidate_id"]) != candidate_id:
                raise PermissionError("source_resume_artifact_id does not belong to the requested candidate")
            if str(artifact["artifact_type"]) not in allowed_types:
                raise ValueError("source_resume_artifact_id must reference resume_markdown or resume_markdown_final")
            return artifact
        normalized_role = self._normalize_role(target_role)
        finals = [
            artifact for artifact in self._artifact_repository.list_candidate_artifacts(
                candidate_id=candidate_id,
                artifact_type=ArtifactType.RESUME_MARKDOWN_FINAL.value,
            )
            if self._normalize_role(str(self._artifact_notes(artifact).get("target_role") or "")) == normalized_role
        ]
        if finals:
            return finals[0]
        drafts = [
            artifact for artifact in self._artifact_repository.list_candidate_artifacts(
                candidate_id=candidate_id,
                artifact_type=ArtifactType.RESUME_MARKDOWN.value,
            )
            if self._normalize_role(str(self._artifact_notes(artifact).get("target_role") or "")) == normalized_role
        ]
        return {
            "status": "needs_source_selection",
            "reason": "No final resume for the vacancy role was found; choose a source draft resume explicitly.",
            "target_role": target_role,
            "source_options": [
                {
                    "artifact_id": artifact["artifact_id"],
                    "artifact_type": artifact["artifact_type"],
                    "storage_path": artifact["storage_path"],
                    "notes": self._artifact_notes(artifact),
                }
                for artifact in drafts
            ],
        }

    def _find_existing_vacancy_artifact(
        self,
        *,
        candidate_id: str,
        artifact_type: str,
        canonical_vacancy_id: str,
    ) -> dict[str, object] | None:
        for artifact in self._artifact_repository.list_candidate_artifacts(candidate_id=candidate_id, artifact_type=artifact_type):
            if str(self._artifact_notes(artifact).get("canonical_vacancy_id") or "") == canonical_vacancy_id:
                return artifact
        return None

    def _artifact_notes(self, artifact: dict[str, object]) -> dict[str, object]:
        raw_notes = artifact.get("notes")
        if not raw_notes:
            return {}
        try:
            notes = json.loads(str(raw_notes))
        except json.JSONDecodeError:
            return {}
        return notes if isinstance(notes, dict) else {}

    def _normalize_role(self, value: str) -> str:
        return " ".join(value.casefold().split())

    def _candidate_label_for_id(self, candidate_id: str) -> str | None:
        profile = self._candidate_repository.get_candidate_profile_view(candidate_id)
        if profile is not None:
            return self._candidate_label_from_profile(asdict(profile))
        candidate = self._candidate_repository.get_candidate(candidate_id)
        return str(candidate.get("display_name") or "") if candidate else None

    def _cleanup_created_file(self, path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            return

    def _candidate_artifact_path(
        self,
        *,
        candidate_id: str,
        artifact_id: str,
        artifact_type: str,
        candidate_label: str | None,
        artifact_label: str | None = None,
    ) -> Path:
        return ArtifactPathService.candidate_artifact_path(
            artifact_root=self._artifact_root,
            candidate_id=candidate_id,
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            candidate_label=candidate_label,
            artifact_label=artifact_label,
        )

    def _candidate_label_from_profile(self, profile: dict[str, object]) -> str | None:
        core = profile.get("core_profile", {})
        if isinstance(core, dict) and core.get("full_name"):
            return str(core["full_name"])
        if profile.get("display_name"):
            return str(profile["display_name"])
        return None
