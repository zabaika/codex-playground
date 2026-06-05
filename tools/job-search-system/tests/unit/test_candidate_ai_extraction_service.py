from __future__ import annotations

import unittest

from job_search.application.services.candidate_ai_extraction_service import CandidateAiExtractionService


class CandidateAiExtractionServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = CandidateAiExtractionService()
        self.sources = [
            {
                "artifact_id": "source-1",
                "artifact_type": "resume_source",
                "source_kind": "resume",
                "content_text": "Example Candidate\nCTO\n",
            }
        ]

    def test_build_request_is_draft_only_and_schema_bound(self) -> None:
        request = self.service.build_request(candidate_id="candidate-1", sources=self.sources)

        self.assertEqual(request["mode"], "draft_only_no_state_mutation")
        self.assertEqual(request["source_set_id"], self.service.source_set_id(self.sources))
        self.assertIn("allowed_draft_payload_keys", request["output_contract"])

    def test_validate_response_accepts_schema_bound_draft(self) -> None:
        source_set_id = self.service.source_set_id(self.sources)
        draft = self.service.validate_response(
            candidate_id="candidate-1",
            expected_source_set_id=source_set_id,
            allowed_source_artifact_ids={"source-1"},
            response_payload={
                "candidate_id": "candidate-1",
                "source_set_id": source_set_id,
                "draft_payload": {
                    "core_profile": {"full_name": "Example Candidate"},
                    "field_statuses": {"full_name": "confirmed"},
                    "experience_entries": [{"company_name": "Example", "source_artifact_id": "source-1"}],
                },
                "field_conflicts": {},
                "field_evidence": {"core_profile.full_name": [{"artifact_id": "source-1"}]},
                "missing_fields": ["primary_email"],
            },
        )

        self.assertEqual(draft.draft_payload["core_profile"]["full_name"], "Example Candidate")
        self.assertEqual(draft.missing_fields, ["primary_email"])

    def test_validate_response_deduplicates_field_evidence(self) -> None:
        source_set_id = self.service.source_set_id(self.sources)
        draft = self.service.validate_response(
            candidate_id="candidate-1",
            expected_source_set_id=source_set_id,
            allowed_source_artifact_ids={"source-1"},
            response_payload={
                "candidate_id": "candidate-1",
                "source_set_id": source_set_id,
                "draft_payload": {
                    "core_profile": {"full_name": "Example Candidate"},
                    "field_statuses": {"full_name": "confirmed"},
                },
                "field_conflicts": {},
                "field_evidence": {
                    "core_profile.full_name": [
                        {"artifact_id": "source-1"},
                        {"artifact_id": "source-1"},
                    ]
                },
                "missing_fields": [],
            },
        )

        self.assertEqual(draft.field_evidence["core_profile.full_name"], [{"artifact_id": "source-1"}])

    def test_validate_response_rejects_unknown_fields(self) -> None:
        source_set_id = self.service.source_set_id(self.sources)

        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            self.service.validate_response(
                candidate_id="candidate-1",
                expected_source_set_id=source_set_id,
                allowed_source_artifact_ids={"source-1"},
                response_payload={
                    "candidate_id": "candidate-1",
                    "source_set_id": source_set_id,
                    "draft_payload": {
                        "core_profile": {"full_name": "Example Candidate", "db_id": "must-not-pass"},
                        "field_statuses": {"full_name": "confirmed"},
                    },
                    "field_conflicts": {},
                    "field_evidence": {},
                    "missing_fields": [],
                },
            )

    def test_validate_response_rejects_foreign_source_references(self) -> None:
        source_set_id = self.service.source_set_id(self.sources)

        with self.assertRaisesRegex(ValueError, "outside selected source set"):
            self.service.validate_response(
                candidate_id="candidate-1",
                expected_source_set_id=source_set_id,
                allowed_source_artifact_ids={"source-1"},
                response_payload={
                    "candidate_id": "candidate-1",
                    "source_set_id": source_set_id,
                    "draft_payload": {
                        "core_profile": {"full_name": "Example Candidate"},
                        "field_statuses": {"full_name": "confirmed"},
                    },
                    "field_conflicts": {},
                    "field_evidence": {"core_profile.full_name": [{"artifact_id": "source-2"}]},
                    "missing_fields": [],
                },
            )


if __name__ == "__main__":
    unittest.main()
