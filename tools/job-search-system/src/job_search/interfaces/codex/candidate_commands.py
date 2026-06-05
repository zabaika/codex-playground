from __future__ import annotations

from pathlib import Path

from job_search.application.commands.candidate import (
    ConfirmCandidateProfileDraft,
    CreateCandidate,
    GenerateCandidateProfileDraftFromSources,
    RegisterCandidateSource,
    SetActiveCandidate,
)
from job_search.application.handlers.candidate_handlers import CandidateHandlers
from job_search.application.services.candidate_ai_extraction_service import CandidateAiExtractionService
from job_search.application.services.candidate_conflict_resolution_service import CandidateConflictResolutionService
from job_search.application.services.candidate_draft_review_service import CandidateDraftReviewService
from job_search.application.services.candidate_extraction_service import CandidateExtractionService
from job_search.application.services.candidate_profile_mapping_service import CandidateProfileMappingService
from job_search.application.services.candidate_source_service import CandidateSourceService
from job_search.application.services.career_pathing_full_service import CareerPathingFullService
from job_search.application.services.career_pathing_lite_service import CareerPathingLiteService
from job_search.application.services.job_search_playbook_service import JobSearchPlaybookService
from job_search.application.services.kb_evidence_retrieval_service import KbEvidenceRetrievalService
from job_search.application.services.resume_assembly_service import ResumeAssemblyService
from job_search.application.services.resume_positioning_service import ResumePositioningService
from job_search.application.services.resume_quality_gate_service import ResumeQualityGateService
from job_search.application.services.resume_roast_report_service import ResumeRoastReportService
from job_search.config import RuntimeSettings
from job_search.infrastructure.db.connection import load_connection
from job_search.infrastructure.db.schema_version import apply_migrations
from job_search.infrastructure.repositories.artifact_repository import ArtifactRepository
from job_search.infrastructure.repositories.audit_repository import AuditRepository
from job_search.infrastructure.repositories.candidate_draft_repository import CandidateDraftRepository
from job_search.infrastructure.repositories.candidate_evidence_repository import CandidateEvidenceRepository
from job_search.infrastructure.repositories.candidate_repository import CandidateRepository
from job_search.infrastructure.repositories.quality_gate_repository import QualityGateRepository
from job_search.infrastructure.repositories.vacancy_repository import VacancyRepository


def build_candidate_handlers(runtime_settings: RuntimeSettings, workspace_path: Path) -> CandidateHandlers:
    conn = load_connection(runtime_settings.db_path, runtime_settings.sqlite_config_path)
    migrations_dir = Path(__file__).resolve().parents[2] / "infrastructure" / "migrations"
    apply_migrations(conn, migrations_dir)
    candidate_repository = CandidateRepository(conn)
    artifact_repository = ArtifactRepository(conn)
    draft_repository = CandidateDraftRepository(conn)
    evidence_repository = CandidateEvidenceRepository(conn)
    vacancy_repository = VacancyRepository(conn)
    audit_repository = AuditRepository(conn)
    quality_gate_repository = QualityGateRepository(conn)
    return CandidateHandlers(
        candidate_repository=candidate_repository,
        artifact_repository=artifact_repository,
        draft_repository=draft_repository,
        evidence_repository=evidence_repository,
        audit_repository=audit_repository,
        candidate_source_service=CandidateSourceService(artifact_repository, runtime_settings.artifact_root),
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
        kb_evidence_retrieval_service=KbEvidenceRetrievalService(config_path=runtime_settings.kb_index_config_path),
        vacancy_repository=vacancy_repository,
        quality_gate_repository=quality_gate_repository,
        workspace_path=workspace_path,
        tx_connection=conn,
    )
