from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

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
from job_search.config import load_runtime_settings
from job_search.interfaces.codex.candidate_commands import build_candidate_handlers


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="job-search-candidate")
    parser.add_argument("--config-path", required=True)
    parser.add_argument("--workspace-path", required=True)
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create")
    create.add_argument("--display-name", required=True)

    select = sub.add_parser("select")
    select.add_argument("--candidate-id", required=True)

    sub.add_parser("active")
    sub.add_parser("list")

    profile = sub.add_parser("show-profile")
    profile.add_argument("--candidate-id")

    sources = sub.add_parser("show-sources")
    sources.add_argument("--candidate-id")

    ext = sub.add_parser("show-external-profiles")
    ext.add_argument("--candidate-id")

    latest_draft = sub.add_parser("show-latest-draft")
    latest_draft.add_argument("--candidate-id")

    draft_review = sub.add_parser("show-draft-review")
    draft_review.add_argument("--candidate-id")
    draft_review.add_argument("--draft-id")

    ingest = sub.add_parser("ingest-text")
    ingest.add_argument("--candidate-id")
    ingest.add_argument("--source-kind", required=True)
    ingest.add_argument("--source-origin", default="text")
    ingest.add_argument("--content-text", required=True)

    ingest_file = sub.add_parser("ingest-file")
    ingest_file.add_argument("--candidate-id")
    ingest_file.add_argument("--source-kind", required=True)
    ingest_file.add_argument("--file-path", required=True)

    ingest_url = sub.add_parser("ingest-url")
    ingest_url.add_argument("--candidate-id")
    ingest_url.add_argument("--source-kind", required=True)
    ingest_url.add_argument("--source-url", required=True)
    ingest_url.add_argument("--content-text")

    attach_artifact = sub.add_parser("attach-artifact")
    attach_artifact.add_argument("--candidate-id")
    attach_artifact.add_argument("--source-kind", required=True)
    attach_artifact.add_argument("--existing-artifact-id", required=True)

    resume = sub.add_parser("generate-resume")
    resume.add_argument("--candidate-id")
    resume.add_argument("--language", default="en")
    resume.add_argument("--target-role")

    positioning = sub.add_parser("generate-positioning-brief")
    positioning.add_argument("--candidate-id")
    positioning.add_argument("--target-role", required=True)
    positioning.add_argument("--language", default="en")

    career = sub.add_parser("career-pathing-lite")
    career.add_argument("--candidate-id")
    career.add_argument("--target-role", action="append")

    career_full = sub.add_parser("career-pathing-full")
    career_full.add_argument("--candidate-id")
    career_full.add_argument("--target-role", action="append")
    career_full.add_argument("--without-kb", action="store_true")

    playbook = sub.add_parser("generate-playbook")
    playbook.add_argument("--candidate-id")

    resume_gate = sub.add_parser("check-resume")
    resume_gate.add_argument("--artifact-id", required=True)

    resume_final = sub.add_parser("finalize-resume")
    resume_final.add_argument("--artifact-id", required=True)
    resume_final.add_argument("--allow-warnings", action="store_true")

    resume_roast = sub.add_parser("roast-resume")
    resume_roast.add_argument("--artifact-id", required=True)
    resume_roast.add_argument("--target-role")

    kb_evidence = sub.add_parser("search-resume-kb-evidence")
    kb_evidence.add_argument("--candidate-id")
    kb_evidence.add_argument("--target-role")
    kb_evidence.add_argument("--query")
    kb_evidence.add_argument("--limit", type=int, default=5)

    draft = sub.add_parser("generate-draft")
    draft.add_argument("--candidate-id")
    draft.add_argument("--source-artifact-id", action="append")

    ai_request = sub.add_parser("build-ai-extraction-request")
    ai_request.add_argument("--candidate-id")
    ai_request.add_argument("--source-artifact-id", action="append")

    ai_import = sub.add_parser("import-ai-draft")
    ai_import.add_argument("--candidate-id")
    ai_import.add_argument("--payload-file", required=True)
    ai_import.add_argument("--source-artifact-id", action="append")

    confirm = sub.add_parser("confirm-draft")
    confirm.add_argument("--candidate-id")
    confirm.add_argument("--draft-id", required=True)
    confirm.add_argument("--accepted-field", action="append", default=[])
    return parser


def _resolve_candidate_id(candidate_id: str | None, handlers) -> str:
    if candidate_id:
        return candidate_id
    active = handlers.get_active_candidate(GetActiveCandidate())
    active_candidate_id = active.get("active_candidate_id")
    if not active_candidate_id:
        raise ValueError("candidate_id is required when no active candidate is selected")
    return str(active_candidate_id)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    runtime_settings = load_runtime_settings(Path(args.config_path))
    handlers = build_candidate_handlers(runtime_settings, Path(args.workspace_path))

    if args.command == "create":
        result = handlers.create_candidate(CreateCandidate(display_name=args.display_name))
    elif args.command == "select":
        result = handlers.set_active_candidate(SetActiveCandidate(candidate_id=args.candidate_id))
    elif args.command == "active":
        result = handlers.get_active_candidate(GetActiveCandidate())
    elif args.command == "list":
        result = handlers.list_candidates(ListCandidates())
    elif args.command == "show-profile":
        result = handlers.get_candidate_profile(GetCandidateProfile(candidate_id=_resolve_candidate_id(args.candidate_id, handlers)))
    elif args.command == "show-sources":
        result = handlers.get_candidate_sources(GetCandidateSources(candidate_id=_resolve_candidate_id(args.candidate_id, handlers)))
    elif args.command == "show-external-profiles":
        result = handlers.get_candidate_external_profiles(
            GetCandidateExternalProfiles(candidate_id=_resolve_candidate_id(args.candidate_id, handlers))
        )
    elif args.command == "show-latest-draft":
        result = handlers.get_latest_candidate_draft(
            GetLatestCandidateDraft(candidate_id=_resolve_candidate_id(args.candidate_id, handlers))
        )
    elif args.command == "show-draft-review":
        result = handlers.get_candidate_draft_review(
            GetCandidateDraftReview(
                candidate_id=_resolve_candidate_id(args.candidate_id, handlers),
                draft_id=args.draft_id,
            )
        )
    elif args.command == "ingest-text":
        result = handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=_resolve_candidate_id(args.candidate_id, handlers),
                source_kind=args.source_kind,
                source_origin=args.source_origin,
                content_text=args.content_text,
            )
        )
    elif args.command == "ingest-file":
        result = handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=_resolve_candidate_id(args.candidate_id, handlers),
                source_kind=args.source_kind,
                source_origin="file",
                file_path=args.file_path,
            )
        )
    elif args.command == "ingest-url":
        result = handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=_resolve_candidate_id(args.candidate_id, handlers),
                source_kind=args.source_kind,
                source_origin="url",
                source_url=args.source_url,
                content_text=args.content_text,
            )
        )
    elif args.command == "attach-artifact":
        result = handlers.register_candidate_source(
            RegisterCandidateSource(
                candidate_id=_resolve_candidate_id(args.candidate_id, handlers),
                source_kind=args.source_kind,
                source_origin="existing_artifact",
                existing_artifact_id=args.existing_artifact_id,
            )
        )
    elif args.command == "generate-draft":
        result = handlers.generate_candidate_profile_draft(
            GenerateCandidateProfileDraftFromSources(
                candidate_id=_resolve_candidate_id(args.candidate_id, handlers),
                source_artifact_ids=args.source_artifact_id,
            )
        )
    elif args.command == "build-ai-extraction-request":
        result = handlers.build_candidate_ai_extraction_request(
            BuildCandidateAiExtractionRequest(
                candidate_id=_resolve_candidate_id(args.candidate_id, handlers),
                source_artifact_ids=args.source_artifact_id,
            )
        )
    elif args.command == "import-ai-draft":
        result = handlers.import_candidate_ai_extraction_draft(
            ImportCandidateAiExtractionDraft(
                candidate_id=_resolve_candidate_id(args.candidate_id, handlers),
                response_payload=json.loads(Path(args.payload_file).read_text(encoding="utf-8")),
                source_artifact_ids=args.source_artifact_id,
            )
        )
    elif args.command == "generate-resume":
        result = handlers.generate_resume_markdown(
            GenerateResumeMarkdown(
                candidate_id=_resolve_candidate_id(args.candidate_id, handlers),
                language=args.language,
                target_role=args.target_role,
            )
        )
    elif args.command == "generate-positioning-brief":
        result = handlers.generate_resume_positioning_brief(
            GenerateResumePositioningBrief(
                candidate_id=_resolve_candidate_id(args.candidate_id, handlers),
                target_role=args.target_role,
                language=args.language,
            )
        )
    elif args.command == "career-pathing-lite":
        result = handlers.generate_career_pathing_lite(
            GenerateCareerPathingLite(
                candidate_id=_resolve_candidate_id(args.candidate_id, handlers),
                target_roles=args.target_role,
            )
        )
    elif args.command == "career-pathing-full":
        result = handlers.generate_career_pathing_full(
            GenerateCareerPathingFull(
                candidate_id=_resolve_candidate_id(args.candidate_id, handlers),
                target_roles=args.target_role,
                include_kb=not args.without_kb,
            )
        )
    elif args.command == "generate-playbook":
        result = handlers.generate_job_search_playbook(
            GenerateJobSearchPlaybook(candidate_id=_resolve_candidate_id(args.candidate_id, handlers))
        )
    elif args.command == "check-resume":
        result = handlers.run_resume_quality_gate(RunResumeQualityGate(artifact_id=args.artifact_id))
    elif args.command == "finalize-resume":
        result = handlers.finalize_resume_markdown(
            FinalizeResumeMarkdown(artifact_id=args.artifact_id, allow_warnings=args.allow_warnings)
        )
    elif args.command == "roast-resume":
        result = handlers.generate_resume_roast_report(
            GenerateResumeRoastReport(artifact_id=args.artifact_id, target_role=args.target_role)
        )
    elif args.command == "search-resume-kb-evidence":
        result = handlers.search_resume_kb_evidence(
            SearchResumeKbEvidence(
                candidate_id=_resolve_candidate_id(args.candidate_id, handlers),
                target_role=args.target_role,
                query=args.query,
                limit=args.limit,
            )
        )
    else:
        accepted: dict[str, str] = {}
        for item in args.accepted_field:
            key, _, value = item.partition("=")
            accepted[key] = value
        result = handlers.confirm_candidate_profile_draft(
            ConfirmCandidateProfileDraft(
                candidate_id=_resolve_candidate_id(args.candidate_id, handlers),
                draft_id=args.draft_id,
                accepted_field_values=accepted,
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
