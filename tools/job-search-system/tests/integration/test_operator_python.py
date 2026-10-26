"""Source the operator layer without touching a workspace or production DB."""
from pathlib import Path
import os
import subprocess
import sys


LIB = Path(__file__).resolve().parents[2] / "scripts/operator/lib.sh"


def test_operator_uses_local_interpreter_choice_from_any_cwd(tmp_path):
    config = tmp_path / "runtime.toml"
    config.write_text('[python]\nexecutable = "' + sys.executable + '"\n')
    env = {k: v for k, v in os.environ.items() if k != "PYTHON_BIN"}
    env.update(CONFIG_PATH=str(config), PATH=os.defpath)
    result = subprocess.run(
        ["/bin/bash", "-c", 'source "$1"; printf "%s" "$PYTHON_BIN"',
         "fixture", str(LIB)], cwd=tmp_path, env=env,
        text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == sys.executable


def test_invalid_explicit_operator_python_aborts_before_command(tmp_path):
    config = tmp_path / "runtime.toml"
    config.write_text('[python]\nexecutable = "' + sys.executable + '"\n')
    marker = tmp_path / "command-started"
    env = {**os.environ, "CONFIG_PATH": str(config),
           "PYTHON_BIN": str(tmp_path / "missing-python")}
    result = subprocess.run(
        ["/bin/bash", "-c", 'source "$1"; printf started > "$2"',
         "fixture", str(LIB), str(marker)], cwd=tmp_path, env=env,
        text=True, capture_output=True, timeout=30,
    )
    assert result.returncode != 0
    assert not marker.exists()
