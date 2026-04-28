from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path
from xml.sax.saxutils import escape

from .config import AutoUpdateConfig, DEFAULT_CONFIG_PATH


def _launchctl_domain() -> str:
    return f'gui/{os.getuid()}'


def service_root_for(_: AutoUpdateConfig) -> Path:
    return Path.home() / 'Library' / 'Application Support' / 'kb_index_service'


def service_runner_path_for(auto_update: AutoUpdateConfig) -> Path:
    return service_root_for(auto_update) / 'scripts' / 'run_kb_index_auto_update.sh'


def service_runtime_config_path_for(auto_update: AutoUpdateConfig) -> Path:
    return service_root_for(auto_update) / 'config' / 'runtime.local.toml'


def service_launchd_log_dir_for(auto_update: AutoUpdateConfig) -> Path:
    return service_root_for(auto_update) / 'data' / 'launchd'


def render_runner_script(auto_update: AutoUpdateConfig) -> str:
    service_root = service_root_for(auto_update)
    runtime_config = service_runtime_config_path_for(auto_update)
    log_dir = service_launchd_log_dir_for(auto_update)
    startup_log = log_dir / 'auto_update.startup.log'
    return f"""#!/usr/bin/env bash
set -euo pipefail

ROOT="$HOME/Library/Application Support/kb_index_service"
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
STARTUP_LOG="{startup_log}"

resolve_python_bin() {{
  local candidates=(
    "/usr/local/bin/python3"
    "/opt/homebrew/bin/python3"
    "/usr/bin/python3"
  )
  local py
  for py in "${{candidates[@]}}"; do
    [[ -x "$py" ]] || continue
    printf '%s\\n' "$py"
    return 0
  done

  echo "No usable python interpreter found." >&2
  exit 1
}}

PYTHON_BIN="$(resolve_python_bin)"

cd "$ROOT"
printf '[%s] starting kb-index auto-update from %s\\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$ROOT" >> "$STARTUP_LOG"
exec "$PYTHON_BIN" -c "import sys; sys.path.insert(0, {str(service_root / 'src')!r}); from kb_index.cli import main; raise SystemExit(main())" update --config-path "{runtime_config}"
"""


def render_launchd_plist(auto_update: AutoUpdateConfig) -> str:
    runner_path = service_runner_path_for(auto_update)
    program_arguments = [str(runner_path)]
    xml_arguments = '\n'.join(
        f'      <string>{escape(argument)}</string>' for argument in program_arguments
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{escape(auto_update.launchd_label)}</string>
  <key>ProgramArguments</key>
  <array>
{xml_arguments}
  </array>
  <key>RunAtLoad</key>
  <{str(auto_update.run_on_load).lower()}/>
  <key>StartInterval</key>
  <integer>{auto_update.interval_minutes * 60}</integer>
  <key>WorkingDirectory</key>
  <string>{escape(str(service_root_for(auto_update)))}</string>
  <key>StandardOutPath</key>
  <string>{escape(str(service_launchd_log_dir_for(auto_update) / 'auto_update.stdout.log'))}</string>
  <key>StandardErrorPath</key>
  <string>{escape(str(service_launchd_log_dir_for(auto_update) / 'auto_update.stderr.log'))}</string>
  <key>ProcessType</key>
  <string>Background</string>
</dict>
</plist>
"""


def _sync_runtime_copy(auto_update: AutoUpdateConfig, config_path: Path, project_root: Path) -> dict[str, Path]:
    service_root = service_root_for(auto_update)
    src_root = project_root / 'src' / 'kb_index'
    dst_src_root = service_root / 'src' / 'kb_index'
    config_dir = service_root / 'config'
    scripts_dir = service_root / 'scripts'
    log_dir = service_launchd_log_dir_for(auto_update)

    config_dir.mkdir(parents=True, exist_ok=True)
    scripts_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    shutil.copytree(src_root, dst_src_root, dirs_exist_ok=True)
    shutil.copy2(config_path, config_dir / 'runtime.local.toml')

    example_config = project_root / 'config' / 'runtime.example.toml'
    if example_config.exists():
        shutil.copy2(example_config, config_dir / 'runtime.example.toml')

    runner_path = service_runner_path_for(auto_update)
    runner_path.write_text(render_runner_script(auto_update), encoding='utf-8')
    runner_path.chmod(0o755)

    return {
        'service_root': service_root,
        'runner_path': runner_path,
        'runtime_config_path': service_runtime_config_path_for(auto_update),
        'log_dir': log_dir,
    }


def install_launchd_auto_update(
    auto_update: AutoUpdateConfig,
    config_path: Path,
    project_root: Path,
) -> dict[str, object]:
    if platform.system() != 'Darwin':
        raise RuntimeError('launchd auto-update is only supported on macOS')
    if auto_update.mode != 'launchd':
        raise RuntimeError(f"Unsupported auto-update mode: {auto_update.mode}")
    if not auto_update.enabled:
        raise RuntimeError('Auto-update is disabled in runtime.local.toml')

    runtime_paths = _sync_runtime_copy(auto_update, config_path, project_root)
    auto_update.plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_content = render_launchd_plist(auto_update)
    auto_update.plist_path.write_text(plist_content, encoding='utf-8')

    domain = _launchctl_domain()
    subprocess.run(
        ['launchctl', 'bootout', domain, str(auto_update.plist_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ['launchctl', 'bootstrap', domain, str(auto_update.plist_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    if auto_update.run_on_load:
        subprocess.run(
            ['launchctl', 'kickstart', '-k', f'{domain}/{auto_update.launchd_label}'],
            check=True,
            capture_output=True,
            text=True,
        )

    return {
        'enabled': auto_update.enabled,
        'mode': auto_update.mode,
        'label': auto_update.launchd_label,
        'plist_path': str(auto_update.plist_path),
        'service_root': str(runtime_paths['service_root']),
        'runner_path': str(runtime_paths['runner_path']),
        'runtime_config_path': str(runtime_paths['runtime_config_path']),
        'log_dir': str(runtime_paths['log_dir']),
        'loaded': True,
    }


def uninstall_launchd_auto_update(auto_update: AutoUpdateConfig) -> dict[str, object]:
    if platform.system() != 'Darwin':
        raise RuntimeError('launchd auto-update is only supported on macOS')
    if auto_update.mode != 'launchd':
        raise RuntimeError(f"Unsupported auto-update mode: {auto_update.mode}")

    domain = _launchctl_domain()
    bootout = subprocess.run(
        ['launchctl', 'bootout', domain, str(auto_update.plist_path)],
        check=False,
        capture_output=True,
        text=True,
    )

    removed_plist = False
    if auto_update.plist_path.exists():
        auto_update.plist_path.unlink()
        removed_plist = True

    service_root = service_root_for(auto_update)
    removed_service_root = False
    if service_root.exists():
        shutil.rmtree(service_root)
        removed_service_root = True

    return {
        'label': auto_update.launchd_label,
        'plist_path': str(auto_update.plist_path),
        'service_root': str(service_root),
        'removed_plist': removed_plist,
        'removed_service_root': removed_service_root,
        'bootout_returncode': bootout.returncode,
    }


def get_launchd_auto_update_status(auto_update: AutoUpdateConfig) -> dict[str, object]:
    if auto_update.mode != 'launchd':
        raise RuntimeError(f"Unsupported auto-update mode: {auto_update.mode}")

    service_root = service_root_for(auto_update)
    runner_path = service_runner_path_for(auto_update)
    runtime_config_path = service_runtime_config_path_for(auto_update)
    log_dir = service_launchd_log_dir_for(auto_update)
    status = {
        'enabled': auto_update.enabled,
        'mode': auto_update.mode,
        'label': auto_update.launchd_label,
        'plist_path': str(auto_update.plist_path),
        'plist_exists': auto_update.plist_path.exists(),
        'service_root': str(service_root),
        'service_root_exists': service_root.exists(),
        'runner_path': str(runner_path),
        'runner_exists': runner_path.exists(),
        'runtime_config_path': str(runtime_config_path),
        'runtime_config_exists': runtime_config_path.exists(),
        'log_dir': str(log_dir),
        'loaded': False,
    }
    if platform.system() != 'Darwin':
        status['platform_supported'] = False
        return status

    result = subprocess.run(
        ['launchctl', 'list', auto_update.launchd_label],
        check=False,
        capture_output=True,
        text=True,
    )
    status['platform_supported'] = True
    status['loaded'] = result.returncode == 0
    status['launchctl_returncode'] = result.returncode
    if result.stdout.strip():
        status['launchctl_stdout'] = result.stdout.strip()
    if result.stderr.strip():
        status['launchctl_stderr'] = result.stderr.strip()
    return status


def resolve_config_path(config_path: str | None) -> Path:
    return Path(config_path) if config_path else DEFAULT_CONFIG_PATH
