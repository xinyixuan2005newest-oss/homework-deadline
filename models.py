"""数据模型与通用工具：Course / Assignment、提醒级别、时间格式化。"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

STATUS_PENDING = "pending"
STATUS_DONE = "done"

# 提醒级别：每作业可配置"截止前 N 分钟"偏移；"overdue" 表示已过截止即触发。
# reminded 中存储的级别键："b{分钟数}"（如 "b1440"）或 "overdue"。
OVERDUE_LEVEL = "overdue"
DEFAULT_REMIND_BEFORE = [1440, 60]   # 默认提前 24 小时、1 小时

KIND_NAMES = {"hw": "作业", "exam": "考试", "project": "项目"}
PRIORITY_NAMES = {"high": "高", "mid": "中", "low": "低"}

# 旧版固定级别的兼容映射（v1 早期数据）
_LEGACY_LEVEL_MAP = {"24h": "b1440", "1h": "b60"}

WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

WEEK_PARITY_NAMES = {"all": "每周", "odd": "单周", "even": "双周"}
MAX_WEEK = 30


def semester_week(semester_monday, today) -> int:
    """按开学周一计算 today 所处教学周（1 起）。开学未设置或早于开学时返回 1。"""
    if semester_monday is None:
        return 1
    monday = today - timedelta(days=today.weekday())
    return max(1, (monday - semester_monday).days // 7 + 1)


def next_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def parse_hhmm(s: str) -> time:
    """解析 "HH:MM"，非法格式抛 ValueError。"""
    parts = str(s).strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"时间格式应为 HH:MM：{s!r}")
    try:
        h, m = int(parts[0]), int(parts[1])
    except ValueError:
        raise ValueError(f"时间格式应为 HH:MM：{s!r}") from None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"时间超出范围：{s!r}")
    return time(h, m)


def format_remaining(delta: timedelta) -> str:
    """把剩余/逾期时长格式化为人类可读文本，如 "2天3小时" / "已逾期45分钟"。"""
    total = int(delta.total_seconds())
    overdue = total < 0
    total = abs(total)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days > 0:
        text = f"{days}天{hours}小时"
    elif hours > 0:
        text = f"{hours}小时{minutes}分钟"
    else:
        text = f"{minutes}分钟"
    return f"已逾期{text}" if overdue else f"剩{text}"


@dataclass
class Course:
    id: str
    name: str
    teacher: str = ""
    location: str = ""
    weekday: int = 1          # 1=周一 ... 7=周日
    start: str = "08:00"      # "HH:MM"
    end: str = "09:40"        # "HH:MM"
    week_start: int = 1       # 周次范围（含）
    week_end: int = MAX_WEEK  # 周次范围（含）
    week_parity: str = "all"  # "all" | "odd" | "even"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "teacher": self.teacher,
            "location": self.location,
            "weekday": self.weekday,
            "start": self.start,
            "end": self.end,
            "week_start": self.week_start,
            "week_end": self.week_end,
            "week_parity": self.week_parity,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Course":
        return cls(
            id=str(d.get("id") or next_id("c")),
            name=str(d.get("name") or "未命名课程"),
            teacher=str(d.get("teacher") or ""),
            location=str(d.get("location") or ""),
            weekday=int(d.get("weekday") or 1),
            start=str(d.get("start") or "08:00"),
            end=str(d.get("end") or "09:40"),
            week_start=int(d.get("week_start") or 1),
            week_end=int(d.get("week_end") or MAX_WEEK),
            week_parity=str(d.get("week_parity") or "all"),
        )

    def validate(self) -> str | None:
        """校验字段，合法返回 None，否则返回错误信息。"""
        if not self.name.strip():
            return "课程名不能为空"
        if not 1 <= self.weekday <= 7:
            return "星期必须在周一~周日之间"
        try:
            t0 = parse_hhmm(self.start)
            t1 = parse_hhmm(self.end)
        except ValueError as e:
            return str(e)
        if t0 >= t1:
            return "开始时间必须早于结束时间"
        if not (1 <= self.week_start <= self.week_end <= MAX_WEEK):
            return f"周次范围应为 1~{MAX_WEEK} 且开始不晚于结束"
        if self.week_parity not in WEEK_PARITY_NAMES:
            return "周次类型无效"
        return None

    def covers(self, week_no: int) -> bool:
        """该课程在第 week_no 教学周是否上课。"""
        if not (self.week_start <= week_no <= self.week_end):
            return False
        if self.week_parity == "odd" and week_no % 2 == 0:
            return False
        if self.week_parity == "even" and week_no % 2 == 1:
            return False
        return True

    def weekday_name(self) -> str:
        return WEEKDAY_NAMES[self.weekday - 1] if 1 <= self.weekday <= 7 else "?"

    def weeks_label(self) -> str:
        """如 "1-16 每周" / "3-9 单周"。"""
        parity = WEEK_PARITY_NAMES.get(self.week_parity, "?")
        if self.week_start == 1 and self.week_end == MAX_WEEK and parity == "每周":
            return "全部周"
        return f"{self.week_start}-{self.week_end} {parity}"


@dataclass
class Assignment:
    id: str
    title: str
    course_id: str | None
    due: datetime             # 本地 naive datetime
    note: str = ""
    status: str = STATUS_PENDING   # "pending" | "done"
    kind: str = "hw"               # "hw" | "exam" | "project"
    priority: str = "mid"          # "high" | "mid" | "low"
    remind_before: list[int] = field(
        default_factory=lambda: list(DEFAULT_REMIND_BEFORE))  # 提前提醒的分钟数
    reminded: list[str] = field(default_factory=list)  # 已触发的级别键

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "course_id": self.course_id,
            "due": self.due.isoformat(timespec="minutes"),
            "note": self.note,
            "status": self.status,
            "kind": self.kind,
            "priority": self.priority,
            "remind_before": sorted(set(self.remind_before)),
            "reminded": list(self.reminded),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Assignment":
        try:
            due = datetime.fromisoformat(str(d.get("due")))
        except (ValueError, TypeError):
            due = datetime.now().replace(second=0, microsecond=0) + timedelta(days=1)
        remind_before = [int(m) for m in (d.get("remind_before") or []) if int(m) > 0]
        reminded = [_LEGACY_LEVEL_MAP.get(k, k) for k in (d.get("reminded") or [])]
        kind = str(d.get("kind") or "hw")
        priority = str(d.get("priority") or "mid")
        return cls(
            id=str(d.get("id") or next_id("a")),
            title=str(d.get("title") or "未命名作业"),
            course_id=d.get("course_id") or None,
            due=due,
            note=str(d.get("note") or ""),
            status=str(d.get("status") or STATUS_PENDING),
            kind=kind if kind in KIND_NAMES else "hw",
            priority=priority if priority in PRIORITY_NAMES else "mid",
            remind_before=sorted(set(remind_before)) or list(DEFAULT_REMIND_BEFORE),
            reminded=reminded,
        )

    def kind_name(self) -> str:
        return KIND_NAMES.get(self.kind, "作业")

    def priority_name(self) -> str:
        return PRIORITY_NAMES.get(self.priority, "中")

    def is_done(self) -> bool:
        return self.status == STATUS_DONE
