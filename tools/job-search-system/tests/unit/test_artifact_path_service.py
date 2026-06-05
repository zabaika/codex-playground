from __future__ import annotations

from pathlib import Path
import unittest

from job_search.application.services.artifact_path_service import ArtifactPathService


class ArtifactPathServiceTest(unittest.TestCase):
    def test_candidate_folder_uses_latin_slug_and_short_id(self) -> None:
        folder = ArtifactPathService.candidate_folder(
            "94f574e8-1111-2222-3333-444444444444",
            "Example Candidate",
        )

        self.assertEqual(folder, "example-candidate--94f574e8")

    def test_candidate_artifact_path_keeps_candidate_namespace(self) -> None:
        path = ArtifactPathService.candidate_artifact_path(
            artifact_root=Path("/tmp/artifacts"),
            candidate_id="abc12345-1111-2222-3333-444444444444",
            artifact_id="4d6e4ff4-c218-4b88-a49b-b3c757fc5255",
            artifact_type="resume_markdown",
            candidate_label="Second Example Candidate",
        )

        self.assertEqual(
            path,
            Path("/tmp/artifacts/candidates/second-example-candidate--abc12345/drafts/resume-markdown--4d6e4ff4.md"),
        )

    def test_candidate_artifact_path_adds_human_context_and_json_suffix_for_profile_drafts(self) -> None:
        path = ArtifactPathService.candidate_artifact_path(
            artifact_root=Path("/tmp/artifacts"),
            candidate_id="abc12345-1111-2222-3333-444444444444",
            artifact_id="26930230-5949-421b-8035-5a031cddd6d1",
            artifact_type="candidate_profile_draft",
            candidate_label="Second Example Candidate",
            artifact_label="AI profile draft",
        )

        self.assertEqual(
            path,
            Path(
                "/tmp/artifacts/candidates/second-example-candidate--abc12345/drafts/"
                "candidate-profile-draft--ai-profile-draft--26930230.json"
            ),
        )

    def test_candidate_artifact_path_uses_label_for_generated_markdown(self) -> None:
        path = ArtifactPathService.candidate_artifact_path(
            artifact_root=Path("/tmp/artifacts"),
            candidate_id="abc12345-1111-2222-3333-444444444444",
            artifact_id="4d6e4ff4-c218-4b88-a49b-b3c757fc5255",
            artifact_type="message_artifact",
            candidate_label="Second Example Candidate",
            artifact_label="Acme CTO",
        )

        self.assertEqual(
            path,
            Path("/tmp/artifacts/candidates/second-example-candidate--abc12345/drafts/message-artifact--acme-cto--4d6e4ff4.md"),
        )

    def test_candidate_artifact_path_separates_final_resume_namespace(self) -> None:
        path = ArtifactPathService.candidate_artifact_path(
            artifact_root=Path("/tmp/artifacts"),
            candidate_id="abc12345-1111-2222-3333-444444444444",
            artifact_id="4d6e4ff4-c218-4b88-a49b-b3c757fc5255",
            artifact_type="resume_markdown_final",
            candidate_label="Second Example Candidate",
            artifact_label="CTO en",
        )

        self.assertEqual(
            path,
            Path("/tmp/artifacts/candidates/second-example-candidate--abc12345/final/resume-final--cto-en--4d6e4ff4.md"),
        )

    def test_candidate_artifact_path_names_roast_report_by_source_resume(self) -> None:
        path = ArtifactPathService.candidate_artifact_path(
            artifact_root=Path("/tmp/artifacts"),
            candidate_id="abc12345-1111-2222-3333-444444444444",
            artifact_id="44d8c80d-c218-4b88-a49b-b3c757fc5255",
            artifact_type="resume_roast_report",
            candidate_label="Second Example Candidate",
            artifact_label="CTO for resume 4d6e4ff4",
        )

        self.assertEqual(
            path,
            Path(
                "/tmp/artifacts/candidates/second-example-candidate--abc12345/drafts/"
                "resume-roast-report--cto-for-resume-4d6e4ff4--44d8c80d.md"
            ),
        )

    def test_candidate_artifact_path_names_vacancy_resume_and_final(self) -> None:
        draft_path = ArtifactPathService.candidate_artifact_path(
            artifact_root=Path("/tmp/artifacts"),
            candidate_id="abc12345-1111-2222-3333-444444444444",
            artifact_id="44d8c80d-c218-4b88-a49b-b3c757fc5255",
            artifact_type="resume_vacancy",
            candidate_label="Second Example Candidate",
            artifact_label="Acme CTO",
        )
        final_path = ArtifactPathService.candidate_artifact_path(
            artifact_root=Path("/tmp/artifacts"),
            candidate_id="abc12345-1111-2222-3333-444444444444",
            artifact_id="aa18c80d-c218-4b88-a49b-b3c757fc5255",
            artifact_type="resume_vacancy_final",
            candidate_label="Second Example Candidate",
            artifact_label="Acme CTO",
        )

        self.assertEqual(
            draft_path,
            Path("/tmp/artifacts/candidates/second-example-candidate--abc12345/drafts/resume-vacancy--acme-cto--44d8c80d.md"),
        )
        self.assertEqual(
            final_path,
            Path("/tmp/artifacts/candidates/second-example-candidate--abc12345/final/resume-vacancy-final--acme-cto--aa18c80d.md"),
        )


if __name__ == "__main__":
    unittest.main()
