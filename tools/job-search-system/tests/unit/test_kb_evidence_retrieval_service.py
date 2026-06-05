from __future__ import annotations

import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from job_search.application.services.kb_evidence_retrieval_service import KbEvidenceRetrievalService


class KbEvidenceRetrievalServiceTest(unittest.TestCase):
    def test_unavailable_when_config_is_not_set(self) -> None:
        result = KbEvidenceRetrievalService(config_path=None).search(
            candidate_profile={"target_roles": ["CTO"]},
            evidence={"skill_signals": [{"skill_name": "Platform Engineering"}]},
            target_role="CTO",
            query=None,
            limit=5,
        )

        self.assertEqual(result["status"], "unavailable")
        self.assertIn("job-search", result["effective_query"])
        self.assertIn("hiring", result["effective_query"])

    def test_search_merges_results_across_allowed_note_types(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config_path = root / "runtime.local.toml"
            search_bin = root / "search_kb"
            config_path.write_text("[paths]\n", encoding="utf-8")
            search_bin.write_text("#!/bin/sh\n", encoding="utf-8")
            calls: list[list[str]] = []

            def runner(args: list[str], timeout_seconds: int) -> subprocess.CompletedProcess[str]:
                calls.append(args)
                note_type = args[args.index("--note-type") + 1]
                payload = [
                    {
                        "path": "Ideas/Hiring.md",
                        "title": "Hiring",
                        "score": 0.9 if note_type == "job" else 0.4,
                        "lead_summary": "Hiring positioning with P&L and budget ownership signals.",
                    }
                ]
                return subprocess.CompletedProcess(args=args, returncode=0, stdout=json.dumps(payload), stderr="")

            result = KbEvidenceRetrievalService(
                config_path=config_path,
                search_bin=search_bin,
                runner=runner,
            ).search(
                candidate_profile={"target_roles": ["VP Engineering"]},
                evidence={"skill_signals": [{"skill_name": "FinOps"}]},
                target_role="CTO",
                query="career positioning",
                limit=5,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(calls), 3)
        self.assertEqual(result["results"][0]["score"], 0.9)
        self.assertEqual(result["results"][0]["note_type_filter"], "job")
        self.assertEqual(result["candidate_review_suggestions"][0]["suggested_action"], "ask_user_to_confirm_candidate_evidence")
        signals = {item["signal"] for item in result["candidate_review_suggestions"]}
        self.assertIn("hiring", signals)
        self.assertIn("p&l", signals)
        self.assertIn("candidate-intake", result["candidate_review_suggestions"][0]["required_next_step"])
        self.assertIn("career positioning", result["effective_query"])
        self.assertIn("FinOps", result["effective_query"])


if __name__ == "__main__":
    unittest.main()
