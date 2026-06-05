from __future__ import annotations

from pathlib import Path

from job_search.application.handlers.vacancy_handlers import VacancyHandlers
from job_search.interfaces.codex.candidate_commands import build_candidate_handlers
from job_search.application.services.application_draft_service import ApplicationDraftService
from job_search.application.services.job_board_operations_service import JobBoardOperationsService
from job_search.application.services.resume_assembly_service import ResumeAssemblyService
from job_search.application.services.resume_quality_gate_service import ResumeQualityGateService
from job_search.application.services.vacancy_normalization_service import VacancyNormalizationService
from job_search.application.services.vacancy_ranking_service import VacancyRankingService
from job_search.application.services.vacancy_url_enrichment_service import VacancyUrlEnrichmentService
from job_search.application.services.vacancy_resume_service import VacancyResumeService
from job_search.config import RuntimeSettings
from job_search.infrastructure.db.connection import load_connection
from job_search.infrastructure.db.schema_version import apply_migrations
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


def build_vacancy_handlers(runtime_settings: RuntimeSettings, workspace_path: Path | None = None) -> VacancyHandlers:
    conn = load_connection(runtime_settings.db_path, runtime_settings.sqlite_config_path)
    migrations_dir = Path(__file__).resolve().parents[2] / "infrastructure" / "migrations"
    apply_migrations(conn, migrations_dir)
    candidate_handlers = build_candidate_handlers(
        runtime_settings,
        workspace_path if workspace_path is not None else Path("workspace.local.toml"),
    )
    return VacancyHandlers(
        vacancy_repository=VacancyRepository(conn),
        candidate_repository=CandidateRepository(conn),
        evidence_repository=CandidateEvidenceRepository(conn),
        artifact_repository=ArtifactRepository(conn),
        artifact_usage_repository=ArtifactUsageRepository(conn),
        audit_repository=AuditRepository(conn),
        approval_repository=ApprovalRepository(conn),
        quality_gate_repository=QualityGateRepository(conn),
        reconciliation_repository=ReconciliationRepository(conn),
        touchpoint_repository=TouchpointRepository(conn),
        interview_repository=InterviewRepository(conn),
        manual_board_action_repository=ManualBoardActionRepository(conn),
        url_enrichment_repository=VacancyUrlEnrichmentRepository(conn),
        normalization_service=VacancyNormalizationService(),
        ranking_service=VacancyRankingService(),
        url_enrichment_service=VacancyUrlEnrichmentService(),
        job_board_operations_service=JobBoardOperationsService(),
        resume_assembly_service=ResumeAssemblyService(),
        application_draft_service=ApplicationDraftService(),
        resume_quality_gate_service=ResumeQualityGateService(),
        vacancy_resume_service=VacancyResumeService(),
        artifact_root=runtime_settings.artifact_root,
        candidate_handlers=candidate_handlers,
        tx_connection=conn,
    )
