from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PLAYGROUND_ROOT = PROJECT_ROOT.parents[1]


class CliErrorSmokeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.config_path = self.root / "config" / "runtime.local.toml"
        self.workspace_path = self.root / "config" / "workspace.local.toml"
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            "\n".join(
                [
                    "[paths]",
                    f"db_path = '{self.root / 'data' / 'job_search.sqlite'}'",
                    f"artifact_root = '{self.root / 'data' / 'artifacts'}'",
                    f"sqlite_config_path = '{PLAYGROUND_ROOT / 'common' / 'config' / 'sqlite.toml'}'",
                    "",
                    "[runtime]",
                    "default_locale = 'en'",
                    "enable_ai_extraction = false",
                    "api_max_body_bytes = 1048576",
                    "api_allow_local_file_sources = false",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_vacancy_cli_returns_json_error_for_invalid_workflow_stage(self) -> None:
        result = self._run_vacancy_cli(
            "update-stage",
            "--candidate-id",
            "candidate-1",
            "--canonical-vacancy-id",
            "vacancy-1",
            "--workflow-stage",
            "maybe",
        )
        error = json.loads(result.stderr)["error"]

        self.assertEqual(result.returncode, 2)
        self.assertEqual(error["type"], "ValueError")
        self.assertIn("workflow_stage must be one of", error["message"])
        self.assertNotIn("Traceback", result.stderr)

    def test_vacancy_cli_returns_json_error_for_malformed_import_payload(self) -> None:
        payload_path = self.root / "bad-vacancies.json"
        payload_path.write_text('{"title": "CTO"}', encoding="utf-8")

        result = self._run_vacancy_cli(
            "import-json",
            "--candidate-id",
            "candidate-1",
            "--source-kind",
            "manual",
            "--items-path",
            str(payload_path),
        )
        error = json.loads(result.stderr)["error"]

        self.assertEqual(result.returncode, 2)
        self.assertEqual(error["type"], "ValueError")
        self.assertIn("Vacancy import payload must be a JSON array", error["message"])
        self.assertNotIn("Traceback", result.stderr)

    def test_candidate_and_vacancy_cli_happy_path_use_shared_workspace(self) -> None:
        candidate = self._json_stdout(self._run_candidate_cli("create", "--display-name", "CLI Candidate"))
        candidate_id = candidate["candidate_id"]

        active = self._json_stdout(self._run_candidate_cli("select", "--candidate-id", candidate_id))
        self.assertEqual(active["active_candidate_id"], candidate_id)

        source = self._json_stdout(
            self._run_candidate_cli(
                "ingest-text",
                "--source-kind",
                "resume",
                "--content-text",
                "Example Candidate\nandrei@example.com\nCTO\nRussian\nEnglish\n",
            )
        )
        draft = self._json_stdout(
            self._run_candidate_cli("generate-draft", "--source-artifact-id", source["artifact_id"])
        )
        confirmed = self._json_stdout(self._run_candidate_cli("confirm-draft", "--draft-id", draft["draft_id"]))
        self.assertEqual(confirmed["candidate_id"], candidate_id)
        profile = self._json_stdout(self._run_candidate_cli("show-profile"))
        self.assertEqual(profile["core_profile"]["full_name"], "Example Candidate")

        vacancy_payload = self.root / "vacancies.json"
        vacancy_payload.write_text(
            json.dumps(
                [
                    {
                        "title": "Head of Engineering",
                        "company_name": "ScaleOps",
                        "location_text": "Remote Europe",
                        "source_url": "https://example.com/jobs/head-of-engineering",
                        "raw_text": "Remote Head of Engineering role. Python, platform, team leadership. Salary 120000 EUR.",
                    }
                ]
            ),
            encoding="utf-8",
        )
        imported = self._json_stdout(
            self._run_vacancy_cli("import-json", "--source-kind", "manual", "--items-path", str(vacancy_payload))
        )
        self.assertEqual(len(imported["imported"]), 1)

        ranked = self._json_stdout(self._run_vacancy_cli("rank"))
        self.assertEqual(ranked[0]["company_name"], "ScaleOps")
        vacancy_id = ranked[0]["canonical_vacancy_id"]

        shortlist = self._json_stdout(self._run_vacancy_cli("shortlist", "--canonical-vacancy-id", vacancy_id))
        self.assertEqual(shortlist["workflow_stage"], "shortlisted")

        draft_result = self._json_stdout(
            self._run_vacancy_cli("create-application-draft", "--canonical-vacancy-id", vacancy_id, "--language", "en")
        )
        self.assertTrue(draft_result["application_id"])
        self.assertEqual(draft_result["quality_gate"]["status"], "pass")

        payload = self._json_stdout(
            self._run_vacancy_cli("prepare-application-payload", "--canonical-vacancy-id", vacancy_id, "--language", "en")
        )
        self.assertEqual(payload["application_id"], draft_result["application_id"])
        self.assertTrue(payload["resume_artifact_id"])
        self.assertTrue(payload["message_artifact_id"])
        self.assertIn(payload["resume_quality_gate"]["status"], {"pass", "warn"})
        self.assertEqual(payload["message_quality_gate"]["status"], "pass")

        interview = self._json_stdout(
            self._run_vacancy_cli(
                "create-interview-round",
                "--application-id",
                payload["application_id"],
                "--round-type",
                "technical",
                "--scheduled-at",
                "2026-06-10T10:00:00+00:00",
                "--idempotency-key",
                "cli-technical-round-1",
            )
        )
        interviews = self._json_stdout(self._run_vacancy_cli("list-interview-rounds"))
        updated_interview = self._json_stdout(
            self._run_vacancy_cli(
                "update-interview-round",
                "--interview-round-id",
                interview["interview_round"]["interview_round_id"],
                "--round-state",
                "completed",
                "--completed-at",
                "2026-06-10T11:00:00+00:00",
            )
        )
        self.assertEqual(interview["application"]["application_state"], "interviewing")
        self.assertEqual(len(interviews), 1)
        self.assertEqual(updated_interview["round_state"], "completed")

        checklist = self._json_stdout(self._run_vacancy_cli("board-checklist", "--platform", "linkedin"))
        self.assertEqual(checklist["platform"], "linkedin")
        self.assertIn("saved_search_settings", checklist)

        acceptance = self._json_stdout(
            self._run_vacancy_cli(
                "record-artifact-acceptance",
                "--artifact-id",
                payload["message_artifact_id"],
                "--idempotency-key",
                "cli-accept-message",
            )
        )
        approval = self._json_stdout(
            self._run_vacancy_cli(
                "record-external-action-approval",
                "--platform",
                "linkedin",
                "--action-type",
                "application_submitted",
                "--canonical-vacancy-id",
                vacancy_id,
                "--application-id",
                payload["application_id"],
                "--artifact-id",
                payload["message_artifact_id"],
                "--external-target",
                "https://www.linkedin.com/jobs/view/123",
                "--idempotency-key",
                "cli-approve-submit-linkedin-123",
            )
        )
        self.assertEqual(acceptance["approval"]["approval_type"], "artifact_acceptance")
        self.assertEqual(approval["approval"]["approval_type"], "external_action_approval")

        board_action = self._json_stdout(
            self._run_vacancy_cli(
                "record-board-action",
                "--platform",
                "linkedin",
                "--action-type",
                "application_submitted",
                "--canonical-vacancy-id",
                vacancy_id,
                "--application-id",
                payload["application_id"],
                "--artifact-id",
                payload["message_artifact_id"],
                "--external-target",
                "https://www.linkedin.com/jobs/view/123",
                "--occurred-at",
                "2026-05-20T12:00:00+00:00",
                "--idempotency-key",
                "cli-submit-linkedin-123",
                "--external-action-approval-id",
                approval["approval"]["approval_id"],
            )
        )
        board_actions = self._json_stdout(self._run_vacancy_cli("list-board-actions", "--platform", "linkedin"))
        self.assertFalse(board_action["reused"])
        self.assertEqual(len(board_actions), 1)

        report = self._json_stdout(self._run_vacancy_cli("pipeline-report"))
        self.assertEqual(report["candidate_id"], candidate_id)

    def test_linkedin_text_intake_cli_imports_through_vacancy_command_handler(self) -> None:
        candidate = self._json_stdout(self._run_candidate_cli("create", "--display-name", "LinkedIn Candidate"))
        candidate_id = candidate["candidate_id"]
        self._json_stdout(self._run_candidate_cli("select", "--candidate-id", candidate_id))

        linkedin_text_path = self.root / "linkedin-job.txt"
        linkedin_text_path.write_text(
            "\n".join(
                [
                    "Head of Platform",
                    "CloudCraft · Remote Europe",
                    "https://www.linkedin.com/jobs/view/987654321/?trk=jobs_biz_prem_srch",
                    "About the job",
                    "Lead platform engineering and Kubernetes operations.",
                ]
            ),
            encoding="utf-8",
        )

        imported = self._json_stdout(
            self._run_vacancy_cli(
                "import-linkedin-text",
                "--content-path",
                str(linkedin_text_path),
                "--source-origin",
                "manual_page",
            )
        )

        self.assertEqual(imported["source_kind"], "linkedin")
        self.assertEqual(len(imported["imported"]), 1)
        self.assertEqual(imported["warnings"], [])

    def test_touchpoint_and_reminder_cli_happy_path(self) -> None:
        candidate = self._json_stdout(self._run_candidate_cli("create", "--display-name", "Touchpoint Candidate"))
        candidate_id = candidate["candidate_id"]
        self._json_stdout(self._run_candidate_cli("select", "--candidate-id", candidate_id))

        vacancy_payload = self.root / "touchpoint-vacancy.json"
        vacancy_payload.write_text(
            json.dumps([{"title": "CTO", "company_name": "Hiring Corp", "location_text": "Remote"}]),
            encoding="utf-8",
        )
        imported = self._json_stdout(
            self._run_vacancy_cli("import-json", "--source-kind", "manual", "--items-path", str(vacancy_payload))
        )
        vacancy_id = imported["imported"][0]["canonical_vacancy_id"]

        created = self._json_stdout(
            self._run_vacancy_cli(
                "create-touchpoint",
                "--canonical-vacancy-id",
                vacancy_id,
                "--channel",
                "email",
                "--direction",
                "outgoing",
                "--touchpoint-state",
                "sent",
                "--follow-up-due-at",
                "2026-05-20T10:00:00+00:00",
            )
        )
        reminder_id = created["reminder"]["reminder_id"]

        touchpoints = self._json_stdout(self._run_vacancy_cli("list-touchpoints", "--canonical-vacancy-id", vacancy_id))
        self.assertEqual(touchpoints[0]["touchpoint_id"], created["touchpoint"]["touchpoint_id"])

        actions = self._json_stdout(self._run_vacancy_cli("daily-actions"))
        self.assertIn("follow_up_due", {item["action_type"] for item in actions})

        resolved = self._json_stdout(self._run_vacancy_cli("resolve-reminder", "--reminder-id", reminder_id))
        self.assertEqual(resolved["reminder_status"], "resolved")

        actions_after = self._json_stdout(self._run_vacancy_cli("daily-actions"))
        self.assertNotIn("follow_up_due", {item["action_type"] for item in actions_after})

    def _json_stdout(self, result: subprocess.CompletedProcess[str]) -> object:
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    def _run_candidate_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "job_search.interfaces.cli.candidate_cli",
                "--config-path",
                str(self.config_path),
                "--workspace-path",
                str(self.workspace_path),
                *args,
            ],
            cwd=PROJECT_ROOT,
            env={
                "PYTHONPATH": f"{PROJECT_ROOT / 'src'}:{PLAYGROUND_ROOT}",
            },
            text=True,
            capture_output=True,
            check=False,
        )

    def _run_vacancy_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "job_search.interfaces.cli.vacancy_cli",
                "--config-path",
                str(self.config_path),
                "--workspace-path",
                str(self.workspace_path),
                *args,
            ],
            cwd=PROJECT_ROOT,
            env={
                "PYTHONPATH": f"{PROJECT_ROOT / 'src'}:{PLAYGROUND_ROOT}",
            },
            text=True,
            capture_output=True,
            check=False,
        )


if __name__ == "__main__":
    unittest.main()
