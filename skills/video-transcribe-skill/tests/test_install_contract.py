from __future__ import annotations

from pathlib import Path
import base64
import csv
import hashlib
import io
import json
import os
import re
import runpy
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import unittest
from unittest.mock import patch
import zipfile


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
        self.assertTrue(vendor["bgutil_plugin"]["source_ref"])
        self.assertIs(type(vendor["bgutil_plugin"]["local_modifications"]), bool)
        self.assertEqual(
            vendor["bgutil_plugin"]["metadata_files"],
            ["third_party/bgutil-plugin/upstream-subpath.txt"],
        )

        self.assertEqual(vendor["bgutil_provider"]["source_repo"], "Brainicism/bgutil-ytdlp-pot-provider")
        self.assertEqual(vendor["bgutil_provider"]["source_ref"],
                         vendor["bgutil_plugin"]["source_ref"])
        self.assertIs(type(vendor["bgutil_provider"]["local_modifications"]), bool)
        self.assertEqual(
            vendor["bgutil_provider"]["metadata_files"],
            [
                "third_party/bgutil-provider/package.json",
                "third_party/bgutil-provider/package-lock.json",
                "third_party/bgutil-provider/deno.lock",
            ],
        )

        self.assertEqual(vendor["youtube_transcript_api_wheels"]["primary_package"], "youtube-transcript-api")
        wheels = vendor["youtube_transcript_api_wheels"]
        inventory = {p["name"]: p["version"] for p in wheels["packages"]}
        self.assertEqual(wheels["primary_version"], inventory[wheels["primary_package"]])
        self.assertNotIn("python", wheels)
        self.assertRegex(wheels["build_python"], r"^\d+\.\d+$")
        self.assertIs(type(wheels["local_modifications"]), bool)
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
                    "PYTHON_BIN": sys.executable,
                },
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

            dest_dir = codex_home / "skills" / "video-transcribe-skill"
            venv_python = dest_dir / "vendor" / "youtube-transcript-api" / "venv" / "bin" / "python"
            self.assertTrue(venv_python.is_file(), f"Missing bootstrapped venv python: {venv_python}")
            self.assertTrue((dest_dir / "vendor" / "bgutil-plugin").is_dir())
            self.assertTrue((dest_dir / "vendor" / "bgutil-provider").is_dir())
            self.assertTrue((dest_dir / "config/runtime.local.toml").is_symlink())
            support_check = subprocess.run(
                [str(venv_python), "-I", str(dest_dir / "common/python_runtime.py"),
                 "--python", str(venv_python), "--path-only"],
                cwd=tmpdir, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(support_check.returncode, 0, support_check.stderr)
            reported = Path(support_check.stdout.strip())
            self.assertEqual(reported.parent.resolve(), venv_python.parent.resolve())
            self.assertTrue(reported.samefile(venv_python))
            expected = {p["name"]: p["version"] for p in load_toml(
                MANIFEST_PATH,
            )["vendor"]["youtube_transcript_api_wheels"]["packages"]}

            import_check = subprocess.run(
                [
                    str(venv_python),
                    "-c",
                    (
                        "import importlib.util, importlib.metadata as m, sys; "
                        f"assert sys.version_info[:2] == {sys.version_info[:2]!r}; "
                        f"expected = {expected!r}; "
                        "assert {n: m.version(n) for n in expected} == expected; "
                        "assert importlib.util.find_spec('youtube_transcript_api'); "
                        "assert importlib.util.find_spec('requests')"
                    ),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(import_check.returncode, 0, msg=import_check.stderr or import_check.stdout)
            transcript_check = subprocess.run(
                [str(venv_python), "-c", '''
import requests
from youtube_transcript_api._transcripts import Transcript
session = requests.Session()
response = requests.Response()
response.status_code = 200
response.encoding = "utf-8"
response._content = '<transcript><text start="1.5" dur="2">Привет &amp; ARM</text></transcript>'.encode()
session.get = lambda url: response
transcript = Transcript(session, "fixture", "https://example.invalid/transcript", "Russian", "ru", False, [])
assert transcript.fetch().to_raw_data() == [{"text": "Привет & ARM", "start": 1.5, "duration": 2.0}]
'''],
                capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(transcript_check.returncode, 0, transcript_check.stderr)

    def test_post_creation_failure_restores_previous_runtime_and_data(self) -> None:
        """Fail a real final-path venv preparation before any package install."""
        common = SKILL_ROOT.parents[1] / "common"
        with patch.dict(os.environ, {"PYTHON_RUNTIME_COMMON_ROOT": str(common)}), \
                patch.object(sys, "path", list(sys.path)):
            worker = runpy.run_path(str(SKILL_ROOT / "scripts/install_runtime.py"))
        binding = worker["probe_python"](sys.executable)
        archives = worker["checked_archives"](load_toml(MANIFEST_PATH), artifact_root())
        config = RUNTIME_LOCAL_PATH if RUNTIME_LOCAL_PATH.exists() else SKILL_ROOT / "config/runtime.example.toml"
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "installed"
            (destination / "data").mkdir(parents=True)
            marker = destination / "previous-runtime"
            marker.write_text("working")
            data = destination / "data/operator-data"
            data.write_bytes(b"preserve original data")
            actual_run = subprocess.run
            created = []

            def fail_package_install(command, **kwargs):
                if "venv" in command:
                    result = actual_run(command, **kwargs)
                    created.append((destination / "vendor/youtube-transcript-api/venv/pyvenv.cfg").is_file())
                    return result
                raise subprocess.CalledProcessError(1, command)

            with patch("subprocess.run", side_effect=fail_package_install):
                with self.assertRaises(subprocess.CalledProcessError):
                    worker["install"](destination, archives, binding, config,
                                      hashlib.sha256(config.read_bytes()).hexdigest())
            self.assertEqual(created, [True])
            self.assertEqual(marker.read_text(), "working")
            self.assertEqual(data.read_bytes(), b"preserve original data")
            self.assertFalse((destination / "vendor").exists())
            self.assertFalse(list(destination.parent.glob(".video-previous-*")))

    def test_missing_archive_preserves_existing_installation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            installed = root / "codex/skills/video-transcribe-skill"
            installed.mkdir(parents=True)
            marker = installed / "existing-runtime.txt"
            marker.write_text("preserve until preflight passes")
            result = subprocess.run(
                ["bash", str(INSTALL_SCRIPT)],
                capture_output=True,
                text=True,
                timeout=30,
                env={
                    "CODEX_HOME": str(root / "codex"),
                    "CODEX_AUDITED_ARTIFACTS_ROOT": str(artifacts),
                    "PYTHON_BIN": sys.executable,
                },
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Missing audited archive", result.stderr)
            self.assertEqual(marker.read_text(), "preserve until preflight passes")

    def test_incompatible_locked_metadata_preserves_existing_runtime(self) -> None:
        """Only a private fixture changes; reviewed input bytes stay intact."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "repo/skills/video-transcribe-skill"
            source.parent.mkdir(parents=True)
            shutil.copytree(SKILL_ROOT, source, ignore=shutil.ignore_patterns(
                "vendor", "common", "__pycache__", "runtime.local.toml", "data",
            ))
            shutil.copytree(SKILL_ROOT.parents[1] / "common", root / "repo/common",
                            ignore=shutil.ignore_patterns("tests", "__pycache__"))
            manifest = load_toml(MANIFEST_PATH)
            artifacts = root / "artifacts"
            for entry in manifest["vendor"].values():
                destination = artifacts / entry["archive"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(artifact_root() / entry["archive"], destination)
            entry = manifest["vendor"]["youtube_transcript_api_wheels"]
            archive_path = artifacts / entry["archive"]
            wheels = root / "wheels"
            wheels.mkdir()
            with tarfile.open(archive_path) as archive:
                archive.extractall(wheels, filter="data")
            wheel = next(wheels.rglob(
                entry["primary_package"].replace("-", "_") + "-*.whl",
            ))
            previous_hash = hashlib.sha256(wheel.read_bytes()).hexdigest()
            with zipfile.ZipFile(wheel) as archive:
                files = {name: archive.read(name) for name in archive.namelist()}
            metadata = next(n for n in files if n.endswith(".dist-info/METADATA"))
            files[metadata] = re.sub(
                rb"(?m)^Requires-Python: .*", b"Requires-Python: <3",
                files[metadata],
            )
            record = metadata.removesuffix("METADATA") + "RECORD"
            rows = io.StringIO()
            writer = csv.writer(rows, lineterminator="\n")
            for name, content in files.items():
                if name != record:
                    digest = base64.urlsafe_b64encode(
                        hashlib.sha256(content).digest(),
                    ).decode().rstrip("=")
                    writer.writerow([name, "sha256=" + digest, len(content)])
            writer.writerow([record, "", ""])
            files[record] = rows.getvalue().encode()
            with zipfile.ZipFile(wheel, "w") as archive:
                for name, content in files.items():
                    archive.writestr(name, content)
            lock = source / "third_party/youtube-transcript-api/requirements.lock"
            lock.write_text(lock.read_text().replace(
                previous_hash, hashlib.sha256(wheel.read_bytes()).hexdigest(),
            ))
            with tarfile.open(archive_path, "w:gz") as archive:
                for path in sorted(wheels.iterdir()):
                    archive.add(path, arcname=path.name)
            fixture_manifest = source / "config/vendor-manifest.toml"
            fixture_manifest.write_text(fixture_manifest.read_text().replace(
                entry["sha256"], hashlib.sha256(archive_path.read_bytes()).hexdigest(),
            ))
            (source / "config/runtime.local.toml").write_text(
                '[artifacts]\nroot_dir = ' + json.dumps(str(artifacts))
                + '\n[paths]\nproject_root = '
                + json.dumps(str(root / "repo")) + '\n',
            )
            codex = root / "codex"
            installed = codex / "skills/video-transcribe-skill"
            installed.mkdir(parents=True)
            marker = installed / "existing-runtime.txt"
            marker.write_text("working runtime")
            result = subprocess.run(
                ["/bin/bash", str(source / "install-local.sh")],
                env={"HOME": str(Path.home()), "PATH": "/usr/bin:/bin",
                     "CODEX_HOME": str(codex), "PYTHON_BIN": sys.executable},
                capture_output=True, text=True, timeout=90,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("requirements preflight", result.stderr)
            self.assertTrue(marker.is_file(), result.stderr)
            self.assertEqual(marker.read_text(), "working runtime")


if __name__ == "__main__":
    unittest.main()
