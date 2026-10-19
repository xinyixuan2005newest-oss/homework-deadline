"""正式单元测试套件：python -m unittest tests -v

覆盖：存储往返与损坏恢复、备份轮换、模型迁移与周次判定、
提醒级别判定、月视图配色。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date, datetime, timedelta

import models
import storage
from models import (DEFAULT_REMIND_BEFORE, KIND_NAMES, MAX_WEEK,
                    PRIORITY_NAMES, WEEK_PARITY_NAMES, Assignment, Course,
                    semester_week)
from monthview import MonthView
from reminder import ReminderService


def make_course(**kw) -> Course:
    base = dict(id="c-1", name="高等数学", teacher="张三", location="A101",
                weekday=1, start="08:00", end="09:40",
                week_start=1, week_end=MAX_WEEK, week_parity="all")
    base.update(kw)
    return Course(**base)


def make_assign(**kw) -> Assignment:
    base = dict(id="a-1", title="习题 3-1", course_id="c-1",
                due=datetime(2026, 10, 10, 23, 59),
                kind="hw", priority="mid",
                remind_before=[1440, 60])
    base.update(kw)
    return Assignment(**base)


class StorageTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self._orig = storage.DATA_FILE
        storage.DATA_FILE = os.path.join(self._tmp, "data.json")

    def tearDown(self) -> None:
        storage.DATA_FILE = self._orig

    def test_round_trip(self) -> None:
        c = make_course()
        a = make_assign(kind="exam", priority="high", remind_before=[4320, 30],
                        reminded=["b1440"])
        storage.save_data([c], [a])
        courses, assigns, settings = storage.load_data()
        self.assertEqual(len(courses), 1)
        self.assertEqual(len(assigns), 1)
        rc, ra = courses[0], assigns[0]
        self.assertEqual((rc.name, rc.week_start, rc.week_end, rc.week_parity),
                         ("高等数学", 1, MAX_WEEK, "all"))
        self.assertEqual((ra.kind, ra.priority), ("exam", "high"))
        self.assertEqual(ra.remind_before, [30, 4320])
        self.assertEqual(ra.reminded, ["b1440"])
        self.assertEqual(set(settings), {"semester_start"})

    def test_corrupted_file_recovered(self) -> None:
        with open(storage.DATA_FILE, "w", encoding="utf-8") as f:
            f.write("{broken json!!!")
        courses, assigns, _ = storage.load_data()
        self.assertEqual((courses, assigns), ([], []))
        baks = [f for f in os.listdir(self._tmp) if f.startswith("data.json.bak")]
        self.assertEqual(len(baks), 1)

    def test_rotate_backup_keeps_latest(self) -> None:
        for _ in range(6):
            storage.save_data([], [])
            storage.rotate_backup(keep=3)
        bdir = os.path.join(self._tmp, "backups")
        files = os.listdir(bdir)
        self.assertEqual(len(files), 3)
        with open(os.path.join(bdir, files[-1]), encoding="utf-8") as f:
            self.assertIn("assignments", f.read())

    def test_settings_validation(self) -> None:
        storage.save_data([], [])
        with open(storage.DATA_FILE, encoding="utf-8") as f:
            raw = f.read()
        with open(storage.DATA_FILE, "w", encoding="utf-8") as f:
            f.write(raw.replace('"settings"', '"ignored"'))
        _, _, s1 = storage.load_data()
        self.assertIsNone(s1["semester_start"])
        with open(storage.DATA_FILE, "w", encoding="utf-8") as f:
            f.write('{"settings": {"semester_start": "not-a-date"}}')
        _, _, s2 = storage.load_data()
        self.assertIsNone(s2["semester_start"])


class ModelsTest(unittest.TestCase):
    def test_semester_week(self) -> None:
        monday = date(2026, 9, 7)
        self.assertEqual(semester_week(monday, date(2026, 9, 7)), 1)
        self.assertEqual(semester_week(monday, date(2026, 9, 13)), 1)  # 周日
        self.assertEqual(semester_week(monday, date(2026, 9, 8)), 1)   # 周二
        self.assertEqual(semester_week(monday, date(2026, 9, 14)), 2)
        self.assertEqual(semester_week(monday, date(2026, 9, 6)), 1)   # 开学前钳位为 1

    def test_course_covers_parity(self) -> None:
        c = make_course(week_start=3, week_end=9, week_parity="odd")
        covers = c.covers
        self.assertTrue(covers(3) and covers(5) and covers(9))
        self.assertFalse(covers(2) or covers(4) or covers(10))
        c2 = make_course(week_parity="even")
        self.assertTrue(c2.covers(2) and c2.covers(MAX_WEEK))
        self.assertFalse(c2.covers(3))
        c3 = make_course()
        self.assertTrue(c3.covers(1) and c3.covers(MAX_WEEK))

    def test_assignment_legacy_and_invalid_migration(self) -> None:
        a = Assignment.from_dict({"id": "x", "title": "旧", "due": "2026-10-10T10:00",
                                  "reminded": ["24h", "1h", "b30"],
                                  "kind": "bogus", "priority": "bogus"})
        self.assertEqual(a.reminded, ["b1440", "b60", "b30"])
        self.assertEqual(a.kind, "hw")
        self.assertEqual(a.priority, "mid")
        self.assertEqual(a.remind_before, DEFAULT_REMIND_BEFORE)

    def test_assignment_names(self) -> None:
        self.assertEqual(make_assign(kind="exam").kind_name(), "考试")
        self.assertEqual(make_assign(priority="low").priority_name(), "低")
        self.assertEqual(set(KIND_NAMES), {"hw", "exam", "project"})
        self.assertEqual(set(PRIORITY_NAMES), {"high", "mid", "low"})

    def test_course_from_dict_defaults(self) -> None:
        c = Course.from_dict({"id": "c-9", "name": "旧课", "weekday": 2,
                              "start": "10:00", "end": "11:40"})
        self.assertEqual((c.week_start, c.week_end, c.week_parity),
                         (1, MAX_WEEK, "all"))


class ReminderLevelTest(unittest.TestCase):
    level = staticmethod(ReminderService._due_level)

    def test_outside_window(self) -> None:
        now = datetime(2026, 10, 1, 10, 0)
        a = make_assign(due=now + timedelta(days=3))
        self.assertIsNone(self.level(a, now))

    def test_most_urgent_window_wins(self) -> None:
        now = datetime(2026, 10, 1, 10, 0)
        a = make_assign(due=now + timedelta(minutes=40))   # 命中 60 窗口
        self.assertEqual(self.level(a, now), "b60")
        a2 = make_assign(due=now + timedelta(days=2), remind_before=[4320, 1440])
        self.assertEqual(self.level(a2, now), "b4320")     # 2880 分：只命中 4320
        self.assertEqual(self.level(
            make_assign(due=now + timedelta(days=1, minutes=-1),
                        remind_before=[4320, 1440]), now), "b1440")

    def test_overdue(self) -> None:
        now = datetime(2026, 10, 1, 10, 0)
        self.assertEqual(
            self.level(make_assign(due=now - timedelta(minutes=1)), now),
            models.OVERDUE_LEVEL)

    def test_no_repeat_no_downgrade(self) -> None:
        now = datetime(2026, 10, 1, 10, 0)
        a = make_assign(due=now + timedelta(minutes=40))
        a.reminded = ["b60"]
        self.assertIsNone(self.level(a, now))
        a2 = make_assign(due=now + timedelta(days=2), remind_before=[4320, 1440])
        a2.reminded = ["b4320"]     # 已报过 3 天级，剩余 2880 分时不得重复报 b4320
        self.assertIsNone(self.level(a2, now))
        a3 = make_assign(due=now + timedelta(minutes=40))
        a3.reminded = ["b1440"]     # 报过宽松级后再报更紧急级，允许
        self.assertEqual(self.level(a3, now), "b60")

    def test_empty_remind_before(self) -> None:
        now = datetime(2026, 10, 1, 10, 0)
        a = make_assign(remind_before=[], due=now + timedelta(minutes=40))
        self.assertIsNone(self.level(a, now))


class MonthViewColorTest(unittest.TestCase):
    def test_colors(self) -> None:
        now = datetime.now()   # _color_of 使用真实时钟，用例须相对其构造
        cases = [
            (make_assign(due=now + timedelta(days=5), status="done"), "#95A5A6"),
            (make_assign(due=now - timedelta(hours=1)), "#C0392B"),
            (make_assign(due=now + timedelta(hours=5)), "#B9770E"),
            (make_assign(due=now + timedelta(days=5)), "#2C3E50"),
        ]
        for a, expect in cases:
            self.assertEqual(MonthView._color_of(a), expect)


if __name__ == "__main__":
    unittest.main(verbosity=2)
