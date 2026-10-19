"""周课表 Canvas 组件：周一~周日网格、课程色块、当前时刻线、按教学周过滤。"""
from __future__ import annotations

import tkinter as tk
from datetime import date, datetime, timedelta

from models import Course, parse_hhmm

# (填充色, 边框色)
_PALETTE = [
    ("#C9DEF2", "#6E93B8"),
    ("#CFE8CD", "#6FA36F"),
    ("#FBE6C4", "#C9964F"),
    ("#E9D3EC", "#A271AD"),
    ("#F9D6D6", "#C87A7A"),
    ("#CDEAE6", "#5FA296"),
]

_DAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


class TimetableCanvas(tk.Canvas):
    """周课表。render(courses, now) 全量重绘（幂等）。

    回调：
      on_open_course(course)  —— 双击课程块
      on_empty_click(weekday, hhmm) —— 双击空白处（预填新建课程）
    """

    HEADER_BASE = 46      # 顶部表头高（96dpi 基准 px），含日期行
    GUTTER_BASE = 56      # 左侧时间刻度宽
    START_HOUR = 8
    END_HOUR = 23
    PXH_BASE = 56         # 每小时像素高

    def __init__(self, master, on_open_course, on_empty_click, **kw):
        super().__init__(master, bg="white", **kw)
        self.on_open_course = on_open_course
        self.on_empty_click = on_empty_click
        self._courses: list[Course] = []
        self._now = datetime.now()
        self._week_no = 1
        self._week_monday: date | None = None
        self._is_current_week = True

        self.bind("<MouseWheel>", self._on_wheel)
        self.bind("<Double-Button-1>", self._on_double_click)
        self.bind("<Configure>", lambda e: self.redraw())

    # ---- 布局度量（随系统 DPI 缩放） ----
    @property
    def _s(self) -> float:
        try:
            return max(1.0, self.winfo_fpixels("1i") / 96.0)
        except Exception:
            return 1.0

    @property
    def HEADER(self) -> int:
        return int(self.HEADER_BASE * self._s)

    @property
    def GUTTER(self) -> int:
        return int(self.GUTTER_BASE * self._s)

    @property
    def PXH(self) -> int:
        return int(self.PXH_BASE * self._s)

    def _col_w(self) -> int:
        w = max(self.winfo_width(), 800)
        return max(40, (w - self.GUTTER) // 7)

    def _total_h(self) -> int:
        return self.HEADER + (self.END_HOUR - self.START_HOUR) * self.PXH

    def _tmin(self, hhmm: str) -> int:
        t = parse_hhmm(hhmm)
        return t.hour * 60 + t.minute

    def _y(self, minutes_from_start: int) -> int:
        minutes = max(0, minutes_from_start)
        return self.HEADER + minutes * self.PXH // 60

    # ---- 公共接口 ----
    def render(self, courses: list[Course], now: datetime, week_no: int = 1,
               week_monday: date | None = None) -> None:
        """全量重绘。week_no 用于过滤周次/单双周课程；week_monday 为该教学周的
        周一日期（None 则表头不显示日期）。"""
        self._courses = [c for c in courses if c.covers(week_no)]
        self._now = now
        self._week_no = week_no
        self._week_monday = week_monday
        self._is_current_week = (week_monday is None or
                                 week_monday == now.date() -
                                 timedelta(days=now.weekday()))
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        if self.winfo_width() <= 1 and not self._courses:
            return
        header, gutter, pxh = self.HEADER, self.GUTTER, self.PXH
        col_w = self._col_w()
        total_w = gutter + 7 * col_w
        total_h = self._total_h()
        self.configure(scrollregion=(0, 0, total_w, total_h + 8),
                       yscrollincrement=max(10, int(20 * self._s)))

        today = self._now.isoweekday()
        f_header = ("Microsoft YaHei UI", 10, "bold")
        f_small = ("Microsoft YaHei UI", 8)

        # 今天列高亮（仅查看当前教学周时）+ 表头 + 列分隔线
        for i in range(7):
            x0 = gutter + i * col_w
            if self._is_current_week and i + 1 == today:
                self.create_rectangle(x0, 0, x0 + col_w, total_h,
                                      fill="#EAF2FB", outline="")
            self.create_line(x0, 0, x0, total_h, fill="#DDDDDD")
            self.create_text(x0 + col_w // 2, int(header * 0.33),
                             text=_DAY_NAMES[i], font=f_header, fill="#333333")
            if self._week_monday:
                d = self._week_monday + timedelta(days=i)
                self.create_text(x0 + col_w // 2, int(header * 0.72),
                                 text=f"{d.month}/{d.day}", font=f_small,
                                 fill="#888888")
        self.create_line(total_w, 0, total_w, total_h, fill="#DDDDDD")
        self.create_line(0, header, total_w, header, fill="#999999")
        self.create_rectangle(0, 0, gutter, header, fill="#F5F5F5",
                              outline="#DDDDDD")

        # 时间刻度
        for h in range(self.START_HOUR, self.END_HOUR + 1):
            y = header + (h - self.START_HOUR) * pxh
            self.create_line(0, y, total_w, y, fill="#EEEEEE")
            self.create_text(gutter // 2, y, text=f"{h:02d}:00",
                             font=f_small, fill="#888888")

        # 课程块（同天重叠分道）
        color_map = {c.id: i % len(_PALETTE) for i, c in enumerate(self._courses)}
        by_day: dict[int, list[Course]] = {}
        for c in self._courses:
            if 1 <= c.weekday <= 7:
                by_day.setdefault(c.weekday, []).append(c)
        for day, day_courses in by_day.items():
            col_x = gutter + (day - 1) * col_w
            self._draw_day(day_courses, col_x, col_w, color_map)

        # 当前时刻线（今天列，仅查看当前教学周时）
        if self._is_current_week and 1 <= today <= 7:
            minutes = (self._now.hour - self.START_HOUR) * 60 + self._now.minute
            y = self._y(minutes)
            if header <= y <= total_h:
                x0 = gutter + (today - 1) * col_w
                self.create_line(x0 + 2, y, x0 + col_w - 2, y,
                                 fill="#E74C3C", width=2)

    def _draw_day(self, courses: list[Course], col_x: int, col_w: int,
                  color_map: dict[str, int]) -> None:
        # 贪心分道：start 排序后放入第一条不冲突的道
        ordered = sorted(courses, key=lambda c: (c.start, c.end))
        lanes: list[list[Course]] = []
        for c in ordered:
            for lane in lanes:
                if all(self._tmin(c.start) >= self._tmin(p.end) for p in lane):
                    lane.append(c)
                    break
            else:
                lanes.append([c])

        s = self._s
        f_name = ("Microsoft YaHei UI", 9, "bold")
        f_detail = ("Microsoft YaHei UI", 8)
        lane_w = col_w / len(lanes)

        for i, lane in enumerate(lanes):
            for c in lane:
                y0 = self._y(self._tmin(c.start) - self.START_HOUR * 60)
                y1 = self._y(self._tmin(c.end) - self.START_HOUR * 60)
                y1 = max(y1, y0 + int(20 * s))
                fill, border = _PALETTE[color_map.get(c.id, 0)]
                x0 = col_x + int(i * lane_w) + 2
                x1 = col_x + int((i + 1) * lane_w) - 2
                tags = ("course", f"cid:{c.id}")
                self.create_rectangle(x0, y0, x1, y1, fill=fill,
                                      outline=border, tags=tags)
                cx = (x0 + x1) // 2
                bh = y1 - y0
                self.create_text(cx, y0 + int(4 * s), text=c.name,
                                 font=f_name, anchor="n", tags=tags)
                detail = " ".join(x for x in (c.teacher, c.location) if x)
                if bh >= int(34 * s) and detail:
                    self.create_text(cx, y0 + int(21 * s), text=detail,
                                     font=f_detail, anchor="n", fill="#444444",
                                     tags=tags)
                if bh >= int(52 * s):
                    self.create_text(cx, y1 - int(6 * s),
                                     text=f"{c.start}-{c.end}",
                                     font=f_detail, anchor="s", fill="#555555",
                                     tags=tags)

    # ---- 交互 ----
    def _on_wheel(self, event) -> None:
        if abs(event.delta) >= 120:
            units = int(-event.delta / 120) * 3
        else:
            units = -event.delta // 12
        if units:
            self.yview_scroll(int(units), "units")

    def _on_double_click(self, event) -> None:
        x = self.canvasx(event.x)
        y = self.canvasy(event.y)
        header, gutter, pxh = self.HEADER, self.GUTTER, self.PXH
        col_w = self._col_w()

        for tag in self.gettags("current"):
            if tag.startswith("cid:"):
                cid = tag[4:]
                course = next((c for c in self._courses if c.id == cid), None)
                if course:
                    self.on_open_course(course)
                return

        if x < gutter or y < header:
            return
        day = min(6, max(0, int((x - gutter) // col_w))) + 1
        minutes = (y - header) * 60 // pxh
        minutes = max(0, min((self.END_HOUR - self.START_HOUR - 1) * 60, minutes))
        slot = (minutes + 15) // 30 * 30
        hh = self.START_HOUR + slot // 60
        mm = slot % 60
        self.on_empty_click(day, f"{hh:02d}:{mm:02d}")
