from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(PROJECT_ROOT.parents[1]) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT.parents[1]))

from job_search.application.commands.candidate import (  # noqa: E402
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
    SetActiveCandidate,
    UpdateCandidateCompensation,
    UpdateCandidateTargets,
)
from job_search.application.queries.candidate import GetActiveCandidate, GetCandidateDraftReview, GetCandidateProfile, ListCandidates  # noqa: E402
from job_search.application.handlers.candidate_handlers import CandidateHandlers  # noqa: E402
from job_search.application.services.candidate_conflict_resolution_service import CandidateConflictResolutionService  # noqa: E402
from job_search.application.services.candidate_draft_review_service import CandidateDraftReviewService  # noqa: E402
from job_search.application.services.candidate_extraction_service import CandidateExtractionService  # noqa: E402
from job_search.application.services.candidate_profile_mapping_service import CandidateProfileMappingService  # noqa: E402
from job_search.application.services.candidate_source_service import CandidateSourceService  # noqa: E402
from job_search.application.services.artifact_path_service import ArtifactPathService  # noqa: E402
from job_search.application.services.candidate_ai_extraction_service import CandidateAiExtractionService  # noqa: E402
from job_search.application.services.career_pathing_full_service import CareerPathingFullService  # noqa: E402
from job_search.application.services.career_pathing_lite_service import CareerPathingLiteService  # noqa: E402
from job_search.application.services.job_search_playbook_service import JobSearchPlaybookService  # noqa: E402
from job_search.application.services.resume_assembly_service import ResumeAssemblyService  # noqa: E402
from job_search.application.services.resume_positioning_service import ResumePositioningService  # noqa: E402
from job_search.application.services.resume_quality_gate_service import ResumeQualityGateService  # noqa: E402
from job_search.application.services.resume_roast_report_service import ResumeRoastReportService  # noqa: E402
from job_search.application.services.vacancy_normalization_service import VacancyNormalizationService  # noqa: E402
from job_search.infrastructure.db.connection import write_tx  # noqa: E402
from job_search.infrastructure.db.connection import load_connection  # noqa: E402
from job_search.infrastructure.db.schema_version import apply_migrations  # noqa: E402
from job_search.infrastructure.repositories.artifact_repository import ArtifactRepository  # noqa: E402
from job_search.infrastructure.repositories.audit_repository import AuditRepository  # noqa: E402
from job_search.infrastructure.repositories.candidate_draft_repository import CandidateDraftRepository  # noqa: E402
from job_search.infrastructure.repositories.candidate_evidence_repository import CandidateEvidenceRepository  # noqa: E402
from job_search.infrastructure.repositories.candidate_repository import CandidateRepository  # noqa: E402
from job_search.infrastructure.repositories.quality_gate_repository import QualityGateRepository  # noqa: E402
from job_search.infrastructure.repositories.vacancy_repository import VacancyRepository  # noqa: E402


class CandidateCoreIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "data" / "job_search.sqlite"
        self.artifact_root = self.root / "data" / "artifacts"
        self.workspace_path = self.root / "config" / "workspace.local.toml"
        self.sqlite_config_path = PROJECT_ROOT.parents[1] / "common" / "config" / "sqlite.toml"
        self.conn = load_connection(self.db_path, self.sqlite_config_path)
        apply_migrations(self.conn, PROJECT_ROOT / "src" / "job_search" / "infrastructure" / "migrations")
        candidate_repository = CandidateRepository(self.conn)
        artifact_repository = ArtifactRepository(self.conn)
        draft_repository = CandidateDraftRepository(self.conn)
        evidence_repository = CandidateEvidenceRepository(self.conn)
        self.vacancy_repository = VacancyRepository(self.conn)
        audit_repository = AuditRepository(self.conn)
        quality_gate_repository = QualityGateRepository(self.conn)
        self.handlers = CandidateHandlers(
            candidate_repository=candidate_repository,
            artifact_repository=artifact_repository,
            draft_repository=draft_repository,
            evidence_repository=evidence_repository,
            audit_repository=audit_repository,
            candidate_source_service=CandidateSourceService(artifact_repository, self.artifact_root),
            extraction_service=CandidateExtractionService(),
            ai_extraction_service=CandidateAiExtractionService(),
            mapping_service=CandidateProfileMappingService(),
            conflict_resolution_service=CandidateConflictResolutionService(),
            draft_review_service=CandidateDraftReviewService(),
            resume_assembly_service=ResumeAssemblyService(),
            resume_positioning_service=ResumePositioningService(),
            career_pathing_lite_service=CareerPathingLiteService(),
            career_pathing_full_service=CareerPathingFullService(),
            job_search_playbook_service=JobSearchPlaybookService(),
            resume_quality_gate_service=ResumeQualityGateService(),
            resume_roast_report_service=ResumeRoastReportService(),
            quality_gate_repository=quality_gate_repository,
            workspace_path=self.workspace_path,
            tx_connection=self.conn,
            vacancy_repository=self.vacancy_repository,
        )

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()

    def test_create_candidate_does_not_create_empty_companion_records(self) -> None:
        candidate = self.handlers.create_candidate(CreateCandidate(display_name="Example Candidate"))
        candidate_id = candidate["candidate_id"]
        tables = [
            "candidate_profiles",
            "candidate_external_profiles",
            "candidate_targets",
            "candidate_compensation",
            "candidate_search_preferences",
            "candidate_profile_drafts",
        ]
        for table in tables:
            count = self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            self.assertEqual(count, 0, table)
        self.assertIsNotNone(candidate_id)

    def test_repeated_source_registration_reuses_artifact_and_source_record(self) -> None:
        candidate_id = self.handlers.create_candidate(CreateCandidate(display_name="Andrei"))["candidate_id"]
        command = RegisterCandidateSource(
            candidate_id=candidate_id,
            source_kind="resume",
            source_origin="text",
            content_text="Example Candidate\ncandidate@example.com",
        )
        first = self.handlers.register_candidate_source(command)
        second = self.handlers.register_candidate_source(command)
        self.assertEqual(first["artifact_id"], second["artifact_id"])
        count = self.conn.execute("SELECT COUNT(*) FROM candidate_sources").fetchone()[0]
        self.assertEqual(count, 1)

    def test_file_source_ingestion_reads_local_text_file(self) -> None:
        candidate_id = self.handlers.create_candidate(CreateCandidate(display_name="Andrei"))["candidate_id"]
        source_path = self.root / "candidate_resume.txt"
        source_path.write_text("Example Candidate\ncandidate@example.com\nПроживает: Москва\n", encoding="utf-8")
        result = self.handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=candidate_id,
                source_kind="resume",
                source_origin="file",
                file_path=str(source_path),
            )
        )
        artifact_path = Path(result["storage_path"])
        self.assertTrue(artifact_path.exists())
        self.assertIn("candidate@example.com", artifact_path.read_text(encoding="utf-8"))

    def test_active_candidate_and_query_surface(self) -> None:
        first = self.handlers.create_candidate(CreateCandidate(display_name="First Candidate"))
        second = self.handlers.create_candidate(CreateCandidate(display_name="Second Candidate"))
        self.handlers.set_active_candidate(SetActiveCandidate(candidate_id=second["candidate_id"]))
        active = self.handlers.get_active_candidate(GetActiveCandidate())
        candidates = self.handlers.list_candidates(ListCandidates())
        self.assertEqual(active["active_candidate_id"], second["candidate_id"])
        self.assertEqual(len(candidates), 2)

    def test_multi_source_draft_preserves_conflicts_and_evidence(self) -> None:
        candidate_id = self.handlers.create_candidate(CreateCandidate(display_name="Andrei"))["candidate_id"]
        first = self.handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=candidate_id,
                source_kind="resume",
                source_origin="text",
                content_text="Example Candidate\ncandidate@example.com\nПроживает: Москва",
            )
        )
        second = self.handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=candidate_id,
                source_kind="linkedin",
                source_origin="text",
                content_text="Example Candidate\ncandidate@example.com\nLocation: Spain\nwww.linkedin.com/in/example-candidate",
            )
        )
        draft = self.handlers.generate_candidate_profile_draft(
            GenerateCandidateProfileDraftFromSources(candidate_id=candidate_id)
        )
        self.assertIn("current_location", draft["field_conflicts"])
        self.assertIn("current_location", draft["field_evidence"])
        self.assertTrue(draft["conflict_groups"])

        review = self.handlers.get_candidate_draft_review(
            GetCandidateDraftReview(candidate_id=candidate_id, draft_id=draft["draft_id"])
        )
        review_fields = {field["field"]: field for field in review["fields"]}
        self.assertEqual(review_fields["current_location"]["status"], "conflict")
        self.assertEqual(len(review_fields["current_location"]["conflicts"]), 2)
        self.assertEqual(
            {entry["source_kind"] for entry in review_fields["current_location"]["conflicts"]},
            {"resume", "linkedin"},
        )
        self.assertTrue(review["sources"])
        self.assertEqual(review["confirm_contract"]["command"], "confirm-draft")

    def test_ai_extraction_import_creates_review_draft_without_confirming_profile(self) -> None:
        candidate_id = self.handlers.create_candidate(CreateCandidate(display_name="Andrei"))["candidate_id"]
        source = self.handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=candidate_id,
                source_kind="resume",
                source_origin="text",
                content_text="Example Candidate\nCTO\n",
            )
        )
        request = self.handlers.build_candidate_ai_extraction_request(
            BuildCandidateAiExtractionRequest(candidate_id=candidate_id)
        )
        result = self.handlers.import_candidate_ai_extraction_draft(
            ImportCandidateAiExtractionDraft(
                candidate_id=candidate_id,
                response_payload={
                    "candidate_id": candidate_id,
                    "source_set_id": request["source_set_id"],
                    "draft_payload": {
                        "core_profile": {"full_name": "Example Candidate", "current_title": "CTO"},
                        "field_statuses": {"full_name": "confirmed", "current_title": "inferred"},
                        "experience_entries": [
                            {"company_name": "Example Corp", "role_title": "CTO", "source_artifact_id": source["artifact_id"]}
                        ],
                    },
                    "field_conflicts": {},
                    "field_evidence": {"core_profile.full_name": [{"artifact_id": source["artifact_id"]}]},
                    "missing_fields": ["primary_email"],
                },
            )
        )

        self.assertEqual(result["source_set_id"], request["source_set_id"])
        profile = self.handlers.get_candidate_profile(GetCandidateProfile(candidate_id=candidate_id))
        self.assertEqual(profile["core_profile"], {})

    def test_confirm_ai_draft_deduplicates_structured_records_before_persistence(self) -> None:
        candidate_id = self.handlers.create_candidate(CreateCandidate(display_name="Andrei"))["candidate_id"]
        source = self.handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=candidate_id,
                source_kind="resume",
                source_origin="text",
                content_text="Example Candidate\nCTO\nPlatform Engineering\n",
            )
        )
        request = self.handlers.build_candidate_ai_extraction_request(
            BuildCandidateAiExtractionRequest(candidate_id=candidate_id)
        )
        draft = self.handlers.import_candidate_ai_extraction_draft(
            ImportCandidateAiExtractionDraft(
                candidate_id=candidate_id,
                response_payload={
                    "candidate_id": candidate_id,
                    "source_set_id": request["source_set_id"],
                    "draft_payload": {
                        "core_profile": {"full_name": "Example Candidate"},
                        "field_statuses": {"full_name": "confirmed"},
                        "skill_signals": [
                            {
                                "skill_name": "Platform Engineering",
                                "skill_group": "engineering",
                                "context": "resume",
                                "source_artifact_id": source["artifact_id"],
                            },
                            {
                                "skill_name": "Platform Engineering",
                                "skill_group": "engineering",
                                "context": "resume",
                                "source_artifact_id": source["artifact_id"],
                            },
                        ],
                    },
                    "field_conflicts": {},
                    "field_evidence": {"core_profile.full_name": [{"artifact_id": source["artifact_id"]}]},
                    "missing_fields": [],
                },
            )
        )

        self.handlers.confirm_candidate_profile_draft(
            ConfirmCandidateProfileDraft(candidate_id=candidate_id, draft_id=draft["draft_id"])
        )

        skill_count = self.conn.execute(
            "SELECT COUNT(*) FROM candidate_skill_signals WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()[0]
        self.assertEqual(skill_count, 1)

    def test_confirm_draft_rejects_foreign_candidate(self) -> None:
        first_candidate_id = self.handlers.create_candidate(CreateCandidate(display_name="First"))["candidate_id"]
        second_candidate_id = self.handlers.create_candidate(CreateCandidate(display_name="Second"))["candidate_id"]
        self.handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=first_candidate_id,
                source_kind="resume",
                source_origin="text",
                content_text="First Candidate\nfirst@example.com\n",
            )
        )
        draft = self.handlers.generate_candidate_profile_draft(
            GenerateCandidateProfileDraftFromSources(candidate_id=first_candidate_id)
        )
        with self.assertRaises(PermissionError):
            self.handlers.confirm_candidate_profile_draft(
                ConfirmCandidateProfileDraft(candidate_id=second_candidate_id, draft_id=draft["draft_id"])
            )

    def test_existing_artifact_source_rejects_foreign_candidate_artifact(self) -> None:
        first_candidate_id = self.handlers.create_candidate(CreateCandidate(display_name="First"))["candidate_id"]
        second_candidate_id = self.handlers.create_candidate(CreateCandidate(display_name="Second"))["candidate_id"]
        source = self.handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=first_candidate_id,
                source_kind="resume",
                source_origin="text",
                content_text="First Candidate\nfirst@example.com\n",
            )
        )
        with self.assertRaises(PermissionError):
            self.handlers.register_candidate_source(
                RegisterCandidateSource(
                    candidate_id=second_candidate_id,
                    source_kind="resume",
                    source_origin="existing_artifact",
                    existing_artifact_id=source["artifact_id"],
                )
            )

    def test_source_registration_cleans_artifact_file_when_db_write_fails(self) -> None:
        candidate_id = self.handlers.create_candidate(CreateCandidate(display_name="Andrei"))["candidate_id"]

        def fail_create_artifact(**kwargs) -> None:
            raise RuntimeError("forced db failure")

        self.handlers._artifact_repository.create_artifact = fail_create_artifact
        with self.assertRaises(RuntimeError):
            self.handlers.register_candidate_source(
                RegisterCandidateSource(
                    candidate_id=candidate_id,
                    source_kind="resume",
                    source_origin="text",
                    content_text="Example Candidate\ncandidate@example.com\n",
                )
            )
        source_dir = (
            self.artifact_root
            / "candidates"
            / ArtifactPathService.candidate_folder(candidate_id, "Andrei")
            / "sources"
        )
        self.assertEqual(list(source_dir.glob("*.md")) if source_dir.exists() else [], [])

    def test_confirm_candidate_profile_draft_writes_snapshot_and_audit(self) -> None:
        candidate_id = self.handlers.create_candidate(CreateCandidate(display_name="Andrei"))["candidate_id"]
        self.handlers.set_active_candidate(SetActiveCandidate(candidate_id=candidate_id))
        self.handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=candidate_id,
                source_kind="resume",
                source_origin="text",
                content_text=(
                    "Example Candidate\ncandidate@example.com\n"
                    "www.linkedin.com/in/example-candidate\nПроживает: Москва\n"
                    "Гражданство: Россия, есть разрешение на работу: Россия\n"
                    "Русский — Родной\nАнглийский — C1\n"
                ),
            )
        )
        draft = self.handlers.generate_candidate_profile_draft(
            GenerateCandidateProfileDraftFromSources(candidate_id=candidate_id)
        )
        result = self.handlers.confirm_candidate_profile_draft(
            ConfirmCandidateProfileDraft(candidate_id=candidate_id, draft_id=draft["draft_id"])
        )
        self.assertIn("snapshot_id", result)
        profile = self.conn.execute(
            "SELECT primary_email, current_location FROM candidate_profiles WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        self.assertEqual(profile["primary_email"], "candidate@example.com")
        snapshots = self.conn.execute("SELECT COUNT(*) FROM candidate_profile_snapshots").fetchone()[0]
        audits = self.conn.execute("SELECT COUNT(*) FROM audit_events WHERE command_name = 'ConfirmCandidateProfileDraft'").fetchone()[0]
        self.assertEqual(snapshots, 1)
        self.assertEqual(audits, 1)

    def test_confirm_candidate_profile_draft_deduplicates_languages_from_multiple_sources(self) -> None:
        candidate_id = self.handlers.create_candidate(CreateCandidate(display_name="Andrei"))["candidate_id"]
        self.handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=candidate_id,
                source_kind="resume",
                source_origin="text",
                content_text="Example Candidate\ncandidate@example.com\nEnglish — C1\nРусский — Родной\n",
            )
        )
        self.handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=candidate_id,
                source_kind="linkedin",
                source_origin="text",
                content_text="Example Candidate\ncandidate@example.com\nEnglish\nRussian\n",
            )
        )
        draft = self.handlers.generate_candidate_profile_draft(
            GenerateCandidateProfileDraftFromSources(candidate_id=candidate_id)
        )

        self.handlers.confirm_candidate_profile_draft(
            ConfirmCandidateProfileDraft(candidate_id=candidate_id, draft_id=draft["draft_id"])
        )

        languages = self.conn.execute(
            """
            SELECT language_name, proficiency_level, is_primary
            FROM candidate_language_proficiencies
            WHERE candidate_id = ?
            ORDER BY language_name
            """,
            (candidate_id,),
        ).fetchall()
        self.assertEqual(len(languages), 2)
        self.assertEqual(languages[0]["language_name"], "English")
        self.assertEqual(languages[0]["proficiency_level"], "C1")
        self.assertEqual(languages[0]["is_primary"], 0)
        self.assertEqual(languages[1]["language_name"], "Russian")
        self.assertEqual(languages[1]["is_primary"], 1)

    def test_confirm_candidate_profile_draft_deduplicates_external_profiles_from_multiple_sources(self) -> None:
        candidate_id = self.handlers.create_candidate(CreateCandidate(display_name="Andrei"))["candidate_id"]
        self.handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=candidate_id,
                source_kind="resume",
                source_origin="text",
                content_text="Example Candidate\ncandidate@example.com\nwww.linkedin.com/in/example-candidate\n",
            )
        )
        self.handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=candidate_id,
                source_kind="linkedin",
                source_origin="text",
                content_text="Example Candidate\ncandidate@example.com\nhttps://www.linkedin.com/in/example-candidate/\n",
            )
        )
        draft = self.handlers.generate_candidate_profile_draft(
            GenerateCandidateProfileDraftFromSources(candidate_id=candidate_id)
        )

        self.handlers.confirm_candidate_profile_draft(
            ConfirmCandidateProfileDraft(candidate_id=candidate_id, draft_id=draft["draft_id"])
        )

        profiles = self.conn.execute(
            """
            SELECT platform, profile_url, handle_or_slug
            FROM candidate_external_profiles
            WHERE candidate_id = ?
            """,
            (candidate_id,),
        ).fetchall()
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0]["platform"], "linkedin")
        self.assertEqual(profiles[0]["profile_url"], "https://www.linkedin.com/in/example-candidate")
        self.assertEqual(profiles[0]["handle_or_slug"], "example-candidate")

    def test_confirm_candidate_profile_draft_persists_evidence_layer(self) -> None:
        candidate_id = self.handlers.create_candidate(CreateCandidate(display_name="Andrei"))["candidate_id"]
        self.handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=candidate_id,
                source_kind="resume",
                source_origin="text",
                content_text=(
                    "Example Candidate\ncandidate@example.com\n"
                    "Опыт работы\n"
                    "PARMA Technologies Group\nDeputy Head of the IT Department\n"
                    "- Improving project performance indicators through monitoring.\n"
                    "- Increasing the company's eNPS through employee motivation.\n"
                    "Education\nMoscow State University\nMaster's degree, Engineer\n"
                    "Skills\nPeople Management\nProject Management\n"
                    "Recommendations\nPARMA Technologies Group\nJohn Smith\nCTO\n"
                ),
            )
        )
        draft = self.handlers.generate_candidate_profile_draft(
            GenerateCandidateProfileDraftFromSources(candidate_id=candidate_id)
        )
        self.handlers.confirm_candidate_profile_draft(
            ConfirmCandidateProfileDraft(candidate_id=candidate_id, draft_id=draft["draft_id"])
        )
        experiences = self.conn.execute("SELECT COUNT(*) FROM candidate_experience_entries WHERE candidate_id = ?", (candidate_id,)).fetchone()[0]
        achievements = self.conn.execute("SELECT COUNT(*) FROM candidate_achievement_evidence WHERE candidate_id = ?", (candidate_id,)).fetchone()[0]
        educations = self.conn.execute("SELECT COUNT(*) FROM candidate_education_entries WHERE candidate_id = ?", (candidate_id,)).fetchone()[0]
        skills = self.conn.execute("SELECT COUNT(*) FROM candidate_skill_signals WHERE candidate_id = ?", (candidate_id,)).fetchone()[0]
        recommendations = self.conn.execute("SELECT COUNT(*) FROM candidate_recommendations WHERE candidate_id = ?", (candidate_id,)).fetchone()[0]
        self.assertGreaterEqual(experiences, 1)
        self.assertGreaterEqual(achievements, 1)
        self.assertGreaterEqual(educations, 1)
        self.assertGreaterEqual(skills, 1)
        self.assertGreaterEqual(recommendations, 1)

    def test_partial_profile_enrichment_preserves_existing_evidence(self) -> None:
        candidate_id = self.handlers.create_candidate(CreateCandidate(display_name="Andrei"))["candidate_id"]
        self.handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=candidate_id,
                source_kind="resume",
                source_origin="text",
                content_text=(
                    "Example Candidate\ncandidate@example.com\n"
                    "Опыт работы\n"
                    "Example Corp\nCTO\n"
                    "- Improved delivery speed by 40%.\n"
                ),
            )
        )
        first_draft = self.handlers.generate_candidate_profile_draft(
            GenerateCandidateProfileDraftFromSources(candidate_id=candidate_id)
        )
        self.handlers.confirm_candidate_profile_draft(
            ConfirmCandidateProfileDraft(candidate_id=candidate_id, draft_id=first_draft["draft_id"])
        )
        first_experience_count = self.conn.execute(
            "SELECT COUNT(*) FROM candidate_experience_entries WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()[0]

        cert_source = self.handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=candidate_id,
                source_kind="profile",
                source_origin="text",
                content_text="Example Candidate\ncandidate@example.com\nСертификаты\nAWS Certified Solutions Architect 2024\n",
            )
        )
        second_draft = self.handlers.generate_candidate_profile_draft(
            GenerateCandidateProfileDraftFromSources(candidate_id=candidate_id, source_artifact_ids=[cert_source["artifact_id"]])
        )
        self.handlers.confirm_candidate_profile_draft(
            ConfirmCandidateProfileDraft(candidate_id=candidate_id, draft_id=second_draft["draft_id"])
        )
        second_experience_count = self.conn.execute(
            "SELECT COUNT(*) FROM candidate_experience_entries WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()[0]
        certification_count = self.conn.execute(
            "SELECT COUNT(*) FROM candidate_certifications WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()[0]
        self.assertEqual(second_experience_count, first_experience_count)
        self.assertGreaterEqual(certification_count, 1)

    def test_hh_style_resume_extracts_targets_compensation_and_search_preferences(self) -> None:
        candidate_id = self.handlers.create_candidate(CreateCandidate(display_name="Andrei"))["candidate_id"]
        self.handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=candidate_id,
                source_kind="resume",
                source_origin="text",
                content_text=(
                    "Example Candidate\n"
                    "candidate@example.com\n"
                    "Проживает: Москва\n"
                    "Гражданство: Россия, есть разрешение на работу: Россия\n"
                    "Не готов к переезду, готов к редким командировкам\n"
                    "Желаемая должность и зарплата\n"
                    "CTO / CIO / Руководитель отдела разработки\n"
                    "350 000 руб\n"
                    "Специализации:\n"
                    "— Директор по информационным технологиям (CIO)\n"
                    "— Руководитель группы разработки\n"
                    "Тип занятости: полная занятость\n"
                    "Формат работы: на месте работодателя, удалённо\n"
                    "Желательное время в пути до работы: не более полутора часов\n"
                ),
            )
        )
        draft = self.handlers.generate_candidate_profile_draft(
            GenerateCandidateProfileDraftFromSources(candidate_id=candidate_id)
        )
        self.handlers.confirm_candidate_profile_draft(
            ConfirmCandidateProfileDraft(candidate_id=candidate_id, draft_id=draft["draft_id"])
        )
        targets = self.conn.execute(
            "SELECT target_roles_json FROM candidate_targets WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        compensation = self.conn.execute(
            "SELECT salary_target, currency FROM candidate_compensation WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        search = self.conn.execute(
            "SELECT relocation_preference, travel_preference, commute_preference, employment_type_preferences_json, work_model_preferences_json "
            "FROM candidate_search_preferences WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        self.assertIn("CTO / CIO / Руководитель отдела разработки", targets["target_roles_json"])
        self.assertEqual(compensation["salary_target"], 350000)
        self.assertEqual(compensation["currency"], "RUB")
        self.assertEqual(search["relocation_preference"], "not_ready")
        self.assertEqual(search["travel_preference"], "rare_travel_ok")
        self.assertEqual(search["commute_preference"], "up_to_90_minutes")
        self.assertIn("full_time", search["employment_type_preferences_json"])
        self.assertIn("remote", search["work_model_preferences_json"])

    def test_profile_context_persists_compensation_by_currency(self) -> None:
        candidate_id = self.handlers.create_candidate(CreateCandidate(display_name="Andrei"))["candidate_id"]
        self.handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=candidate_id,
                source_kind="profile",
                source_origin="text",
                content_text=(
                    "Example Candidate\n"
                    "andrei@example.com\n"
                    "Compensation EUR:\n- salary floor: 60000\n- salary target: 100000\n- salary aspiration: 150000\n- currency: EUR\n\n"
                    "Compensation USD:\n- salary floor: 100000\n- salary target: 150000\n- salary aspiration: 180000\n- currency: USD\n\n"
                    "Compensation RUB:\n- salary floor: 500000\n- salary target: 650000\n- salary aspiration: 800000\n- currency: RUB\n\n"
                    "Company avoid list:\n- Sberbank\n\n"
                    "Company priority list:\n- Yandex\n- Avito\n\n"
                    "Search preferences:\n- hybrid acceptable only for strong role fit\n"
                ),
            )
        )
        draft = self.handlers.generate_candidate_profile_draft(
            GenerateCandidateProfileDraftFromSources(candidate_id=candidate_id)
        )
        self.handlers.confirm_candidate_profile_draft(
            ConfirmCandidateProfileDraft(candidate_id=candidate_id, draft_id=draft["draft_id"])
        )

        profile = self.handlers.get_candidate_profile(GetCandidateProfile(candidate_id=candidate_id))
        compensation = profile["compensation"]
        self.assertEqual(compensation["currency"], "EUR")
        self.assertEqual(compensation["salary_floor"], 60000)
        self.assertEqual(compensation["compensation_by_currency"]["USD"]["salary_floor"], 100000)
        self.assertEqual(compensation["compensation_by_currency"]["RUB"]["salary_target"], 650000)
        self.assertEqual(profile["search_preferences"]["company_avoid_list"], ["Sberbank"])
        self.assertEqual(profile["search_preferences"]["company_priorities"], ["Avito", "Yandex"])
        self.assertEqual(profile["search_preferences"]["hybrid_policy"], "hybrid acceptable only for strong role fit")

    def test_generate_resume_markdown_creates_reusable_artifact(self) -> None:
        candidate_id = self.handlers.create_candidate(CreateCandidate(display_name="Andrei"))["candidate_id"]
        self.handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=candidate_id,
                source_kind="resume",
                source_origin="text",
                content_text=(
                    "Example Candidate\n"
                    "candidate@example.com\n"
                    "Проживает: Москва\n"
                    "www.linkedin.com/in/example-candidate\n"
                    "Желаемая должность и зарплата\n"
                    "CTO / CIO / Руководитель отдела разработки\n"
                    "Опыт работы\n"
                    "PARMA Technologies Group\n"
                    "Deputy Head of the IT Department\n"
                    "- Improving project performance indicators through monitoring.\n"
                    "Education\n"
                    "Moscow State University\n"
                    "Master's degree, Engineer\n"
                    "Skills\n"
                    "People Management\n"
                ),
            )
        )
        draft = self.handlers.generate_candidate_profile_draft(
            GenerateCandidateProfileDraftFromSources(candidate_id=candidate_id)
        )
        self.handlers.confirm_candidate_profile_draft(
            ConfirmCandidateProfileDraft(candidate_id=candidate_id, draft_id=draft["draft_id"])
        )
        first = self.handlers.generate_resume_markdown(
            GenerateResumeMarkdown(
                candidate_id=candidate_id,
                language="ru",
                target_role="CTO / CIO / Руководитель отдела разработки",
            )
        )
        second = self.handlers.generate_resume_markdown(
            GenerateResumeMarkdown(
                candidate_id=candidate_id,
                language="ru",
                target_role="CTO / CIO / Руководитель отдела разработки",
            )
        )
        artifact_path = Path(first["storage_path"])
        content = artifact_path.read_text(encoding="utf-8")
        self.assertTrue(artifact_path.exists())
        self.assertIn("# Example Candidate", content)
        self.assertIn("## Опыт", content)
        self.assertIn("PARMA Technologies Group", content)
        self.assertIn("## Навыки", content)
        self.assertFalse(first["reused"])
        self.assertTrue(second["reused"])
        self.assertIn(first["quality_gate"]["status"], {"pass", "warn"})
        self.assertIn(second["quality_gate"]["status"], {"pass", "warn"})
        artifacts = self.conn.execute(
            "SELECT COUNT(*) FROM artifacts WHERE candidate_id = ? AND artifact_type = 'resume_markdown'",
            (candidate_id,),
        ).fetchone()[0]
        audits = self.conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE command_name = 'GenerateResumeMarkdown'",
        ).fetchone()[0]
        gate_runs = self.conn.execute(
            "SELECT COUNT(*) FROM quality_gate_runs WHERE subject_id = ?",
            (first["artifact_id"],),
        ).fetchone()[0]
        self.assertEqual(artifacts, 1)
        self.assertEqual(audits, 1)
        self.assertEqual(gate_runs, 2)

    def test_finalize_resume_markdown_creates_final_artifact_after_explicit_warning_acceptance(self) -> None:
        candidate_id = self.handlers.create_candidate(CreateCandidate(display_name="Andrei"))["candidate_id"]
        self.handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=candidate_id,
                source_kind="resume",
                source_origin="text",
                content_text=(
                    "Example Candidate\n"
                    "andrei@example.com\n"
                    "Желаемая должность и зарплата\nCTO\n"
                    "Обо мне\nTODO\n"
                    "Опыт работы\n"
                    "Example Corp\n"
                    "CTO\n"
                    "- Improved delivery speed by 40%.\n"
                    "- Improved delivery speed by 40%.\n"
                ),
            )
        )
        draft = self.handlers.generate_candidate_profile_draft(
            GenerateCandidateProfileDraftFromSources(candidate_id=candidate_id)
        )
        self.handlers.confirm_candidate_profile_draft(
            ConfirmCandidateProfileDraft(candidate_id=candidate_id, draft_id=draft["draft_id"])
        )
        resume = self.handlers.generate_resume_markdown(
            GenerateResumeMarkdown(candidate_id=candidate_id, language="en", target_role="CTO")
        )

        with self.assertRaisesRegex(ValueError, "allow_warnings"):
            self.handlers.finalize_resume_markdown(FinalizeResumeMarkdown(artifact_id=resume["artifact_id"]))

        final = self.handlers.finalize_resume_markdown(
            FinalizeResumeMarkdown(artifact_id=resume["artifact_id"], allow_warnings=True)
        )
        repeated = self.handlers.finalize_resume_markdown(
            FinalizeResumeMarkdown(artifact_id=resume["artifact_id"], allow_warnings=True)
        )

        final_path = Path(final["storage_path"])
        self.assertTrue(final_path.exists())
        self.assertIn("/final/resume-final--cto-en--", str(final_path))
        self.assertEqual(final["artifact_type"], "resume_markdown_final")
        self.assertEqual(final["derived_from_artifact_id"], resume["artifact_id"])
        self.assertFalse(final["reused"])
        self.assertTrue(repeated["reused"])
        final_record = self.conn.execute(
            "SELECT derived_from_artifact_id FROM artifacts WHERE artifact_id = ?",
            (final["artifact_id"],),
        ).fetchone()
        self.assertEqual(final_record["derived_from_artifact_id"], resume["artifact_id"])

    def test_resume_roast_report_is_one_overwritten_artifact_per_resume_draft(self) -> None:
        candidate_id = self.handlers.create_candidate(CreateCandidate(display_name="Andrei"))["candidate_id"]
        self.handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=candidate_id,
                source_kind="resume",
                source_origin="text",
                content_text=(
                    "Example Candidate\n"
                    "andrei@example.com\n"
                    "Желаемая должность и зарплата\nCTO\n"
                    "Обо мне\nResponsible for platform work.\n"
                ),
            )
        )
        draft = self.handlers.generate_candidate_profile_draft(
            GenerateCandidateProfileDraftFromSources(candidate_id=candidate_id)
        )
        self.handlers.confirm_candidate_profile_draft(
            ConfirmCandidateProfileDraft(candidate_id=candidate_id, draft_id=draft["draft_id"])
        )
        resume = self.handlers.generate_resume_markdown(
            GenerateResumeMarkdown(candidate_id=candidate_id, language="en", target_role="CTO")
        )

        first = self.handlers.generate_resume_roast_report(
            GenerateResumeRoastReport(artifact_id=resume["artifact_id"], target_role="CTO")
        )
        second = self.handlers.generate_resume_roast_report(
            GenerateResumeRoastReport(artifact_id=resume["artifact_id"], target_role="CTO")
        )

        self.assertEqual(first["artifact_id"], second["artifact_id"])
        self.assertFalse(first["overwritten"])
        self.assertTrue(second["overwritten"])
        self.assertEqual(first["derived_from_artifact_id"], resume["artifact_id"])
        roast_path = Path(first["storage_path"])
        self.assertTrue(roast_path.exists())
        self.assertIn("/drafts/resume-roast-report--cto-for-resume-", str(roast_path))
        content = roast_path.read_text(encoding="utf-8")
        self.assertIn("Source resume artifact", content)
        self.assertIn("Future Rewrite Linkage", content)
        roast_count = self.conn.execute(
            """
            SELECT COUNT(*) FROM artifacts
            WHERE candidate_id = ? AND artifact_type = 'resume_roast_report' AND derived_from_artifact_id = ?
            """,
            (candidate_id, resume["artifact_id"]),
        ).fetchone()[0]
        self.assertEqual(roast_count, 1)

    def test_confirm_candidate_profile_draft_persists_certifications_publications_and_awards(self) -> None:
        candidate_id = self.handlers.create_candidate(CreateCandidate(display_name="Andrei"))["candidate_id"]
        self.handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=candidate_id,
                source_kind="profile",
                source_origin="text",
                content_text=(
                    "Example Candidate\n"
                    "candidate@example.com\n"
                    "Сертификаты\n"
                    "AWS Certified Solutions Architect 2024\n"
                    "Публикации\n"
                    "How to Scale Engineering Teams\n"
                    "https://example.com/scale-engineering\n"
                    "Награды\n"
                    "CTO of the Year 2023\n"
                ),
            )
        )
        draft = self.handlers.generate_candidate_profile_draft(
            GenerateCandidateProfileDraftFromSources(candidate_id=candidate_id)
        )
        self.handlers.confirm_candidate_profile_draft(
            ConfirmCandidateProfileDraft(candidate_id=candidate_id, draft_id=draft["draft_id"])
        )
        certifications = self.conn.execute(
            "SELECT COUNT(*) FROM candidate_certifications WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()[0]
        publications = self.conn.execute(
            "SELECT COUNT(*) FROM candidate_publications WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()[0]
        awards = self.conn.execute(
            "SELECT COUNT(*) FROM candidate_awards WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()[0]
        self.assertGreaterEqual(certifications, 1)
        self.assertGreaterEqual(publications, 1)
        self.assertGreaterEqual(awards, 1)

    def test_generate_resume_positioning_brief_creates_artifact(self) -> None:
        candidate_id = self.handlers.create_candidate(CreateCandidate(display_name="Andrei"))["candidate_id"]
        self.handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=candidate_id,
                source_kind="resume",
                source_origin="text",
                content_text=(
                    "Example Candidate\n"
                    "candidate@example.com\n"
                    "Обо мне\n"
                    "Technology leader focused on platform effectiveness and engineering organizations.\n"
                    "Опыт работы\n"
                    "PARMA Technologies Group\n"
                    "Deputy Head of the IT Department\n"
                    "- Improved platform cost efficiency by 40%.\n"
                    "- Led multiple engineering teams.\n"
                    "Skills\n"
                    "Platform Engineering\n"
                    "People Management\n"
                    "FinOps\n"
                ),
            )
        )
        draft = self.handlers.generate_candidate_profile_draft(
            GenerateCandidateProfileDraftFromSources(candidate_id=candidate_id)
        )
        self.handlers.confirm_candidate_profile_draft(
            ConfirmCandidateProfileDraft(candidate_id=candidate_id, draft_id=draft["draft_id"])
        )
        result = self.handlers.generate_resume_positioning_brief(
            GenerateResumePositioningBrief(
                candidate_id=candidate_id,
                target_role="CTO",
                language="en",
            )
        )
        artifact_path = Path(result["storage_path"])
        content = artifact_path.read_text(encoding="utf-8")
        self.assertTrue(artifact_path.exists())
        self.assertIn("# Resume Positioning Brief", content)
        self.assertIn("CTO", content)
        self.assertIn("Improved platform cost efficiency by 40%", content)
        self.assertIn("Skills to Emphasize", content)

    def test_generate_career_pathing_lite_creates_artifact(self) -> None:
        candidate_id = self.handlers.create_candidate(CreateCandidate(display_name="Andrei"))["candidate_id"]
        self.handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=candidate_id,
                source_kind="resume",
                source_origin="text",
                content_text=(
                    "Example Candidate\n"
                    "candidate@example.com\n"
                    "Обо мне\n"
                    "Platform engineering leader managing teams and delivery.\n"
                    "Опыт работы\n"
                    "Example Corp\n"
                    "Head of Engineering\n"
                    "- Led engineering teams and improved cloud delivery.\n"
                    "Skills\n"
                    "Platform Engineering\n"
                    "People Management\n"
                ),
            )
        )
        draft = self.handlers.generate_candidate_profile_draft(
            GenerateCandidateProfileDraftFromSources(candidate_id=candidate_id)
        )
        self.handlers.confirm_candidate_profile_draft(
            ConfirmCandidateProfileDraft(candidate_id=candidate_id, draft_id=draft["draft_id"])
        )
        result = self.handlers.generate_career_pathing_lite(
            GenerateCareerPathingLite(candidate_id=candidate_id, target_roles=["CTO", "Head of Engineering"])
        )
        artifact_path = Path(result["storage_path"])
        content = artifact_path.read_text(encoding="utf-8")
        self.assertTrue(artifact_path.exists())
        self.assertIn("# Career Pathing Lite", content)
        self.assertIn("primary_target_role", result["analysis"])
        self.assertGreaterEqual(len(result["analysis"]["roles"]), 2)

    def test_generate_career_pathing_full_uses_local_vacancy_signals_without_state_mutation(self) -> None:
        candidate_id = self.handlers.create_candidate(CreateCandidate(display_name="Andrei"))["candidate_id"]
        self.handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=candidate_id,
                source_kind="resume",
                source_origin="text",
                content_text=(
                    "Example Candidate\n"
                    "Head of Engineering\n"
                    "Summary Platform engineering leader with cloud delivery and people management experience.\n"
                    "Experience\n"
                    "Led engineering teams and improved platform delivery.\n"
                    "Skills\n"
                    "Cloud\n"
                    "Platform Engineering\n"
                ),
            )
        )
        draft = self.handlers.generate_candidate_profile_draft(
            GenerateCandidateProfileDraftFromSources(candidate_id=candidate_id)
        )
        self.handlers.confirm_candidate_profile_draft(
            ConfirmCandidateProfileDraft(candidate_id=candidate_id, draft_id=draft["draft_id"])
        )
        normalized = VacancyNormalizationService().normalize_item(
            {
                "title": "VP Engineering",
                "company_name": "ScaleOps",
                "location_text": "Remote Europe",
                "source_url": "https://example.com/jobs/vp-engineering",
                "raw_text": (
                    "VP Engineering owning strategy, board communication, budget, hiring, cloud platform, "
                    "security and delivery."
                ),
            }
        )
        with write_tx(self.conn, immediate=True):
            self.vacancy_repository.import_occurrence(candidate_id=candidate_id, source_kind="manual", normalized_item=normalized)

        result = self.handlers.generate_career_pathing_full(
            GenerateCareerPathingFull(candidate_id=candidate_id, target_roles=["VP Engineering"], include_kb=False)
        )

        artifact_path = Path(result["storage_path"])
        content = artifact_path.read_text(encoding="utf-8")
        self.assertTrue(artifact_path.exists())
        self.assertIn("# Career Pathing Full", content)
        self.assertEqual(result["analysis"]["state_mutation"], "none")
        self.assertIn("VP Engineering", result["analysis"]["role_universe"])
        self.assertIn("professional_brand_plan", result["analysis"])
        self.assertTrue(result["analysis"]["trajectory_ranking"])
        top = result["analysis"]["trajectory_ranking"][0]
        self.assertIn("capability_gaps", top)

    def test_generate_job_search_playbook_creates_saved_search_pack(self) -> None:
        candidate_id = self.handlers.create_candidate(CreateCandidate(display_name="Andrei"))["candidate_id"]
        self.handlers.update_candidate_targets(
            UpdateCandidateTargets(
                candidate_id=candidate_id,
                target_roles=["CTO", "Head of Engineering"],
                target_markets=["Europe"],
            )
        )
        self.handlers.update_candidate_compensation(
            UpdateCandidateCompensation(
                candidate_id=candidate_id,
                salary_floor=100000,
                salary_target=150000,
                currency="EUR",
            )
        )
        result = self.handlers.generate_job_search_playbook(GenerateJobSearchPlaybook(candidate_id=candidate_id))
        artifact_path = Path(result["storage_path"])
        content = artifact_path.read_text(encoding="utf-8")
        self.assertTrue(artifact_path.exists())
        self.assertIn("# Job Search Playbook", content)
        self.assertIn("Saved Search Design Pack", content)
        self.assertIn("Interview Artifacts", content)
        self.assertEqual(result["playbook"]["primary_role"], "CTO")
        self.assertTrue(result["playbook"]["saved_search_design_pack"])
        self.assertTrue(result["playbook"]["interview_artifacts"])
        self.assertEqual(result["playbook"]["compensation_framework"]["salary_floor"], 100000)
