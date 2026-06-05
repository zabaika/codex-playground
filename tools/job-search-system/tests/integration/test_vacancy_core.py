from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(PROJECT_ROOT.parents[1]) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT.parents[1]))

from job_search.application.commands.vacancy import (  # noqa: E402
    ConfirmVacancyUrlEnrichmentImport,
    CreateVacancyUrlEnrichmentSeed,
    ImportVacancyBatch,
    MarkVacancyProcessed,
    PreviewVacancyUrlEnrichmentSeed,
    UpdateVacancyWorkflowStage,
    VacancyImportItem,
)
from job_search.application.commands.candidate import CreateCandidate  # noqa: E402
from job_search.application.handlers.candidate_handlers import CandidateHandlers  # noqa: E402
from job_search.application.handlers.vacancy_handlers import VacancyHandlers  # noqa: E402
from job_search.application.queries.vacancy import GetBoardChecklist, GetPipelineReport, GetVacancy, ListApprovals, ListManualBoardActions, ListMaterialChangeReview, ListRankedVacancies, ListVacancies, ListVacancyUrlEnrichmentSeeds  # noqa: E402
from job_search.application.commands.vacancy import CreateApplicationDraft  # noqa: E402
from job_search.application.commands.vacancy import ShortlistVacancy  # noqa: E402
from job_search.application.commands.vacancy import PrepareApplicationPayload  # noqa: E402
from job_search.application.commands.vacancy import GenerateVacancyResume, FinalizeVacancyResume  # noqa: E402
from job_search.application.commands.vacancy import CreateInterviewRound, CreateTouchpoint, ResolveReconciliationItem, ResolveReminder, UpdateInterviewRoundState, UpdateTouchpointState  # noqa: E402
from job_search.application.commands.vacancy import RecordArtifactAcceptance, RecordExternalActionApproval, RecordManualBoardAction  # noqa: E402
from job_search.application.services.application_draft_service import ApplicationDraftService  # noqa: E402
from job_search.application.queries.vacancy import ListDailyActions, ListInterviewRounds, ListReconciliationItems  # noqa: E402
from job_search.application.queries.vacancy import ListTouchpoints  # noqa: E402
from job_search.application.services.candidate_conflict_resolution_service import CandidateConflictResolutionService  # noqa: E402
from job_search.application.services.candidate_draft_review_service import CandidateDraftReviewService  # noqa: E402
from job_search.application.services.candidate_extraction_service import CandidateExtractionService  # noqa: E402
from job_search.application.services.candidate_profile_mapping_service import CandidateProfileMappingService  # noqa: E402
from job_search.application.services.artifact_path_service import ArtifactPathService  # noqa: E402
from job_search.application.services.candidate_ai_extraction_service import CandidateAiExtractionService  # noqa: E402
from job_search.application.services.candidate_source_service import CandidateSourceService  # noqa: E402
from job_search.application.services.career_pathing_lite_service import CareerPathingLiteService  # noqa: E402
from job_search.application.services.job_search_playbook_service import JobSearchPlaybookService  # noqa: E402
from job_search.application.services.job_board_operations_service import JobBoardOperationsService  # noqa: E402
from job_search.application.services.resume_assembly_service import ResumeAssemblyService  # noqa: E402
from job_search.application.services.resume_positioning_service import ResumePositioningService  # noqa: E402
from job_search.application.services.resume_assembly_service import ResumeAssemblyService  # noqa: E402
from job_search.application.services.resume_quality_gate_service import ResumeQualityGateService  # noqa: E402
from job_search.application.services.resume_roast_report_service import ResumeRoastReportService  # noqa: E402
from job_search.application.services.vacancy_normalization_service import VacancyNormalizationService  # noqa: E402
from job_search.application.services.vacancy_ranking_service import VacancyRankingService  # noqa: E402
from job_search.application.services.vacancy_url_enrichment_service import VacancyUrlEnrichmentService  # noqa: E402
from job_search.application.services.vacancy_resume_service import VacancyResumeService  # noqa: E402
from job_search.application.commands.candidate import RegisterCandidateSource, GenerateCandidateProfileDraftFromSources, ConfirmCandidateProfileDraft, GenerateResumeMarkdown, FinalizeResumeMarkdown  # noqa: E402
from job_search.infrastructure.db.connection import load_connection  # noqa: E402
from job_search.infrastructure.db.schema_version import apply_migrations  # noqa: E402
from job_search.infrastructure.repositories.artifact_repository import ArtifactRepository  # noqa: E402
from job_search.infrastructure.repositories.artifact_usage_repository import ArtifactUsageRepository  # noqa: E402
from job_search.infrastructure.repositories.audit_repository import AuditRepository  # noqa: E402
from job_search.infrastructure.repositories.approval_repository import ApprovalRepository  # noqa: E402
from job_search.infrastructure.repositories.candidate_draft_repository import CandidateDraftRepository  # noqa: E402
from job_search.infrastructure.repositories.candidate_evidence_repository import CandidateEvidenceRepository  # noqa: E402
from job_search.infrastructure.repositories.candidate_repository import CandidateRepository  # noqa: E402
from job_search.infrastructure.repositories.interview_repository import InterviewRepository  # noqa: E402
from job_search.infrastructure.repositories.manual_board_action_repository import ManualBoardActionRepository  # noqa: E402
from job_search.infrastructure.repositories.quality_gate_repository import QualityGateRepository  # noqa: E402
from job_search.infrastructure.repositories.reconciliation_repository import ReconciliationRepository  # noqa: E402
from job_search.infrastructure.repositories.touchpoint_repository import TouchpointRepository  # noqa: E402
from job_search.infrastructure.repositories.vacancy_url_enrichment_repository import VacancyUrlEnrichmentRepository  # noqa: E402
from job_search.infrastructure.repositories.vacancy_repository import VacancyRepository  # noqa: E402


class VacancyCoreIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "data" / "job_search.sqlite"
        self.artifact_root = self.root / "data" / "artifacts"
        self.workspace_path = self.root / "config" / "workspace.local.toml"
        self.sqlite_config_path = PROJECT_ROOT.parents[1] / "common" / "config" / "sqlite.toml"
        self.conn = load_connection(self.db_path, self.sqlite_config_path)
        apply_migrations(self.conn, PROJECT_ROOT / "src" / "job_search" / "infrastructure" / "migrations")
        self.candidate_handlers = CandidateHandlers(
            candidate_repository=CandidateRepository(self.conn),
            artifact_repository=ArtifactRepository(self.conn),
            draft_repository=CandidateDraftRepository(self.conn),
            evidence_repository=CandidateEvidenceRepository(self.conn),
            audit_repository=AuditRepository(self.conn),
            candidate_source_service=CandidateSourceService(ArtifactRepository(self.conn), self.artifact_root),
            extraction_service=CandidateExtractionService(),
            ai_extraction_service=CandidateAiExtractionService(),
            mapping_service=CandidateProfileMappingService(),
            conflict_resolution_service=CandidateConflictResolutionService(),
            draft_review_service=CandidateDraftReviewService(),
            resume_assembly_service=ResumeAssemblyService(),
            resume_positioning_service=ResumePositioningService(),
            career_pathing_lite_service=CareerPathingLiteService(),
            job_search_playbook_service=JobSearchPlaybookService(),
            resume_quality_gate_service=ResumeQualityGateService(),
            resume_roast_report_service=ResumeRoastReportService(),
            quality_gate_repository=QualityGateRepository(self.conn),
            workspace_path=self.workspace_path,
            tx_connection=self.conn,
        )
        self.candidate_id = self.candidate_handlers.create_candidate(CreateCandidate(display_name="Candidate"))["candidate_id"]
        self.handlers = VacancyHandlers(
            vacancy_repository=VacancyRepository(self.conn),
            candidate_repository=CandidateRepository(self.conn),
            evidence_repository=CandidateEvidenceRepository(self.conn),
            artifact_repository=ArtifactRepository(self.conn),
            artifact_usage_repository=ArtifactUsageRepository(self.conn),
            audit_repository=AuditRepository(self.conn),
            approval_repository=ApprovalRepository(self.conn),
            quality_gate_repository=QualityGateRepository(self.conn),
            reconciliation_repository=ReconciliationRepository(self.conn),
            touchpoint_repository=TouchpointRepository(self.conn),
            interview_repository=InterviewRepository(self.conn),
            manual_board_action_repository=ManualBoardActionRepository(self.conn),
            url_enrichment_repository=VacancyUrlEnrichmentRepository(self.conn),
            normalization_service=VacancyNormalizationService(),
            ranking_service=VacancyRankingService(),
            url_enrichment_service=VacancyUrlEnrichmentService(),
            job_board_operations_service=JobBoardOperationsService(),
            resume_assembly_service=ResumeAssemblyService(),
            application_draft_service=ApplicationDraftService(),
            resume_quality_gate_service=ResumeQualityGateService(),
            vacancy_resume_service=VacancyResumeService(),
            artifact_root=self.artifact_root,
            candidate_handlers=self.candidate_handlers,
            tx_connection=self.conn,
        )

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()

    def test_import_batch_creates_single_canonical_vacancy_for_duplicates(self) -> None:
        result = self.handlers.import_vacancy_batch(
            ImportVacancyBatch(
                candidate_id=self.candidate_id,
                source_kind="manual",
                items=[
                    VacancyImportItem(
                        title="CTO",
                        company_name="Example Corp",
                        location_text="Remote",
                        source_url="https://example.com/jobs/1",
                    ),
                    VacancyImportItem(
                        title="  CTO ",
                        company_name="Example   Corp",
                        location_text="remote",
                        source_url="https://example.com/jobs/2",
                    ),
                ],
            )
        )
        vacancies = self.handlers.list_vacancies(ListVacancies(candidate_id=self.candidate_id))
        self.assertEqual(len(result["imported"]), 2)
        self.assertEqual(len(vacancies), 1)
        occurrences = self.conn.execute("SELECT COUNT(*) FROM source_occurrences").fetchone()[0]
        self.assertEqual(occurrences, 2)

    def test_same_vacancy_can_exist_for_multiple_candidates(self) -> None:
        second_candidate_id = self.candidate_handlers.create_candidate(CreateCandidate(display_name="Second Candidate"))["candidate_id"]
        for candidate_id in (self.candidate_id, second_candidate_id):
            self.handlers.import_vacancy_batch(
                ImportVacancyBatch(
                    candidate_id=candidate_id,
                    source_kind="manual",
                    items=[VacancyImportItem(title="CTO", company_name="Example Corp", location_text="Remote")],
                )
            )
        first_vacancies = self.handlers.list_vacancies(ListVacancies(candidate_id=self.candidate_id))
        second_vacancies = self.handlers.list_vacancies(ListVacancies(candidate_id=second_candidate_id))
        self.assertEqual(len(first_vacancies), 1)
        self.assertEqual(len(second_vacancies), 1)
        self.assertNotEqual(first_vacancies[0]["canonical_vacancy_id"], second_vacancies[0]["canonical_vacancy_id"])

    def test_url_seed_requires_supervised_preview_before_importing_vacancy(self) -> None:
        seed_result = self.handlers.create_vacancy_url_enrichment_seed(
            CreateVacancyUrlEnrichmentSeed(
                candidate_id=self.candidate_id,
                source_url="https://example.com/jobs/platform-director",
                platform="generic",
                idempotency_key="url-seed-example-platform-director",
            )
        )
        repeated_seed = self.handlers.create_vacancy_url_enrichment_seed(
            CreateVacancyUrlEnrichmentSeed(
                candidate_id=self.candidate_id,
                source_url="https://example.com/jobs/platform-director",
                platform="generic",
                idempotency_key="url-seed-example-platform-director",
            )
        )
        seed_id = seed_result["seed"]["url_seed_id"]
        self.assertFalse(seed_result["reused"])
        self.assertTrue(repeated_seed["reused"])
        self.assertEqual(self.handlers.list_vacancies(ListVacancies(candidate_id=self.candidate_id)), [])

        with self.assertRaisesRegex(ValueError, "must be previewed"):
            self.handlers.confirm_vacancy_url_enrichment_import(
                ConfirmVacancyUrlEnrichmentImport(candidate_id=self.candidate_id, url_seed_id=seed_id)
            )

        preview = self.handlers.preview_vacancy_url_enrichment_seed(
            PreviewVacancyUrlEnrichmentSeed(
                candidate_id=self.candidate_id,
                url_seed_id=seed_id,
                content_text=(
                    "Title: Platform Director\n"
                    "Company: Example Corp\n"
                    "Location: Remote Europe\n"
                    "Lead platform engineering, cloud delivery, SRE and FinOps."
                ),
            )
        )
        listed = self.handlers.list_vacancy_url_enrichment_seeds(
            ListVacancyUrlEnrichmentSeeds(candidate_id=self.candidate_id, seed_status="previewed")
        )
        self.assertTrue(preview["preview"]["importable"])
        self.assertEqual(preview["preview"]["items"][0]["source_url"], "https://example.com/jobs/platform-director")
        self.assertEqual(len(listed), 1)

        imported = self.handlers.confirm_vacancy_url_enrichment_import(
            ConfirmVacancyUrlEnrichmentImport(candidate_id=self.candidate_id, url_seed_id=seed_id)
        )
        repeated_import = self.handlers.confirm_vacancy_url_enrichment_import(
            ConfirmVacancyUrlEnrichmentImport(candidate_id=self.candidate_id, url_seed_id=seed_id)
        )
        vacancies = self.handlers.list_vacancies(ListVacancies(candidate_id=self.candidate_id))
        self.assertFalse(imported["reused"])
        self.assertTrue(repeated_import["reused"])
        self.assertEqual(len(vacancies), 1)
        self.assertEqual(imported["seed"]["seed_status"], "imported")
        self.assertEqual(vacancies[0]["role_title"], "Platform Director")

    def test_processed_vacancy_sets_material_change_on_new_occurrence(self) -> None:
        imported = self.handlers.import_vacancy_batch(
            ImportVacancyBatch(
                candidate_id=self.candidate_id,
                source_kind="manual",
                items=[VacancyImportItem(title="CTO", company_name="Example Corp", location_text="Remote")],
            )
        )
        canonical_vacancy_id = imported["imported"][0]["canonical_vacancy_id"]
        self.handlers.mark_vacancy_processed(
            MarkVacancyProcessed(candidate_id=self.candidate_id, canonical_vacancy_id=canonical_vacancy_id)
        )
        self.handlers.import_vacancy_batch(
            ImportVacancyBatch(
                candidate_id=self.candidate_id,
                source_kind="manual",
                items=[
                    VacancyImportItem(
                        title="CTO",
                        company_name="Example Corp",
                        location_text="Remote",
                        raw_text="Updated vacancy body with changed details",
                    )
                ],
            )
        )
        vacancy = self.handlers.get_vacancy(
            GetVacancy(candidate_id=self.candidate_id, canonical_vacancy_id=canonical_vacancy_id)
        )
        self.assertEqual(vacancy["processed"], 1)
        self.assertEqual(vacancy["material_change_detected"], 1)
        review_bucket = self.handlers.list_material_change_review(
            ListMaterialChangeReview(candidate_id=self.candidate_id)
        )
        self.assertEqual(review_bucket[0]["canonical_vacancy_id"], canonical_vacancy_id)
        self.assertEqual(review_bucket[0]["review_bucket"], "material_change")
        daily_actions = self.handlers.list_daily_actions(ListDailyActions(candidate_id=self.candidate_id))
        material_action = next(item for item in daily_actions if item["action_type"] == "review_material_change")
        self.assertEqual(material_action["action_group"], "vacancy_review")
        report = self.handlers.get_pipeline_report(GetPipelineReport(candidate_id=self.candidate_id))
        self.assertEqual(report["summary"]["material_change_review"], 1)
        self.assertEqual(report["review_buckets"]["material_change"], 1)
        self.assertEqual(report["daily_action_group_counts"]["vacancy_review"], 1)

    def test_processed_vacancy_cannot_return_to_new(self) -> None:
        imported = self.handlers.import_vacancy_batch(
            ImportVacancyBatch(
                candidate_id=self.candidate_id,
                source_kind="manual",
                items=[VacancyImportItem(title="Engineering Director", company_name="Example Corp")],
            )
        )
        canonical_vacancy_id = imported["imported"][0]["canonical_vacancy_id"]
        self.handlers.mark_vacancy_processed(
            MarkVacancyProcessed(candidate_id=self.candidate_id, canonical_vacancy_id=canonical_vacancy_id)
        )
        with self.assertRaises(ValueError):
            self.handlers.update_vacancy_workflow_stage(
                UpdateVacancyWorkflowStage(
                    candidate_id=self.candidate_id,
                    canonical_vacancy_id=canonical_vacancy_id,
                    workflow_stage="new",
                )
            )

    def test_invalid_workflow_stage_is_rejected_before_storage_mutation(self) -> None:
        imported = self.handlers.import_vacancy_batch(
            ImportVacancyBatch(
                candidate_id=self.candidate_id,
                source_kind="manual",
                items=[VacancyImportItem(title="Engineering Director", company_name="Example Corp")],
            )
        )
        canonical_vacancy_id = imported["imported"][0]["canonical_vacancy_id"]

        with self.assertRaisesRegex(ValueError, "workflow_stage must be one of"):
            self.handlers.update_vacancy_workflow_stage(
                UpdateVacancyWorkflowStage(
                    candidate_id=self.candidate_id,
                    canonical_vacancy_id=canonical_vacancy_id,
                    workflow_stage="maybe",
                )
            )

    def test_ranked_vacancies_prioritize_target_role_match(self) -> None:
        self.candidate_handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=self.candidate_id,
                source_kind="resume",
                source_origin="text",
                content_text=(
                    "Candidate\ncandidate@example.com\n"
                    "Желаемая должность и зарплата\nCTO\n"
                    "Обо мне\nPlatform engineering and FinOps leader.\n"
                    "Skills\nPlatform Engineering\nFinOps\n"
                ),
            )
        )
        draft = self.candidate_handlers.generate_candidate_profile_draft(
            GenerateCandidateProfileDraftFromSources(candidate_id=self.candidate_id)
        )
        self.candidate_handlers.confirm_candidate_profile_draft(
            ConfirmCandidateProfileDraft(candidate_id=self.candidate_id, draft_id=draft["draft_id"])
        )
        self.handlers.import_vacancy_batch(
            ImportVacancyBatch(
                candidate_id=self.candidate_id,
                source_kind="manual",
                items=[
                    VacancyImportItem(
                        title="CTO",
                        company_name="Example Corp",
                        raw_text="Looking for a platform engineering leader with FinOps experience",
                    ),
                    VacancyImportItem(
                        title="Backend Developer",
                        company_name="Another Corp",
                        raw_text="Python developer role",
                    ),
                ],
            )
        )
        ranked = self.handlers.list_ranked_vacancies(
            ListRankedVacancies(candidate_id=self.candidate_id, processed=False)
        )
        self.assertEqual(ranked[0]["role_title"], "CTO")
        self.assertGreater(ranked[0]["ranking_score"], ranked[1]["ranking_score"])
        self.assertIn(ranked[0]["fit_label"], {"medium", "high"})
        self.assertTrue(ranked[0]["score_reasons"])
        self.assertIn("matched_signals", ranked[0])

    def test_ranking_query_does_not_mutate_vacancy_lifecycle(self) -> None:
        self.candidate_handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=self.candidate_id,
                source_kind="resume",
                source_origin="text",
                content_text=(
                    "Candidate\ncandidate@example.com\n"
                    "Желаемая должность и зарплата\nCTO\n"
                    "Обо мне\nPlatform Engineering FinOps Cloud leader.\n"
                    "Skills\nPlatform Engineering\nFinOps\nCloud\n"
                ),
            )
        )
        draft = self.candidate_handlers.generate_candidate_profile_draft(
            GenerateCandidateProfileDraftFromSources(candidate_id=self.candidate_id)
        )
        self.candidate_handlers.confirm_candidate_profile_draft(
            ConfirmCandidateProfileDraft(candidate_id=self.candidate_id, draft_id=draft["draft_id"])
        )
        imported = self.handlers.import_vacancy_batch(
            ImportVacancyBatch(
                candidate_id=self.candidate_id,
                source_kind="manual",
                items=[
                    VacancyImportItem(
                        title="CTO",
                        company_name="Example Corp",
                        location_text="Remote",
                        raw_text="Remote Platform Engineering FinOps Cloud leadership role.",
                    )
                ],
            )
        )
        canonical_vacancy_id = imported["imported"][0]["canonical_vacancy_id"]
        before = self.handlers.get_vacancy(
            GetVacancy(candidate_id=self.candidate_id, canonical_vacancy_id=canonical_vacancy_id)
        )

        ranked = self.handlers.list_ranked_vacancies(
            ListRankedVacancies(candidate_id=self.candidate_id, processed=False)
        )
        after = self.handlers.get_vacancy(
            GetVacancy(candidate_id=self.candidate_id, canonical_vacancy_id=canonical_vacancy_id)
        )

        self.assertEqual(ranked[0]["canonical_vacancy_id"], canonical_vacancy_id)
        self.assertEqual(after["workflow_stage"], before["workflow_stage"])
        self.assertEqual(after["processed"], before["processed"])

    def test_pipeline_report_summarizes_ranking_actions_and_applications(self) -> None:
        self.candidate_handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=self.candidate_id,
                source_kind="resume",
                source_origin="text",
                content_text=(
                    "Candidate\ncandidate@example.com\n"
                    "Желаемая должность и зарплата\nCTO\n"
                    "Обо мне\nPlatform Engineering FinOps Cloud leader.\n"
                    "Skills\nPlatform Engineering\nFinOps\nCloud\n"
                ),
            )
        )
        draft = self.candidate_handlers.generate_candidate_profile_draft(
            GenerateCandidateProfileDraftFromSources(candidate_id=self.candidate_id)
        )
        self.candidate_handlers.confirm_candidate_profile_draft(
            ConfirmCandidateProfileDraft(candidate_id=self.candidate_id, draft_id=draft["draft_id"])
        )
        imported = self.handlers.import_vacancy_batch(
            ImportVacancyBatch(
                candidate_id=self.candidate_id,
                source_kind="manual",
                items=[
                    VacancyImportItem(
                        title="CTO",
                        company_name="Good Corp",
                        location_text="Remote",
                        raw_text="Remote Platform Engineering FinOps Cloud leadership role.",
                    ),
                    VacancyImportItem(
                        title="Backend Developer",
                        company_name="Other Corp",
                        raw_text="Python backend role.",
                    ),
                ],
            )
        )
        self.handlers.shortlist_vacancy(
            ShortlistVacancy(
                candidate_id=self.candidate_id,
                canonical_vacancy_id=imported["imported"][0]["canonical_vacancy_id"],
            )
        )

        report = self.handlers.get_pipeline_report(GetPipelineReport(candidate_id=self.candidate_id))

        self.assertEqual(report["summary"]["total_vacancies"], 2)
        self.assertEqual(report["workflow_counts"]["shortlisted"], 1)
        self.assertTrue(report["top_ranked"])
        self.assertIn("daily_actions", report)
        self.assertIn("recommendations", report)

    def test_create_application_draft_persists_application_and_message_artifact(self) -> None:
        self.candidate_handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=self.candidate_id,
                source_kind="resume",
                source_origin="text",
                content_text=(
                    "Candidate\ncandidate@example.com\n"
                    "Обо мне\nTechnology leader focused on delivery and engineering effectiveness.\n"
                    "Опыт работы\nExample Corp\nCTO\n- Improved delivery speed by 40%.\n"
                ),
            )
        )
        draft = self.candidate_handlers.generate_candidate_profile_draft(
            GenerateCandidateProfileDraftFromSources(candidate_id=self.candidate_id)
        )
        self.candidate_handlers.confirm_candidate_profile_draft(
            ConfirmCandidateProfileDraft(candidate_id=self.candidate_id, draft_id=draft["draft_id"])
        )
        imported = self.handlers.import_vacancy_batch(
            ImportVacancyBatch(
                candidate_id=self.candidate_id,
                source_kind="manual",
                items=[VacancyImportItem(title="CTO", company_name="Hiring Corp")],
            )
        )
        canonical_vacancy_id = imported["imported"][0]["canonical_vacancy_id"]
        result = self.handlers.create_application_draft(
            CreateApplicationDraft(
                candidate_id=self.candidate_id,
                canonical_vacancy_id=canonical_vacancy_id,
                language="en",
                target_role="CTO",
            )
        )
        artifact_count = self.conn.execute(
            "SELECT COUNT(*) FROM artifacts WHERE artifact_id = ? AND artifact_type = 'message_artifact'",
            (result["artifact_id"],),
        ).fetchone()[0]
        application_row = self.conn.execute(
            "SELECT application_state, message_artifact_id FROM applications WHERE candidate_id = ? AND canonical_vacancy_id = ?",
            (self.candidate_id, canonical_vacancy_id),
        ).fetchone()
        usage_events = self.conn.execute(
            "SELECT COUNT(*) FROM artifact_usage_events WHERE artifact_id = ? AND usage_type = 'application_draft_attached'",
            (result["artifact_id"],),
        ).fetchone()[0]
        self.assertEqual(artifact_count, 1)
        self.assertEqual(application_row["application_state"], "drafted")
        self.assertEqual(application_row["message_artifact_id"], result["artifact_id"])
        self.assertEqual(result["quality_gate"]["status"], "pass")
        self.assertEqual(usage_events, 1)

    def test_repeated_application_draft_attach_does_not_duplicate_usage_event(self) -> None:
        self.candidate_handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=self.candidate_id,
                source_kind="resume",
                source_origin="text",
                content_text=(
                    "Candidate\ncandidate@example.com\n"
                    "Обо мне\nTechnology leader focused on delivery and engineering effectiveness.\n"
                    "Опыт работы\nExample Corp\nCTO\n- Improved delivery speed by 40%.\n"
                ),
            )
        )
        draft = self.candidate_handlers.generate_candidate_profile_draft(
            GenerateCandidateProfileDraftFromSources(candidate_id=self.candidate_id)
        )
        self.candidate_handlers.confirm_candidate_profile_draft(
            ConfirmCandidateProfileDraft(candidate_id=self.candidate_id, draft_id=draft["draft_id"])
        )
        imported = self.handlers.import_vacancy_batch(
            ImportVacancyBatch(
                candidate_id=self.candidate_id,
                source_kind="manual",
                items=[VacancyImportItem(title="CTO", company_name="Hiring Corp")],
            )
        )
        canonical_vacancy_id = imported["imported"][0]["canonical_vacancy_id"]
        first = self.handlers.create_application_draft(
            CreateApplicationDraft(candidate_id=self.candidate_id, canonical_vacancy_id=canonical_vacancy_id)
        )
        second = self.handlers.create_application_draft(
            CreateApplicationDraft(candidate_id=self.candidate_id, canonical_vacancy_id=canonical_vacancy_id)
        )
        usage_events = self.conn.execute(
            "SELECT COUNT(*) FROM artifact_usage_events WHERE artifact_id = ? AND usage_type = 'application_draft_attached'",
            (first["artifact_id"],),
        ).fetchone()[0]

        self.assertEqual(second["artifact_id"], first["artifact_id"])
        self.assertEqual(usage_events, 1)

    def test_create_application_draft_cleans_artifact_file_when_db_write_fails(self) -> None:
        self.candidate_handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=self.candidate_id,
                source_kind="resume",
                source_origin="text",
                content_text=(
                    "Candidate\ncandidate@example.com\n"
                    "Обо мне\nTechnology leader focused on delivery.\n"
                    "Опыт работы\nExample Corp\nCTO\n- Improved delivery speed by 40%.\n"
                ),
            )
        )
        draft = self.candidate_handlers.generate_candidate_profile_draft(
            GenerateCandidateProfileDraftFromSources(candidate_id=self.candidate_id)
        )
        self.candidate_handlers.confirm_candidate_profile_draft(
            ConfirmCandidateProfileDraft(candidate_id=self.candidate_id, draft_id=draft["draft_id"])
        )
        imported = self.handlers.import_vacancy_batch(
            ImportVacancyBatch(
                candidate_id=self.candidate_id,
                source_kind="manual",
                items=[VacancyImportItem(title="CTO", company_name="Hiring Corp")],
            )
        )
        canonical_vacancy_id = imported["imported"][0]["canonical_vacancy_id"]
        drafts_dir = (
            self.artifact_root
            / "candidates"
            / ArtifactPathService.candidate_folder(self.candidate_id, "Andrei")
            / "drafts"
        )
        before = set(drafts_dir.glob("*.md")) if drafts_dir.exists() else set()

        def fail_create_artifact(**kwargs) -> None:
            raise RuntimeError("forced db failure")

        self.handlers._artifact_repository.create_artifact = fail_create_artifact
        with self.assertRaises(RuntimeError):
            self.handlers.create_application_draft(
                CreateApplicationDraft(
                    candidate_id=self.candidate_id,
                    canonical_vacancy_id=canonical_vacancy_id,
                    language="en",
                    target_role="CTO",
                )
            )
        after = set(drafts_dir.glob("*.md")) if drafts_dir.exists() else set()
        self.assertEqual(after, before)

    def test_daily_actions_projection_combines_review_and_application_work(self) -> None:
        self.candidate_handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=self.candidate_id,
                source_kind="resume",
                source_origin="text",
                content_text="Candidate\ncandidate@example.com\nОбо мне\nTechnology leader.\n",
            )
        )
        draft = self.candidate_handlers.generate_candidate_profile_draft(
            GenerateCandidateProfileDraftFromSources(candidate_id=self.candidate_id)
        )
        self.candidate_handlers.confirm_candidate_profile_draft(
            ConfirmCandidateProfileDraft(candidate_id=self.candidate_id, draft_id=draft["draft_id"])
        )
        imported = self.handlers.import_vacancy_batch(
            ImportVacancyBatch(
                candidate_id=self.candidate_id,
                source_kind="manual",
                items=[
                    VacancyImportItem(title="CTO", company_name="New Corp"),
                    VacancyImportItem(title="VP Engineering", company_name="Shortlist Corp"),
                ],
            )
        )
        new_vacancy_id = imported["imported"][0]["canonical_vacancy_id"]
        shortlisted_vacancy_id = imported["imported"][1]["canonical_vacancy_id"]
        self.handlers.shortlist_vacancy(
            ShortlistVacancy(candidate_id=self.candidate_id, canonical_vacancy_id=shortlisted_vacancy_id)
        )
        self.handlers.create_application_draft(
            CreateApplicationDraft(
                candidate_id=self.candidate_id,
                canonical_vacancy_id=shortlisted_vacancy_id,
                language="en",
            )
        )
        actions = self.handlers.list_daily_actions(ListDailyActions(candidate_id=self.candidate_id))
        action_types = [item["action_type"] for item in actions]
        self.assertIn("review_new_vacancy", action_types)
        self.assertIn("prepare_application", action_types)
        self.assertIn("review_application_draft", action_types)

    def test_daily_actions_suppresses_skip_and_downgrades_low_fit_vacancies(self) -> None:
        self.candidate_handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=self.candidate_id,
                source_kind="profile",
                source_origin="text",
                content_text=(
                    "Candidate\ncandidate@example.com\n"
                    "Target roles:\n- CTO\n"
                    "Compensation EUR:\n- salary floor: 60000\n- salary target: 100000\n- currency: EUR\n"
                    "Search preferences:\n- remote only\n"
                    "Company avoid list:\n- Sberbank\n"
                ),
            )
        )
        draft = self.candidate_handlers.generate_candidate_profile_draft(
            GenerateCandidateProfileDraftFromSources(candidate_id=self.candidate_id)
        )
        self.candidate_handlers.confirm_candidate_profile_draft(
            ConfirmCandidateProfileDraft(candidate_id=self.candidate_id, draft_id=draft["draft_id"])
        )
        imported = self.handlers.import_vacancy_batch(
            ImportVacancyBatch(
                candidate_id=self.candidate_id,
                source_kind="manual",
                items=[
                    VacancyImportItem(
                        title="CTO",
                        company_name="Good Corp",
                        location_text="Remote, Europe",
                        raw_text="Remote CTO role. Salary 120000 EUR.",
                    ),
                    VacancyImportItem(
                        title="Backend Developer",
                        company_name="CodeFactory",
                        location_text="Remote, Europe",
                        raw_text="Python backend individual contributor role. Salary 90000 EUR.",
                    ),
                    VacancyImportItem(
                        title="CTO",
                        company_name="Sberbank",
                        location_text="Remote, Europe",
                        raw_text="Remote CTO role. Salary 180000 EUR.",
                    ),
                ],
            )
        )
        good_id = imported["imported"][0]["canonical_vacancy_id"]
        low_id = imported["imported"][1]["canonical_vacancy_id"]
        skip_id = imported["imported"][2]["canonical_vacancy_id"]

        actions = self.handlers.list_daily_actions(ListDailyActions(candidate_id=self.candidate_id))
        actions_by_vacancy = {item["canonical_vacancy_id"]: item for item in actions if "canonical_vacancy_id" in item}

        self.assertEqual(actions_by_vacancy[good_id]["action_type"], "review_new_vacancy")
        self.assertEqual(actions_by_vacancy[low_id]["action_type"], "review_low_fit_vacancy")
        self.assertEqual(actions_by_vacancy[low_id]["priority"], 35)
        self.assertNotIn(skip_id, actions_by_vacancy)

    def test_prepare_application_payload_returns_resume_and_message_artifacts(self) -> None:
        self.candidate_handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=self.candidate_id,
                source_kind="resume",
                source_origin="text",
                content_text=(
                    "Example Candidate\ncandidate@example.com\n"
                    "Желаемая должность и зарплата\nCTO\n"
                    "Обо мне\nTechnology leader with platform and delivery focus.\n"
                    "Опыт работы\nExample Corp\nCTO\n- Improved delivery speed by 40%.\n"
                ),
            )
        )
        draft = self.candidate_handlers.generate_candidate_profile_draft(
            GenerateCandidateProfileDraftFromSources(candidate_id=self.candidate_id)
        )
        self.candidate_handlers.confirm_candidate_profile_draft(
            ConfirmCandidateProfileDraft(candidate_id=self.candidate_id, draft_id=draft["draft_id"])
        )
        imported = self.handlers.import_vacancy_batch(
            ImportVacancyBatch(
                candidate_id=self.candidate_id,
                source_kind="manual",
                items=[VacancyImportItem(title="CTO", company_name="Hiring Corp")],
            )
        )
        canonical_vacancy_id = imported["imported"][0]["canonical_vacancy_id"]
        payload = self.handlers.prepare_application_payload(
            PrepareApplicationPayload(
                candidate_id=self.candidate_id,
                canonical_vacancy_id=canonical_vacancy_id,
                language="en",
            )
        )
        self.assertIn(payload["resume_quality_gate"]["status"], {"pass", "warn"})
        self.assertEqual(payload["message_quality_gate"]["status"], "pass")
        self.assertTrue(payload["resume_artifact_id"])
        self.assertTrue(payload["message_artifact_id"])
        self.assertTrue(payload["application_id"])

    def test_prepare_application_payload_rolls_back_artifacts_when_second_artifact_write_fails(self) -> None:
        self.candidate_handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=self.candidate_id,
                source_kind="resume",
                source_origin="text",
                content_text=(
                    "Example Candidate\ncandidate@example.com\n"
                    "Желаемая должность и зарплата\nCTO\n"
                    "Обо мне\nTechnology leader with platform and delivery focus.\n"
                    "Опыт работы\nExample Corp\nCTO\n- Improved delivery speed by 40%.\n"
                ),
            )
        )
        draft = self.candidate_handlers.generate_candidate_profile_draft(
            GenerateCandidateProfileDraftFromSources(candidate_id=self.candidate_id)
        )
        self.candidate_handlers.confirm_candidate_profile_draft(
            ConfirmCandidateProfileDraft(candidate_id=self.candidate_id, draft_id=draft["draft_id"])
        )
        imported = self.handlers.import_vacancy_batch(
            ImportVacancyBatch(
                candidate_id=self.candidate_id,
                source_kind="manual",
                items=[VacancyImportItem(title="CTO", company_name="Hiring Corp")],
            )
        )
        canonical_vacancy_id = imported["imported"][0]["canonical_vacancy_id"]
        drafts_dir = (
            self.artifact_root
            / "candidates"
            / ArtifactPathService.candidate_folder(self.candidate_id, "Andrei")
            / "drafts"
        )
        before_files = set(drafts_dir.glob("*.md")) if drafts_dir.exists() else set()
        original_create_artifact = self.handlers._artifact_repository.create_artifact
        calls = {"count": 0}

        def fail_second_artifact(**kwargs) -> None:
            calls["count"] += 1
            if calls["count"] == 2:
                raise RuntimeError("forced message artifact failure")
            original_create_artifact(**kwargs)

        self.handlers._artifact_repository.create_artifact = fail_second_artifact
        with self.assertRaises(RuntimeError):
            self.handlers.prepare_application_payload(
                PrepareApplicationPayload(
                    candidate_id=self.candidate_id,
                    canonical_vacancy_id=canonical_vacancy_id,
                    language="en",
                )
            )
        after_files = set(drafts_dir.glob("*.md")) if drafts_dir.exists() else set()
        generated_artifacts = self.conn.execute(
            "SELECT COUNT(*) FROM artifacts WHERE candidate_id = ? AND artifact_type IN ('resume_markdown', 'message_artifact')",
            (self.candidate_id,),
        ).fetchone()[0]
        applications = self.conn.execute(
            "SELECT COUNT(*) FROM applications WHERE candidate_id = ?",
            (self.candidate_id,),
        ).fetchone()[0]
        self.assertEqual(after_files, before_files)
        self.assertEqual(generated_artifacts, 0)
        self.assertEqual(applications, 0)

    def test_create_touchpoint_with_follow_up_reminder_and_usage_event(self) -> None:
        self.candidate_handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=self.candidate_id,
                source_kind="resume",
                source_origin="text",
                content_text="Example Candidate\ncandidate@example.com\nОбо мне\nTechnology leader.\n",
            )
        )
        draft = self.candidate_handlers.generate_candidate_profile_draft(
            GenerateCandidateProfileDraftFromSources(candidate_id=self.candidate_id)
        )
        self.candidate_handlers.confirm_candidate_profile_draft(
            ConfirmCandidateProfileDraft(candidate_id=self.candidate_id, draft_id=draft["draft_id"])
        )
        imported = self.handlers.import_vacancy_batch(
            ImportVacancyBatch(
                candidate_id=self.candidate_id,
                source_kind="manual",
                items=[VacancyImportItem(title="CTO", company_name="Hiring Corp")],
            )
        )
        canonical_vacancy_id = imported["imported"][0]["canonical_vacancy_id"]
        payload = self.handlers.prepare_application_payload(
            PrepareApplicationPayload(
                candidate_id=self.candidate_id,
                canonical_vacancy_id=canonical_vacancy_id,
                language="en",
            )
        )
        created = self.handlers.create_touchpoint(
            CreateTouchpoint(
                candidate_id=self.candidate_id,
                canonical_vacancy_id=canonical_vacancy_id,
                application_id=payload["application_id"],
                message_artifact_id=payload["message_artifact_id"],
                channel="email",
                touchpoint_state="sent",
                follow_up_due_at="2026-05-20T10:00:00+00:00",
            )
        )
        touchpoints = self.handlers.list_touchpoints(ListTouchpoints(candidate_id=self.candidate_id))
        usage_events = self.conn.execute(
            "SELECT COUNT(*) FROM artifact_usage_events WHERE artifact_id = ? AND usage_type = 'touchpoint_message_used'",
            (payload["message_artifact_id"],),
        ).fetchone()[0]
        self.assertIsNotNone(created["touchpoint"])
        self.assertIsNotNone(created["reminder"])
        self.assertEqual(len(touchpoints), 1)
        self.assertEqual(usage_events, 1)

    def test_touchpoint_rejects_invalid_state_direction_and_dates(self) -> None:
        imported = self.handlers.import_vacancy_batch(
            ImportVacancyBatch(
                candidate_id=self.candidate_id,
                source_kind="manual",
                items=[VacancyImportItem(title="CTO", company_name="Hiring Corp")],
            )
        )
        canonical_vacancy_id = imported["imported"][0]["canonical_vacancy_id"]

        with self.assertRaisesRegex(ValueError, "touchpoint_state must be one of"):
            self.handlers.create_touchpoint(
                CreateTouchpoint(
                    candidate_id=self.candidate_id,
                    canonical_vacancy_id=canonical_vacancy_id,
                    touchpoint_state="maybe",
                )
            )
        with self.assertRaisesRegex(ValueError, "direction must be one of"):
            self.handlers.create_touchpoint(
                CreateTouchpoint(
                    candidate_id=self.candidate_id,
                    canonical_vacancy_id=canonical_vacancy_id,
                    direction="outbound",
                )
            )
        with self.assertRaisesRegex(ValueError, "follow_up_due_at must be an ISO 8601 datetime"):
            self.handlers.create_touchpoint(
                CreateTouchpoint(
                    candidate_id=self.candidate_id,
                    canonical_vacancy_id=canonical_vacancy_id,
                    follow_up_due_at="tomorrow",
                )
            )

    def test_touchpoint_rejects_foreign_candidate_message_artifact(self) -> None:
        second_candidate_id = self.candidate_handlers.create_candidate(CreateCandidate(display_name="Second Candidate"))["candidate_id"]
        foreign_artifact = self.candidate_handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=second_candidate_id,
                source_kind="resume",
                source_origin="text",
                content_text="Second Candidate\nsecond@example.com\n",
            )
        )
        imported = self.handlers.import_vacancy_batch(
            ImportVacancyBatch(
                candidate_id=self.candidate_id,
                source_kind="manual",
                items=[VacancyImportItem(title="CTO", company_name="Hiring Corp")],
            )
        )
        canonical_vacancy_id = imported["imported"][0]["canonical_vacancy_id"]
        with self.assertRaises(PermissionError):
            self.handlers.create_touchpoint(
                CreateTouchpoint(
                    candidate_id=self.candidate_id,
                    canonical_vacancy_id=canonical_vacancy_id,
                    message_artifact_id=foreign_artifact["artifact_id"],
                    channel="email",
                    touchpoint_state="sent",
                )
            )

    def test_daily_actions_include_follow_up_due_and_resolve_reminder(self) -> None:
        imported = self.handlers.import_vacancy_batch(
            ImportVacancyBatch(
                candidate_id=self.candidate_id,
                source_kind="manual",
                items=[VacancyImportItem(title="CTO", company_name="Hiring Corp")],
            )
        )
        canonical_vacancy_id = imported["imported"][0]["canonical_vacancy_id"]
        created = self.handlers.create_touchpoint(
            CreateTouchpoint(
                candidate_id=self.candidate_id,
                canonical_vacancy_id=canonical_vacancy_id,
                channel="email",
                touchpoint_state="sent",
                follow_up_due_at="2026-05-20T10:00:00+00:00",
            )
        )
        actions = self.handlers.list_daily_actions(ListDailyActions(candidate_id=self.candidate_id))
        action_types = [item["action_type"] for item in actions]
        self.assertIn("follow_up_due", action_types)
        reminder_id = created["reminder"]["reminder_id"]
        self.handlers.resolve_reminder(ResolveReminder(candidate_id=self.candidate_id, reminder_id=reminder_id))
        actions_after = self.handlers.list_daily_actions(ListDailyActions(candidate_id=self.candidate_id))
        action_types_after = [item["action_type"] for item in actions_after]
        self.assertNotIn("follow_up_due", action_types_after)

    def test_interview_round_lifecycle_is_separate_from_application_state(self) -> None:
        self.candidate_handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=self.candidate_id,
                source_kind="resume",
                source_origin="text",
                content_text="Candidate\ncandidate@example.com\nEngineering leader.\n",
            )
        )
        draft = self.candidate_handlers.generate_candidate_profile_draft(
            GenerateCandidateProfileDraftFromSources(candidate_id=self.candidate_id)
        )
        self.candidate_handlers.confirm_candidate_profile_draft(
            ConfirmCandidateProfileDraft(candidate_id=self.candidate_id, draft_id=draft["draft_id"])
        )
        imported = self.handlers.import_vacancy_batch(
            ImportVacancyBatch(
                candidate_id=self.candidate_id,
                source_kind="manual",
                items=[VacancyImportItem(title="CTO", company_name="Interview Corp")],
            )
        )
        canonical_vacancy_id = imported["imported"][0]["canonical_vacancy_id"]
        application = self.handlers.create_application_draft(
            CreateApplicationDraft(candidate_id=self.candidate_id, canonical_vacancy_id=canonical_vacancy_id)
        )
        created = self.handlers.create_interview_round(
            CreateInterviewRound(
                candidate_id=self.candidate_id,
                application_id=application["application_id"],
                round_type="technical",
                scheduled_at="2026-06-10T10:00:00+00:00",
                interviewer_name="Hiring Manager",
                idempotency_key="technical-round-1",
            )
        )
        repeated = self.handlers.create_interview_round(
            CreateInterviewRound(
                candidate_id=self.candidate_id,
                application_id=application["application_id"],
                round_type="technical",
                scheduled_at="2026-06-10T10:00:00+00:00",
                interviewer_name="Hiring Manager",
                idempotency_key="technical-round-1",
            )
        )
        rounds = self.handlers.list_interview_rounds(ListInterviewRounds(candidate_id=self.candidate_id))
        actions = self.handlers.list_daily_actions(ListDailyActions(candidate_id=self.candidate_id))
        current_application = self.conn.execute(
            "SELECT application_state FROM applications WHERE application_id = ?",
            (application["application_id"],),
        ).fetchone()

        self.assertFalse(created["reused"])
        self.assertTrue(repeated["reused"])
        self.assertEqual(created["application"]["application_state"], "interviewing")
        self.assertEqual(current_application["application_state"], "interviewing")
        self.assertEqual(len(rounds), 1)
        self.assertEqual(rounds[0]["round_state"], "scheduled")
        self.assertIn("interview_round_due", {item["action_type"] for item in actions})

        updated = self.handlers.update_interview_round_state(
            UpdateInterviewRoundState(
                candidate_id=self.candidate_id,
                interview_round_id=created["interview_round"]["interview_round_id"],
                round_state="completed",
                completed_at="2026-06-10T11:00:00+00:00",
            )
        )
        actions_after = self.handlers.list_daily_actions(ListDailyActions(candidate_id=self.candidate_id))
        self.assertEqual(updated["round_state"], "completed")
        self.assertNotIn("interview_round_due", {item["action_type"] for item in actions_after})

    def test_manual_board_action_records_usage_and_is_idempotent(self) -> None:
        self.candidate_handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=self.candidate_id,
                source_kind="resume",
                source_origin="text",
                content_text="Example Candidate\ncandidate@example.com\nTechnology leader.\n",
            )
        )
        draft = self.candidate_handlers.generate_candidate_profile_draft(
            GenerateCandidateProfileDraftFromSources(candidate_id=self.candidate_id)
        )
        self.candidate_handlers.confirm_candidate_profile_draft(
            ConfirmCandidateProfileDraft(candidate_id=self.candidate_id, draft_id=draft["draft_id"])
        )
        imported = self.handlers.import_vacancy_batch(
            ImportVacancyBatch(
                candidate_id=self.candidate_id,
                source_kind="manual",
                items=[VacancyImportItem(title="CTO", company_name="Hiring Corp")],
            )
        )
        canonical_vacancy_id = imported["imported"][0]["canonical_vacancy_id"]
        payload = self.handlers.prepare_application_payload(
            PrepareApplicationPayload(candidate_id=self.candidate_id, canonical_vacancy_id=canonical_vacancy_id)
        )
        self.assertIn(payload["application_payload_quality_gate"]["status"], {"pass", "warn", "fail"})
        with self.assertRaisesRegex(ValueError, "external_action_approval_id is required"):
            self.handlers.record_manual_board_action(
                RecordManualBoardAction(
                    candidate_id=self.candidate_id,
                    platform="LinkedIn",
                    action_type="application_submitted",
                    canonical_vacancy_id=canonical_vacancy_id,
                    application_id=payload["application_id"],
                    artifact_id=payload["message_artifact_id"],
                    external_target="https://www.linkedin.com/jobs/view/123",
                    occurred_at="2026-05-20T12:00:00+00:00",
                )
            )
        acceptance = self.handlers.record_artifact_acceptance(
            RecordArtifactAcceptance(
                candidate_id=self.candidate_id,
                artifact_id=payload["message_artifact_id"],
                idempotency_key="accept-message-submit",
            )
        )
        repeated_acceptance = self.handlers.record_artifact_acceptance(
            RecordArtifactAcceptance(
                candidate_id=self.candidate_id,
                artifact_id=payload["message_artifact_id"],
                idempotency_key="accept-message-submit",
            )
        )
        approval = self.handlers.record_external_action_approval(
            RecordExternalActionApproval(
                candidate_id=self.candidate_id,
                platform="LinkedIn",
                action_type="application_submitted",
                canonical_vacancy_id=canonical_vacancy_id,
                application_id=payload["application_id"],
                artifact_id=payload["message_artifact_id"],
                external_target="https://www.linkedin.com/jobs/view/123",
                idempotency_key="approve-submit-linkedin-123",
            )
        )
        approvals = self.handlers.list_approvals(ListApprovals(candidate_id=self.candidate_id))
        self.assertFalse(acceptance["reused"])
        self.assertTrue(repeated_acceptance["reused"])
        self.assertEqual(len(approvals), 2)

        command = RecordManualBoardAction(
            candidate_id=self.candidate_id,
            platform="LinkedIn",
            action_type="application_submitted",
            canonical_vacancy_id=canonical_vacancy_id,
            application_id=payload["application_id"],
            artifact_id=payload["message_artifact_id"],
            external_target="https://www.linkedin.com/jobs/view/123",
            occurred_at="2026-05-20T12:00:00+00:00",
            idempotency_key="submit-linkedin-123",
            external_action_approval_id=approval["approval"]["approval_id"],
        )
        first = self.handlers.record_manual_board_action(command)
        second = self.handlers.record_manual_board_action(command)
        actions = self.handlers.list_manual_board_actions(
            ListManualBoardActions(candidate_id=self.candidate_id, platform="linkedin")
        )
        report = self.handlers.get_pipeline_report(GetPipelineReport(candidate_id=self.candidate_id))
        usage_events = self.conn.execute(
            """
            SELECT COUNT(*)
            FROM artifact_usage_events
            WHERE artifact_id = ? AND usage_type = 'manual_board_action_artifact_used'
            """,
            (payload["message_artifact_id"],),
        ).fetchone()[0]

        self.assertFalse(first["reused"])
        self.assertTrue(second["reused"])
        self.assertEqual(len(actions), 1)
        self.assertEqual(report["summary"]["manual_board_actions"], 1)
        self.assertEqual(report["board_action_counts"]["application_submitted"], 1)
        self.assertEqual(report["recent_board_actions"][0]["board_action_id"], first["board_action"]["board_action_id"])
        self.assertEqual(
            report["recent_board_actions"][0]["external_action_approval_id"],
            approval["approval"]["approval_id"],
        )
        self.assertEqual(first["reconciliation_item"]["outcome"], "auto_accept")
        self.assertEqual(first["reconciliation_item"]["review_status"], "resolved")
        self.assertEqual(second["reconciliation_item"]["reconciliation_item_id"], first["reconciliation_item"]["reconciliation_item_id"])
        self.assertEqual(usage_events, 1)

    def test_manual_board_action_reconciliation_needs_review_when_not_confidently_linked(self) -> None:
        action = self.handlers.record_manual_board_action(
            RecordManualBoardAction(
                candidate_id=self.candidate_id,
                platform="linkedin",
                action_type="vacancy_hidden",
                external_target="https://www.linkedin.com/jobs/view/orphan",
                occurred_at="2026-05-20T12:00:00+00:00",
                idempotency_key="orphan-hidden-linkedin",
            )
        )
        item = action["reconciliation_item"]
        open_items = self.handlers.list_reconciliation_items(
            ListReconciliationItems(candidate_id=self.candidate_id, review_status="open")
        )
        daily_actions = self.handlers.list_daily_actions(ListDailyActions(candidate_id=self.candidate_id))

        self.assertEqual(item["outcome"], "needs_review")
        self.assertEqual(item["drift_type"], "conflict_drift")
        self.assertEqual(item["review_status"], "open")
        self.assertEqual(len(open_items), 1)
        self.assertEqual(open_items[0]["reconciliation_item_id"], item["reconciliation_item_id"])
        self.assertTrue(
            any(
                action["action_type"] == "review_reconciliation_item"
                and action["reconciliation_item_id"] == item["reconciliation_item_id"]
                for action in daily_actions
            )
        )

        resolved = self.handlers.resolve_reconciliation_item(
            ResolveReconciliationItem(
                candidate_id=self.candidate_id,
                reconciliation_item_id=str(item["reconciliation_item_id"]),
                review_status="resolved",
                resolution_notes="Linked to no local vacancy; record only.",
            )
        )
        remaining_open = self.handlers.list_reconciliation_items(
            ListReconciliationItems(candidate_id=self.candidate_id, review_status="open")
        )
        self.assertEqual(resolved["review_status"], "resolved")
        self.assertEqual(remaining_open, [])

    def test_manual_board_action_requires_artifact_for_external_submit(self) -> None:
        imported = self.handlers.import_vacancy_batch(
            ImportVacancyBatch(
                candidate_id=self.candidate_id,
                source_kind="manual",
                items=[VacancyImportItem(title="CTO", company_name="Hiring Corp")],
            )
        )
        canonical_vacancy_id = imported["imported"][0]["canonical_vacancy_id"]
        with self.assertRaisesRegex(ValueError, "artifact_id is required"):
            self.handlers.record_manual_board_action(
                RecordManualBoardAction(
                    candidate_id=self.candidate_id,
                    platform="linkedin",
                    action_type="application_submitted",
                    canonical_vacancy_id=canonical_vacancy_id,
                )
            )

    def test_vacancy_resume_uses_matching_final_source_and_overwrites_per_vacancy(self) -> None:
        self.candidate_handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=self.candidate_id,
                source_kind="resume",
                source_origin="text",
                content_text=(
                    "Candidate\ncandidate@example.com\n"
                    "Желаемая должность и зарплата\nCTO\n"
                    "Обо мне\nPlatform engineering leader.\n"
                ),
            )
        )
        draft = self.candidate_handlers.generate_candidate_profile_draft(
            GenerateCandidateProfileDraftFromSources(candidate_id=self.candidate_id)
        )
        self.candidate_handlers.confirm_candidate_profile_draft(
            ConfirmCandidateProfileDraft(candidate_id=self.candidate_id, draft_id=draft["draft_id"])
        )
        base_resume = self.candidate_handlers.generate_resume_markdown(
            GenerateResumeMarkdown(candidate_id=self.candidate_id, language="en", target_role="CTO")
        )
        final_resume = self.candidate_handlers.finalize_resume_markdown(
            FinalizeResumeMarkdown(
                artifact_id=base_resume["artifact_id"],
                allow_warnings=base_resume["quality_gate"]["status"] == "warn",
            )
        )
        imported = self.handlers.import_vacancy_batch(
            ImportVacancyBatch(
                candidate_id=self.candidate_id,
                source_kind="manual",
                items=[VacancyImportItem(title="CTO", company_name="Acme", location_text="Remote Europe")],
            )
        )
        canonical_vacancy_id = imported["imported"][0]["canonical_vacancy_id"]

        first = self.handlers.generate_vacancy_resume(
            GenerateVacancyResume(candidate_id=self.candidate_id, canonical_vacancy_id=canonical_vacancy_id)
        )
        second = self.handlers.generate_vacancy_resume(
            GenerateVacancyResume(candidate_id=self.candidate_id, canonical_vacancy_id=canonical_vacancy_id)
        )
        vacancy_final = self.handlers.finalize_vacancy_resume(
            FinalizeVacancyResume(
                candidate_id=self.candidate_id,
                artifact_id=first["artifact_id"],
                allow_warnings=first["quality_gate"]["status"] == "warn",
            )
        )
        repeated_vacancy_final = self.handlers.finalize_vacancy_resume(
            FinalizeVacancyResume(
                candidate_id=self.candidate_id,
                artifact_id=first["artifact_id"],
                allow_warnings=first["quality_gate"]["status"] == "warn",
            )
        )

        self.assertEqual(first["source_resume_artifact_id"], final_resume["artifact_id"])
        self.assertEqual(first["source_resume_artifact_type"], "resume_markdown_final")
        self.assertFalse(first["overwritten"])
        self.assertTrue(second["overwritten"])
        self.assertEqual(first["artifact_id"], second["artifact_id"])
        self.assertIn("/drafts/resume-vacancy--acme-cto--", first["storage_path"])
        self.assertIn("/final/resume-vacancy-final--acme-cto--", vacancy_final["storage_path"])
        self.assertEqual(vacancy_final["derived_from_artifact_id"], first["artifact_id"])
        self.assertEqual(repeated_vacancy_final["artifact_id"], vacancy_final["artifact_id"])
        self.assertTrue(repeated_vacancy_final["overwritten"])
        record_count = self.conn.execute(
            """
            SELECT COUNT(*) FROM artifacts
            WHERE candidate_id = ? AND artifact_type = 'resume_vacancy'
            """,
            (self.candidate_id,),
        ).fetchone()[0]
        final_record_count = self.conn.execute(
            """
            SELECT COUNT(*) FROM artifacts
            WHERE candidate_id = ? AND artifact_type = 'resume_vacancy_final'
            """,
            (self.candidate_id,),
        ).fetchone()[0]
        self.assertEqual(record_count, 1)
        self.assertEqual(final_record_count, 1)

    def test_vacancy_resume_requests_source_selection_when_only_one_role_draft_exists_without_final(self) -> None:
        self.candidate_handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=self.candidate_id,
                source_kind="resume",
                source_origin="text",
                content_text="Candidate\ncandidate@example.com\nЖелаемая должность и зарплата\nCTO\nОбо мне\nPlatform leader.\n",
            )
        )
        draft = self.candidate_handlers.generate_candidate_profile_draft(
            GenerateCandidateProfileDraftFromSources(candidate_id=self.candidate_id)
        )
        self.candidate_handlers.confirm_candidate_profile_draft(
            ConfirmCandidateProfileDraft(candidate_id=self.candidate_id, draft_id=draft["draft_id"])
        )
        resume = self.candidate_handlers.generate_resume_markdown(
            GenerateResumeMarkdown(candidate_id=self.candidate_id, language="en", target_role="CTO")
        )
        imported = self.handlers.import_vacancy_batch(
            ImportVacancyBatch(
                candidate_id=self.candidate_id,
                source_kind="manual",
                items=[VacancyImportItem(title="CTO", company_name="Acme")],
            )
        )
        canonical_vacancy_id = imported["imported"][0]["canonical_vacancy_id"]

        selection = self.handlers.generate_vacancy_resume(
            GenerateVacancyResume(candidate_id=self.candidate_id, canonical_vacancy_id=canonical_vacancy_id)
        )

        self.assertEqual(selection["status"], "needs_source_selection")
        self.assertEqual(len(selection["source_options"]), 1)
        self.assertEqual(selection["source_options"][0]["artifact_id"], resume["artifact_id"])

    def test_vacancy_resume_requests_source_selection_when_multiple_role_drafts_exist_without_final(self) -> None:
        self.candidate_handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=self.candidate_id,
                source_kind="resume",
                source_origin="text",
                content_text="Candidate\ncandidate@example.com\nЖелаемая должность и зарплата\nCTO\nОбо мне\nPlatform leader.\n",
            )
        )
        draft = self.candidate_handlers.generate_candidate_profile_draft(
            GenerateCandidateProfileDraftFromSources(candidate_id=self.candidate_id)
        )
        self.candidate_handlers.confirm_candidate_profile_draft(
            ConfirmCandidateProfileDraft(candidate_id=self.candidate_id, draft_id=draft["draft_id"])
        )
        first_resume = self.candidate_handlers.generate_resume_markdown(
            GenerateResumeMarkdown(candidate_id=self.candidate_id, language="en", target_role="CTO")
        )
        second_resume = self.candidate_handlers.generate_resume_markdown(
            GenerateResumeMarkdown(candidate_id=self.candidate_id, language="ru", target_role="CTO")
        )
        imported = self.handlers.import_vacancy_batch(
            ImportVacancyBatch(
                candidate_id=self.candidate_id,
                source_kind="manual",
                items=[VacancyImportItem(title="CTO", company_name="Acme")],
            )
        )
        canonical_vacancy_id = imported["imported"][0]["canonical_vacancy_id"]

        selection = self.handlers.generate_vacancy_resume(
            GenerateVacancyResume(candidate_id=self.candidate_id, canonical_vacancy_id=canonical_vacancy_id)
        )
        explicit = self.handlers.generate_vacancy_resume(
            GenerateVacancyResume(
                candidate_id=self.candidate_id,
                canonical_vacancy_id=canonical_vacancy_id,
                source_resume_artifact_id=second_resume["artifact_id"],
            )
        )

        self.assertEqual(selection["status"], "needs_source_selection")
        self.assertEqual(
            {option["artifact_id"] for option in selection["source_options"]},
            {first_resume["artifact_id"], second_resume["artifact_id"]},
        )
        self.assertEqual(explicit["source_resume_artifact_id"], second_resume["artifact_id"])

    def test_finalize_vacancy_resume_requires_allow_warnings_for_warn_gate(self) -> None:
        self.candidate_handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=self.candidate_id,
                source_kind="resume",
                source_origin="text",
                content_text=(
                    "Candidate\ncandidate@example.com\n"
                    "Желаемая должность и зарплата\nCTO\n"
                    "Обо мне\nPlatform engineering leader.\n"
                ),
            )
        )
        draft = self.candidate_handlers.generate_candidate_profile_draft(
            GenerateCandidateProfileDraftFromSources(candidate_id=self.candidate_id)
        )
        self.candidate_handlers.confirm_candidate_profile_draft(
            ConfirmCandidateProfileDraft(candidate_id=self.candidate_id, draft_id=draft["draft_id"])
        )
        base_resume = self.candidate_handlers.generate_resume_markdown(
            GenerateResumeMarkdown(candidate_id=self.candidate_id, language="en", target_role="CTO")
        )
        final_resume = self.candidate_handlers.finalize_resume_markdown(
            FinalizeResumeMarkdown(
                artifact_id=base_resume["artifact_id"],
                allow_warnings=base_resume["quality_gate"]["status"] == "warn",
            )
        )
        imported = self.handlers.import_vacancy_batch(
            ImportVacancyBatch(
                candidate_id=self.candidate_id,
                source_kind="manual",
                items=[VacancyImportItem(title="CTO", company_name="Acme", location_text="Remote Europe")],
            )
        )
        canonical_vacancy_id = imported["imported"][0]["canonical_vacancy_id"]
        vacancy_resume = self.handlers.generate_vacancy_resume(
            GenerateVacancyResume(candidate_id=self.candidate_id, canonical_vacancy_id=canonical_vacancy_id)
        )

        self.assertEqual(vacancy_resume["source_resume_artifact_id"], final_resume["artifact_id"])

        storage_path = Path(vacancy_resume["storage_path"])
        storage_path.write_text(f"{storage_path.read_text(encoding='utf-8')}\nTODO: tailor further\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "allow_warnings is true"):
            self.handlers.finalize_vacancy_resume(
                FinalizeVacancyResume(candidate_id=self.candidate_id, artifact_id=vacancy_resume["artifact_id"])
            )

        finalized = self.handlers.finalize_vacancy_resume(
            FinalizeVacancyResume(
                candidate_id=self.candidate_id,
                artifact_id=vacancy_resume["artifact_id"],
                allow_warnings=True,
            )
        )

        self.assertEqual(finalized["artifact_type"], "resume_vacancy_final")
        self.assertEqual(finalized["quality_gate"]["status"], "warn")

    def test_board_checklist_is_projection_not_mutation(self) -> None:
        before = self.conn.execute("SELECT COUNT(*) FROM manual_board_actions").fetchone()[0]
        checklist = self.handlers.get_board_checklist(
            GetBoardChecklist(candidate_id=self.candidate_id, platform="linkedin")
        )
        after = self.conn.execute("SELECT COUNT(*) FROM manual_board_actions").fetchone()[0]

        self.assertEqual(checklist["platform"], "linkedin")
        self.assertIn("saved_search_settings", checklist)
        self.assertEqual(before, after)
