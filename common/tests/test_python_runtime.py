"""Policy fixtures describe boundaries, not the current production minor."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from common import python_runtime as runtime


@pytest.fixture
def policy(tmp_path, monkeypatch):
    monkeypatch.delenv("PYTHON_BIN", raising=False)
    path = tmp_path / "policy.toml"
    path.write_text('''[python]
minimum_version = [3, 11]
implementation = "cpython"
architecture = "arm64"
free_threaded = false
''')
    return path


def descriptor(path, *, version=None, architecture="arm64", **changes):
    data = {
        "executable": str(path), "base_executable": str(path),
        "base_prefix": "/fixture/base", "prefix": "/fixture/base",
        "version": version or [3, 50, 0], "implementation": "cpython",
        "architecture": architecture, "platform": "fixture-arm64",
        "free_threaded": False,
    }
    return data | changes


def fake_environment(monkeypatch, paths):
    monkeypatch.setattr(runtime, "discover_candidates", lambda: [
        ("homebrew" if i == 0 else "path", str(p))
        for i, p in enumerate(paths)
    ])
    monkeypatch.setattr(runtime, "probe_python", lambda p: descriptor(p))


def test_explicit_override_wins_over_local_and_discovery(policy, tmp_path, monkeypatch):
    config = tmp_path / "runtime.toml"
    config.write_text('[python]\nexecutable = "local-python"\n')
    fake_environment(monkeypatch, ["discovered"])
    binding = runtime.resolve_python(
        explicit="explicit path with spaces", runtime_config=config,
        policy_path=policy,
    )
    assert binding["executable"] == "explicit path with spaces"
    assert binding["source"] == "explicit"


@pytest.mark.parametrize("source", ["explicit", "local"])
def test_invalid_requested_interpreter_never_falls_back(
    source, policy, tmp_path, monkeypatch,
):
    fake_environment(monkeypatch, ["good"])
    monkeypatch.setattr(runtime, "probe_python", lambda p: descriptor(
        p, architecture="x86_64" if p == "bad" else "arm64",
    ))
    kwargs = {"policy_path": policy}
    if source == "explicit":
        kwargs["explicit"] = "bad"
    else:
        config = tmp_path / "local.toml"
        config.write_text('[python]\nexecutable = "bad"\n')
        kwargs["runtime_config"] = config
    with pytest.raises(runtime.RuntimeSelectionError, match="architecture"):
        runtime.resolve_python(**kwargs)


def test_discovery_skips_incompatible_candidates(policy, monkeypatch):
    fake_environment(monkeypatch, ["intel", "native"])
    monkeypatch.setattr(runtime, "probe_python", lambda p: descriptor(
        p, architecture="x86_64" if p == "intel" else "arm64",
    ))
    assert runtime.resolve_python(policy_path=policy)["executable"] == "native"


@pytest.mark.parametrize("changes,reason", [
    ({"version": [3, 10, 9]}, "minimum"),
    ({"version": [4, 0, 0]}, "major"),
    ({"free_threaded": True}, "free_threaded"),
    ({"implementation": "pypy"}, "implementation"),
])
def test_policy_boundaries(policy, monkeypatch, changes, reason):
    fake_environment(monkeypatch, ["candidate"])
    monkeypatch.setattr(runtime, "probe_python", lambda p: descriptor(p, **changes))
    with pytest.raises(runtime.RuntimeSelectionError, match=reason):
        runtime.resolve_python(policy_path=policy)


def test_future_minor_descriptor_passes_basic_policy_only(policy, monkeypatch):
    fake_environment(monkeypatch, ["future"])
    binding = runtime.resolve_python(policy_path=policy)
    assert binding["version"] == [3, 50, 0]
    assert binding["source"] == "homebrew"
    assert binding["policy_digest"]


def test_component_validator_controls_candidate_compatibility(policy, monkeypatch):
    fake_environment(monkeypatch, ["incompatible", "compatible"])

    def validate(binding):
        if binding["executable"] == "incompatible":
            raise runtime.RuntimeSelectionError("locked dependency metadata")

    selected = runtime.resolve_python(policy_path=policy, validator=validate)
    assert selected["executable"] == "compatible"
    with pytest.raises(runtime.RuntimeSelectionError, match="locked dependency"):
        runtime.resolve_python(
            explicit="incompatible", policy_path=policy, validator=validate,
        )


def test_missing_or_invalid_policy_is_not_replaced_by_defaults(tmp_path):
    with pytest.raises(runtime.RuntimeSelectionError):
        runtime.resolve_python(policy_path=tmp_path / "missing.toml")
    p = tmp_path / "bad.toml"
    p.write_text('[python]\nminimum_version = "latest"\n')
    with pytest.raises(runtime.RuntimeSelectionError):
        runtime.resolve_python(policy_path=p)


def test_public_pip_checks_project_requires_python_without_installing(tmp_path):
    binding = runtime.probe_python(sys.executable)
    major, minor, _ = binding["version"]
    runtime.check_requires_python(binding, f">={major}.{minor}", tmp_path)
    with pytest.raises(runtime.RuntimeSelectionError, match="Requires-Python"):
        runtime.check_requires_python(
            binding, f">={major}.{minor + 1}", tmp_path,
        )
    assert not list(tmp_path.iterdir())


def test_venv_probe_preserves_environment_executable():
    binding = runtime.probe_python(sys.executable)
    assert Path(binding["executable"]) == Path(sys.executable)
    assert binding["prefix"] == sys.prefix
    assert binding["base_prefix"] == sys.base_prefix


def test_real_cli_records_observations_with_no_ambient_pythonpath(tmp_path):
    record = tmp_path / "binding.json"
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    result = subprocess.run(
        [sys.executable, str(Path(runtime.__file__)), "--python", sys.executable,
         "--record", str(record), "--path-only"],
        text=True, capture_output=True, env=env, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == sys.executable
    binding = json.loads(record.read_text())
    assert binding["version"] == list(sys.version_info[:3])
    assert binding["source"] == "explicit"
    assert record.stat().st_mode & 0o077 == 0


def test_invalid_project_metadata_cannot_be_ignored_by_pip(tmp_path):
    with pytest.raises(runtime.RuntimeSelectionError, match="Requires-Python"):
        runtime.check_requires_python(
            runtime.probe_python(sys.executable), "not-a-specifier", tmp_path,
        )


def test_shell_bootstrap_works_without_homebrew_on_path(tmp_path):
    helper = Path(runtime.__file__).with_suffix(".sh")
    result = subprocess.run(
        ["/bin/bash", "-c",
         'source "$1"; python_runtime_select --python "$2" --path-only',
         "fixture", str(helper), sys.executable],
        env={"PATH": os.defpath, "HOME": str(Path.home())},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == sys.executable


def test_failed_cli_keeps_existing_binding(tmp_path):
    record = tmp_path / "binding.json"
    record.write_text('{"existing": true}')
    result = subprocess.run(
        [sys.executable, str(Path(runtime.__file__)), "--python",
         str(tmp_path / "missing-python"), "--record", str(record)],
        text=True, capture_output=True, timeout=30,
    )
    assert result.returncode != 0
    assert json.loads(record.read_text()) == {"existing": True}


def test_offline_requirements_preflight_checks_real_hashes(tmp_path):
    import hashlib
    import zipfile

    wheel = tmp_path / "fixture_dependency-0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        prefix = "fixture_dependency-0.dist-info"
        archive.writestr(prefix + "/METADATA", (
            "Metadata-Version: 2.1\nName: fixture-dependency\nVersion: 0\n"
        ))
        archive.writestr(prefix + "/WHEEL", (
            "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        ))
        archive.writestr(prefix + "/RECORD", "")
    lock = tmp_path / "requirements.lock"
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    lock.write_text(f"fixture-dependency==0 --hash=sha256:{digest}\n")
    binding = runtime.probe_python(sys.executable)
    runtime.validate_requirements(
        binding, lock, tmp_path, wheels=tmp_path, require_hashes=True,
    )
    lock.write_text("fixture-dependency==0 --hash=sha256:" + "0" * 64 + "\n")
    with pytest.raises(runtime.RuntimeSelectionError, match="requirements"):
        runtime.validate_requirements(
            binding, lock, tmp_path, wheels=tmp_path, require_hashes=True,
        )


def test_env_override_and_local_precedence(policy, tmp_path, monkeypatch):
    fake_environment(monkeypatch, ["discovered"])
    config = tmp_path / "local.toml"
    config.write_text('[python]\nexecutable = "local"\n')
    monkeypatch.setenv("PYTHON_BIN", "environment")
    assert runtime.resolve_python(
        runtime_config=config, policy_path=policy,
    )["executable"] == "environment"
    monkeypatch.setenv("PYTHON_BIN", "")
    assert runtime.resolve_python(
        runtime_config=config, policy_path=policy,
    )["source"] == "local"


def test_invalid_local_value_is_rejected_before_probing(
    policy, tmp_path, monkeypatch,
):
    config = tmp_path / "local.toml"
    config.write_text('[python]\nexecutable = 1\n')
    monkeypatch.setattr(runtime, "probe_python", lambda p: pytest.fail("probe"))
    with pytest.raises(runtime.RuntimeSelectionError, match="executable"):
        runtime.resolve_python(runtime_config=config, policy_path=policy)


def test_actual_cli_validates_pyproject_without_build_backend(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nrequires-python = ">=3"\n')
    record = tmp_path / "binding.json"
    command = [sys.executable, str(Path(runtime.__file__)), "--python",
               sys.executable, "--pyproject", str(pyproject),
               "--record", str(record)]
    good = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert good.returncode == 0, good.stderr
    previous = record.read_bytes()
    pyproject.write_text('[project]\nrequires-python = "<3"\n')
    bad = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert bad.returncode != 0
    assert "Requires-Python" in bad.stderr
    assert record.read_bytes() == previous


def test_shell_bootstrap_ignores_ambient_sitecustomize(tmp_path):
    poison = tmp_path / "sitecustomize.py"
    marker = tmp_path / "executed"
    poison.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    script = Path(runtime.__file__).with_suffix(".sh")
    result = subprocess.run(
        ["/bin/bash", "-c", 'source "$1"; python_runtime_select --python "$2"',
         "fixture", str(script), sys.executable], cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(tmp_path)},
        text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert not marker.exists()


def test_cli_records_all_dependency_inputs(tmp_path):
    metadata = tmp_path / "pyproject.toml"
    metadata.write_text('[project]\nrequires-python = ">=3"\n')
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("# intentionally empty fixture\n")
    result = subprocess.run(
        [sys.executable, "-I", runtime.__file__, "--python", sys.executable,
         "--pyproject", str(metadata), "--requirements", str(requirements),
         "--scratch", str(tmp_path)],
        text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert set(json.loads(result.stdout)["dependency_inputs"]) == {
        "pyproject", "requirements.txt",
    }


def test_incompatible_installed_metadata_is_rejected(tmp_path):
    import zipfile

    venv = tmp_path / "venv"
    subprocess.run([sys.executable, "-I", "-m", "venv", str(venv)], check=True)
    python = str(venv / "bin/python")
    wheel = tmp_path / "fixture_dependency-0-py3-none-any.whl"
    prefix = "fixture_dependency-0.dist-info"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(prefix + "/METADATA", "Metadata-Version: 2.1\n"
                         "Name: fixture-dependency\nVersion: 0\nRequires-Python: <3\n")
        archive.writestr(prefix + "/WHEEL", "Wheel-Version: 1.0\n"
                         "Root-Is-Purelib: true\nTag: py3-none-any\n")
        archive.writestr(prefix + "/RECORD", "")
    subprocess.run([python, "-I", "-m", "pip", "install", "--no-index",
                    "--ignore-requires-python", str(wheel)],
                   check=True, capture_output=True)
    with pytest.raises(runtime.RuntimeSelectionError, match="Requires-Python"):
        runtime.check_installed_python(runtime.probe_python(python), tmp_path)


def test_outer_selection_ttl_cleans_hung_probe_and_child(tmp_path, monkeypatch):
    from dataclasses import replace
    import time

    marker = tmp_path / "orphan-effect"
    child = f"import time;time.sleep(2);open({str(marker)!r},'w').write('orphan')"
    executable = tmp_path / "hung-python"
    executable.write_text(
        f"#!{sys.executable}\nimport subprocess,sys,time,signal\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
        "time.sleep(30)\n",
    )
    executable.chmod(0o755)
    config = runtime.process.load_process_config()
    monkeypatch.setattr(runtime.process, "load_process_config", lambda: replace(
        config, default_run_total_timeout_seconds=0.5,
        default_termination_grace_seconds=0.1, poll_interval_seconds=0.05,
    ))
    assert runtime.main(["--python", str(executable)]) == config.timeout_exit_code
    time.sleep(2.1)
    assert not marker.exists()


@pytest.mark.parametrize("term_signal", ["TERM", "INT"])
def test_external_ttl_stops_nested_resolver_probe_and_descendant(tmp_path, term_signal):
    import time

    group_file = tmp_path / "probe-group.json"
    ready = tmp_path / "descendant-ready"
    effect = tmp_path / "delayed-operation"
    descendant = (
        "import signal,time;from pathlib import Path;"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
        "signal.signal(signal.SIGINT,signal.SIG_IGN);"
        f"Path({str(ready)!r}).touch();time.sleep(5);"
        f"Path({str(effect)!r}).write_text('ran after outer TTL')"
    )
    executable = tmp_path / "hung-python"
    executable.write_text(
        f"#!{sys.executable}\nimport json,os,signal,subprocess,sys,time\n"
        "from pathlib import Path\n"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
        "signal.signal(signal.SIGINT,signal.SIG_IGN)\n"
        f"child=subprocess.Popen([sys.executable,'-I','-c',{descendant!r}])\n"
        f"while not Path({str(ready)!r}).exists():time.sleep(0.01)\n"
        f"Path({str(group_file)!r}).write_text(json.dumps({{'pgid':os.getpgrp()}}))\n"
        "time.sleep(30)\n",
    )
    executable.chmod(0o755)
    group = None
    try:
        result = runtime.ttl_runner.run_with_ttl(
            [sys.executable, "-I", runtime.__file__, "--python", str(executable)],
            timeout_seconds=3, grace_seconds=0.1, poll_interval_seconds=0.02,
            timeout_exit_code=124, term_signal=term_signal, kill_signal="KILL",
            audit_file=None, timeout_reason="fixture_outer_timeout", use_caffeinate=False,
        )
        assert result == 124
        assert group_file.exists(), "Probe and descendant must start before cancellation"
        group = json.loads(group_file.read_text())["pgid"]
        deadline = time.monotonic() + 3
        while runtime.ttl_runner.process_group_exists(group) and time.monotonic() < deadline and not effect.exists():
            time.sleep(0.02)
        assert not effect.exists(), "Owned work continued after the outer timeout returned"
        assert not runtime.ttl_runner.process_group_exists(group)
    finally:
        if group is None and group_file.exists():
            group = json.loads(group_file.read_text())["pgid"]
        if group is not None:
            runtime.ttl_runner.send_signal_to_process_group(group, "KILL")
