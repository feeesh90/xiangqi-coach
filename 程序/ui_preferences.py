"""Validated UI choices, stored separately from engine and API settings."""
import json
from pathlib import Path
import tempfile

from api_client import CONFIG_DIR


class UIPreferences:
    def __init__(self, levels, coaches, path=None):
        self.path = Path(path) if path is not None else CONFIG_DIR / "ui_preferences.json"
        self.levels = frozenset(levels)
        self.coaches = frozenset(coaches)

    def load(self):
        result = {"level": "中级", "coach": "标准"}
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            return result
        if isinstance(saved, dict):
            for key, choices in (("level", self.levels), ("coach", self.coaches)):
                value = saved.get(key)
                if isinstance(value, str) and value in choices:
                    result[key] = value
        return result

    def save(self, level, coach):
        if level not in self.levels or coach not in self.coaches:
            raise ValueError("无效的棋力或指导速度")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                    dir=self.path.parent, prefix=self.path.name+".", suffix=".tmp",
                    delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({"level": level, "coach": coach}, stream,
                          ensure_ascii=False, indent=2)
            temporary.replace(self.path)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
