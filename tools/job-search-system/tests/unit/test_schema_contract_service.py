from __future__ import annotations

from pathlib import Path
import unittest

from job_search.application.services.schema_contract_service import SchemaContractService


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class SchemaContractServiceTest(unittest.TestCase):
    def test_manifest_lists_existing_versioned_runtime_schemas(self) -> None:
        manifest = SchemaContractService(schemas_dir=PROJECT_ROOT / "schemas").manifest()

        self.assertEqual(manifest["schema_contract_version"], "2026-06-01.2")
        schema_names = {item["name"] for item in manifest["schemas"]}
        self.assertEqual(
            schema_names,
            {
                "candidate_source_registration",
                "candidate_profile_draft",
                "candidate_profile_confirm_request",
                "artifact_acceptance",
                "external_action_approval",
            },
        )


if __name__ == "__main__":
    unittest.main()
