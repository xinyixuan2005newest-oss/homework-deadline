"""月视图日历：按月展示作业截止分布（零依赖，tkinter Canvas）。"""
from __future__ import annotations

import calendar
import tkinter as tk
from datetime import date, datetime, timedelta

from models import Assignment

HEADER_H = 26          # 星期表头高度
MAX_SHOW = 3           # 每格最多显示的作业条数
WEEK_LABELS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


class MonthView(tk.Canvas):
    """月份网格。show(year, month, assignments) 渲染；双击某天触发回调。"""

    def __init__(self, master, on_day_double_click=None, **kw):
        super().__init__(master, background="white",
                         highlightthickness=0, **kw)
        self._on_day_dclick = on_day_double_click
        self._year = date.today().year
        self._month = date.today().month
        self._by_day: dict[date, list[Assignment]] = {}
        self.bind("<Double-Button-1>", self._handle_double_click)
        self.bind("<Configure>", lambda _e: self.redraw())

    # ---------- 数据与渲染 ----------

    def show(self, year: int, month: int, assignments: list[Assignment]) -> None:
        self._year, self._month = year, month
        self._by_day = {}
        for a in assignments:
            self._by_day.setdefault(a.due.date(), []).append(a)
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        w = self.winfo_width() or 900
        h = self.winfo_height() or 560
        weeks = calendar.Calendar(firstweekday=0).monthdayscalendar(
            self._year, self._month)
        cw, ch = w / 7, (h - HEADER_H) / len(weeks)
        today = date.today()

        # 星期表头
        for i, label in enumerate(WEEK_LABELS):
            x0 = i * cw
            self.create_rectangle(x0, 0, x0 + cw, HEADER_H,
                                  fill="#34495E", outline="#34495E")
            self.create_text(x0 + cw / 2, HEADER_H / 2, text=label,
                             fill="white",
                             font=("Microsoft YaHei UI", 10, "bold"))

        # 日期格
        for r, week in enumerate(weeks):
            for c, day in enumerate(week):
                x0, y0 = c * cw, HEADER_H + r * ch
                if day == 0:   # 上月/下月补位格
                    self.create_rectangle(x0, y0, x0 + cw, y0 + ch,
                                          fill="#F4F6F7", outline="#E5E8E8")
                    continue
                d = date(self._year, self._month, day)
                bg = "#D6EAF8" if d == today else "white"
                self.create_rectangle(x0, y0, x0 + cw, y0 + ch,
                                      fill=bg, outline="#E5E8E8")
                num_color = "#C0392B" if d == today else "#2C3E50"
                self.create_text(x0 + 6, y0 + 5, text=str(day), anchor="nw",
                                 fill=num_color,
                                 font=("Segoe UI", 10, "bold"))
                items = sorted(self._by_day.get(d, []),
                               key=lambda a: a.due)
                y = y0 + 27
                for a in items[:MAX_SHOW]:
                    mark = "!" if (a.priority == "high"
                                   and not a.is_done()) else "·"
                    title = (a.title if len(a.title) <= 10
                             else a.title[:9] + "…")
                    self.create_text(x0 + 8, y, anchor="nw",
                                     text=f"{mark}{title}",
                                     fill=self._color_of(a),
                                     font=("Microsoft YaHei UI", 9))
                    y += 18
                if len(items) > MAX_SHOW:
                    self.create_text(x0 + 8, y, anchor="nw",
                                     text=f"…还有 {len(items) - MAX_SHOW} 项",
                                     fill="#7F8C8D",
                                     font=("Microsoft YaHei UI", 9))

    @staticmethod
    def _color_of(a: Assignment) -> str:
        if a.is_done():
            return "#95A5A6"
        now = datetime.now()
        if a.due <= now:
            return "#C0392B"          # 已逾期
        if a.due - now <= timedelta(hours=24):
            return "#B9770E"          # 24 小时内
        return "#2C3E50"

    # ---------- 交互 ----------

    def _handle_double_click(self, event) -> None:
        if self._on_day_dclick is None or event.y < HEADER_H:
            return
        weeks = calendar.Calendar(firstweekday=0).monthdayscalendar(
            self._year, self._month)
        cw = (self.winfo_width() or 900) / 7
        ch = ((self.winfo_height() or 560) - HEADER_H) / len(weeks)
        c = min(max(int(event.x / cw), 0), 6)
        r = min(max(int((event.y - HEADER_H) / ch), 0), len(weeks) - 1)
        day = weeks[r][c]
        if day:
            self._on_day_dclick(date(self._year, self._month, day))
