"""Private installer worker; run through install-local.sh and its outer TTL."""
from __future__ import annotations

import hashlib
from email.parser import BytesParser
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile

SKILL_ROOT = Path(__file__).resolve().parents[1]
COMMON_ROOT = Path(os.environ["PYTHON_RUNTIME_COMMON_ROOT"])
sys.path.insert(0, str(COMMON_ROOT.parent))
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from common.json_io import write_json_atomic
from common.python_environment import recoverable_backup
from common.python_runtime import (
    RuntimeSelectionError, probe_python, resolve_python, validate_requirements,
)
from runtime_paths import resolve_project_root


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_toml(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def checked_archives(manifest: dict, artifacts: Path) -> list[tuple[Path, str]]:
    entries = manifest.get("vendor", {})
    if not entries:
        raise RuntimeSelectionError("Missing vendor entries")
    archives = []
    for name, entry in entries.items():
        if not all(entry.get(key) for key in
                   ("archive", "install_subdir", "sha256", "metadata_files")):
            raise RuntimeSelectionError(f"Incomplete vendor metadata: {name}")
        for filename in entry["metadata_files"]:
            if not (SKILL_ROOT / filename).is_file():
                raise RuntimeSelectionError("Missing vendor metadata file")
        archive = artifacts / entry["archive"]
        if not archive.is_file():
            raise RuntimeSelectionError(f"Missing audited archive: {name}")
        if sha256_file(archive) != entry["sha256"]:
            raise RuntimeSelectionError(f"Audited archive hash mismatch: {name}")
        archives.append((archive, entry["install_subdir"]))
    return archives


def extract(archive: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as handle:
        for member in handle.getmembers():
            if not (target / member.name).resolve().is_relative_to(target.resolve()):
                raise RuntimeSelectionError("Archive member escapes vendor root")
        handle.extractall(target)


def check_wheel_inventory(wheels: Path, entry: dict) -> None:
    actual = {}
    for path in wheels.rglob("*.whl"):
        with zipfile.ZipFile(path) as archive:
            names = [n for n in archive.namelist()
                     if n.endswith(".dist-info/METADATA")]
            if len(names) != 1:
                raise RuntimeSelectionError("Invalid wheel metadata layout")
            metadata = BytesParser().parsebytes(archive.read(names[0]))
            name = metadata["Name"]
            if not name or name in actual or not metadata["Version"]:
                raise RuntimeSelectionError("Invalid wheel package inventory")
            actual[name] = metadata["Version"]
    expected = {p["name"]: p["version"] for p in entry["packages"]}
    if actual != expected:
        raise RuntimeSelectionError("Vendor manifest differs from wheel inventory")
    if actual.get(entry["primary_package"]) != entry["primary_version"]:
        raise RuntimeSelectionError("Vendor primary version differs from wheel")


def install(
    destination: Path, archives: list[tuple[Path, str]], binding: dict,
    config_path: Path, config_digest: str,
) -> None:
    local = SKILL_ROOT / "config/runtime.local.toml"
    previous_local = destination / "config/runtime.local.toml"
    if previous_local.exists() and (
        not local.exists() or previous_local.read_bytes() != local.read_bytes()
    ):
        raise RuntimeSelectionError("Installed local config differs from repo owner")
    if sha256_file(config_path) != config_digest:
        raise RuntimeSelectionError("Runtime config changed during preflight")
    if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
        raise RuntimeSelectionError("Destination is not an owned runtime directory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with recoverable_backup(destination.parent, prefix=".video-previous-") as previous:
        had_previous = destination.exists()
        had_data = (destination / "data").exists()
        try:
            if had_previous:
                destination.rename(previous)
            shutil.copytree(SKILL_ROOT, destination, ignore=shutil.ignore_patterns(
                "vendor", "common", "__pycache__", "data", "runtime.local.toml",
            ))
            shutil.copytree(COMMON_ROOT, destination / "common",
                            ignore=shutil.ignore_patterns("tests", "__pycache__"))
            if local.exists():
                (destination / "config/runtime.local.toml").symlink_to(local)
            for archive, subdir in archives:
                target = destination / subdir
                if not target.resolve().is_relative_to(destination.resolve()):
                    raise RuntimeSelectionError("Invalid vendor install_subdir")
                extract(archive, target)
            api = destination / "vendor/youtube-transcript-api"
            python = api / "venv/bin/python"
            lock = (destination /
                    "third_party/youtube-transcript-api/requirements.lock")
            commands = [
                [binding["executable"], "-I", "-m", "venv", str(api / "venv")],
                [str(python), "-I", "-m", "pip", "install", "--quiet", "--no-index",
                 "--find-links", str(api / "wheels"), "--require-hashes",
                 "-r", str(lock)],
                [str(python), "-I", "-m", "pip", "check"],
                [str(python), "-I", "-c",
                 "import youtube_transcript_api, requests, charset_normalizer"],
            ]
            for command in commands:
                subprocess.run(command, check=True, capture_output=True, text=True)
            observed = probe_python(str(python))
            if observed["base_executable"] != binding["base_executable"]:
                raise RuntimeSelectionError("Installed venv base identity changed")
            write_json_atomic(destination / ".python-runtime.json", binding | {
                "environment": observed,
            })
            if (previous / "data").exists():
                (previous / "data").rename(destination / "data")
        except BaseException:
            if previous.exists():
                if (had_data and not (previous / "data").exists()
                        and (destination / "data").exists()):
                    (destination / "data").rename(previous / "data")
                if destination.exists():
                    shutil.rmtree(destination)
                previous.rename(destination)
            elif not had_previous and destination.exists():
                shutil.rmtree(destination)
            raise


def main() -> int:
    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    destination = codex_home.expanduser() / "skills/video-transcribe-skill"
    if SKILL_ROOT == destination.resolve():
        raise RuntimeSelectionError("Run the installer from its repository owner")
    local = SKILL_ROOT / "config/runtime.local.toml"
    config_path = local if local.exists() else SKILL_ROOT / "config/runtime.example.toml"
    if local.is_symlink() and not local.exists():
        raise RuntimeSelectionError("Broken repository local config link")
    config = load_toml(config_path)
    config_digest = sha256_file(config_path)
    root = resolve_project_root(config=config, skill_dir=SKILL_ROOT)
    if root is None:
        raise RuntimeSelectionError("Configure a project root for install staging")
    scratch = root / "scratch/video-transcribe-skill-install"
    scratch.mkdir(parents=True, exist_ok=True, mode=0o700)
    artifacts_raw = os.environ.get("CODEX_AUDITED_ARTIFACTS_ROOT", "").strip()
    artifacts_raw = artifacts_raw or config.get("artifacts", {}).get("root_dir", "")
    if not artifacts_raw:
        raise RuntimeSelectionError("Configure the audited artifacts root")
    artifacts = Path(artifacts_raw).expanduser()
    manifest_path = SKILL_ROOT / "config/vendor-manifest.toml"
    manifest = load_toml(manifest_path)
    archives = checked_archives(manifest, artifacts)
    lock = SKILL_ROOT / "third_party/youtube-transcript-api/requirements.lock"
    entry = manifest["vendor"]["youtube_transcript_api_wheels"]
    with tempfile.TemporaryDirectory(dir=scratch) as directory:
        wheels = Path(directory) / "wheels"
        extract(artifacts / entry["archive"], wheels)
        check_wheel_inventory(wheels, entry)

        def validate(binding: dict) -> None:
            validate_requirements(
                binding, lock, scratch, wheels=wheels, require_hashes=True,
            )

        binding = resolve_python(runtime_config=config_path, validator=validate)
        binding["dependency_inputs"]["vendor-manifest.toml"] = sha256_file(manifest_path)
        install(destination, archives, binding, config_path, config_digest)
    print(f"Installed video-transcribe-skill to {destination}")
    print("Restart Codex to refresh skill discovery when needed.")
    return 0


if __name__ == "__main__":
    def interrupted(signum, frame):
        raise InterruptedError("Runtime installation interrupted")

    signal.signal(signal.SIGTERM, interrupted)
    try:
        raise SystemExit(main())
    except (RuntimeSelectionError, OSError, ValueError, tarfile.TarError,
            subprocess.CalledProcessError) as exc:
        message = (str(exc) if isinstance(exc, RuntimeSelectionError)
                   else f"Runtime installation failed ({type(exc).__name__})")
        print(message, file=sys.stderr)
        raise SystemExit(1)
