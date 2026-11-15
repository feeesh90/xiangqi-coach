import json
from pathlib import Path

import pytest

from app import LEVELS, COACH_MS
from ui_preferences import UIPreferences


@pytest.mark.parametrize("saved,expected", [
    (None, {"level": "中级", "coach": "标准"}),
    ({"level": "高手", "coach": "深入"}, {"level": "高手", "coach": "深入"}),
    ({"level": "obsolete", "coach": "极速"}, {"level": "中级", "coach": "极速"}),
    ({"level": "休闲", "coach": []}, {"level": "休闲", "coach": "标准"}),
    ({"level": True}, {"level": "中级", "coach": "标准"}),
    ([], {"level": "中级", "coach": "标准"}),
])
def test_load_validates_each_field_without_rewriting(tmp_path, saved, expected):
    path = tmp_path/"prefs.json"
    if saved is not None:
        path.write_text(json.dumps(saved), encoding="utf-8")
    before = path.read_bytes() if path.exists() else None
    assert UIPreferences(LEVELS, COACH_MS, path).load() == expected
    assert (path.read_bytes() if path.exists() else None) == before


@pytest.mark.parametrize("contents", [b"{broken", b"\xff"])
def test_broken_json_and_encoding_fall_back(tmp_path, contents):
    path = tmp_path/"prefs.json"
    path.write_bytes(contents)
    assert UIPreferences(LEVELS, COACH_MS, path).load() == {"level": "中级", "coach": "标准"}
    assert path.read_bytes() == contents


def test_atomic_save_failure_keeps_previous_file_and_other_configs(tmp_path, monkeypatch):
    path = tmp_path/"prefs.json"
    store = UIPreferences(LEVELS, COACH_MS, path)
    store.save("休闲", "极速")
    previous = path.read_bytes()
    for name in ("api_settings.json", "engine_settings.json"):
        (tmp_path/name).write_bytes(b"unchanged")
    def fail(*args):
        raise PermissionError("read only")
    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(PermissionError):
        store.save("大师", "深入")
    assert path.read_bytes() == previous
    assert not list(tmp_path.glob("*.tmp"))
    assert all((tmp_path/name).read_bytes() == b"unchanged"
               for name in ("api_settings.json", "engine_settings.json"))


def test_read_failure_and_invalid_choice_are_safe(tmp_path, monkeypatch):
    store = UIPreferences(LEVELS, COACH_MS, tmp_path/"prefs.json")
    def fail(*args, **kwargs):
        raise PermissionError("denied")
    monkeypatch.setattr(Path, "read_text", fail)
    assert store.load() == {"level": "中级", "coach": "标准"}
    with pytest.raises(ValueError):
        store.save("未知", "标准")
    assert not store.path.exists()
