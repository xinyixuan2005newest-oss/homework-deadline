"""编辑对话框（课程/作业）与提醒弹窗。"""
from __future__ import annotations

import tkinter as tk
from datetime import datetime, timedelta
from tkinter import messagebox, ttk

from models import (DEFAULT_REMIND_BEFORE, KIND_NAMES, MAX_WEEK,
                    PRIORITY_NAMES, WEEKDAY_NAMES, WEEK_PARITY_NAMES,
                    Assignment, Course, format_remaining, next_id, parse_hhmm)

_TIME_SLOTS = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 30)]


def _place_near_parent(win: tk.Toplevel, parent: tk.Misc) -> None:
    """把对话框放在屏幕正中（保留 parent 参数以便未来改回相对定位）。"""
    win.update_idletasks()
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    w, h = win.winfo_reqwidth(), win.winfo_reqheight()
    win.geometry(f"+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2)}")


class CourseDialog(tk.Toplevel):
    """新建/编辑课程。关闭后 self.result 为 Course 或 None（取消）。"""

    def __init__(self, master, course: Course | None = None,
                 preset: tuple[int, str] | None = None):
        super().__init__(master)
        self.course = course
        self.result: Course | None = None
        self.title("编辑课程" if course else "新建课程")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        frm = ttk.Frame(self, padding=16)
        frm.grid(sticky="nsew")
        frm.columnconfigure(1, weight=1)

        self.e_name = ttk.Entry(frm, width=28)
        self.e_teacher = ttk.Entry(frm, width=28)
        self.e_location = ttk.Entry(frm, width=28)
        self.cb_weekday = ttk.Combobox(frm, state="readonly", width=25,
                                       values=WEEKDAY_NAMES)
        self.cb_start = ttk.Combobox(frm, width=25, values=_TIME_SLOTS)
        self.cb_end = ttk.Combobox(frm, width=25, values=_TIME_SLOTS)

        rows = [("课程名 *", self.e_name), ("教师", self.e_teacher),
                ("地点", self.e_location), ("星期", self.cb_weekday),
                ("开始时间", self.cb_start), ("结束时间", self.cb_end)]
        for i, (label, widget) in enumerate(rows):
            ttk.Label(frm, text=label).grid(row=i, column=0, sticky="e", padx=(0, 8), pady=4)
            widget.grid(row=i, column=1, sticky="ew", pady=4)

        # 周次范围 + 单双周
        wk = ttk.Frame(frm)
        self.sb_week_start = ttk.Spinbox(wk, from_=1, to=MAX_WEEK, width=5)
        self.sb_week_end = ttk.Spinbox(wk, from_=1, to=MAX_WEEK, width=5)
        self.sb_week_start.pack(side="left")
        ttk.Label(wk, text="至").pack(side="left", padx=6)
        self.sb_week_end.pack(side="left")
        ttk.Label(frm, text="周次范围").grid(row=len(rows), column=0,
                                             sticky="e", padx=(0, 8), pady=4)
        wk.grid(row=len(rows), column=1, sticky="w", pady=4)

        self.cb_parity = ttk.Combobox(frm, state="readonly", width=25,
                                      values=list(WEEK_PARITY_NAMES.values()))
        ttk.Label(frm, text="单双周").grid(row=len(rows) + 1, column=0,
                                           sticky="e", padx=(0, 8), pady=4)
        self.cb_parity.grid(row=len(rows) + 1, column=1, sticky="ew", pady=4)

        if course:
            self.e_name.insert(0, course.name)
            self.e_teacher.insert(0, course.teacher)
            self.e_location.insert(0, course.location)
            self.cb_weekday.current(max(0, course.weekday - 1))
            self.cb_start.set(course.start)
            self.cb_end.set(course.end)
            self.sb_week_start.set(course.week_start)
            self.sb_week_end.set(course.week_end)
            self.cb_parity.current(
                list(WEEK_PARITY_NAMES).index(course.week_parity))
        else:
            self.cb_weekday.current(0)
            if preset:
                weekday, start = preset
                self.cb_weekday.current(max(0, min(6, weekday - 1)))
                self.cb_start.set(start)
                self.cb_end.set(self._plus_hour(start))
            else:
                self.cb_start.set("08:00")
                self.cb_end.set("09:40")
            self.sb_week_start.set(1)
            self.sb_week_end.set(MAX_WEEK)
            self.cb_parity.current(0)

        btns = ttk.Frame(frm)
        btns.grid(row=len(rows) + 2, column=0, columnspan=2, sticky="e",
                  pady=(12, 0))
        ttk.Button(btns, text="确定", command=self._on_ok).pack(side="left", padx=4)
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="left", padx=4)

        self.bind("<Return>", lambda e: self._on_ok())
        self.bind("<Escape>", lambda e: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        _place_near_parent(self, master)
        self.e_name.focus_set()
        self.wait_window()

    @staticmethod
    def _plus_hour(hhmm: str) -> str:
        try:
            t = parse_hhmm(hhmm)
        except ValueError:
            return "09:40"
        total = min(23 * 60 + 59, t.hour * 60 + t.minute + 60)
        return f"{total // 60:02d}:{total % 60:02d}"

    def _on_ok(self) -> None:
        try:
            t0 = parse_hhmm(self.cb_start.get())
            t1 = parse_hhmm(self.cb_end.get())
            w0 = int(self.sb_week_start.get())
            w1 = int(self.sb_week_end.get())
        except ValueError as e:
            messagebox.showerror("输入有误", f"请输入有效的时间与周次数字：{e}",
                                 parent=self)
            return
        course = Course(
            id=self.course.id if self.course else next_id("c"),
            name=self.e_name.get().strip(),
            teacher=self.e_teacher.get().strip(),
            location=self.e_location.get().strip(),
            weekday=self.cb_weekday.current() + 1,
            start=f"{t0.hour:02d}:{t0.minute:02d}",
            end=f"{t1.hour:02d}:{t1.minute:02d}",
            week_start=w0,
            week_end=w1,
            week_parity=list(WEEK_PARITY_NAMES)[self.cb_parity.current()],
        )
        err = course.validate()
        if err:
            messagebox.showerror("输入有误", err, parent=self)
            return
        self.result = course
        self.destroy()


class AssignmentDialog(tk.Toplevel):
    """新建/编辑作业。关闭后 self.result 为 (Assignment, reset_reminder) 或 None。

    reset_reminder：编辑且截止时间被修改时为 True，调用方应清空已提醒记录。
    """

    def __init__(self, master, assignment: Assignment | None,
                 courses: list[Course], default_date=None):
        super().__init__(master)
        self.assignment = assignment
        self.courses = courses
        self._default_date = default_date
        self.result: tuple[Assignment, bool] | None = None
        self.title("编辑作业" if assignment else "新建作业")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        frm = ttk.Frame(self, padding=16)
        frm.grid(sticky="nsew")
        frm.columnconfigure(1, weight=1)

        self.e_title = ttk.Entry(frm, width=30)
        course_names = ["— 未分类 —"] + [c.name for c in courses]
        self.cb_course = ttk.Combobox(frm, state="readonly", width=27,
                                      values=course_names)
        self.e_date = ttk.Entry(frm, width=30)
        self.cb_time = ttk.Combobox(frm, width=27, values=_TIME_SLOTS)
        self.cb_kind = ttk.Combobox(frm, state="readonly", width=27,
                                    values=list(KIND_NAMES.values()))
        self.cb_priority = ttk.Combobox(frm, state="readonly", width=27,
                                        values=list(PRIORITY_NAMES.values()))
        self.txt_note = tk.Text(frm, width=32, height=4)

        for i, (label, widget, sticky) in enumerate([
                ("标题 *", self.e_title, "ew"),
                ("课程", self.cb_course, "ew"),
                ("截止日期 *", self.e_date, "ew"),
                ("截止时间 *", self.cb_time, "ew"),
                ("类型", self.cb_kind, "ew"),
                ("优先级", self.cb_priority, "ew")]):
            ttk.Label(frm, text=label).grid(row=i, column=0, sticky="e",
                                            padx=(0, 8), pady=4)
            widget.grid(row=i, column=1, sticky=sticky, pady=4)

        # 提前提醒多选（截止前 N 分钟）
        self._remind_vars: dict[int, tk.BooleanVar] = {}
        rframe = ttk.Frame(frm)
        for label, minutes in (("3天", 4320), ("24小时", 1440),
                               ("1小时", 60), ("30分钟", 30)):
            var = tk.BooleanVar(value=False)
            self._remind_vars[minutes] = var
            ttk.Checkbutton(rframe, text=label, variable=var).pack(
                side="left", padx=(0, 10))
        ttk.Label(frm, text="提前提醒").grid(row=6, column=0, sticky="e",
                                             padx=(0, 8), pady=4)
        rframe.grid(row=6, column=1, sticky="w", pady=4)

        ttk.Label(frm, text="备注").grid(row=7, column=0, sticky="ne",
                                         padx=(0, 8), pady=4)
        self.txt_note.grid(row=7, column=1, sticky="ew", pady=4)

        self._init_values()

        btns = ttk.Frame(frm)
        btns.grid(row=8, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="确定", command=self._on_ok).pack(side="left", padx=4)
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="left", padx=4)

        self.bind("<Return>", lambda e: self._on_ok())
        self.bind("<Escape>", lambda e: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        _place_near_parent(self, master)
        self.e_title.focus_set()
        self.wait_window()

    def _init_values(self) -> None:
        a = self.assignment
        if a:
            self.e_title.insert(0, a.title)
            idx = 0
            for i, c in enumerate(self.courses):
                if c.id == a.course_id:
                    idx = i + 1
                    break
            self.cb_course.current(idx)
            self.e_date.insert(0, a.due.strftime("%Y-%m-%d"))
            self.cb_time.set(a.due.strftime("%H:%M"))
            for m in a.remind_before:
                if m in self._remind_vars:
                    self._remind_vars[m].set(True)
            if a.note:
                self.txt_note.insert("1.0", a.note)
            self.cb_kind.current(
                list(KIND_NAMES).index(a.kind) if a.kind in KIND_NAMES else 0)
            self.cb_priority.current(
                list(PRIORITY_NAMES).index(a.priority)
                if a.priority in PRIORITY_NAMES else 1)
        else:
            self.cb_course.current(0)
            d = self._default_date or datetime.now()
            self.e_date.insert(0, d.strftime("%Y-%m-%d"))
            self.cb_time.set("23:59")
            self.cb_kind.current(0)
            self.cb_priority.current(1)
            for m in DEFAULT_REMIND_BEFORE:
                if m in self._remind_vars:
                    self._remind_vars[m].set(True)

    def _on_ok(self) -> None:
        title = self.e_title.get().strip()
        if not title:
            messagebox.showerror("输入有误", "标题不能为空", parent=self)
            return
        try:
            d = datetime.strptime(self.e_date.get().strip(), "%Y-%m-%d").date()
        except ValueError:
            messagebox.showerror("输入有误", "日期格式应为 YYYY-MM-DD，如 2026-10-08",
                                 parent=self)
            return
        try:
            t = parse_hhmm(self.cb_time.get())
        except ValueError as e:
            messagebox.showerror("输入有误", str(e), parent=self)
            return
        course_idx = self.cb_course.current()
        course_id = self.courses[course_idx - 1].id if course_idx > 0 else None
        due = datetime.combine(d, t)
        reset_reminder = bool(self.assignment and self.assignment.due != due)
        old = self.assignment
        remind_before = sorted(m for m, v in self._remind_vars.items() if v.get())
        assignment = Assignment(
            id=old.id if old else next_id("a"),
            title=title,
            course_id=course_id,
            due=due,
            note=self.txt_note.get("1.0", "end").strip(),
            status=old.status if old else "pending",
            kind=list(KIND_NAMES)[self.cb_kind.current()],
            priority=list(PRIORITY_NAMES)[self.cb_priority.current()],
            remind_before=remind_before,
            reminded=list(old.reminded) if old else [],
        )
        self.result = (assignment, reset_reminder)
        self.destroy()


class ReminderPopup(tk.Toplevel):
    """无模态提醒弹窗，显示在屏幕右下角，多个弹窗向上级联。"""

    def __init__(self, master, evt, on_done, cascade: int = 0):
        super().__init__(master)
        self.title("作业提醒")
        self.resizable(False, False)
        self.attributes("-topmost", True)

        overdue = evt.level == "overdue"
        head = "作业已逾期" if overdue else "作业即将截止"
        color = "#C0392B" if overdue else "#B9770E"
        remaining = format_remaining(evt.due - datetime.now())

        frm = ttk.Frame(self, padding=16)
        frm.grid(sticky="nsew")
        frm.columnconfigure(0, weight=1)
        ttk.Label(frm, text=head, foreground=color,
                  font=("Microsoft YaHei UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(frm, text=evt.title,
                  font=("Microsoft YaHei UI", 13, "bold")).grid(row=1, column=0, sticky="w", pady=(6, 2))
        info = f"课程：{evt.course_name or '未分类'}\n截止：{evt.due:%m-%d %H:%M}（{remaining}）"
        ttk.Label(frm, text=info, justify="left").grid(row=2, column=0, sticky="w", pady=(0, 10))

        btns = ttk.Frame(frm)
        btns.grid(row=3, column=0, sticky="e")
        ttk.Button(btns, text="标记完成",
                   command=lambda: self._finish(on_done)).pack(side="left", padx=4)
        ttk.Button(btns, text="知道了", command=self.destroy).pack(side="left", padx=4)

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        x = max(0, self.winfo_screenwidth() - w - 40)
        y = max(0, self.winfo_screenheight() - h - 70 - cascade * (h + 12))
        self.geometry(f"+{x}+{y}")

    def _finish(self, on_done) -> None:
        on_done()
        self.destroy()
