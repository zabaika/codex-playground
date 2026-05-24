#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export VIDEO_TRANSCRIBE_SKILL_SOURCE_DIR="${script_dir}"

python3 - <<'PY'
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tomllib


REQUIRED_PYTHON = (3, 14)


def fail(message: str) -> "None":
    raise SystemExit(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_toml(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def resolve_artifact_root(skill_root: Path) -> Path:
    override = os.environ.get("CODEX_AUDITED_ARTIFACTS_ROOT", "").strip()
    if override:
        return Path(override).expanduser()

    runtime_local = skill_root / "config" / "runtime.local.toml"
    runtime_example = skill_root / "config" / "runtime.example.toml"
    for candidate in (runtime_local, runtime_example):
        if not candidate.exists():
            continue
        root_dir = load_toml(candidate).get("artifacts", {}).get("root_dir", "").strip()
        if root_dir:
            return Path(root_dir).expanduser()

    fail(
        "Missing audited artifacts root. Set CODEX_AUDITED_ARTIFACTS_ROOT or configure "
        "[artifacts].root_dir in config/runtime.local.toml."
    )


def safe_extract_tar(archive_path: Path, target_dir: Path) -> None:
    with tarfile.open(archive_path, "r:gz") as tf:
        members = tf.getmembers()
        for member in members:
            member_path = (target_dir / member.name).resolve()
            target_root = target_dir.resolve()
            if not str(member_path).startswith(str(target_root)):
                fail(f"Unsafe archive member path in {archive_path}: {member.name}")
        tf.extractall(target_dir)


def main() -> int:
    if sys.version_info[:2] != REQUIRED_PYTHON:
        fail(
            "video-transcribe-skill install requires python3.14 to match the audited local wheels."
        )

    skill_root = Path(os.environ["VIDEO_TRANSCRIBE_SKILL_SOURCE_DIR"]).resolve()
    manifest_path = skill_root / "config" / "vendor-manifest.toml"
    if not manifest_path.exists():
        fail(f"Missing vendor manifest: {manifest_path}")

    manifest = load_toml(manifest_path)
    vendor_entries = manifest.get("vendor", {})
    if not vendor_entries:
        fail(f"Vendor manifest has no [vendor.*] entries: {manifest_path}")

    artifact_root = resolve_artifact_root(skill_root)
    if not artifact_root.is_dir():
        fail(f"Audited artifacts root does not exist: {artifact_root}")

    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
    dest_root = codex_home / "skills"
    dest_dir = dest_root / "video-transcribe-skill"
    dest_root.mkdir(parents=True, exist_ok=True)
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    shutil.copytree(skill_root, dest_dir)

    for entry_name, entry in vendor_entries.items():
        archive_rel = entry.get("archive", "").strip()
        install_subdir = entry.get("install_subdir", "").strip()
        expected_sha = entry.get("sha256", "").strip()
        if not archive_rel or not install_subdir or not expected_sha:
            fail(f"Vendor manifest entry {entry_name} is incomplete in {manifest_path}")

        metadata_files = entry.get("metadata_files", [])
        if not metadata_files:
            fail(f"Vendor manifest entry {entry_name} is missing metadata_files in {manifest_path}")
        for metadata_rel in metadata_files:
            metadata_path = skill_root / metadata_rel
            if not metadata_path.is_file():
                fail(f"Missing metadata file for {entry_name}: {metadata_path}")

        archive_path = (artifact_root / archive_rel).resolve()
        if not archive_path.is_file():
            fail(f"Missing audited archive for {entry_name}: {archive_path}")

        actual_sha = sha256_file(archive_path)
        if actual_sha != expected_sha:
            fail(
                f"SHA256 mismatch for {archive_path}. Expected {expected_sha}, got {actual_sha}."
            )

        target_dir = (dest_dir / install_subdir).resolve()
        if target_dir.exists():
            shutil.rmtree(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        safe_extract_tar(archive_path, target_dir)

    yta_root = dest_dir / "vendor" / "youtube-transcript-api"
    yta_venv = yta_root / "venv"
    yta_wheels = yta_root / "wheels"
    yta_requirements = dest_dir / "third_party" / "youtube-transcript-api" / "requirements.lock"

    if not yta_requirements.is_file():
        fail(f"Missing locked requirements file: {yta_requirements}")
    if not yta_wheels.is_dir():
        fail(f"Missing extracted wheels directory: {yta_wheels}")

    subprocess.run([sys.executable, "-m", "venv", str(yta_venv)], check=True)
    venv_python = yta_venv / "bin" / "python"
    subprocess.run(
        [
            str(venv_python),
            "-m",
            "pip",
            "install",
            "--quiet",
            "--no-index",
            "--find-links",
            str(yta_wheels),
            "--require-hashes",
            "-r",
            str(yta_requirements),
        ],
        check=True,
    )

    print(f"Installed video-transcribe-skill to {dest_dir}")
    print("Restart Codex to pick up skill changes if the current session does not see them yet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
PY
