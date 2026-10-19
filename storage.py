"""JSON 数据文件读写：原子写、损坏恢复、应用设置。"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime

from models import Assignment, Course

# 打包为 exe（PyInstaller onefile）时 __file__ 位于临时解压目录，
# 数据文件必须跟随 exe 本体，否则每次运行数据都会丢失。
if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(_BASE_DIR, "data.json")
VERSION = 1


def _load_settings(raw: dict) -> dict:
    """解析应用设置，仅保留合法的 semester_start（ISO 日期字符串或 None）。"""
    settings = raw.get("settings") or {}
    result = {"semester_start": None}
    s = settings.get("semester_start")
    if s:
        try:
            result["semester_start"] = date.fromisoformat(str(s)).isoformat()
        except ValueError:
            pass
    return result


def load_data() -> tuple[list[Course], list[Assignment], dict]:
    """读取数据。文件不存在或损坏时返回空数据（损坏文件备份为 .bak-<时间戳>）。"""
    if not os.path.exists(DATA_FILE):
        return [], [], _load_settings({})
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        courses = [Course.from_dict(d) for d in raw.get("courses") or []]
        assignments = [Assignment.from_dict(d) for d in raw.get("assignments") or []]
        return courses, assignments, _load_settings(raw)
    except (json.JSONDecodeError, AttributeError, TypeError, ValueError, OSError):
        bak = DATA_FILE + f".bak-{datetime.now():%Y%m%d-%H%M%S}"
        try:
            os.replace(DATA_FILE, bak)
        except OSError:
            pass
        return [], [], _load_settings({})


def rotate_backup(keep: int = 5) -> str | None:
    """启动时把 data.json 复制到 backups/ 子目录，保留最近 keep 份。

    返回备份文件路径；无数据文件或失败返回 None（不抛异常）。
    """
    import shutil
    if not os.path.exists(DATA_FILE):
        return None
    backup_dir = os.path.join(os.path.dirname(DATA_FILE), "backups")
    try:
        os.makedirs(backup_dir, exist_ok=True)
        dest = os.path.join(
            backup_dir, f"data-{datetime.now():%Y%m%d-%H%M%S-%f}.json")
        shutil.copy2(DATA_FILE, dest)
        olds = sorted(
            (os.path.join(backup_dir, f) for f in os.listdir(backup_dir)
             if f.startswith("data-") and f.endswith(".json")),
            key=os.path.getmtime)
        for old in olds[:-keep] if keep > 0 else olds:
            try:
                os.remove(old)
            except OSError:
                pass
        return dest
    except OSError:
        return None


def save_data(courses: list[Course], assignments: list[Assignment],
              settings: dict | None = None) -> None:
    """原子写入：先写临时文件再替换。"""
    payload = {
        "version": VERSION,
        "settings": settings or {"semester_start": None},
        "courses": [c.to_dict() for c in courses],
        "assignments": [a.to_dict() for a in assignments],
    }
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DATA_FILE)
