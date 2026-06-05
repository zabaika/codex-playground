from __future__ import annotations

from dataclasses import asdict
import hashlib
from pathlib import Path
import json
import uuid

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
    UpdateCandidateCompensation,
    UpdateCandidateTargets,
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
from job_search.application.dto.candidate_profile_draft import CandidateSourceRegistrationDTO
from job_search.application.services.candidate_ai_extraction_service import CandidateAiExtractionService
from job_search.application.services.candidate_conflict_resolution_service import CandidateConflictResolutionService
from job_search.application.services.candidate_draft_review_service import CandidateDraftReviewService
from job_search.application.services.candidate_extraction_service import CandidateExtractionService
from job_search.application.services.candidate_profile_mapping_service import CandidateProfileMappingService
from job_search.application.services.candidate_source_service import CandidateSourceService
from job_search.application.services.career_pathing_lite_service import CareerPathingLiteService
from job_search.application.services.career_pathing_full_service import CareerPathingFullService
from job_search.application.services.job_search_playbook_service import JobSearchPlaybookService
from job_search.application.services.kb_evidence_retrieval_service import KbEvidenceRetrievalService
from job_search.application.services.input_validation_service import InputValidationService
from job_search.application.services.resume_assembly_service import ResumeAssemblyService
from job_search.application.services.resume_positioning_service import ResumePositioningService
from job_search.application.services.resume_quality_gate_service import ResumeQualityGateService
from job_search.application.services.resume_roast_report_service import ResumeRoastReportService
from job_search.domain.enums import ArtifactType
from job_search.config import WorkspaceSettings, save_workspace_settings
from job_search.infrastructure.db.connection import write_tx
from job_search.infrastructure.repositories.artifact_repository import ArtifactRepository
from job_search.infrastructure.repositories.audit_repository import AuditRepository
from job_search.infrastructure.repositories.candidate_draft_repository import CandidateDraftRepository
from job_search.infrastructure.repositories.candidate_evidence_repository import CandidateEvidenceRepository
from job_search.infrastructure.repositories.candidate_repository import CandidateRepository
from job_search.infrastructure.repositories.quality_gate_repository import QualityGateRepository
from job_search.infrastructure.repositories.vacancy_repository import VacancyRepository


class CandidateHandlers:
    def __init__(
        self,
        *,
        candidate_repository: CandidateRepository,
        artifact_repository: ArtifactRepository,
        draft_repository: CandidateDraftRepository,
        evidence_repository: CandidateEvidenceRepository,
        audit_repository: AuditRepository,
        candidate_source_service: CandidateSourceService,
        extraction_service: CandidateExtractionService,
        ai_extraction_service: CandidateAiExtractionService,
        mapping_service: CandidateProfileMappingService,
        conflict_resolution_service: CandidateConflictResolutionService,
        draft_review_service: CandidateDraftReviewService,
        resume_assembly_service: ResumeAssemblyService,
        resume_positioning_service: ResumePositioningService,
        career_pathing_lite_service: CareerPathingLiteService,
        job_search_playbook_service: JobSearchPlaybookService,
        resume_quality_gate_service: ResumeQualityGateService,
        resume_roast_report_service: ResumeRoastReportService,
        quality_gate_repository: QualityGateRepository,
        workspace_path: Path,
        tx_connection,
        career_pathing_full_service: CareerPathingFullService | None = None,
        kb_evidence_retrieval_service: KbEvidenceRetrievalService | None = None,
        vacancy_repository: VacancyRepository | None = None,
    ) -> None:
        self._candidate_repository = candidate_repository
        self._artifact_repository = artifact_repository
        self._draft_repository = draft_repository
        self._evidence_repository = evidence_repository
        self._audit_repository = audit_repository
        self._candidate_source_service = candidate_source_service
        self._extraction_service = extraction_service
        self._ai_extraction_service = ai_extraction_service
        self._mapping_service = mapping_service
        self._conflict_resolution_service = conflict_resolution_service
        self._draft_review_service = draft_review_service
        self._resume_assembly_service = resume_assembly_service
        self._resume_positioning_service = resume_positioning_service
        self._career_pathing_lite_service = career_pathing_lite_service
        self._career_pathing_full_service = career_pathing_full_service or CareerPathingFullService()
        self._job_search_playbook_service = job_search_playbook_service
        self._resume_quality_gate_service = resume_quality_gate_service
        self._resume_roast_report_service = resume_roast_report_service
        self._kb_evidence_retrieval_service = kb_evidence_retrieval_service or KbEvidenceRetrievalService(config_path=None)
        self._quality_gate_repository = quality_gate_repository
        self._vacancy_repository = vacancy_repository
        self._workspace_path = workspace_path
        self._conn = tx_connection

    def close(self) -> None:
        self._conn.close()

    def create_candidate(self, command: CreateCandidate) -> dict[str, object]:
        display_name = InputValidationService.required_string(command.display_name, "display_name", max_length=200)
        with write_tx(self._conn, immediate=True):
            candidate = self._candidate_repository.create_candidate(display_name)
            self._audit_repository.record_event(
                command_name=type(command).__name__,
                actor="system",
                entity_type="candidate",
                entity_id=candidate.candidate_id,
                previous_state=None,
                new_state=asdict(candidate),
            )
        return asdict(candidate)

    def set_active_candidate(self, command: SetActiveCandidate) -> dict[str, object]:
        candidate = self._candidate_repository.get_candidate(command.candidate_id)
        if candidate is None:
            raise KeyError(f"Unknown candidate_id: {command.candidate_id}")
        save_workspace_settings(self._workspace_path, WorkspaceSettings(active_candidate_id=command.candidate_id))
        return {"active_candidate_id": command.candidate_id}

    def register_candidate_source(self, command: RegisterCandidateSource) -> dict[str, object]:
        dto = CandidateSourceRegistrationDTO(**asdict(command))
        candidate = self._candidate_repository.get_candidate(dto.candidate_id)
        if candidate is None:
            raise KeyError(f"Unknown candidate_id: {dto.candidate_id}")
        materialized = self._candidate_source_service.materialize(
            dto,
            candidate_label=str(candidate.get("display_name") or ""),
        )
        try:
            with write_tx(self._conn, immediate=True):
                if self._artifact_repository.get_artifact(materialized.artifact_id) is None:
                    self._artifact_repository.create_artifact(
                        artifact_id=materialized.artifact_id,
                        artifact_type=materialized.artifact_type,
                        candidate_id=dto.candidate_id,
                        storage_path=materialized.storage_path,
                        content_hash=materialized.content_hash,
                        notes=dto.notes,
                    )
                candidate_source_id = self._artifact_repository.register_candidate_source(
                    candidate_id=dto.candidate_id,
                    artifact_id=materialized.artifact_id,
                    source_kind=dto.source_kind,
                    source_origin=dto.source_origin,
                    external_profile_id=dto.external_profile_id,
                    notes=dto.notes,
                )
                self._audit_repository.record_event(
                    command_name=type(command).__name__,
                    actor="system",
                    entity_type="candidate_source",
                    entity_id=candidate_source_id,
                    previous_state=None,
                    new_state={
                        "candidate_id": dto.candidate_id,
                        "artifact_id": materialized.artifact_id,
                        "source_kind": dto.source_kind,
                        "source_origin": dto.source_origin,
                    },
                )
        except Exception:
            if materialized.created_file:
                self._cleanup_created_file(Path(materialized.storage_path))
            raise
        return {
            "candidate_source_id": candidate_source_id,
            "artifact_id": materialized.artifact_id,
            "artifact_type": materialized.artifact_type,
            "storage_path": materialized.storage_path,
        }

    def generate_candidate_profile_draft(self, command: GenerateCandidateProfileDraftFromSources) -> dict[str, object]:
        candidate, enriched_sources = self._load_candidate_source_artifacts(
            command.candidate_id,
            source_artifact_ids=command.source_artifact_ids,
        )
        draft = self._extraction_service.build_draft(candidate_id=command.candidate_id, sources=enriched_sources)
        draft_artifact_id = str(uuid.uuid4())
        draft_text = json.dumps(draft.draft_payload, ensure_ascii=False, indent=2)
        draft_path = self._candidate_source_service.artifact_storage_path(
            command.candidate_id,
            draft_artifact_id,
            "candidate_profile_draft",
            candidate_label=str(candidate.get("display_name") or ""),
            artifact_label="deterministic-profile-draft",
        )
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text(draft_text, encoding="utf-8")
        try:
            with write_tx(self._conn, immediate=True):
                self._artifact_repository.create_artifact(
                    artifact_id=draft_artifact_id,
                    artifact_type="candidate_profile_draft",
                    candidate_id=command.candidate_id,
                    storage_path=str(draft_path),
                    content_hash=hashlib.sha256(draft_text.encode("utf-8")).hexdigest(),
                )
                draft_id = self._draft_repository.save_draft(command.candidate_id, draft_artifact_id, draft)
                self._audit_repository.record_event(
                    command_name=type(command).__name__,
                    actor="system",
                    entity_type="candidate_profile_draft",
                    entity_id=draft_id,
                    previous_state=None,
                    new_state={
                        "candidate_id": command.candidate_id,
                        "draft_artifact_id": draft_artifact_id,
                        "source_set_id": draft.source_set_id,
                    },
                )
        except Exception:
            self._cleanup_created_file(draft_path)
            raise
        return {
            "draft_id": draft_id,
            "candidate_id": command.candidate_id,
            "source_set_id": draft.source_set_id,
            "field_conflicts": draft.field_conflicts,
            "field_evidence": draft.field_evidence,
            "missing_fields": draft.missing_fields,
            "conflict_groups": self._conflict_resolution_service.group_conflicts(draft.field_conflicts),
        }

    def build_candidate_ai_extraction_request(self, command: BuildCandidateAiExtractionRequest) -> dict[str, object]:
        _, enriched_sources = self._load_candidate_source_artifacts(
            command.candidate_id,
            source_artifact_ids=command.source_artifact_ids,
        )
        return self._ai_extraction_service.build_request(candidate_id=command.candidate_id, sources=enriched_sources)

    def import_candidate_ai_extraction_draft(self, command: ImportCandidateAiExtractionDraft) -> dict[str, object]:
        candidate, enriched_sources = self._load_candidate_source_artifacts(
            command.candidate_id,
            source_artifact_ids=command.source_artifact_ids,
        )
        draft = self._ai_extraction_service.validate_response(
            candidate_id=command.candidate_id,
            expected_source_set_id=self._ai_extraction_service.source_set_id(enriched_sources),
            allowed_source_artifact_ids={source["artifact_id"] for source in enriched_sources},
            response_payload=command.response_payload,
        )
        draft_artifact_id = str(uuid.uuid4())
        draft_text = json.dumps(draft.draft_payload, ensure_ascii=False, indent=2)
        draft_path = self._candidate_source_service.artifact_storage_path(
            command.candidate_id,
            draft_artifact_id,
            "candidate_profile_draft",
            candidate_label=str(candidate.get("display_name") or ""),
            artifact_label="ai-profile-draft",
        )
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text(draft_text, encoding="utf-8")
        try:
            with write_tx(self._conn, immediate=True):
                self._artifact_repository.create_artifact(
                    artifact_id=draft_artifact_id,
                    artifact_type="candidate_profile_draft",
                    candidate_id=command.candidate_id,
                    storage_path=str(draft_path),
                    content_hash=hashlib.sha256(draft_text.encode("utf-8")).hexdigest(),
                    notes=json.dumps({"source": "ai_extraction"}, ensure_ascii=False),
                )
                draft_id = self._draft_repository.save_draft(command.candidate_id, draft_artifact_id, draft)
                self._audit_repository.record_event(
                    command_name=type(command).__name__,
                    actor="system",
                    entity_type="candidate_profile_draft",
                    entity_id=draft_id,
                    previous_state=None,
                    new_state={
                        "candidate_id": command.candidate_id,
                        "draft_artifact_id": draft_artifact_id,
                        "source_set_id": draft.source_set_id,
                        "field_conflict_count": len(draft.field_conflicts),
                        "source": "ai_extraction",
                    },
                )
        except Exception:
            self._cleanup_created_file(draft_path)
            raise
        return {
            "draft_id": draft_id,
            "candidate_id": command.candidate_id,
            "source_set_id": draft.source_set_id,
            "field_conflicts": draft.field_conflicts,
            "field_evidence": draft.field_evidence,
            "missing_fields": draft.missing_fields,
            "conflict_groups": self._conflict_resolution_service.group_conflicts(draft.field_conflicts),
        }

    def confirm_candidate_profile_draft(self, command: ConfirmCandidateProfileDraft) -> dict[str, object]:
        draft = self._draft_repository.get_draft(command.draft_id)
        if draft is None:
            raise KeyError(f"Unknown draft_id: {command.draft_id}")
        if str(draft["candidate_id"]) != command.candidate_id:
            raise PermissionError("draft_id does not belong to the requested candidate")
        previous = self._candidate_repository.get_candidate_profile_view(command.candidate_id)
        resolved = self._mapping_service.resolve_confirmed_payload(
            draft_payload=draft["draft_payload"],
            conflicts=draft["field_conflicts"],
            accepted_field_values=command.accepted_field_values,
        )
        previous_evidence = self._evidence_repository.get_resume_evidence(command.candidate_id)
        resolved = self._merge_with_existing_profile(previous=asdict(previous) if previous else None, previous_evidence=previous_evidence, resolved=resolved)
        with write_tx(self._conn, immediate=True):
            self._candidate_repository.upsert_core_profile(command.candidate_id, resolved["core_profile"])
            self._candidate_repository.replace_external_profiles(command.candidate_id, resolved["external_profiles"])
            self._candidate_repository.replace_work_authorizations(command.candidate_id, resolved["work_authorizations"])
            self._candidate_repository.replace_languages(command.candidate_id, resolved["languages"])
            self._candidate_repository.upsert_targets(command.candidate_id, resolved["targets"])
            self._candidate_repository.upsert_compensation(command.candidate_id, resolved["compensation"])
            self._candidate_repository.upsert_platform_preferences(command.candidate_id, resolved["platform_preferences"])
            self._candidate_repository.upsert_search_preferences(command.candidate_id, resolved["search_preferences"])
            experience_map = self._evidence_repository.replace_experience_entries(
                command.candidate_id, resolved["experience_entries"]
            )
            self._evidence_repository.replace_achievement_evidence(
                command.candidate_id, resolved["achievement_evidence"], experience_map
            )
            self._evidence_repository.replace_education_entries(command.candidate_id, resolved["education_entries"])
            self._evidence_repository.replace_skill_signals(command.candidate_id, resolved["skill_signals"])
            self._evidence_repository.replace_recommendations(command.candidate_id, resolved["recommendations"])
            self._evidence_repository.replace_certifications(command.candidate_id, resolved["certifications"])
            self._evidence_repository.replace_publications(command.candidate_id, resolved["publications"])
            self._evidence_repository.replace_awards(command.candidate_id, resolved["awards"])
            snapshot_id = self._draft_repository.create_snapshot(command.candidate_id, command.draft_id, resolved)
            current = self._candidate_repository.get_candidate_profile_view(command.candidate_id)
            self._audit_repository.record_event(
                command_name=type(command).__name__,
                actor="system",
                entity_type="candidate",
                entity_id=command.candidate_id,
                previous_state=asdict(previous) if previous is not None else None,
                new_state=asdict(current) if current is not None else resolved,
            )
        return {"candidate_id": command.candidate_id, "snapshot_id": snapshot_id}

    def update_candidate_targets(self, command: UpdateCandidateTargets) -> None:
        previous = self._candidate_repository.get_candidate_profile_view(command.candidate_id)
        if previous is None:
            raise KeyError(f"Unknown candidate_id: {command.candidate_id}")
        with write_tx(self._conn, immediate=True):
            self._candidate_repository.upsert_targets(
                command.candidate_id,
                {"target_roles": command.target_roles, "target_markets": command.target_markets},
            )
            current = self._candidate_repository.get_candidate_profile_view(command.candidate_id)
            self._audit_repository.record_event(
                command_name=type(command).__name__,
                actor="system",
                entity_type="candidate_targets",
                entity_id=command.candidate_id,
                previous_state=asdict(previous),
                new_state=asdict(current) if current is not None else None,
            )

    def update_candidate_compensation(self, command: UpdateCandidateCompensation) -> None:
        previous = self._candidate_repository.get_candidate_profile_view(command.candidate_id)
        if previous is None:
            raise KeyError(f"Unknown candidate_id: {command.candidate_id}")
        with write_tx(self._conn, immediate=True):
            self._candidate_repository.upsert_compensation(command.candidate_id, asdict(command))
            current = self._candidate_repository.get_candidate_profile_view(command.candidate_id)
            self._audit_repository.record_event(
                command_name=type(command).__name__,
                actor="system",
                entity_type="candidate_compensation",
                entity_id=command.candidate_id,
                previous_state=asdict(previous),
                new_state=asdict(current) if current is not None else None,
            )

    def generate_resume_markdown(self, command: GenerateResumeMarkdown) -> dict[str, object]:
        profile = self._candidate_repository.get_candidate_profile_view(command.candidate_id)
        if profile is None:
            raise KeyError(f"Unknown candidate_id: {command.candidate_id}")
        evidence = self._evidence_repository.get_resume_evidence(command.candidate_id)
        markdown = self._resume_assembly_service.assemble_markdown(
            profile=asdict(profile),
            evidence=evidence,
            language=command.language,
            target_role=command.target_role,
        )
        content_hash = str(uuid.uuid5(uuid.NAMESPACE_URL, markdown))
        existing = self._artifact_repository.find_reusable_artifact(
            candidate_id=command.candidate_id,
            artifact_type=ArtifactType.RESUME_MARKDOWN.value,
            content_hash=content_hash,
        )
        if existing is not None:
            gate_result = self.run_resume_quality_gate(RunResumeQualityGate(artifact_id=str(existing["artifact_id"])))
            return {
                "artifact_id": str(existing["artifact_id"]),
                "artifact_type": str(existing["artifact_type"]),
                "storage_path": str(existing["storage_path"]),
                "reused": True,
                "quality_gate": gate_result,
            }

        artifact_id = str(uuid.uuid4())
        storage_path = self._candidate_source_service.artifact_storage_path(
            command.candidate_id,
            artifact_id,
            ArtifactType.RESUME_MARKDOWN.value,
            candidate_label=self._candidate_label_from_profile(asdict(profile)),
            artifact_label=f"{command.target_role or 'target-role'}-{command.language}",
        )
        storage_path.parent.mkdir(parents=True, exist_ok=True)
        storage_path.write_text(markdown, encoding="utf-8")
        artifact_state = {
            "artifact_type": ArtifactType.RESUME_MARKDOWN.value,
            "language": command.language,
            "target_role": command.target_role,
            "storage_path": str(storage_path),
        }
        try:
            with write_tx(self._conn, immediate=True):
                self._artifact_repository.create_artifact(
                    artifact_id=artifact_id,
                    artifact_type=ArtifactType.RESUME_MARKDOWN.value,
                    candidate_id=command.candidate_id,
                    storage_path=str(storage_path),
                    content_hash=content_hash,
                    notes=json.dumps(
                        {"language": command.language, "target_role": command.target_role},
                        ensure_ascii=False,
                    ),
                )
                self._audit_repository.record_event(
                    command_name=type(command).__name__,
                    actor="system",
                    entity_type="artifact",
                    entity_id=artifact_id,
                    previous_state=None,
                    new_state=artifact_state,
                )
        except Exception:
            self._cleanup_created_file(storage_path)
            raise
        gate_result = self.run_resume_quality_gate(RunResumeQualityGate(artifact_id=artifact_id))
        return {
            "artifact_id": artifact_id,
            "artifact_type": ArtifactType.RESUME_MARKDOWN.value,
            "storage_path": str(storage_path),
            "reused": False,
            "quality_gate": gate_result,
        }

    def run_resume_quality_gate(self, command: RunResumeQualityGate) -> dict[str, object]:
        artifact = self._artifact_repository.get_artifact(command.artifact_id)
        if artifact is None:
            raise KeyError(f"Unknown artifact_id: {command.artifact_id}")
        markdown = Path(str(artifact["storage_path"])).read_text(encoding="utf-8")
        candidate_id = str(artifact["candidate_id"]) if artifact.get("candidate_id") else None
        candidate_profile = None
        if candidate_id:
            profile = self._candidate_repository.get_candidate_profile_view(candidate_id)
            candidate_profile = asdict(profile) if profile is not None else None
        gate_result = self._resume_quality_gate_service.check_markdown(
            markdown=markdown,
            candidate_profile=candidate_profile,
        )
        with write_tx(self._conn, immediate=True):
            quality_gate_run_id = self._quality_gate_repository.record_run(
                gate_name="resume_markdown_quality_gate",
                subject_type="artifact",
                subject_id=command.artifact_id,
                candidate_id=candidate_id,
                status=str(gate_result["status"]),
                issues=list(gate_result["issues"]),
            )
        return {
            "quality_gate_run_id": quality_gate_run_id,
            "status": gate_result["status"],
            "issues": gate_result["issues"],
        }

    def finalize_resume_markdown(self, command: FinalizeResumeMarkdown) -> dict[str, object]:
        source_artifact = self._artifact_repository.get_artifact(command.artifact_id)
        if source_artifact is None:
            raise KeyError(f"Unknown artifact_id: {command.artifact_id}")
        if str(source_artifact["artifact_type"]) != ArtifactType.RESUME_MARKDOWN.value:
            raise ValueError("Only resume_markdown artifacts can be finalized")
        candidate_id = str(source_artifact["candidate_id"])
        source_quality_gate = self.run_resume_quality_gate(RunResumeQualityGate(artifact_id=command.artifact_id))
        if source_quality_gate["status"] == "fail":
            raise ValueError("Cannot finalize resume artifact with failing quality gate")
        if source_quality_gate["status"] == "warn" and not command.allow_warnings:
            raise ValueError("Cannot finalize resume artifact with warnings unless allow_warnings is true")

        markdown = Path(str(source_artifact["storage_path"])).read_text(encoding="utf-8")
        content_hash = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
        existing = self._artifact_repository.find_derived_artifact(
            candidate_id=candidate_id,
            artifact_type=ArtifactType.RESUME_MARKDOWN_FINAL.value,
            derived_from_artifact_id=command.artifact_id,
            content_hash=content_hash,
        )
        if existing is not None:
            final_quality_gate = self.run_resume_quality_gate(RunResumeQualityGate(artifact_id=str(existing["artifact_id"])))
            return {
                "artifact_id": str(existing["artifact_id"]),
                "artifact_type": str(existing["artifact_type"]),
                "storage_path": str(existing["storage_path"]),
                "derived_from_artifact_id": command.artifact_id,
                "reused": True,
                "quality_gate": final_quality_gate,
            }

        notes = self._artifact_notes(source_artifact)
        artifact_id = str(uuid.uuid4())
        storage_path = self._candidate_source_service.artifact_storage_path(
            candidate_id,
            artifact_id,
            ArtifactType.RESUME_MARKDOWN_FINAL.value,
            candidate_label=self._candidate_label(candidate_id),
            artifact_label=self._final_resume_label(notes),
        )
        storage_path.parent.mkdir(parents=True, exist_ok=True)
        storage_path.write_text(markdown, encoding="utf-8")
        try:
            with write_tx(self._conn, immediate=True):
                self._artifact_repository.create_artifact(
                    artifact_id=artifact_id,
                    artifact_type=ArtifactType.RESUME_MARKDOWN_FINAL.value,
                    candidate_id=candidate_id,
                    storage_path=str(storage_path),
                    content_hash=content_hash,
                    notes=json.dumps(
                        {
                            **notes,
                            "finalized_from_artifact_id": command.artifact_id,
                            "allow_warnings": command.allow_warnings,
                        },
                        ensure_ascii=False,
                    ),
                    derived_from_artifact_id=command.artifact_id,
                )
                self._audit_repository.record_event(
                    command_name=type(command).__name__,
                    actor="system",
                    entity_type="artifact",
                    entity_id=artifact_id,
                    previous_state=source_artifact,
                    new_state={
                        "artifact_type": ArtifactType.RESUME_MARKDOWN_FINAL.value,
                        "storage_path": str(storage_path),
                        "derived_from_artifact_id": command.artifact_id,
                        "quality_gate_status": source_quality_gate["status"],
                    },
                )
        except Exception:
            self._cleanup_created_file(storage_path)
            raise
        final_quality_gate = self.run_resume_quality_gate(RunResumeQualityGate(artifact_id=artifact_id))
        return {
            "artifact_id": artifact_id,
            "artifact_type": ArtifactType.RESUME_MARKDOWN_FINAL.value,
            "storage_path": str(storage_path),
            "derived_from_artifact_id": command.artifact_id,
            "reused": False,
            "quality_gate": final_quality_gate,
        }

    def generate_resume_roast_report(self, command: GenerateResumeRoastReport) -> dict[str, object]:
        source_artifact = self._artifact_repository.get_artifact(command.artifact_id)
        if source_artifact is None:
            raise KeyError(f"Unknown artifact_id: {command.artifact_id}")
        if str(source_artifact["artifact_type"]) != ArtifactType.RESUME_MARKDOWN.value:
            raise ValueError("Only resume_markdown artifacts can be roasted")
        candidate_id = str(source_artifact["candidate_id"])
        notes = self._artifact_notes(source_artifact)
        target_role = command.target_role or str(notes.get("target_role") or "").strip() or None
        markdown = Path(str(source_artifact["storage_path"])).read_text(encoding="utf-8")
        quality_gate = self.run_resume_quality_gate(RunResumeQualityGate(artifact_id=command.artifact_id))
        report_markdown = self._resume_roast_report_service.build_report(
            resume_artifact_id=command.artifact_id,
            resume_storage_path=str(source_artifact["storage_path"]),
            markdown=markdown,
            target_role=target_role,
            quality_gate=quality_gate,
        )
        content_hash = hashlib.sha256(report_markdown.encode("utf-8")).hexdigest()
        existing = self._artifact_repository.get_derived_artifact(
            candidate_id=candidate_id,
            artifact_type=ArtifactType.RESUME_ROAST_REPORT.value,
            derived_from_artifact_id=command.artifact_id,
        )
        report_notes = {
            "target_role": target_role,
            "source_resume_artifact_id": command.artifact_id,
            "future_resume_derivation": "new resume artifacts created from this roast should derive_from this report artifact",
        }
        if existing is not None:
            storage_path = Path(str(existing["storage_path"]))
            previous_state = dict(existing)
            storage_path.parent.mkdir(parents=True, exist_ok=True)
            storage_path.write_text(report_markdown, encoding="utf-8")
            with write_tx(self._conn, immediate=True):
                self._artifact_repository.update_artifact_content(
                    artifact_id=str(existing["artifact_id"]),
                    storage_path=str(storage_path),
                    content_hash=content_hash,
                    notes=json.dumps(report_notes, ensure_ascii=False),
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
            return {
                "artifact_id": str(existing["artifact_id"]),
                "artifact_type": str(existing["artifact_type"]),
                "storage_path": str(storage_path),
                "derived_from_artifact_id": command.artifact_id,
                "overwritten": True,
                "quality_gate": quality_gate,
            }

        artifact_id = str(uuid.uuid4())
        storage_path = self._candidate_source_service.artifact_storage_path(
            candidate_id,
            artifact_id,
            ArtifactType.RESUME_ROAST_REPORT.value,
            candidate_label=self._candidate_label(candidate_id),
            artifact_label=self._roast_report_label(command.artifact_id, target_role),
        )
        storage_path.parent.mkdir(parents=True, exist_ok=True)
        storage_path.write_text(report_markdown, encoding="utf-8")
        try:
            with write_tx(self._conn, immediate=True):
                self._artifact_repository.create_artifact(
                    artifact_id=artifact_id,
                    artifact_type=ArtifactType.RESUME_ROAST_REPORT.value,
                    candidate_id=candidate_id,
                    storage_path=str(storage_path),
                    content_hash=content_hash,
                    notes=json.dumps(report_notes, ensure_ascii=False),
                    derived_from_artifact_id=command.artifact_id,
                )
                self._audit_repository.record_event(
                    command_name=type(command).__name__,
                    actor="system",
                    entity_type="artifact",
                    entity_id=artifact_id,
                    previous_state=None,
                    new_state={
                        "artifact_type": ArtifactType.RESUME_ROAST_REPORT.value,
                        "storage_path": str(storage_path),
                        "derived_from_artifact_id": command.artifact_id,
                    },
                )
        except Exception:
            self._cleanup_created_file(storage_path)
            raise
        return {
            "artifact_id": artifact_id,
            "artifact_type": ArtifactType.RESUME_ROAST_REPORT.value,
            "storage_path": str(storage_path),
            "derived_from_artifact_id": command.artifact_id,
            "overwritten": False,
            "quality_gate": quality_gate,
        }

    def generate_resume_positioning_brief(self, command: GenerateResumePositioningBrief) -> dict[str, object]:
        profile = self._candidate_repository.get_candidate_profile_view(command.candidate_id)
        if profile is None:
            raise KeyError(f"Unknown candidate_id: {command.candidate_id}")
        evidence = self._evidence_repository.get_resume_evidence(command.candidate_id)
        markdown = self._resume_positioning_service.build_positioning_brief(
            profile=asdict(profile),
            evidence=evidence,
            target_role=command.target_role,
            language=command.language,
        )
        content_hash = str(uuid.uuid5(uuid.NAMESPACE_URL, markdown))
        existing = self._artifact_repository.find_reusable_artifact(
            candidate_id=command.candidate_id,
            artifact_type="resume_positioning_brief",
            content_hash=content_hash,
        )
        if existing is not None:
            return {
                "artifact_id": str(existing["artifact_id"]),
                "artifact_type": str(existing["artifact_type"]),
                "storage_path": str(existing["storage_path"]),
                "reused": True,
            }

        artifact_id = str(uuid.uuid4())
        storage_path = self._candidate_source_service.artifact_storage_path(
            command.candidate_id,
            artifact_id,
            "resume_positioning_brief",
            candidate_label=self._candidate_label_from_profile(asdict(profile)),
            artifact_label=f"{command.target_role}-{command.language}",
        )
        storage_path.parent.mkdir(parents=True, exist_ok=True)
        storage_path.write_text(markdown, encoding="utf-8")
        try:
            with write_tx(self._conn, immediate=True):
                self._artifact_repository.create_artifact(
                    artifact_id=artifact_id,
                    artifact_type="resume_positioning_brief",
                    candidate_id=command.candidate_id,
                    storage_path=str(storage_path),
                    content_hash=content_hash,
                    notes=json.dumps(
                        {"language": command.language, "target_role": command.target_role},
                        ensure_ascii=False,
                    ),
                )
                self._audit_repository.record_event(
                    command_name=type(command).__name__,
                    actor="system",
                    entity_type="artifact",
                    entity_id=artifact_id,
                    previous_state=None,
                    new_state={
                        "artifact_type": "resume_positioning_brief",
                        "language": command.language,
                        "target_role": command.target_role,
                        "storage_path": str(storage_path),
                    },
                )
        except Exception:
            self._cleanup_created_file(storage_path)
            raise
        return {
            "artifact_id": artifact_id,
            "artifact_type": "resume_positioning_brief",
            "storage_path": str(storage_path),
            "reused": False,
        }

    def generate_career_pathing_lite(self, command: GenerateCareerPathingLite) -> dict[str, object]:
        profile = self._candidate_repository.get_candidate_profile_view(command.candidate_id)
        if profile is None:
            raise KeyError(f"Unknown candidate_id: {command.candidate_id}")
        evidence = self._evidence_repository.get_resume_evidence(command.candidate_id)
        analysis = self._career_pathing_lite_service.analyze(
            profile=asdict(profile),
            evidence=evidence,
            target_roles=command.target_roles,
        )
        markdown = self._career_pathing_lite_service.render_markdown(analysis)
        artifact = self._create_markdown_artifact(
            candidate_id=command.candidate_id,
            artifact_type="career_pathing_lite",
            markdown=markdown,
            command_name=type(command).__name__,
            notes={"target_roles": command.target_roles or []},
            artifact_label="-".join(command.target_roles or []) or "target-roles",
        )
        return {**artifact, "analysis": analysis}

    def generate_career_pathing_full(self, command: GenerateCareerPathingFull) -> dict[str, object]:
        profile = self._candidate_repository.get_candidate_profile_view(command.candidate_id)
        if profile is None:
            raise KeyError(f"Unknown candidate_id: {command.candidate_id}")
        evidence = self._evidence_repository.get_resume_evidence(command.candidate_id)
        lite_analysis = self._career_pathing_lite_service.analyze(
            profile=asdict(profile),
            evidence=evidence,
            target_roles=command.target_roles,
        )
        vacancies = (
            self._vacancy_repository.list_vacancy_ranking_inputs(candidate_id=command.candidate_id, processed=None)
            if self._vacancy_repository is not None
            else []
        )
        kb_context = None
        if command.include_kb:
            kb_context = self._kb_evidence_retrieval_service.search(
                candidate_profile=asdict(profile),
                evidence=evidence,
                target_role=str(lite_analysis.get("primary_target_role") or ""),
                query="career pathing capability gap professional brand",
                limit=3,
            )
        analysis = self._career_pathing_full_service.analyze(
            profile=asdict(profile),
            evidence=evidence,
            vacancies=vacancies,
            lite_analysis=lite_analysis,
            kb_context=kb_context,
            target_roles=command.target_roles,
        )
        markdown = self._career_pathing_full_service.render_markdown(analysis)
        artifact = self._create_markdown_artifact(
            candidate_id=command.candidate_id,
            artifact_type="career_pathing_full",
            markdown=markdown,
            command_name=type(command).__name__,
            notes={
                "target_roles": command.target_roles or [],
                "include_kb": command.include_kb,
                "state_mutation": "none",
            },
            artifact_label="-".join(command.target_roles or []) or "trajectory-analysis",
        )
        return {**artifact, "analysis": analysis}

    def generate_job_search_playbook(self, command: GenerateJobSearchPlaybook) -> dict[str, object]:
        profile = self._candidate_repository.get_candidate_profile_view(command.candidate_id)
        if profile is None:
            raise KeyError(f"Unknown candidate_id: {command.candidate_id}")
        evidence = self._evidence_repository.get_resume_evidence(command.candidate_id)
        career_analysis = self._career_pathing_lite_service.analyze(profile=asdict(profile), evidence=evidence)
        playbook = self._job_search_playbook_service.build(
            profile=asdict(profile),
            career_analysis=career_analysis,
        )
        markdown = self._job_search_playbook_service.render_markdown(playbook)
        artifact = self._create_markdown_artifact(
            candidate_id=command.candidate_id,
            artifact_type="job_search_playbook",
            markdown=markdown,
            command_name=type(command).__name__,
            notes={"primary_role": playbook["primary_role"]},
            artifact_label=str(playbook["primary_role"]),
        )
        return {**artifact, "playbook": playbook}

    def get_active_candidate(self, query: GetActiveCandidate) -> dict[str, object]:
        if not self._workspace_path.exists():
            return {"active_candidate_id": None}
        from job_search.config import load_workspace_settings

        settings = load_workspace_settings(self._workspace_path)
        return {"active_candidate_id": settings.active_candidate_id or None}

    def list_candidates(self, query: ListCandidates) -> list[dict[str, object]]:
        return self._candidate_repository.list_candidates()

    def get_candidate_profile(self, query: GetCandidateProfile) -> dict[str, object] | None:
        profile = self._candidate_repository.get_candidate_profile_view(query.candidate_id)
        if profile is None:
            return None
        return asdict(profile)

    def get_candidate_sources(self, query: GetCandidateSources) -> list[dict[str, object]]:
        return self._artifact_repository.list_candidate_sources(query.candidate_id)

    def get_latest_candidate_draft(self, query: GetLatestCandidateDraft) -> dict[str, object] | None:
        return self._draft_repository.get_latest_draft(query.candidate_id)

    def get_candidate_draft_review(self, query: GetCandidateDraftReview) -> dict[str, object]:
        draft = (
            self._draft_repository.get_draft(query.draft_id)
            if query.draft_id
            else self._draft_repository.get_latest_draft(query.candidate_id)
        )
        if draft is None:
            raise KeyError(f"Unknown draft_id: {query.draft_id}" if query.draft_id else "No candidate profile draft found")
        if str(draft["candidate_id"]) != query.candidate_id:
            raise PermissionError("draft_id does not belong to the requested candidate")
        sources = self._artifact_repository.list_candidate_sources(query.candidate_id)
        return self._draft_review_service.build_review(
            draft=draft,
            sources=sources,
            conflict_groups=self._conflict_resolution_service.group_conflicts(dict(draft["field_conflicts"])),
        )

    def get_candidate_external_profiles(self, query: GetCandidateExternalProfiles) -> list[dict[str, object]]:
        return self._candidate_repository.get_external_profiles(query.candidate_id)

    def search_resume_kb_evidence(self, query: SearchResumeKbEvidence) -> dict[str, object]:
        profile = self._candidate_repository.get_candidate_profile_view(query.candidate_id)
        if profile is None:
            raise KeyError(f"Unknown candidate_id: {query.candidate_id}")
        evidence = self._evidence_repository.get_resume_evidence(query.candidate_id)
        return self._kb_evidence_retrieval_service.search(
            candidate_profile=asdict(profile),
            evidence=evidence,
            target_role=query.target_role,
            query=query.query,
            limit=query.limit,
        )

    def _load_candidate_source_artifacts(
        self,
        candidate_id: str,
        *,
        source_artifact_ids: list[str] | None,
    ) -> tuple[dict[str, object], list[dict[str, str]]]:
        candidate = self._candidate_repository.get_candidate(candidate_id)
        if candidate is None:
            raise KeyError(f"Unknown candidate_id: {candidate_id}")
        sources = self._artifact_repository.list_candidate_sources(candidate_id)
        if not sources:
            raise ValueError("No candidate sources registered")
        requested = set(source_artifact_ids or [])
        selected = [
            source
            for source in sources
            if not requested or str(source["artifact_id"]) in requested
        ]
        if not selected:
            raise ValueError("No candidate sources matched the requested artifact set")
        return candidate, [
            {
                "artifact_id": str(source["artifact_id"]),
                "artifact_type": str(source["artifact_type"]),
                "source_kind": str(source["source_kind"]),
                "content_text": Path(str(source["storage_path"])).read_text(encoding="utf-8"),
            }
            for source in selected
        ]

    def _create_markdown_artifact(
        self,
        *,
        candidate_id: str,
        artifact_type: str,
        markdown: str,
        command_name: str,
        notes: dict[str, object] | None = None,
        artifact_label: str | None = None,
    ) -> dict[str, object]:
        content_hash = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
        existing = self._artifact_repository.find_reusable_artifact(
            candidate_id=candidate_id,
            artifact_type=artifact_type,
            content_hash=content_hash,
        )
        if existing is not None:
            return {
                "artifact_id": str(existing["artifact_id"]),
                "artifact_type": str(existing["artifact_type"]),
                "storage_path": str(existing["storage_path"]),
                "reused": True,
            }

        artifact_id = str(uuid.uuid4())
        storage_path = self._candidate_source_service.artifact_storage_path(
            candidate_id,
            artifact_id,
            artifact_type,
            candidate_label=self._candidate_label(candidate_id),
            artifact_label=artifact_label,
        )
        storage_path.parent.mkdir(parents=True, exist_ok=True)
        storage_path.write_text(markdown, encoding="utf-8")
        try:
            with write_tx(self._conn, immediate=True):
                self._artifact_repository.create_artifact(
                    artifact_id=artifact_id,
                    artifact_type=artifact_type,
                    candidate_id=candidate_id,
                    storage_path=str(storage_path),
                    content_hash=content_hash,
                    notes=json.dumps(notes or {}, ensure_ascii=False),
                )
                self._audit_repository.record_event(
                    command_name=command_name,
                    actor="system",
                    entity_type="artifact",
                    entity_id=artifact_id,
                    previous_state=None,
                    new_state={
                        "artifact_type": artifact_type,
                        "storage_path": str(storage_path),
                        "notes": notes or {},
                    },
                )
        except Exception:
            self._cleanup_created_file(storage_path)
            raise
        return {
            "artifact_id": artifact_id,
            "artifact_type": artifact_type,
            "storage_path": str(storage_path),
            "reused": False,
        }

    def _merge_with_existing_profile(
        self,
        *,
        previous: dict[str, object] | None,
        previous_evidence: dict[str, list[dict[str, object]]],
        resolved: dict[str, object],
    ) -> dict[str, object]:
        if previous is None:
            return resolved
        merged = dict(resolved)
        merged["core_profile"] = self._merge_dict(previous.get("core_profile", {}), resolved.get("core_profile", {}))
        for key in ("targets", "compensation", "platform_preferences", "search_preferences"):
            merged[key] = self._merge_dict(previous.get(key, {}), resolved.get(key, {}))
        for key in ("external_profiles", "languages", "work_authorizations"):
            merged[key] = self._merge_records(list(previous.get(key, [])), list(resolved.get(key, [])))
        for key in (
            "experience_entries",
            "achievement_evidence",
            "education_entries",
            "skill_signals",
            "recommendations",
            "certifications",
            "publications",
            "awards",
        ):
            merged[key] = self._merge_records(previous_evidence.get(key, []), list(resolved.get(key, [])))
        return merged

    def _merge_dict(self, previous: object, incoming: object) -> dict[str, object]:
        merged = dict(previous) if isinstance(previous, dict) else {}
        incoming_dict = dict(incoming) if isinstance(incoming, dict) else {}
        for key, value in incoming_dict.items():
            if value in (None, "", []):
                continue
            if isinstance(value, list) and isinstance(merged.get(key), list):
                merged[key] = sorted({*merged[key], *value})
            else:
                merged[key] = value
        return merged

    def _merge_records(self, previous: list[dict[str, object]], incoming: list[dict[str, object]]) -> list[dict[str, object]]:
        ignored_keys = {
            "candidate_id",
            "created_at",
            "updated_at",
            "experience_entry_id",
            "achievement_evidence_id",
            "education_entry_id",
            "skill_signal_id",
            "recommendation_id",
            "certification_id",
            "publication_id",
            "award_id",
            "language_proficiency_id",
            "external_profile_id",
            "work_authorization_id",
        }
        records: dict[str, dict[str, object]] = {}
        for record in [*previous, *incoming]:
            clean = {key: value for key, value in record.items() if key not in ignored_keys and value not in (None, "", [])}
            if not clean:
                continue
            key = json.dumps(clean, ensure_ascii=False, sort_keys=True, default=str)
            records[key] = clean
        return list(records.values())

    def _cleanup_created_file(self, path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            return

    def _candidate_label(self, candidate_id: str) -> str | None:
        profile = self._candidate_repository.get_candidate_profile_view(candidate_id)
        if profile is not None:
            return self._candidate_label_from_profile(asdict(profile))
        candidate = self._candidate_repository.get_candidate(candidate_id)
        if candidate is None:
            return None
        return str(candidate.get("display_name") or "")

    def _candidate_label_from_profile(self, profile: dict[str, object]) -> str | None:
        core = profile.get("core_profile", {})
        if isinstance(core, dict) and core.get("full_name"):
            return str(core["full_name"])
        if profile.get("display_name"):
            return str(profile["display_name"])
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

    def _final_resume_label(self, notes: dict[str, object]) -> str:
        target_role = str(notes.get("target_role") or "resume").strip()
        language = str(notes.get("language") or "en").strip()
        return f"{target_role}-{language}"

    def _roast_report_label(self, resume_artifact_id: str, target_role: str | None) -> str:
        short_id = resume_artifact_id.split("-", 1)[0] or resume_artifact_id[:8]
        role = target_role or "resume"
        return f"{role}-for-resume-{short_id}"
