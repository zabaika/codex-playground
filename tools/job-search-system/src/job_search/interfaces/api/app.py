from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from job_search.application.commands.candidate import (
    BuildCandidateAiExtractionRequest,
    ConfirmCandidateProfileDraft,
    CreateCandidate,
    FinalizeResumeMarkdown,
    GenerateCareerPathingFull,
    GenerateCandidateProfileDraftFromSources,
    GenerateCareerPathingLite,
    GenerateJobSearchPlaybook,
    GenerateResumeMarkdown,
    GenerateResumePositioningBrief,
    GenerateResumeRoastReport,
    ImportCandidateAiExtractionDraft,
    RegisterCandidateSource,
    RunResumeQualityGate,
    SetActiveCandidate,
)
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
from job_search.application.queries.candidate import (
    GetActiveCandidate,
    GetCandidateDraftReview,
    GetCandidateExternalProfiles,
    GetCandidateProfile,
    GetCandidateSources,
    GetLatestCandidateDraft,
    ListCandidates,
    SearchResumeKbEvidence,
)
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
from job_search.application.services.system_status_service import SystemStatusService
from job_search import API_CONTRACT_VERSION
from job_search.config import RuntimeSettings, load_workspace_settings
from job_search.infrastructure.board_adapters.generic_vacancy_text_adapter import GenericVacancyTextAdapter
from job_search.infrastructure.board_adapters.hh_ru_vacancy_adapter import HhRuVacancyAdapter
from job_search.infrastructure.board_adapters.linkedin_vacancy_adapter import LinkedInVacancyAdapter
from job_search.interfaces.codex.candidate_commands import build_candidate_handlers
from job_search.interfaces.codex.vacancy_commands import build_vacancy_handlers


@dataclass(frozen=True, slots=True)
class ApiResponse:
    status: int
    payload: Any


class ApiRequestTooLargeError(ValueError):
    pass


class JobSearchApi:
    def __init__(self, *, runtime_settings: RuntimeSettings, workspace_path: Path) -> None:
        self._workspace_path = workspace_path
        self._runtime_settings = runtime_settings
        self._candidate_handlers = build_candidate_handlers(runtime_settings, workspace_path)
        self._vacancy_handlers = build_vacancy_handlers(runtime_settings, workspace_path)
        self._system_status_service = SystemStatusService(
            runtime_settings=runtime_settings,
            workspace_settings=load_workspace_settings(workspace_path),
            migrations_dir=Path(__file__).resolve().parents[2] / "infrastructure" / "migrations",
        )

    def close(self) -> None:
        self._candidate_handlers.close()
        self._vacancy_handlers.close()

    @property
    def max_body_bytes(self) -> int:
        return self._runtime_settings.api_max_body_bytes

    def dispatch(self, *, method: str, raw_path: str, body: bytes = b"") -> ApiResponse:
        parsed = urlsplit(raw_path)
        path = parsed.path.rstrip("/") or "/"
        query = {key: values[-1] for key, values in parse_qs(parsed.query).items() if values}
        try:
            if len(body) > self._runtime_settings.api_max_body_bytes:
                raise ApiRequestTooLargeError(
                    f"Request body exceeds api_max_body_bytes={self._runtime_settings.api_max_body_bytes}"
                )
            payload = self._parse_body(body) if method in {"POST", "PUT", "PATCH"} else {}
            result = self._dispatch_unsafe(method=method, path=path, query=query, payload=payload)
            return ApiResponse(status=200, payload={"ok": True, "data": result})
        except (ValueError, KeyError, PermissionError, RuntimeError) as exc:
            status = 413 if isinstance(exc, ApiRequestTooLargeError) else 400 if not isinstance(exc, KeyError) else 404
            return ApiResponse(
                status=status,
                payload={"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}},
            )

    def _dispatch_unsafe(
        self,
        *,
        method: str,
        path: str,
        query: dict[str, str],
        payload: dict[str, Any],
    ) -> Any:
        if method == "GET" and path == "/health":
            return {"status": "ok", "api_contract_version": API_CONTRACT_VERSION}
        if method == "GET" and path == "/version":
            return self._system_status_service.version()
        if method == "GET" and path == "/system/status":
            return self._system_status_service.status()
        if method == "GET" and path == "/system/observability":
            return self._system_status_service.observability(
                candidate_id=query.get("candidate_id"),
                limit=self._optional_int(query.get("limit"), default=20),
            )
        if method == "GET" and path == "/system/strategy-report":
            return self._system_status_service.strategy_report(
                candidate_id=query.get("candidate_id"),
                limit=self._optional_int(query.get("limit"), default=20),
            )

        if path.startswith("/candidates"):
            return self._dispatch_candidate(method=method, path=path, query=query, payload=payload)
        if path.startswith("/vacancies"):
            return self._dispatch_vacancy(method=method, path=path, query=query, payload=payload)
        raise KeyError(f"Unknown API route: {method} {path}")

    def _dispatch_candidate(
        self,
        *,
        method: str,
        path: str,
        query: dict[str, str],
        payload: dict[str, Any],
    ) -> Any:
        handlers = self._candidate_handlers
        if method == "POST" and path == "/candidates":
            return handlers.create_candidate(CreateCandidate(display_name=self._required_str(payload, "display_name")))
        if method == "POST" and path == "/candidates/active":
            return handlers.set_active_candidate(SetActiveCandidate(candidate_id=self._required_str(payload, "candidate_id")))
        if method == "GET" and path == "/candidates/active":
            return handlers.get_active_candidate(GetActiveCandidate())
        if method == "GET" and path == "/candidates":
            return handlers.list_candidates(ListCandidates())

        if method == "GET" and path == "/candidates/profile":
            return handlers.get_candidate_profile(GetCandidateProfile(candidate_id=self._candidate_id(query))) or {}
        if method == "GET" and path == "/candidates/sources":
            return handlers.get_candidate_sources(GetCandidateSources(candidate_id=self._candidate_id(query)))
        if method == "GET" and path == "/candidates/external-profiles":
            return handlers.get_candidate_external_profiles(GetCandidateExternalProfiles(candidate_id=self._candidate_id(query)))
        if method == "GET" and path == "/candidates/latest-draft":
            return self._normalize_latest_draft(
                handlers.get_latest_candidate_draft(GetLatestCandidateDraft(candidate_id=self._candidate_id(query)))
            )
        if method == "GET" and path == "/candidates/draft-review":
            return handlers.get_candidate_draft_review(
                GetCandidateDraftReview(candidate_id=self._candidate_id(query), draft_id=query.get("draft_id"))
            )
        if method == "GET" and path == "/candidates/resume-kb-evidence":
            return handlers.search_resume_kb_evidence(
                SearchResumeKbEvidence(
                    candidate_id=self._candidate_id(query),
                    target_role=query.get("target_role"),
                    query=query.get("query"),
                    limit=self._optional_int(query.get("limit"), default=5),
                )
            )

        if method == "POST" and path == "/candidates/sources/text":
            return handlers.register_candidate_source(
                RegisterCandidateSource(
                    candidate_id=self._candidate_id(payload),
                    source_kind=self._required_str(payload, "source_kind"),
                    source_origin=str(payload.get("source_origin") or "text"),
                    content_text=self._required_str(payload, "content_text"),
                    notes=payload.get("notes"),
                )
            )
        if method == "POST" and path == "/candidates/sources/file":
            if not self._runtime_settings.api_allow_local_file_sources:
                raise PermissionError(
                    "API local file source ingestion is disabled by default; use CLI for file sources or enable api_allow_local_file_sources"
                )
            return handlers.register_candidate_source(
                RegisterCandidateSource(
                    candidate_id=self._candidate_id(payload),
                    source_kind=self._required_str(payload, "source_kind"),
                    source_origin="file",
                    file_path=self._required_str(payload, "file_path"),
                    notes=payload.get("notes"),
                )
            )
        if method == "POST" and path == "/candidates/sources/url":
            return handlers.register_candidate_source(
                RegisterCandidateSource(
                    candidate_id=self._candidate_id(payload),
                    source_kind=self._required_str(payload, "source_kind"),
                    source_origin="url",
                    source_url=self._required_str(payload, "source_url"),
                    content_text=payload.get("content_text"),
                    notes=payload.get("notes"),
                )
            )
        if method == "POST" and path == "/candidates/drafts":
            return handlers.generate_candidate_profile_draft(
                GenerateCandidateProfileDraftFromSources(
                    candidate_id=self._candidate_id(payload),
                    source_artifact_ids=self._optional_string_list(payload.get("source_artifact_ids")),
                )
            )
        if method == "POST" and path == "/candidates/ai-extraction-request":
            return handlers.build_candidate_ai_extraction_request(
                BuildCandidateAiExtractionRequest(
                    candidate_id=self._candidate_id(payload),
                    source_artifact_ids=self._optional_string_list(payload.get("source_artifact_ids")),
                )
            )
        if method == "POST" and path == "/candidates/ai-drafts":
            return handlers.import_candidate_ai_extraction_draft(
                ImportCandidateAiExtractionDraft(
                    candidate_id=self._candidate_id(payload),
                    response_payload=self._required_dict(payload, "response_payload"),
                    source_artifact_ids=self._optional_string_list(payload.get("source_artifact_ids")),
                )
            )
        if method == "POST" and path == "/candidates/confirm-draft":
            return handlers.confirm_candidate_profile_draft(
                ConfirmCandidateProfileDraft(
                    candidate_id=self._candidate_id(payload),
                    draft_id=self._required_str(payload, "draft_id"),
                    accepted_field_values=self._required_dict(payload, "accepted_field_values", default={}),
                )
            )
        if method == "POST" and path == "/candidates/resume":
            return handlers.generate_resume_markdown(
                GenerateResumeMarkdown(
                    candidate_id=self._candidate_id(payload),
                    language=str(payload.get("language") or "en"),
                    target_role=payload.get("target_role"),
                )
            )
        if method == "POST" and path == "/candidates/resume-quality":
            return handlers.run_resume_quality_gate(RunResumeQualityGate(artifact_id=self._required_str(payload, "artifact_id")))
        if method == "POST" and path == "/candidates/resume-final":
            return handlers.finalize_resume_markdown(
                FinalizeResumeMarkdown(
                    artifact_id=self._required_str(payload, "artifact_id"),
                    allow_warnings=bool(payload.get("allow_warnings", False)),
                )
            )
        if method == "POST" and path == "/candidates/resume-roast":
            return handlers.generate_resume_roast_report(
                GenerateResumeRoastReport(
                    artifact_id=self._required_str(payload, "artifact_id"),
                    target_role=payload.get("target_role"),
                )
            )
        if method == "POST" and path == "/candidates/positioning-brief":
            return handlers.generate_resume_positioning_brief(
                GenerateResumePositioningBrief(
                    candidate_id=self._candidate_id(payload),
                    target_role=self._required_str(payload, "target_role"),
                    language=str(payload.get("language") or "en"),
                )
            )
        if method == "POST" and path == "/candidates/career-pathing-lite":
            return handlers.generate_career_pathing_lite(
                GenerateCareerPathingLite(
                    candidate_id=self._candidate_id(payload),
                    target_roles=self._optional_string_list(payload.get("target_roles")),
                )
            )
        if method == "POST" and path == "/candidates/career-pathing-full":
            return handlers.generate_career_pathing_full(
                GenerateCareerPathingFull(
                    candidate_id=self._candidate_id(payload),
                    target_roles=self._optional_string_list(payload.get("target_roles")),
                    include_kb=bool(payload.get("include_kb", True)),
                )
            )
        if method == "POST" and path == "/candidates/playbook":
            return handlers.generate_job_search_playbook(GenerateJobSearchPlaybook(candidate_id=self._candidate_id(payload)))

        raise KeyError(f"Unknown candidate API route: {method} {path}")

    def _dispatch_vacancy(
        self,
        *,
        method: str,
        path: str,
        query: dict[str, str],
        payload: dict[str, Any],
    ) -> Any:
        handlers = self._vacancy_handlers
        if method == "POST" and path == "/vacancies/import-json":
            return handlers.import_vacancy_batch(
                ImportVacancyBatch(
                    candidate_id=self._candidate_id(payload),
                    source_kind=str(payload.get("source_kind") or "manual"),
                    items=[VacancyImportItem(**item) for item in self._required_list(payload, "items")],
                )
            )
        if method == "POST" and path == "/vacancies/url-seeds":
            return handlers.create_vacancy_url_enrichment_seed(
                CreateVacancyUrlEnrichmentSeed(
                    candidate_id=self._candidate_id(payload),
                    source_url=self._required_str(payload, "source_url"),
                    platform=payload.get("platform"),
                    source_origin=str(payload.get("source_origin") or "saved_url"),
                    notes=payload.get("notes"),
                    idempotency_key=payload.get("idempotency_key"),
                )
            )
        if method == "GET" and path == "/vacancies/url-seeds":
            return handlers.list_vacancy_url_enrichment_seeds(
                ListVacancyUrlEnrichmentSeeds(
                    candidate_id=self._candidate_id(query),
                    seed_status=query.get("seed_status"),
                    platform=query.get("platform"),
                )
            )
        if method == "POST" and path == "/vacancies/url-seeds/preview":
            return handlers.preview_vacancy_url_enrichment_seed(
                PreviewVacancyUrlEnrichmentSeed(
                    candidate_id=self._candidate_id(payload),
                    url_seed_id=self._required_str(payload, "url_seed_id"),
                    content_text=self._required_str(payload, "content_text"),
                    source_origin=str(payload.get("source_origin") or "manual_page_text"),
                )
            )
        if method == "POST" and path == "/vacancies/url-seeds/confirm":
            return handlers.confirm_vacancy_url_enrichment_import(
                ConfirmVacancyUrlEnrichmentImport(
                    candidate_id=self._candidate_id(payload),
                    url_seed_id=self._required_str(payload, "url_seed_id"),
                    source_kind=payload.get("source_kind"),
                )
            )
        if method == "POST" and path == "/vacancies/url-seeds/reject":
            return handlers.reject_vacancy_url_enrichment_seed(
                RejectVacancyUrlEnrichmentSeed(
                    candidate_id=self._candidate_id(payload),
                    url_seed_id=self._required_str(payload, "url_seed_id"),
                    rejection_reason=payload.get("rejection_reason"),
                )
            )
        if method == "POST" and path == "/vacancies/import-linkedin-text":
            content = self._required_str(payload, "content_text")
            extraction = LinkedInVacancyAdapter().extract_from_text(
                content,
                source_origin=str(payload.get("source_origin") or "manual_page"),
            )
            if not extraction.items:
                raise ValueError(f"No importable LinkedIn vacancies found: {', '.join(extraction.warnings)}")
            imported = handlers.import_vacancy_batch(
                ImportVacancyBatch(
                    candidate_id=self._candidate_id(payload),
                    source_kind="linkedin",
                    items=extraction.items,
                )
            )
            return {**imported, "source_kind": "linkedin", "warnings": extraction.warnings}
        if method == "POST" and path == "/vacancies/import-hh-ru-text":
            content = self._required_str(payload, "content_text")
            extraction = HhRuVacancyAdapter().extract_from_text(
                content,
                source_origin=str(payload.get("source_origin") or "search_results"),
            )
            if not extraction.items:
                raise ValueError(f"No importable hh.ru vacancies found: {', '.join(extraction.warnings)}")
            imported = handlers.import_vacancy_batch(
                ImportVacancyBatch(
                    candidate_id=self._candidate_id(payload),
                    source_kind="hh_ru",
                    items=extraction.items,
                )
            )
            return {**imported, "source_kind": "hh_ru", "warnings": extraction.warnings}
        if method == "POST" and path == "/vacancies/import-text":
            content = self._required_str(payload, "content_text")
            extraction = GenericVacancyTextAdapter().extract_from_text(
                content,
                source_origin=str(payload.get("source_origin") or "manual_text"),
            )
            if not extraction.items:
                raise ValueError(f"No importable vacancies found: {', '.join(extraction.warnings)}")
            imported = handlers.import_vacancy_batch(
                ImportVacancyBatch(
                    candidate_id=self._candidate_id(payload),
                    source_kind=str(payload.get("source_kind") or "generic_text"),
                    items=extraction.items,
                )
            )
            return {**imported, "source_kind": str(payload.get("source_kind") or "generic_text"), "warnings": extraction.warnings}
        if method == "GET" and path == "/vacancies":
            return handlers.list_vacancies(
                ListVacancies(
                    candidate_id=self._candidate_id(query),
                    processed=self._optional_bool(query.get("processed")),
                    workflow_stage=query.get("workflow_stage"),
                )
            )
        if method == "GET" and path == "/vacancies/show":
            return handlers.get_vacancy(
                GetVacancy(
                    candidate_id=self._candidate_id(query),
                    canonical_vacancy_id=self._required_str(query, "canonical_vacancy_id"),
                )
            )
        if method == "GET" and path == "/vacancies/rank":
            return handlers.list_ranked_vacancies(
                ListRankedVacancies(
                    candidate_id=self._candidate_id(query),
                    processed=self._optional_bool(query.get("processed"), default=False),
                )
            )
        if method == "POST" and path == "/vacancies/shortlist":
            return handlers.shortlist_vacancy(
                ShortlistVacancy(
                    candidate_id=self._candidate_id(payload),
                    canonical_vacancy_id=self._required_str(payload, "canonical_vacancy_id"),
                )
            )
        if method == "POST" and path == "/vacancies/stage":
            return handlers.update_vacancy_workflow_stage(
                UpdateVacancyWorkflowStage(
                    candidate_id=self._candidate_id(payload),
                    canonical_vacancy_id=self._required_str(payload, "canonical_vacancy_id"),
                    workflow_stage=self._required_str(payload, "workflow_stage"),
                )
            )
        if method == "POST" and path == "/vacancies/processed":
            return handlers.mark_vacancy_processed(
                MarkVacancyProcessed(
                    candidate_id=self._candidate_id(payload),
                    canonical_vacancy_id=self._required_str(payload, "canonical_vacancy_id"),
                )
            )
        if method == "POST" and path == "/vacancies/application-draft":
            return handlers.create_application_draft(
                CreateApplicationDraft(
                    candidate_id=self._candidate_id(payload),
                    canonical_vacancy_id=self._required_str(payload, "canonical_vacancy_id"),
                    language=str(payload.get("language") or "en"),
                    target_role=payload.get("target_role"),
                )
            )
        if method == "POST" and path == "/vacancies/application-payload":
            return handlers.prepare_application_payload(
                PrepareApplicationPayload(
                    candidate_id=self._candidate_id(payload),
                    canonical_vacancy_id=self._required_str(payload, "canonical_vacancy_id"),
                    language=str(payload.get("language") or "en"),
                    target_role=payload.get("target_role"),
                )
            )
        if method == "POST" and path == "/vacancies/resume":
            return handlers.generate_vacancy_resume(
                GenerateVacancyResume(
                    candidate_id=self._candidate_id(payload),
                    canonical_vacancy_id=self._required_str(payload, "canonical_vacancy_id"),
                    language=str(payload.get("language") or "en"),
                    source_resume_artifact_id=payload.get("source_resume_artifact_id"),
                )
            )
        if method == "POST" and path == "/vacancies/resume-final":
            return handlers.finalize_vacancy_resume(
                FinalizeVacancyResume(
                    candidate_id=self._candidate_id(payload),
                    artifact_id=self._required_str(payload, "artifact_id"),
                    allow_warnings=bool(payload.get("allow_warnings", False)),
                )
            )
        if method == "POST" and path == "/vacancies/touchpoints":
            return handlers.create_touchpoint(
                CreateTouchpoint(
                    candidate_id=self._candidate_id(payload),
                    canonical_vacancy_id=self._required_str(payload, "canonical_vacancy_id"),
                    application_id=payload.get("application_id"),
                    message_artifact_id=payload.get("message_artifact_id"),
                    channel=str(payload.get("channel") or "email"),
                    direction=str(payload.get("direction") or "outgoing"),
                    touchpoint_state=str(payload.get("touchpoint_state") or "sent"),
                    contact_name=payload.get("contact_name"),
                    occurred_at=payload.get("occurred_at"),
                    notes=payload.get("notes"),
                    follow_up_due_at=payload.get("follow_up_due_at"),
                )
            )
        if method == "GET" and path == "/vacancies/touchpoints":
            return handlers.list_touchpoints(
                ListTouchpoints(
                    candidate_id=self._candidate_id(query),
                    canonical_vacancy_id=query.get("canonical_vacancy_id"),
                    application_id=query.get("application_id"),
                )
            )
        if method == "POST" and path == "/vacancies/touchpoints/state":
            return handlers.update_touchpoint_state(
                UpdateTouchpointState(
                    candidate_id=self._candidate_id(payload),
                    touchpoint_id=self._required_str(payload, "touchpoint_id"),
                    touchpoint_state=self._required_str(payload, "touchpoint_state"),
                    replied_at=payload.get("replied_at"),
                )
            )
        if method == "POST" and path == "/vacancies/reminders/resolve":
            return handlers.resolve_reminder(
                ResolveReminder(candidate_id=self._candidate_id(payload), reminder_id=self._required_str(payload, "reminder_id"))
            )
        if method == "GET" and path == "/vacancies/daily-actions":
            return handlers.list_daily_actions(ListDailyActions(candidate_id=self._candidate_id(query)))
        if method == "GET" and path == "/vacancies/material-change-review":
            return handlers.list_material_change_review(ListMaterialChangeReview(candidate_id=self._candidate_id(query)))
        if method == "GET" and path == "/vacancies/pipeline-report":
            return handlers.get_pipeline_report(GetPipelineReport(candidate_id=self._candidate_id(query)))
        if method == "GET" and path == "/vacancies/board-checklist":
            return handlers.get_board_checklist(
                GetBoardChecklist(
                    candidate_id=self._candidate_id(query),
                    platform=self._required_str(query, "platform"),
                    canonical_vacancy_id=query.get("canonical_vacancy_id"),
                )
            )
        if method == "POST" and path == "/vacancies/board-actions":
            return handlers.record_manual_board_action(
                RecordManualBoardAction(
                    candidate_id=self._candidate_id(payload),
                    platform=self._required_str(payload, "platform"),
                    action_type=self._required_str(payload, "action_type"),
                    action_state=str(payload.get("action_state") or "completed"),
                    canonical_vacancy_id=payload.get("canonical_vacancy_id"),
                    application_id=payload.get("application_id"),
                    artifact_id=payload.get("artifact_id"),
                    external_target=payload.get("external_target"),
                    occurred_at=payload.get("occurred_at"),
                    notes=payload.get("notes"),
                    idempotency_key=payload.get("idempotency_key"),
                    external_action_approval_id=payload.get("external_action_approval_id"),
                )
            )
        if method == "GET" and path == "/vacancies/board-actions":
            return handlers.list_manual_board_actions(
                ListManualBoardActions(
                    candidate_id=self._candidate_id(query),
                    platform=query.get("platform"),
                    canonical_vacancy_id=query.get("canonical_vacancy_id"),
                )
            )
        if method == "GET" and path == "/vacancies/reconciliation":
            return handlers.list_reconciliation_items(
                ListReconciliationItems(
                    candidate_id=self._candidate_id(query),
                    review_status=query.get("review_status"),
                    outcome=query.get("outcome"),
                    platform=query.get("platform"),
                )
            )
        if method == "POST" and path == "/vacancies/reconciliation/resolve":
            return handlers.resolve_reconciliation_item(
                ResolveReconciliationItem(
                    candidate_id=self._candidate_id(payload),
                    reconciliation_item_id=self._required_str(payload, "reconciliation_item_id"),
                    review_status=str(payload.get("review_status") or "resolved"),
                    resolution_notes=payload.get("resolution_notes"),
                )
            )
        if method == "POST" and path == "/vacancies/artifact-acceptance":
            return handlers.record_artifact_acceptance(
                RecordArtifactAcceptance(
                    candidate_id=self._candidate_id(payload),
                    artifact_id=self._required_str(payload, "artifact_id"),
                    approval_state=str(payload.get("approval_state") or "accepted"),
                    actor=str(payload.get("actor") or "operator"),
                    reason=payload.get("reason"),
                    notes=payload.get("notes"),
                    idempotency_key=payload.get("idempotency_key"),
                )
            )
        if method == "POST" and path == "/vacancies/external-action-approval":
            return handlers.record_external_action_approval(
                RecordExternalActionApproval(
                    candidate_id=self._candidate_id(payload),
                    platform=self._required_str(payload, "platform"),
                    action_type=self._required_str(payload, "action_type"),
                    approval_state=str(payload.get("approval_state") or "approved"),
                    actor=str(payload.get("actor") or "operator"),
                    artifact_id=payload.get("artifact_id"),
                    canonical_vacancy_id=payload.get("canonical_vacancy_id"),
                    application_id=payload.get("application_id"),
                    external_target=payload.get("external_target"),
                    reason=payload.get("reason"),
                    notes=payload.get("notes"),
                    idempotency_key=payload.get("idempotency_key"),
                )
            )
        if method == "GET" and path == "/vacancies/approvals":
            return handlers.list_approvals(
                ListApprovals(
                    candidate_id=self._candidate_id(query),
                    approval_type=query.get("approval_type"),
                    artifact_id=query.get("artifact_id"),
                )
            )
        if method == "POST" and path == "/vacancies/interview-rounds":
            return handlers.create_interview_round(
                CreateInterviewRound(
                    candidate_id=self._candidate_id(payload),
                    application_id=self._required_str(payload, "application_id"),
                    round_type=self._required_str(payload, "round_type"),
                    round_state=str(payload.get("round_state") or "scheduled"),
                    scheduled_at=payload.get("scheduled_at"),
                    interviewer_name=payload.get("interviewer_name"),
                    notes=payload.get("notes"),
                    idempotency_key=payload.get("idempotency_key"),
                )
            )
        if method == "POST" and path == "/vacancies/interview-rounds/state":
            return handlers.update_interview_round_state(
                UpdateInterviewRoundState(
                    candidate_id=self._candidate_id(payload),
                    interview_round_id=self._required_str(payload, "interview_round_id"),
                    round_state=self._required_str(payload, "round_state"),
                    completed_at=payload.get("completed_at"),
                    notes=payload.get("notes"),
                )
            )
        if method == "GET" and path == "/vacancies/interview-rounds":
            return handlers.list_interview_rounds(
                ListInterviewRounds(
                    candidate_id=self._candidate_id(query),
                    application_id=query.get("application_id"),
                    canonical_vacancy_id=query.get("canonical_vacancy_id"),
                )
            )

        raise KeyError(f"Unknown vacancy API route: {method} {path}")

    def _parse_body(self, body: bytes) -> dict[str, Any]:
        if not body:
            return {}
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("JSON request body must be an object")
        return payload

    def _normalize_latest_draft(self, draft: dict[str, object] | None) -> dict[str, object]:
        if draft is None:
            return {}
        normalized = dict(draft)
        if "draft_id" not in normalized and "candidate_profile_draft_id" in normalized:
            normalized["draft_id"] = normalized["candidate_profile_draft_id"]
        return normalized

    def _candidate_id(self, values: dict[str, Any]) -> str:
        candidate_id = values.get("candidate_id")
        if candidate_id:
            return str(candidate_id)
        active = load_workspace_settings(self._workspace_path).active_candidate_id
        if active:
            return active
        raise ValueError("candidate_id is required when no active candidate is selected")

    def _required_str(self, values: dict[str, Any], key: str) -> str:
        value = values.get(key)
        if value is None or str(value).strip() == "":
            raise ValueError(f"{key} is required")
        return str(value)

    def _required_dict(self, values: dict[str, Any], key: str, *, default: dict[str, Any] | None = None) -> dict[str, Any]:
        value = values.get(key, default)
        if not isinstance(value, dict):
            raise ValueError(f"{key} must be an object")
        return value

    def _required_list(self, values: dict[str, Any], key: str) -> list[dict[str, Any]]:
        value = values.get(key)
        if not isinstance(value, list):
            raise ValueError(f"{key} must be a list")
        if not all(isinstance(item, dict) for item in value):
            raise ValueError(f"{key} entries must be objects")
        return value

    def _optional_string_list(self, value: object) -> list[str] | None:
        if value is None:
            return None
        if not isinstance(value, list):
            raise ValueError("Expected a list of strings")
        return [str(item) for item in value if str(item).strip()]

    def _optional_bool(self, value: object, *, default: bool | None = None) -> bool | None:
        if value is None:
            return default
        normalized = str(value).strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
        raise ValueError("Boolean query values must be true or false")

    def _optional_int(self, value: object, *, default: int) -> int:
        if value in (None, ""):
            return default
        try:
            return int(str(value))
        except ValueError as exc:
            raise ValueError("Integer query values must be valid integers") from exc
