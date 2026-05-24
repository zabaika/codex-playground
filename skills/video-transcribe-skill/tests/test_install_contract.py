from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import tomllib
import unittest


SKILL_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SCRIPT = SKILL_ROOT / "install-local.sh"
MANIFEST_PATH = SKILL_ROOT / "config" / "vendor-manifest.toml"
RUNTIME_LOCAL_PATH = SKILL_ROOT / "config" / "runtime.local.toml"


def load_toml(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def artifact_root() -> Path:
    return Path(load_toml(RUNTIME_LOCAL_PATH)["artifacts"]["root_dir"]).expanduser()


class VideoTranscribeInstallContractTests(unittest.TestCase):
    def test_manifest_declares_provenance_complete_audited_inputs(self) -> None:
        manifest = load_toml(MANIFEST_PATH)
        vendor = manifest["vendor"]

        self.assertEqual(vendor["bgutil_plugin"]["source_repo"], "Brainicism/bgutil-ytdlp-pot-provider")
        self.assertEqual(vendor["bgutil_plugin"]["source_ref"], "1.3.1")
        self.assertFalse(vendor["bgutil_plugin"]["local_modifications"])
        self.assertEqual(
            vendor["bgutil_plugin"]["metadata_files"],
            ["third_party/bgutil-plugin/upstream-subpath.txt"],
        )

        self.assertEqual(vendor["bgutil_provider"]["source_repo"], "Brainicism/bgutil-ytdlp-pot-provider")
        self.assertEqual(vendor["bgutil_provider"]["source_ref"], "1.3.1")
        self.assertFalse(vendor["bgutil_provider"]["local_modifications"])
        self.assertEqual(
            vendor["bgutil_provider"]["metadata_files"],
            [
                "third_party/bgutil-provider/package.json",
                "third_party/bgutil-provider/package-lock.json",
                "third_party/bgutil-provider/deno.lock",
            ],
        )

        self.assertEqual(vendor["youtube_transcript_api_wheels"]["primary_package"], "youtube-transcript-api")
        self.assertEqual(vendor["youtube_transcript_api_wheels"]["primary_version"], "1.2.4")
        self.assertEqual(vendor["youtube_transcript_api_wheels"]["python"], "3.14")
        self.assertFalse(vendor["youtube_transcript_api_wheels"]["local_modifications"])
        self.assertEqual(
            vendor["youtube_transcript_api_wheels"]["metadata_files"],
            ["third_party/youtube-transcript-api/requirements.lock"],
        )

    def test_local_audited_archives_exist_for_all_manifest_entries(self) -> None:
        manifest = load_toml(MANIFEST_PATH)
        root = artifact_root()
        self.assertTrue(root.is_dir(), f"Missing audited artifacts root: {root}")

        for entry in manifest["vendor"].values():
            archive_path = root / entry["archive"]
            self.assertTrue(archive_path.is_file(), f"Missing audited archive: {archive_path}")
            self.assertTrue(entry["sha256"])
            for metadata_rel in entry["metadata_files"]:
                self.assertTrue((SKILL_ROOT / metadata_rel).is_file(), f"Missing metadata file: {metadata_rel}")

    def test_runtime_files_do_not_reference_legacy_skill_name(self) -> None:
        checked_suffixes = {".sh", ".py", ".toml", ".md"}
        legacy_hits: list[Path] = []
        for path in SKILL_ROOT.rglob("*"):
            if not path.is_file() or path.suffix not in checked_suffixes:
                continue
            if path.is_relative_to(SKILL_ROOT / "tests"):
                continue
            text = path.read_text(encoding="utf-8")
            if "youtube-transcribe-skill" in text:
                legacy_hits.append(path)
        self.assertEqual(
            legacy_hits,
            [],
            f"Found legacy youtube skill references in: {legacy_hits}",
        )

    def test_install_local_bootstraps_runtime_from_audited_vendor_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir) / ".codex"
            artifacts_root = artifact_root()
            result = subprocess.run(
                ["bash", str(INSTALL_SCRIPT)],
                check=False,
                capture_output=True,
                text=True,
                env={
                    "CODEX_HOME": str(codex_home),
                    "CODEX_AUDITED_ARTIFACTS_ROOT": str(artifacts_root),
                },
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

            dest_dir = codex_home / "skills" / "video-transcribe-skill"
            venv_python = dest_dir / "vendor" / "youtube-transcript-api" / "venv" / "bin" / "python"
            self.assertTrue(venv_python.is_file(), f"Missing bootstrapped venv python: {venv_python}")
            self.assertTrue((dest_dir / "vendor" / "bgutil-plugin").is_dir())
            self.assertTrue((dest_dir / "vendor" / "bgutil-provider").is_dir())

            import_check = subprocess.run(
                [
                    str(venv_python),
                    "-c",
                    (
                        "import importlib.util; "
                        "assert importlib.util.find_spec('youtube_transcript_api'); "
                        "assert importlib.util.find_spec('requests')"
                    ),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(import_check.returncode, 0, msg=import_check.stderr or import_check.stdout)


if __name__ == "__main__":
    unittest.main()
