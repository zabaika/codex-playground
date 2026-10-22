import os
from pathlib import Path
import shutil
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_installer_stages_self_contained_runtime(tmp_path):
    source = tmp_path / "source"
    project = source / "telegram_agent_bot"
    (project / "scripts").mkdir(parents=True)
    (project / "config").mkdir()
    installer = REPO_ROOT / "telegram_agent_bot/scripts/install_launch_agent.sh"
    # Fail before executing legacy installers, which have no isolated mode.
    assert "--staging-root" in installer.read_text()
    shutil.copy2(installer, project / "scripts/install_launch_agent.sh")
    for name in ("telegram_agent_bridge.py", "telegram_agent_worker.py"):
        shutil.copy2(REPO_ROOT / "telegram_agent_bot" / name, project / name)
    for name in ("runtime.local.toml", "runtime.example.toml"):
        (project / "config" / name).write_text("[telegram]\n")
    for name in ("common", "telegram_shared"):
        shutil.copytree(
            REPO_ROOT / name, source / name,
            ignore=shutil.ignore_patterns("__pycache__", "tests"),
        )
    staged = tmp_path / "staged"
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    env["PATH"] = os.defpath
    subprocess.run(
        ["/bin/bash", str(project / "scripts/install_launch_agent.sh"),
         "--staging-root", str(staged)],
        check=True, capture_output=True, text=True, env=env, timeout=10,
    )
    runtime = staged / "telegram_agent_bot_service"
    for name in ("telegram_agent_bridge.py", "telegram_agent_worker.py"):
        result = subprocess.run(
            [sys.executable, "-I", "-c",
             "import runpy,sys; from pathlib import Path; "
             "sys.path.insert(0,str(Path.cwd())); "
             f"sys.argv=['{name}','--help']; runpy.run_path('{name}',run_name='__main__')"],
            cwd=runtime, capture_output=True, text=True,
            env=env, timeout=10,
        )
        assert result.returncode == 0, result.stderr
