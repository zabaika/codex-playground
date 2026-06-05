from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

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
from job_search.config import load_runtime_settings, load_workspace_settings
from job_search.infrastructure.board_adapters.generic_vacancy_text_adapter import GenericVacancyTextAdapter
from job_search.infrastructure.board_adapters.hh_ru_vacancy_adapter import HhRuVacancyAdapter
from job_search.infrastructure.board_adapters.linkedin_vacancy_adapter import LinkedInVacancyAdapter
from job_search.interfaces.codex.vacancy_commands import build_vacancy_handlers


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="job-search-vacancy")
    parser.add_argument("--config-path", required=True)
    parser.add_argument("--workspace-path", required=True)
    sub = parser.add_subparsers(dest="command", required=True)

    import_json = sub.add_parser("import-json")
    import_json.add_argument("--candidate-id")
    import_json.add_argument("--source-kind", required=True)
    import_json.add_argument("--items-path", required=True)

    url_seed = sub.add_parser("create-url-seed")
    url_seed.add_argument("--candidate-id")
    url_seed.add_argument("--source-url", required=True)
    url_seed.add_argument("--platform")
    url_seed.add_argument("--source-origin", default="saved_url")
    url_seed.add_argument("--notes")
    url_seed.add_argument("--idempotency-key")

    list_url_seeds = sub.add_parser("list-url-seeds")
    list_url_seeds.add_argument("--candidate-id")
    list_url_seeds.add_argument("--seed-status")
    list_url_seeds.add_argument("--platform")

    preview_url_seed = sub.add_parser("preview-url-seed")
    preview_url_seed.add_argument("--candidate-id")
    preview_url_seed.add_argument("--url-seed-id", required=True)
    preview_url_seed.add_argument("--content-text")
    preview_url_seed.add_argument("--content-path")
    preview_url_seed.add_argument("--source-origin", default="manual_page_text")

    confirm_url_seed = sub.add_parser("confirm-url-seed-import")
    confirm_url_seed.add_argument("--candidate-id")
    confirm_url_seed.add_argument("--url-seed-id", required=True)
    confirm_url_seed.add_argument("--source-kind")

    reject_url_seed = sub.add_parser("reject-url-seed")
    reject_url_seed.add_argument("--candidate-id")
    reject_url_seed.add_argument("--url-seed-id", required=True)
    reject_url_seed.add_argument("--rejection-reason")

    import_linkedin = sub.add_parser("import-linkedin-text")
    import_linkedin.add_argument("--candidate-id")
    import_linkedin.add_argument("--content-text")
    import_linkedin.add_argument("--content-path")
    import_linkedin.add_argument("--source-origin", default="manual_page")

    import_hh_ru = sub.add_parser("import-hh-ru-text")
    import_hh_ru.add_argument("--candidate-id")
    import_hh_ru.add_argument("--content-text")
    import_hh_ru.add_argument("--content-path")
    import_hh_ru.add_argument("--source-origin", default="search_results")

    import_text = sub.add_parser("import-text")
    import_text.add_argument("--candidate-id")
    import_text.add_argument("--content-text")
    import_text.add_argument("--content-path")
    import_text.add_argument("--source-kind", default="generic_text")
    import_text.add_argument("--source-origin", default="manual_text")

    list_cmd = sub.add_parser("list")
    list_cmd.add_argument("--candidate-id")
    list_cmd.add_argument("--processed", choices=["true", "false"])
    list_cmd.add_argument("--workflow-stage")

    show = sub.add_parser("show")
    show.add_argument("--candidate-id")
    show.add_argument("--canonical-vacancy-id", required=True)

    mark_processed = sub.add_parser("mark-processed")
    mark_processed.add_argument("--candidate-id")
    mark_processed.add_argument("--canonical-vacancy-id", required=True)

    update_stage = sub.add_parser("update-stage")
    update_stage.add_argument("--candidate-id")
    update_stage.add_argument("--canonical-vacancy-id", required=True)
    update_stage.add_argument("--workflow-stage", required=True)

    rank = sub.add_parser("rank")
    rank.add_argument("--candidate-id")
    rank.add_argument("--processed", choices=["true", "false"], default="false")

    draft = sub.add_parser("create-application-draft")
    draft.add_argument("--candidate-id")
    draft.add_argument("--canonical-vacancy-id", required=True)
    draft.add_argument("--language", default="en")
    draft.add_argument("--target-role")

    payload = sub.add_parser("prepare-application-payload")
    payload.add_argument("--candidate-id")
    payload.add_argument("--canonical-vacancy-id", required=True)
    payload.add_argument("--language", default="en")
    payload.add_argument("--target-role")

    vacancy_resume = sub.add_parser("generate-vacancy-resume")
    vacancy_resume.add_argument("--candidate-id")
    vacancy_resume.add_argument("--canonical-vacancy-id", required=True)
    vacancy_resume.add_argument("--language", default="en")
    vacancy_resume.add_argument("--source-resume-artifact-id")

    vacancy_resume_final = sub.add_parser("finalize-vacancy-resume")
    vacancy_resume_final.add_argument("--candidate-id")
    vacancy_resume_final.add_argument("--artifact-id", required=True)
    vacancy_resume_final.add_argument("--allow-warnings", action="store_true")

    shortlist = sub.add_parser("shortlist")
    shortlist.add_argument("--candidate-id")
    shortlist.add_argument("--canonical-vacancy-id", required=True)

    daily = sub.add_parser("daily-actions")
    daily.add_argument("--candidate-id")

    material_change = sub.add_parser("material-change-review")
    material_change.add_argument("--candidate-id")

    pipeline_report = sub.add_parser("pipeline-report")
    pipeline_report.add_argument("--candidate-id")

    touchpoint = sub.add_parser("create-touchpoint")
    touchpoint.add_argument("--candidate-id")
    touchpoint.add_argument("--canonical-vacancy-id", required=True)
    touchpoint.add_argument("--application-id")
    touchpoint.add_argument("--message-artifact-id")
    touchpoint.add_argument("--channel", default="email")
    touchpoint.add_argument("--direction", default="outgoing")
    touchpoint.add_argument("--touchpoint-state", default="sent")
    touchpoint.add_argument("--contact-name")
    touchpoint.add_argument("--occurred-at")
    touchpoint.add_argument("--notes")
    touchpoint.add_argument("--follow-up-due-at")

    list_touchpoints = sub.add_parser("list-touchpoints")
    list_touchpoints.add_argument("--candidate-id")
    list_touchpoints.add_argument("--canonical-vacancy-id")
    list_touchpoints.add_argument("--application-id")

    update_touchpoint = sub.add_parser("update-touchpoint")
    update_touchpoint.add_argument("--candidate-id")
    update_touchpoint.add_argument("--touchpoint-id", required=True)
    update_touchpoint.add_argument("--touchpoint-state", required=True)
    update_touchpoint.add_argument("--replied-at")

    resolve = sub.add_parser("resolve-reminder")
    resolve.add_argument("--candidate-id")
    resolve.add_argument("--reminder-id", required=True)

    board_checklist = sub.add_parser("board-checklist")
    board_checklist.add_argument("--candidate-id")
    board_checklist.add_argument("--platform", required=True)
    board_checklist.add_argument("--canonical-vacancy-id")

    board_action = sub.add_parser("record-board-action")
    board_action.add_argument("--candidate-id")
    board_action.add_argument("--platform", required=True)
    board_action.add_argument("--action-type", required=True)
    board_action.add_argument("--action-state", default="completed")
    board_action.add_argument("--canonical-vacancy-id")
    board_action.add_argument("--application-id")
    board_action.add_argument("--artifact-id")
    board_action.add_argument("--external-target")
    board_action.add_argument("--occurred-at")
    board_action.add_argument("--notes")
    board_action.add_argument("--idempotency-key")
    board_action.add_argument("--external-action-approval-id")

    list_board_actions = sub.add_parser("list-board-actions")
    list_board_actions.add_argument("--candidate-id")
    list_board_actions.add_argument("--platform")
    list_board_actions.add_argument("--canonical-vacancy-id")

    list_reconciliation = sub.add_parser("list-reconciliation")
    list_reconciliation.add_argument("--candidate-id")
    list_reconciliation.add_argument("--review-status")
    list_reconciliation.add_argument("--outcome")
    list_reconciliation.add_argument("--platform")

    resolve_reconciliation = sub.add_parser("resolve-reconciliation")
    resolve_reconciliation.add_argument("--candidate-id")
    resolve_reconciliation.add_argument("--reconciliation-item-id", required=True)
    resolve_reconciliation.add_argument("--review-status", default="resolved")
    resolve_reconciliation.add_argument("--resolution-notes")

    artifact_acceptance = sub.add_parser("record-artifact-acceptance")
    artifact_acceptance.add_argument("--candidate-id")
    artifact_acceptance.add_argument("--artifact-id", required=True)
    artifact_acceptance.add_argument("--approval-state", default="accepted")
    artifact_acceptance.add_argument("--actor", default="operator")
    artifact_acceptance.add_argument("--reason")
    artifact_acceptance.add_argument("--notes")
    artifact_acceptance.add_argument("--idempotency-key")

    external_approval = sub.add_parser("record-external-action-approval")
    external_approval.add_argument("--candidate-id")
    external_approval.add_argument("--platform", required=True)
    external_approval.add_argument("--action-type", required=True)
    external_approval.add_argument("--approval-state", default="approved")
    external_approval.add_argument("--actor", default="operator")
    external_approval.add_argument("--artifact-id")
    external_approval.add_argument("--canonical-vacancy-id")
    external_approval.add_argument("--application-id")
    external_approval.add_argument("--external-target")
    external_approval.add_argument("--reason")
    external_approval.add_argument("--notes")
    external_approval.add_argument("--idempotency-key")

    approvals = sub.add_parser("list-approvals")
    approvals.add_argument("--candidate-id")
    approvals.add_argument("--approval-type")
    approvals.add_argument("--artifact-id")

    interview = sub.add_parser("create-interview-round")
    interview.add_argument("--candidate-id")
    interview.add_argument("--application-id", required=True)
    interview.add_argument("--round-type", required=True)
    interview.add_argument("--round-state", default="scheduled")
    interview.add_argument("--scheduled-at")
    interview.add_argument("--interviewer-name")
    interview.add_argument("--notes")
    interview.add_argument("--idempotency-key")

    update_interview = sub.add_parser("update-interview-round")
    update_interview.add_argument("--candidate-id")
    update_interview.add_argument("--interview-round-id", required=True)
    update_interview.add_argument("--round-state", required=True)
    update_interview.add_argument("--completed-at")
    update_interview.add_argument("--notes")

    list_interviews = sub.add_parser("list-interview-rounds")
    list_interviews.add_argument("--candidate-id")
    list_interviews.add_argument("--application-id")
    list_interviews.add_argument("--canonical-vacancy-id")
    return parser


def _load_items(path: Path) -> list[VacancyImportItem]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("Vacancy import payload must be a JSON array")
    return [VacancyImportItem(**item) for item in raw]


def _resolve_candidate_id(candidate_id: str | None, workspace_path: Path) -> str:
    if candidate_id:
        return candidate_id
    settings = load_workspace_settings(workspace_path)
    if not settings.active_candidate_id:
        raise ValueError("candidate_id is required when no active candidate is selected")
    return settings.active_candidate_id


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    runtime_settings = load_runtime_settings(Path(args.config_path))
    workspace_path = Path(args.workspace_path)
    handlers = build_vacancy_handlers(runtime_settings, workspace_path)

    if args.command == "import-json":
        result = handlers.import_vacancy_batch(
            ImportVacancyBatch(
                candidate_id=_resolve_candidate_id(args.candidate_id, workspace_path),
                source_kind=args.source_kind,
                items=_load_items(Path(args.items_path)),
            )
        )
    elif args.command == "create-url-seed":
        result = handlers.create_vacancy_url_enrichment_seed(
            CreateVacancyUrlEnrichmentSeed(
                candidate_id=_resolve_candidate_id(args.candidate_id, workspace_path),
                source_url=args.source_url,
                platform=args.platform,
                source_origin=args.source_origin,
                notes=args.notes,
                idempotency_key=args.idempotency_key,
            )
        )
    elif args.command == "list-url-seeds":
        result = handlers.list_vacancy_url_enrichment_seeds(
            ListVacancyUrlEnrichmentSeeds(
                candidate_id=_resolve_candidate_id(args.candidate_id, workspace_path),
                seed_status=args.seed_status,
                platform=args.platform,
            )
        )
    elif args.command == "preview-url-seed":
        if bool(args.content_text) == bool(args.content_path):
            raise ValueError("Provide exactly one of --content-text or --content-path")
        content = args.content_text if args.content_text is not None else Path(args.content_path).read_text(encoding="utf-8")
        result = handlers.preview_vacancy_url_enrichment_seed(
            PreviewVacancyUrlEnrichmentSeed(
                candidate_id=_resolve_candidate_id(args.candidate_id, workspace_path),
                url_seed_id=args.url_seed_id,
                content_text=content,
                source_origin=args.source_origin,
            )
        )
    elif args.command == "confirm-url-seed-import":
        result = handlers.confirm_vacancy_url_enrichment_import(
            ConfirmVacancyUrlEnrichmentImport(
                candidate_id=_resolve_candidate_id(args.candidate_id, workspace_path),
                url_seed_id=args.url_seed_id,
                source_kind=args.source_kind,
            )
        )
    elif args.command == "reject-url-seed":
        result = handlers.reject_vacancy_url_enrichment_seed(
            RejectVacancyUrlEnrichmentSeed(
                candidate_id=_resolve_candidate_id(args.candidate_id, workspace_path),
                url_seed_id=args.url_seed_id,
                rejection_reason=args.rejection_reason,
            )
        )
    elif args.command == "import-linkedin-text":
        if bool(args.content_text) == bool(args.content_path):
            raise ValueError("Provide exactly one of --content-text or --content-path")
        content = args.content_text if args.content_text is not None else Path(args.content_path).read_text(encoding="utf-8")
        extraction = LinkedInVacancyAdapter().extract_from_text(content, source_origin=args.source_origin)
        if not extraction.items:
            raise ValueError(f"No importable LinkedIn vacancies found: {', '.join(extraction.warnings)}")
        imported = handlers.import_vacancy_batch(
            ImportVacancyBatch(
                candidate_id=_resolve_candidate_id(args.candidate_id, workspace_path),
                source_kind="linkedin",
                items=extraction.items,
            )
        )
        result = {**imported, "source_kind": "linkedin", "warnings": extraction.warnings}
    elif args.command == "import-hh-ru-text":
        if bool(args.content_text) == bool(args.content_path):
            raise ValueError("Provide exactly one of --content-text or --content-path")
        content = args.content_text if args.content_text is not None else Path(args.content_path).read_text(encoding="utf-8")
        extraction = HhRuVacancyAdapter().extract_from_text(content, source_origin=args.source_origin)
        if not extraction.items:
            raise ValueError(f"No importable hh.ru vacancies found: {', '.join(extraction.warnings)}")
        imported = handlers.import_vacancy_batch(
            ImportVacancyBatch(
                candidate_id=_resolve_candidate_id(args.candidate_id, workspace_path),
                source_kind="hh_ru",
                items=extraction.items,
            )
        )
        result = {**imported, "source_kind": "hh_ru", "warnings": extraction.warnings}
    elif args.command == "import-text":
        if bool(args.content_text) == bool(args.content_path):
            raise ValueError("Provide exactly one of --content-text or --content-path")
        content = args.content_text if args.content_text is not None else Path(args.content_path).read_text(encoding="utf-8")
        extraction = GenericVacancyTextAdapter().extract_from_text(content, source_origin=args.source_origin)
        if not extraction.items:
            raise ValueError(f"No importable vacancies found: {', '.join(extraction.warnings)}")
        imported = handlers.import_vacancy_batch(
            ImportVacancyBatch(
                candidate_id=_resolve_candidate_id(args.candidate_id, workspace_path),
                source_kind=args.source_kind,
                items=extraction.items,
            )
        )
        result = {**imported, "source_kind": args.source_kind, "warnings": extraction.warnings}
    elif args.command == "list":
        processed = None
        if args.processed == "true":
            processed = True
        elif args.processed == "false":
            processed = False
        result = handlers.list_vacancies(
            ListVacancies(
                candidate_id=_resolve_candidate_id(args.candidate_id, workspace_path),
                processed=processed,
                workflow_stage=args.workflow_stage,
            )
        )
    elif args.command == "show":
        result = handlers.get_vacancy(
            GetVacancy(
                candidate_id=_resolve_candidate_id(args.candidate_id, workspace_path),
                canonical_vacancy_id=args.canonical_vacancy_id,
            )
        )
    elif args.command == "mark-processed":
        result = handlers.mark_vacancy_processed(
            MarkVacancyProcessed(
                candidate_id=_resolve_candidate_id(args.candidate_id, workspace_path),
                canonical_vacancy_id=args.canonical_vacancy_id,
            )
        )
    elif args.command == "rank":
        processed = None
        if args.processed == "true":
            processed = True
        elif args.processed == "false":
            processed = False
        result = handlers.list_ranked_vacancies(
            ListRankedVacancies(
                candidate_id=_resolve_candidate_id(args.candidate_id, workspace_path),
                processed=processed,
            )
        )
    elif args.command == "create-application-draft":
        result = handlers.create_application_draft(
            CreateApplicationDraft(
                candidate_id=_resolve_candidate_id(args.candidate_id, workspace_path),
                canonical_vacancy_id=args.canonical_vacancy_id,
                language=args.language,
                target_role=args.target_role,
            )
        )
    elif args.command == "prepare-application-payload":
        result = handlers.prepare_application_payload(
            PrepareApplicationPayload(
                candidate_id=_resolve_candidate_id(args.candidate_id, workspace_path),
                canonical_vacancy_id=args.canonical_vacancy_id,
                language=args.language,
                target_role=args.target_role,
            )
        )
    elif args.command == "generate-vacancy-resume":
        result = handlers.generate_vacancy_resume(
            GenerateVacancyResume(
                candidate_id=_resolve_candidate_id(args.candidate_id, workspace_path),
                canonical_vacancy_id=args.canonical_vacancy_id,
                language=args.language,
                source_resume_artifact_id=args.source_resume_artifact_id,
            )
        )
    elif args.command == "finalize-vacancy-resume":
        result = handlers.finalize_vacancy_resume(
            FinalizeVacancyResume(
                candidate_id=_resolve_candidate_id(args.candidate_id, workspace_path),
                artifact_id=args.artifact_id,
                allow_warnings=args.allow_warnings,
            )
        )
    elif args.command == "shortlist":
        result = handlers.shortlist_vacancy(
            ShortlistVacancy(
                candidate_id=_resolve_candidate_id(args.candidate_id, workspace_path),
                canonical_vacancy_id=args.canonical_vacancy_id,
            )
        )
    elif args.command == "daily-actions":
        result = handlers.list_daily_actions(
            ListDailyActions(candidate_id=_resolve_candidate_id(args.candidate_id, workspace_path))
        )
    elif args.command == "material-change-review":
        result = handlers.list_material_change_review(
            ListMaterialChangeReview(candidate_id=_resolve_candidate_id(args.candidate_id, workspace_path))
        )
    elif args.command == "pipeline-report":
        result = handlers.get_pipeline_report(
            GetPipelineReport(candidate_id=_resolve_candidate_id(args.candidate_id, workspace_path))
        )
    elif args.command == "create-touchpoint":
        result = handlers.create_touchpoint(
            CreateTouchpoint(
                candidate_id=_resolve_candidate_id(args.candidate_id, workspace_path),
                canonical_vacancy_id=args.canonical_vacancy_id,
                application_id=args.application_id,
                message_artifact_id=args.message_artifact_id,
                channel=args.channel,
                direction=args.direction,
                touchpoint_state=args.touchpoint_state,
                contact_name=args.contact_name,
                occurred_at=args.occurred_at,
                notes=args.notes,
                follow_up_due_at=args.follow_up_due_at,
            )
        )
    elif args.command == "list-touchpoints":
        result = handlers.list_touchpoints(
            ListTouchpoints(
                candidate_id=_resolve_candidate_id(args.candidate_id, workspace_path),
                canonical_vacancy_id=args.canonical_vacancy_id,
                application_id=args.application_id,
            )
        )
    elif args.command == "update-touchpoint":
        result = handlers.update_touchpoint_state(
            UpdateTouchpointState(
                candidate_id=_resolve_candidate_id(args.candidate_id, workspace_path),
                touchpoint_id=args.touchpoint_id,
                touchpoint_state=args.touchpoint_state,
                replied_at=args.replied_at,
            )
        )
    elif args.command == "resolve-reminder":
        result = handlers.resolve_reminder(
            ResolveReminder(
                candidate_id=_resolve_candidate_id(args.candidate_id, workspace_path),
                reminder_id=args.reminder_id,
            )
        )
    elif args.command == "board-checklist":
        result = handlers.get_board_checklist(
            GetBoardChecklist(
                candidate_id=_resolve_candidate_id(args.candidate_id, workspace_path),
                platform=args.platform,
                canonical_vacancy_id=args.canonical_vacancy_id,
            )
        )
    elif args.command == "record-board-action":
        result = handlers.record_manual_board_action(
            RecordManualBoardAction(
                candidate_id=_resolve_candidate_id(args.candidate_id, workspace_path),
                platform=args.platform,
                action_type=args.action_type,
                action_state=args.action_state,
                canonical_vacancy_id=args.canonical_vacancy_id,
                application_id=args.application_id,
                artifact_id=args.artifact_id,
                external_target=args.external_target,
                occurred_at=args.occurred_at,
                notes=args.notes,
                idempotency_key=args.idempotency_key,
                external_action_approval_id=args.external_action_approval_id,
            )
        )
    elif args.command == "list-board-actions":
        result = handlers.list_manual_board_actions(
            ListManualBoardActions(
                candidate_id=_resolve_candidate_id(args.candidate_id, workspace_path),
                platform=args.platform,
                canonical_vacancy_id=args.canonical_vacancy_id,
            )
        )
    elif args.command == "list-reconciliation":
        result = handlers.list_reconciliation_items(
            ListReconciliationItems(
                candidate_id=_resolve_candidate_id(args.candidate_id, workspace_path),
                review_status=args.review_status,
                outcome=args.outcome,
                platform=args.platform,
            )
        )
    elif args.command == "resolve-reconciliation":
        result = handlers.resolve_reconciliation_item(
            ResolveReconciliationItem(
                candidate_id=_resolve_candidate_id(args.candidate_id, workspace_path),
                reconciliation_item_id=args.reconciliation_item_id,
                review_status=args.review_status,
                resolution_notes=args.resolution_notes,
            )
        )
    elif args.command == "record-artifact-acceptance":
        result = handlers.record_artifact_acceptance(
            RecordArtifactAcceptance(
                candidate_id=_resolve_candidate_id(args.candidate_id, workspace_path),
                artifact_id=args.artifact_id,
                approval_state=args.approval_state,
                actor=args.actor,
                reason=args.reason,
                notes=args.notes,
                idempotency_key=args.idempotency_key,
            )
        )
    elif args.command == "record-external-action-approval":
        result = handlers.record_external_action_approval(
            RecordExternalActionApproval(
                candidate_id=_resolve_candidate_id(args.candidate_id, workspace_path),
                platform=args.platform,
                action_type=args.action_type,
                approval_state=args.approval_state,
                actor=args.actor,
                artifact_id=args.artifact_id,
                canonical_vacancy_id=args.canonical_vacancy_id,
                application_id=args.application_id,
                external_target=args.external_target,
                reason=args.reason,
                notes=args.notes,
                idempotency_key=args.idempotency_key,
            )
        )
    elif args.command == "list-approvals":
        result = handlers.list_approvals(
            ListApprovals(
                candidate_id=_resolve_candidate_id(args.candidate_id, workspace_path),
                approval_type=args.approval_type,
                artifact_id=args.artifact_id,
            )
        )
    elif args.command == "create-interview-round":
        result = handlers.create_interview_round(
            CreateInterviewRound(
                candidate_id=_resolve_candidate_id(args.candidate_id, workspace_path),
                application_id=args.application_id,
                round_type=args.round_type,
                round_state=args.round_state,
                scheduled_at=args.scheduled_at,
                interviewer_name=args.interviewer_name,
                notes=args.notes,
                idempotency_key=args.idempotency_key,
            )
        )
    elif args.command == "update-interview-round":
        result = handlers.update_interview_round_state(
            UpdateInterviewRoundState(
                candidate_id=_resolve_candidate_id(args.candidate_id, workspace_path),
                interview_round_id=args.interview_round_id,
                round_state=args.round_state,
                completed_at=args.completed_at,
                notes=args.notes,
            )
        )
    elif args.command == "list-interview-rounds":
        result = handlers.list_interview_rounds(
            ListInterviewRounds(
                candidate_id=_resolve_candidate_id(args.candidate_id, workspace_path),
                application_id=args.application_id,
                canonical_vacancy_id=args.canonical_vacancy_id,
            )
        )
    else:
        result = handlers.update_vacancy_workflow_stage(
            UpdateVacancyWorkflowStage(
                candidate_id=_resolve_candidate_id(args.candidate_id, workspace_path),
                canonical_vacancy_id=args.canonical_vacancy_id,
                workflow_stage=args.workflow_stage,
            )
        )

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, PermissionError, RuntimeError) as exc:
        print(json.dumps({"error": {"type": type(exc).__name__, "message": str(exc)}}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
