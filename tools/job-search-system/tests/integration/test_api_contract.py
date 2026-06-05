from __future__ import annotations

import json
from http.client import HTTPConnection
from http.server import HTTPServer
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from threading import Thread
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PLAYGROUND_ROOT = PROJECT_ROOT.parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(PLAYGROUND_ROOT) not in sys.path:
    sys.path.insert(0, str(PLAYGROUND_ROOT))

from job_search.config import RuntimeSettings  # noqa: E402
from job_search.interfaces.api.app import JobSearchApi  # noqa: E402
from job_search.interfaces.api.server import JobSearchHttpHandler, validate_bind_host, validate_runtime_network_safety  # noqa: E402


class ApiContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.workspace_path = self.root / "config" / "workspace.local.toml"
        self.api = JobSearchApi(
            runtime_settings=RuntimeSettings(
                db_path=self.root / "data" / "job_search.sqlite",
                artifact_root=self.root / "data" / "artifacts",
                sqlite_config_path=PLAYGROUND_ROOT / "common" / "config" / "sqlite.toml",
                default_locale="en",
                enable_ai_extraction=False,
                api_max_body_bytes=1024 * 1024,
                api_allow_local_file_sources=False,
            ),
            workspace_path=self.workspace_path,
        )

    def tearDown(self) -> None:
        self.api.close()
        self._tmp.cleanup()

    def test_stage2_reference_flow_over_api_contract(self) -> None:
        health = self._get("/health")
        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["api_contract_version"], "2026-06-05.1")
        version = self._get("/version")
        self.assertEqual(version["package_version"], "0.1.0")
        system_status = self._get("/system/status")
        self.assertEqual(system_status["api_contract_version"], "2026-06-05.1")
        self.assertIn("checks", system_status)

        candidate = self._post("/candidates", {"display_name": "API Candidate"})
        candidate_id = candidate["candidate_id"]
        active = self._post("/candidates/active", {"candidate_id": candidate_id})
        self.assertEqual(active["active_candidate_id"], candidate_id)

        source = self._post(
            "/candidates/sources/text",
            {
                "source_kind": "resume",
                "content_text": (
                    "Example Candidate\nandrei@example.com\nCTO\n"
                    "Summary Platform engineering leader with cloud, delivery and FinOps experience.\n"
                    "Russian\nEnglish\n"
                ),
            },
        )
        ai_request = self._post("/candidates/ai-extraction-request", {"source_artifact_ids": [source["artifact_id"]]})
        self.assertEqual(ai_request["candidate_id"], candidate_id)

        draft = self._post("/candidates/drafts", {"source_artifact_ids": [source["artifact_id"]]})
        latest_draft = self._get("/candidates/latest-draft")
        self.assertEqual(latest_draft["draft_id"], draft["draft_id"])
        draft_review = self._get(f"/candidates/draft-review?draft_id={draft['draft_id']}")
        self.assertEqual(draft_review["draft_id"], draft["draft_id"])
        self.assertTrue(draft_review["fields"])

        confirmed = self._post("/candidates/confirm-draft", {"draft_id": draft["draft_id"], "accepted_field_values": {}})
        self.assertEqual(confirmed["candidate_id"], candidate_id)
        profile = self._get("/candidates/profile")
        self.assertEqual(profile["core_profile"]["full_name"], "Example Candidate")
        career_full = self._post(
            "/candidates/career-pathing-full",
            {"candidate_id": candidate_id, "target_roles": ["CTO", "Head of Engineering"], "include_kb": False},
        )
        self.assertEqual(career_full["analysis"]["state_mutation"], "none")
        self.assertEqual(career_full["analysis"]["mode"], "full")
        self.assertTrue(career_full["artifact_id"])
        kb_evidence = self._get(
            f"/candidates/resume-kb-evidence?candidate_id={candidate_id}&target_role=Head%20of%20Engineering&query=platform&limit=3"
        )
        self.assertEqual(kb_evidence["status"], "unavailable")
        self.assertIn("job-search", kb_evidence["evidence_surface"])

        imported = self._post(
            "/vacancies/import-json",
            {
                "source_kind": "manual",
                "items": [
                    {
                        "title": "Head of Engineering",
                        "company_name": "ScaleOps",
                        "location_text": "Remote Europe",
                        "source_url": "https://example.com/jobs/head-of-engineering",
                        "raw_text": "Remote Head of Engineering role. Platform Engineering, Cloud and FinOps. Salary 145000 EUR.",
                    }
                ],
            },
        )
        vacancy_id = imported["imported"][0]["canonical_vacancy_id"]
        url_seed = self._post(
            "/vacancies/url-seeds",
            {
                "source_url": "https://example.com/jobs/platform-director",
                "platform": "generic",
                "idempotency_key": "api-url-seed-platform-director",
            },
        )
        self.assertEqual(url_seed["seed"]["seed_status"], "pending")
        preview = self._post(
            "/vacancies/url-seeds/preview",
            {
                "url_seed_id": url_seed["seed"]["url_seed_id"],
                "content_text": "Title: Platform Director\nCompany: Example Corp\nLocation: Remote Europe",
            },
        )
        self.assertTrue(preview["preview"]["importable"])
        confirmed_seed = self._post(
            "/vacancies/url-seeds/confirm",
            {"url_seed_id": url_seed["seed"]["url_seed_id"]},
        )
        url_seeds = self._get("/vacancies/url-seeds?seed_status=imported")
        self.assertEqual(confirmed_seed["seed"]["seed_status"], "imported")
        self.assertEqual(len(url_seeds), 1)
        ranked = self._get("/vacancies/rank")
        ranked_ids = {item["canonical_vacancy_id"] for item in ranked}
        self.assertIn(vacancy_id, ranked_ids)
        self.assertIn(confirmed_seed["imported"][0]["canonical_vacancy_id"], ranked_ids)
        self.assertIn("role", ranked[0]["scoring_breakdown"])
        self.assertIn("compensation", ranked[0]["scoring_breakdown"])

        shortlisted = self._post("/vacancies/shortlist", {"canonical_vacancy_id": vacancy_id})
        self.assertEqual(shortlisted["workflow_stage"], "shortlisted")

        application_draft = self._post("/vacancies/application-draft", {"canonical_vacancy_id": vacancy_id, "language": "en"})
        self.assertTrue(application_draft["application_id"])
        self.assertEqual(application_draft["quality_gate"]["status"], "pass")
        interview = self._post(
            "/vacancies/interview-rounds",
            {
                "application_id": application_draft["application_id"],
                "round_type": "technical",
                "scheduled_at": "2026-06-10T10:00:00+00:00",
                "idempotency_key": "api-technical-round-1",
            },
        )
        repeated_interview = self._post(
            "/vacancies/interview-rounds",
            {
                "application_id": application_draft["application_id"],
                "round_type": "technical",
                "scheduled_at": "2026-06-10T10:00:00+00:00",
                "idempotency_key": "api-technical-round-1",
            },
        )
        interviews = self._get("/vacancies/interview-rounds")
        self.assertFalse(interview["reused"])
        self.assertTrue(repeated_interview["reused"])
        self.assertEqual(interview["application"]["application_state"], "interviewing")
        self.assertEqual(len(interviews), 1)
        self._post(
            "/vacancies/interview-rounds/state",
            {
                "interview_round_id": interview["interview_round"]["interview_round_id"],
                "round_state": "completed",
                "completed_at": "2026-06-10T11:00:00+00:00",
            },
        )

        payload = self._post("/vacancies/application-payload", {"canonical_vacancy_id": vacancy_id, "language": "en"})
        self.assertEqual(payload["application_id"], application_draft["application_id"])
        self.assertIn(payload["resume_quality_gate"]["status"], {"pass", "warn"})
        self.assertEqual(payload["message_quality_gate"]["status"], "pass")
        self.assertIn(payload["application_payload_quality_gate"]["status"], {"pass", "warn"})
        final_resume = self._post(
            "/candidates/resume-final",
            {
                "artifact_id": payload["resume_artifact_id"],
                "allow_warnings": payload["resume_quality_gate"]["status"] == "warn",
            },
        )
        self.assertEqual(final_resume["artifact_type"], "resume_markdown_final")
        self.assertEqual(final_resume["derived_from_artifact_id"], payload["resume_artifact_id"])
        self.assertIn("/final/resume-final--", final_resume["storage_path"])
        vacancy_resume = self._post(
            "/vacancies/resume",
            {"canonical_vacancy_id": vacancy_id, "language": "en"},
        )
        repeated_vacancy_resume = self._post(
            "/vacancies/resume",
            {"canonical_vacancy_id": vacancy_id, "language": "en"},
        )
        vacancy_resume_final = self._post(
            "/vacancies/resume-final",
            {
                "artifact_id": vacancy_resume["artifact_id"],
                "allow_warnings": vacancy_resume["quality_gate"]["status"] == "warn",
            },
        )
        self.assertEqual(vacancy_resume["artifact_type"], "resume_vacancy")
        self.assertEqual(vacancy_resume["source_resume_artifact_id"], final_resume["artifact_id"])
        self.assertEqual(vacancy_resume["artifact_id"], repeated_vacancy_resume["artifact_id"])
        self.assertTrue(repeated_vacancy_resume["overwritten"])
        self.assertEqual(vacancy_resume_final["artifact_type"], "resume_vacancy_final")
        self.assertEqual(vacancy_resume_final["derived_from_artifact_id"], vacancy_resume["artifact_id"])
        roast = self._post(
            "/candidates/resume-roast",
            {"artifact_id": payload["resume_artifact_id"], "target_role": "Head of Engineering"},
        )
        repeated_roast = self._post(
            "/candidates/resume-roast",
            {"artifact_id": payload["resume_artifact_id"], "target_role": "Head of Engineering"},
        )
        self.assertEqual(roast["artifact_type"], "resume_roast_report")
        self.assertEqual(roast["derived_from_artifact_id"], payload["resume_artifact_id"])
        self.assertEqual(roast["artifact_id"], repeated_roast["artifact_id"])
        self.assertTrue(repeated_roast["overwritten"])

        touchpoint = self._post(
            "/vacancies/touchpoints",
            {
                "canonical_vacancy_id": vacancy_id,
                "application_id": payload["application_id"],
                "message_artifact_id": payload["message_artifact_id"],
                "follow_up_due_at": "2026-05-20T10:00:00+00:00",
            },
        )
        reminder_id = touchpoint["reminder"]["reminder_id"]
        touchpoints = self._get(f"/vacancies/touchpoints?canonical_vacancy_id={vacancy_id}")
        self.assertEqual(touchpoints[0]["touchpoint_id"], touchpoint["touchpoint"]["touchpoint_id"])

        actions = self._get("/vacancies/daily-actions")
        self.assertIn("follow_up_due", {item["action_type"] for item in actions})
        resolved = self._post("/vacancies/reminders/resolve", {"reminder_id": reminder_id})
        self.assertEqual(resolved["reminder_status"], "resolved")

        checklist = self._get(f"/vacancies/board-checklist?platform=linkedin&canonical_vacancy_id={vacancy_id}")
        self.assertEqual(checklist["platform"], "linkedin")
        self.assertIn("saved_search_settings", checklist)

        acceptance = self._post(
            "/vacancies/artifact-acceptance",
            {
                "artifact_id": payload["message_artifact_id"],
                "approval_state": "accepted",
                "idempotency_key": "accept-message-api",
            },
        )
        self.assertEqual(acceptance["approval"]["approval_type"], "artifact_acceptance")
        external_approval = self._post(
            "/vacancies/external-action-approval",
            {
                "platform": "linkedin",
                "action_type": "application_submitted",
                "canonical_vacancy_id": vacancy_id,
                "application_id": payload["application_id"],
                "artifact_id": payload["message_artifact_id"],
                "external_target": "https://www.linkedin.com/jobs/view/123",
                "idempotency_key": "approve-submit-linkedin-123",
            },
        )
        self.assertEqual(external_approval["approval"]["approval_type"], "external_action_approval")
        approvals = self._get("/vacancies/approvals?approval_type=external_action_approval")
        self.assertEqual(len(approvals), 1)

        board_action = self._post(
            "/vacancies/board-actions",
            {
                "platform": "linkedin",
                "action_type": "application_submitted",
                "canonical_vacancy_id": vacancy_id,
                "application_id": payload["application_id"],
                "artifact_id": payload["message_artifact_id"],
                "external_target": "https://www.linkedin.com/jobs/view/123",
                "occurred_at": "2026-05-20T12:00:00+00:00",
                "idempotency_key": "api-submit-linkedin-123",
                "external_action_approval_id": external_approval["approval"]["approval_id"],
            },
        )
        repeated_board_action = self._post(
            "/vacancies/board-actions",
            {
                "platform": "linkedin",
                "action_type": "application_submitted",
                "canonical_vacancy_id": vacancy_id,
                "application_id": payload["application_id"],
                "artifact_id": payload["message_artifact_id"],
                "external_target": "https://www.linkedin.com/jobs/view/123",
                "occurred_at": "2026-05-20T12:00:00+00:00",
                "idempotency_key": "api-submit-linkedin-123",
                "external_action_approval_id": external_approval["approval"]["approval_id"],
            },
        )
        board_actions = self._get("/vacancies/board-actions?platform=linkedin")
        self.assertFalse(board_action["reused"])
        self.assertTrue(repeated_board_action["reused"])
        self.assertEqual(len(board_actions), 1)
        self.assertEqual(
            board_action["board_action"]["external_action_approval_id"],
            external_approval["approval"]["approval_id"],
        )
        self.assertEqual(board_action["reconciliation_item"]["outcome"], "auto_accept")
        reconciliation = self._get("/vacancies/reconciliation?platform=linkedin")
        self.assertEqual(len(reconciliation), 1)
        self.assertEqual(reconciliation[0]["review_status"], "resolved")

        self._post("/vacancies/processed", {"canonical_vacancy_id": vacancy_id})
        self._post(
            "/vacancies/import-json",
            {
                "source_kind": "manual",
                "items": [
                    {
                        "title": "Head of Engineering",
                        "company_name": "ScaleOps",
                        "location_text": "Remote Europe",
                        "source_url": "https://example.com/jobs/head-of-engineering",
                        "raw_text": "Updated remote role body with material compensation and scope changes.",
                    }
                ],
            },
        )
        material_review = self._get("/vacancies/material-change-review")
        self.assertEqual(material_review[0]["canonical_vacancy_id"], vacancy_id)
        self.assertEqual(material_review[0]["review_bucket"], "material_change")

        report = self._get("/vacancies/pipeline-report")
        self.assertEqual(report["candidate_id"], candidate_id)
        self.assertEqual(report["summary"]["manual_board_actions"], 1)
        self.assertEqual(report["summary"]["material_change_review"], 1)
        self.assertEqual(report["review_buckets"]["material_change"], 1)
        self.assertEqual(report["board_action_counts"]["application_submitted"], 1)

        observability = self._get(f"/system/observability?candidate_id={candidate_id}&limit=5")
        self.assertEqual(observability["candidate_id"], candidate_id)
        self.assertGreater(observability["counts"]["audit_events"], 0)
        self.assertGreater(observability["counts"]["quality_gate_runs"], 0)
        self.assertEqual(observability["counts"]["manual_board_actions"], 1)
        self.assertIn("pass", observability["quality_gate_counts"])
        self.assertTrue(observability["recent_audit_events"])
        self.assertTrue(observability["recent_artifact_usage_events"])
        self.assertEqual(
            observability["recent_board_action_idempotency_keys"][0]["idempotency_key"],
            "api-submit-linkedin-123",
        )
        strategy = self._get(f"/system/strategy-report?candidate_id={candidate_id}&limit=5")
        self.assertEqual(strategy["candidate_id"], candidate_id)
        self.assertEqual(strategy["summary"]["applications"], 1)
        self.assertEqual(strategy["summary"]["submitted_actions"], 1)
        self.assertGreaterEqual(strategy["summary"]["completed_interview_rounds"], 1)
        self.assertTrue(strategy["resume_effectiveness"])
        self.assertEqual(strategy["resume_effectiveness"][0]["final_resume_artifact_id"], final_resume["artifact_id"])
        self.assertEqual(strategy["resume_effectiveness"][0]["submitted_actions"], 1)
        self.assertTrue(strategy["position_effectiveness"])
        self.assertIn("Head of Engineering", {item["position"] for item in strategy["position_effectiveness"]})

    def test_linkedin_text_intake_and_error_envelope(self) -> None:
        candidate = self._post("/candidates", {"display_name": "LinkedIn API Candidate"})
        self._post("/candidates/active", {"candidate_id": candidate["candidate_id"]})

        imported = self._post(
            "/vacancies/import-linkedin-text",
            {
                "content_text": "\n".join(
                    [
                        "Head of Platform",
                        "CloudCraft · Remote Europe",
                        "https://www.linkedin.com/jobs/view/987654321/?trk=jobs_biz_prem_srch",
                        "About the job",
                        "Lead platform engineering and Kubernetes operations.",
                    ]
                ),
                "source_origin": "manual_page",
            },
        )
        self.assertEqual(imported["source_kind"], "linkedin")
        self.assertEqual(len(imported["imported"]), 1)
        imported_without_url = self._post(
            "/vacancies/import-linkedin-text",
            {
                "content_text": "\n".join(
                    [
                        "Jobgether",
                        "Chief Technology Officer (CTO)",
                        "Greater Madrid Metropolitan Area · 3 days ago · 95 applicants",
                        "No response insights available yet",
                        "Remote",
                        "Easy Apply",
                        "Save",
                        "About the job",
                        "Lead AI/RAG product and enterprise platform architecture.",
                    ]
                ),
                "source_origin": "manual_page",
            },
        )
        self.assertEqual(imported_without_url["source_kind"], "linkedin")
        self.assertEqual(len(imported_without_url["imported"]), 1)
        self.assertIn("imported without external_vacancy_id", imported_without_url["warnings"][0])

        generic_imported = self._post(
            "/vacancies/import-text",
            {
                "content_text": "\n".join(
                    [
                        "Title: Engineering Director",
                        "Company: Generic Cloud",
                        "Location: Remote EU",
                        "URL: https://jobs.example.com/engineering-director",
                    ]
                ),
                "source_origin": "manual_text",
            },
        )
        self.assertEqual(generic_imported["source_kind"], "generic_text")
        self.assertEqual(len(generic_imported["imported"]), 1)

        hh_imported = self._post(
            "/vacancies/import-hh-ru-text",
            {
                "content_text": "\n".join(
                    [
                        "[Технический директор](https://hh.ru/vacancy/133645519?hhtmFrom=vacancy_search_list)",
                        "Опыт более 6 лет",
                        "Можно удалённо",
                        "[ООО Инфотек](https://hh.ru/employer/4194553?hhtmFrom=vacancy_search_list)",
                        "Москва, р-н Марьина Роща",
                        "Разработка технической стратегии, выбор технологий...",
                    ]
                ),
                "source_origin": "search_results",
            },
        )
        self.assertEqual(hh_imported["source_kind"], "hh_ru")
        self.assertEqual(len(hh_imported["imported"]), 1)

        response = self.api.dispatch(
            method="POST",
            raw_path="/vacancies/import-linkedin-text",
            body=json.dumps({"content_text": "https://www.linkedin.com/jobs/view/123/"}).encode("utf-8"),
        )
        self.assertEqual(response.status, 400)
        self.assertFalse(response.payload["ok"])
        self.assertEqual(response.payload["error"]["type"], "ValueError")

        malformed = self.api.dispatch(method="POST", raw_path="/candidates", body=b"{")
        self.assertEqual(malformed.status, 400)
        self.assertFalse(malformed.payload["ok"])
        self.assertEqual(malformed.payload["error"]["type"], "JSONDecodeError")

        invalid_limit = self.api.dispatch(method="GET", raw_path="/system/observability?limit=bad")
        self.assertEqual(invalid_limit.status, 400)
        self.assertEqual(invalid_limit.payload["error"]["type"], "ValueError")

    def test_api_rejects_local_file_source_ingestion_by_default(self) -> None:
        candidate = self._post("/candidates", {"display_name": "File API Candidate"})
        self._post("/candidates/active", {"candidate_id": candidate["candidate_id"]})

        response = self.api.dispatch(
            method="POST",
            raw_path="/candidates/sources/file",
            body=json.dumps({"source_kind": "resume", "file_path": "/tmp/resume.txt"}).encode("utf-8"),
        )
        self.assertEqual(response.status, 400)
        self.assertFalse(response.payload["ok"])
        self.assertEqual(response.payload["error"]["type"], "PermissionError")

    def test_api_rejects_invalid_candidate_and_vacancy_inputs(self) -> None:
        blank_candidate = self.api.dispatch(
            method="POST",
            raw_path="/candidates",
            body=json.dumps({"display_name": "  "}).encode("utf-8"),
        )
        self.assertEqual(blank_candidate.status, 400)
        self.assertEqual(blank_candidate.payload["error"]["type"], "ValueError")

        candidate = self._post("/candidates", {"display_name": "Validation Candidate"})
        self._post("/candidates/active", {"candidate_id": candidate["candidate_id"]})
        empty_batch = self.api.dispatch(
            method="POST",
            raw_path="/vacancies/import-json",
            body=json.dumps({"items": []}).encode("utf-8"),
        )
        self.assertEqual(empty_batch.status, 400)
        self.assertIn("at least one", empty_batch.payload["error"]["message"])

        bad_url = self.api.dispatch(
            method="POST",
            raw_path="/vacancies/import-json",
            body=json.dumps(
                {
                    "items": [
                        {
                            "title": "Head of Engineering",
                            "company_name": "ScaleOps",
                            "source_url": "ftp://example.com/job",
                        }
                    ]
                }
            ).encode("utf-8"),
        )
        self.assertEqual(bad_url.status, 400)
        self.assertIn("http(s)", bad_url.payload["error"]["message"])

    def test_api_rejects_oversized_request_body(self) -> None:
        self.api.close()
        self.api = JobSearchApi(
            runtime_settings=RuntimeSettings(
                db_path=self.root / "data" / "small_limit.sqlite",
                artifact_root=self.root / "data" / "small_limit_artifacts",
                sqlite_config_path=PLAYGROUND_ROOT / "common" / "config" / "sqlite.toml",
                default_locale="en",
                enable_ai_extraction=False,
                api_max_body_bytes=32,
                api_allow_local_file_sources=False,
            ),
            workspace_path=self.workspace_path,
        )
        response = self.api.dispatch(method="POST", raw_path="/candidates", body=b"x" * 33)
        self.assertEqual(response.status, 413)
        self.assertFalse(response.payload["ok"])
        self.assertEqual(response.payload["error"]["type"], "ApiRequestTooLargeError")

    def test_validate_bind_host_rejects_non_loopback_without_unsafe_flag(self) -> None:
        validate_bind_host("127.0.0.1", allow_unsafe_remote_bind=False)
        validate_bind_host("localhost", allow_unsafe_remote_bind=False)
        with self.assertRaisesRegex(ValueError, "loopback"):
            validate_bind_host("0.0.0.0", allow_unsafe_remote_bind=False)
        validate_bind_host("0.0.0.0", allow_unsafe_remote_bind=True)

    def test_validate_runtime_network_safety_rejects_remote_bind_with_local_file_sources(self) -> None:
        validate_runtime_network_safety(allow_unsafe_remote_bind=False, api_allow_local_file_sources=True)
        validate_runtime_network_safety(allow_unsafe_remote_bind=True, api_allow_local_file_sources=False)
        with self.assertRaisesRegex(PermissionError, "local file ingestion"):
            validate_runtime_network_safety(allow_unsafe_remote_bind=True, api_allow_local_file_sources=True)

    def test_http_server_exposes_same_json_envelope_for_skills(self) -> None:
        server_queue: Queue[HTTPServer | BaseException] = Queue()
        stop_queue: Queue[object] = Queue()

        def run_server() -> None:
            thread_api: JobSearchApi | None = None
            try:
                thread_api = JobSearchApi(
                    runtime_settings=RuntimeSettings(
                        db_path=self.root / "data" / "http_job_search.sqlite",
                        artifact_root=self.root / "data" / "http_artifacts",
                        sqlite_config_path=PLAYGROUND_ROOT / "common" / "config" / "sqlite.toml",
                        default_locale="en",
                        enable_ai_extraction=False,
                        api_max_body_bytes=1024 * 1024,
                        api_allow_local_file_sources=False,
                    ),
                    workspace_path=self.root / "config" / "http_workspace.local.toml",
                )
                handler = type("TestJobSearchHttpHandler", (JobSearchHttpHandler,), {"api": thread_api})
                thread_server = HTTPServer(("127.0.0.1", 0), handler)
                server_queue.put(thread_server)
                while stop_queue.empty():
                    thread_server.handle_request()
                thread_server.server_close()
            except BaseException as exc:
                server_queue.put(exc)
            finally:
                if thread_api is not None:
                    thread_api.close()

        thread = Thread(target=run_server, daemon=True)
        thread.start()
        server_or_exc = server_queue.get(timeout=5)
        if isinstance(server_or_exc, PermissionError):
            raise unittest.SkipTest(f"localhost bind is not permitted in this sandbox: {server_or_exc}")
        if isinstance(server_or_exc, BaseException):
            raise server_or_exc
        server = server_or_exc
        try:
            conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            conn.request("GET", "/health")
            health_response = conn.getresponse()
            health_payload = json.loads(health_response.read().decode("utf-8"))
            self.assertEqual(health_response.status, 200)
            self.assertTrue(health_payload["ok"])
            self.assertEqual(health_payload["data"]["status"], "ok")

            body = json.dumps({"display_name": "HTTP Skill Candidate"})
            conn.request("POST", "/candidates", body=body, headers={"Content-Type": "application/json"})
            create_response = conn.getresponse()
            create_payload = json.loads(create_response.read().decode("utf-8"))
            self.assertEqual(create_response.status, 200)
            self.assertTrue(create_payload["ok"])
            self.assertTrue(create_payload["data"]["candidate_id"])

            conn.request(
                "POST",
                "/candidates",
                body=body,
                headers={"Content-Type": "application/json", "Origin": "https://evil.example"},
            )
            rejected_response = conn.getresponse()
            rejected_payload = json.loads(rejected_response.read().decode("utf-8"))
            self.assertEqual(rejected_response.status, 403)
            self.assertFalse(rejected_payload["ok"])
            self.assertEqual(rejected_payload["error"]["type"], "ApiOriginRejectedError")
        finally:
            stop_queue.put(object())
            try:
                conn.request("GET", "/health")
                conn.getresponse().read()
            except OSError:
                pass
            thread.join(timeout=5)

    def _get(self, path: str) -> object:
        response = self.api.dispatch(method="GET", raw_path=path)
        self.assertEqual(response.status, 200, response.payload)
        self.assertTrue(response.payload["ok"])
        return response.payload["data"]

    def _post(self, path: str, payload: dict[str, object]) -> object:
        response = self.api.dispatch(method="POST", raw_path=path, body=json.dumps(payload).encode("utf-8"))
        self.assertEqual(response.status, 200, response.payload)
        self.assertTrue(response.payload["ok"])
        return response.payload["data"]


if __name__ == "__main__":
    unittest.main()
