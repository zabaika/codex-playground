"""Atomic replacement, durability ordering and failure cleanup."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import stat
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common import json_io


def test_private_json_round_trip_and_formatting(tmp_path):
    path = tmp_path / "nested/state.json"
    payload = {"z": "EN TRÁMITE", "a": [True, None]}
    json_io.write_json_atomic(path, payload, sort_keys=True)
    assert path.read_text() == json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert list(path.parent.iterdir()) == [path]


def test_serialization_failure_does_not_touch_the_filesystem(tmp_path):
    path = tmp_path / "absent/state.json"
    with pytest.raises(TypeError):
        json_io.write_json_atomic(path, {"invalid": object()})
    assert not path.parent.exists()


@pytest.mark.parametrize("failure", ["file_fsync", "replace"])
def test_failure_before_replacement_preserves_target_and_cleans_temp(monkeypatch, tmp_path, failure):
    path = tmp_path / "state.json"
    path.write_text('{"old":true}\n')
    def fail(*_):
        raise OSError("Synthetic IO failure")
    monkeypatch.setattr(json_io.os, "fsync" if failure == "file_fsync" else "replace", fail)
    with pytest.raises(OSError, match="Synthetic"):
        json_io.write_json_atomic(path, {"new": True})
    assert path.read_text() == '{"old":true}\n'
    assert list(tmp_path.iterdir()) == [path]


def test_file_fsync_precedes_replace_and_directory_fsync(monkeypatch, tmp_path):
    path = tmp_path / "state.json"
    events = []
    original_fsync, original_replace = os.fsync, os.replace
    def fsync(fd):
        events.append("directory_fsync" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file_fsync")
        original_fsync(fd)
    def replace(source, target):
        assert Path(source).parent == Path(target).parent
        events.append("replace")
        original_replace(source, target)
    monkeypatch.setattr(json_io.os, "fsync", fsync)
    monkeypatch.setattr(json_io.os, "replace", replace)
    json_io.write_json_atomic(path, {"new": True})
    assert events == ["file_fsync", "replace", "directory_fsync"]


def test_directory_fsync_failure_reports_error_after_replacement(monkeypatch, tmp_path):
    path = tmp_path / "state.json"
    original_fsync = os.fsync
    def fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("Synthetic directory sync failure")
        original_fsync(fd)
    monkeypatch.setattr(json_io.os, "fsync", fsync)
    with pytest.raises(OSError, match="directory sync"):
        json_io.write_json_atomic(path, {"new": True})
    assert json.loads(path.read_text()) == {"new": True}
    assert list(tmp_path.iterdir()) == [path]


def test_parallel_writers_use_distinct_temporary_files(monkeypatch, tmp_path):
    path = tmp_path / "audit.json"
    sources = []
    original_replace = os.replace
    def replace(source, target):
        sources.append(source)
        original_replace(source, target)
    monkeypatch.setattr(json_io.os, "replace", replace)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda value: json_io.write_json_atomic(path, {"value": value}), range(8)))
    assert len(set(sources)) == 8
    assert json.loads(path.read_text())["value"] in range(8)
    assert list(tmp_path.iterdir()) == [path]


def test_text_artifact_round_trip_is_private_and_keeps_exact_newlines(tmp_path):
    function = getattr(json_io, "write_text_atomic", None)
    assert callable(function), "Markdown and JSON must share the atomic UTF-8 replacement owner"
    path = tmp_path / "nested/report.md"
    content = "# Отчёт\n\nУсловия: 1250 EUR.\n"
    function(path, content)
    assert path.read_bytes() == content.encode("utf-8")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert list(path.parent.iterdir()) == [path]


def test_text_replace_failure_keeps_existing_artifact_and_cleans_temp(monkeypatch, tmp_path):
    function = getattr(json_io, "write_text_atomic", None)
    assert callable(function)
    path = tmp_path / "report.md"
    path.write_text("Прежний отчёт")
    def fail(*args):
        raise OSError("replacement fault")
    monkeypatch.setattr(json_io.os, "replace", fail)
    with pytest.raises(OSError, match="replacement fault"):
        function(path, "Новый отчёт")
    assert path.read_text() == "Прежний отчёт" and list(tmp_path.iterdir()) == [path]


def test_json_adapter_delegates_serialized_utf8_to_the_same_writer(monkeypatch, tmp_path):
    function = getattr(json_io, "write_text_atomic", None)
    assert callable(function)
    captured = []
    monkeypatch.setattr(json_io, "write_text_atomic", lambda path, text: captured.append((path, text)))
    path = tmp_path / "state.json"
    json_io.write_json_atomic(path, {"z": "Á", "a": 1}, sort_keys=True)
    assert captured == [(path, json.dumps({"z": "Á", "a": 1}, ensure_ascii=False, indent=2, sort_keys=True) + "\n")]
