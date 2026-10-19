"""课程作业管理 GUI 程序：入口 + 主窗口。"""
from __future__ import annotations

import ctypes
import csv
import queue
import threading
import tkinter as tk
import winsound
from dataclasses import replace
from datetime import date, datetime, timedelta
from tkinter import filedialog, messagebox, ttk

from dialogs import AssignmentDialog, CourseDialog, ReminderPopup
from models import (KIND_NAMES, MAX_WEEK, PRIORITY_NAMES, STATUS_DONE,
                    STATUS_PENDING, WEEKDAY_NAMES, Assignment, Course,
                    format_remaining, semester_week)
from monthview import MonthView
from reminder import ReminderEvent, ReminderService, send_windows_toast
from storage import load_data, rotate_backup, save_data
from timetable import TimetableCanvas
from trayicon import TrayIcon, icon_file

FILTERS = ["全部", "待完成", "已完成", "即将截止"]


def _enable_dpi_awareness() -> None:
    """必须在创建 Tk 窗口之前调用。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        try:
            dpi = ctypes.windll.shcore.GetDpiForSystem()
            self.tk.call("tk", "scaling", dpi / 72.0)
        except Exception:
            pass

        self.title("课程作业管理")
        self.geometry("1000x640")
        self.minsize(860, 560)

        self._lock = threading.RLock()
        self.courses: list[Course] = []
        self.assignments: list[Assignment] = []
        self.courses, self.assignments, self.settings = load_data()
        rotate_backup()   # 启动自动备份（静默失败）
        self._week_offset = 0   # 课表相对当前教学周的偏移

        self.var_filter = tk.StringVar(value=FILTERS[0])
        self.var_status = tk.StringVar(value="")
        self._popups: list[ReminderPopup] = []

        self._build_ui()
        self._refresh_all()

        self._queue: queue.Queue[ReminderEvent] = queue.Queue()
        self._service = ReminderService(
            provider=self._assignment_snapshot,
            out_queue=self._queue,
            course_name_of=self._course_name_of,
        )
        self._service.start()

        # 系统托盘（关窗最小化，提醒不中断）
        self._tray_events: queue.Queue = queue.Queue()
        self.tray = TrayIcon(tip="课程作业管理 - 作业截止提醒运行中",
                             events=self._tray_events)
        self._tray_balloon_shown = False
        try:
            self.iconbitmap(icon_file())
        except Exception:
            pass

        self.after(500, self._poll_reminder_queue)
        self.after(200, self._poll_tray_queue)
        self.after(60_000, self._minute_tick)
        self.protocol("WM_DELETE_WINDOW", self._minimize_to_tray)

    # ================= UI 构建 =================
    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        nb = ttk.Notebook(self)
        nb.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 4))
        self._build_assignment_tab(nb)
        self._build_timetable_tab(nb)
        self._build_month_tab(nb)
        self._build_course_tab(nb)
        self._build_stats_tab(nb)

        ttk.Label(self, textvariable=self.var_status, anchor="e",
                  padding=(8, 2)).grid(row=1, column=0, sticky="ew")

    def _build_assignment_tab(self, nb: ttk.Notebook) -> None:
        frame = ttk.Frame(nb, padding=8)
        nb.add(frame, text="作业列表")

        top = ttk.Frame(frame)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        ttk.Label(top, text="筛选:").pack(side="left")
        cb = ttk.Combobox(top, textvariable=self.var_filter, values=FILTERS,
                          state="readonly", width=8)
        cb.pack(side="left", padx=(4, 12))
        cb.bind("<<ComboboxSelected>>", lambda e: self._refresh_assignments())
        for text, cmd in (("新建作业", self._on_add_assignment),
                          ("编辑", self._on_edit_assignment),
                          ("标记完成/撤销", self._on_toggle_done),
                          ("删除", self._on_delete_assignment),
                          ("导出 CSV", self._on_export_csv)):
            ttk.Button(top, text=text, command=cmd).pack(side="left", padx=3)

        cols = ("title", "course", "kind", "due", "remaining",
                "priority", "status", "note")
        self.tree_assign = tree = ttk.Treeview(frame, columns=cols, show="headings")
        for cid, text, width, anchor in (
                ("title", "标题", 170, "w"), ("course", "课程", 95, "w"),
                ("kind", "类型", 55, "center"),
                ("due", "截止时间", 105, "w"), ("remaining", "剩余", 100, "w"),
                ("priority", "优先级", 55, "center"),
                ("status", "状态", 70, "center"), ("note", "备注", 220, "w")):
            tree.heading(cid, text=text)
            tree.column(cid, width=width, anchor=anchor)
        tree.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        sb.grid(row=1, column=1, sticky="ns")
        tree.tag_configure("overdue", foreground="#C0392B")
        tree.tag_configure("soon", foreground="#B9770E")
        tree.tag_configure("done", foreground="#95A5A6")
        tree.tag_configure("hipri", foreground="#C0392B")
        tree.bind("<Double-Button-1>", lambda e: self._on_edit_assignment())
        tree.bind("<Button-1>", self._on_assign_click)

        frame.rowconfigure(1, weight=1)
        frame.columnconfigure(0, weight=1)

    def _on_assign_click(self, event):
        """单击已选中的作业行时取消高亮（再次点击同一行 = 反选）。"""
        tree = self.tree_assign
        if tree.identify_region(event.x, event.y) != "cell":
            return None   # 表头等区域走默认行为
        iid = tree.identify_row(event.y)
        if iid and tree.selection() == (iid,):
            tree.selection_remove(iid)
            return "break"   # 阻止默认绑定把它重新选中
        return None

    def _build_timetable_tab(self, nb: ttk.Notebook) -> None:
        frame = ttk.Frame(nb)
        nb.add(frame, text="周课表")

        # 教学周工具栏
        bar = ttk.Frame(frame)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=6, pady=4)
        ttk.Button(bar, text="◀", width=3, command=lambda: self._shift_week(-1)).pack(side="left")
        ttk.Button(bar, text="▶", width=3, command=lambda: self._shift_week(1)).pack(side="left", padx=(2, 8))
        ttk.Button(bar, text="本周", command=self._reset_week).pack(side="left")
        self.var_week = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.var_week, font=("Microsoft YaHei UI", 10, "bold")).pack(side="left", padx=10)
        ttk.Label(bar, text="开学日期:").pack(side="left", padx=(16, 4))
        self.e_semester = ttk.Entry(bar, width=12)
        self.e_semester.pack(side="left")
        ttk.Button(bar, text="设置", command=self._on_set_semester).pack(side="left", padx=4)

        self.timetable = TimetableCanvas(
            frame,
            on_open_course=self._on_edit_course_obj,
            on_empty_click=self._on_add_course_preset,
        )
        self.timetable.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.timetable.yview)
        self.timetable.configure(yscrollcommand=sb.set)
        sb.grid(row=1, column=1, sticky="ns")
        frame.rowconfigure(1, weight=1)
        frame.columnconfigure(0, weight=1)

    # ================= 月历页 =================

    def _build_month_tab(self, nb: ttk.Notebook) -> None:
        frame = ttk.Frame(nb)
        nb.add(frame, text="月历")
        top = ttk.Frame(frame)
        top.pack(fill="x", padx=8, pady=6)
        ttk.Button(top, text="◀", width=3,
                   command=lambda: self._month_step(-1)).pack(side="left")
        self.var_month = tk.StringVar()
        ttk.Label(top, textvariable=self.var_month,
                  font=("Microsoft YaHei UI", 11, "bold")
                  ).pack(side="left", padx=12)
        ttk.Button(top, text="▶", width=3,
                   command=lambda: self._month_step(1)).pack(side="left")
        ttk.Button(top, text="本月",
                   command=self._month_today).pack(side="left", padx=12)
        ttk.Label(top, text="双击某一天可新建当天的作业",
                  foreground="#7F8C8D").pack(side="right")
        self._month_anchor = date.today()
        self.month_view = MonthView(
            frame, on_day_double_click=self._on_month_day_dclick)
        self.month_view.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    def _month_step(self, delta: int) -> None:
        y, m = self._month_anchor.year, self._month_anchor.month + delta
        if m == 0:
            y, m = y - 1, 12
        elif m == 13:
            y, m = y + 1, 1
        self._month_anchor = date(y, m, 1)
        self._refresh_month()

    def _month_today(self) -> None:
        self._month_anchor = date.today()
        self._refresh_month()

    def _refresh_month(self) -> None:
        a = self._month_anchor
        self.var_month.set(f"{a.year} 年 {a.month} 月")
        self.month_view.show(a.year, a.month, self.assignments)

    def _on_month_day_dclick(self, d: date) -> None:
        self._on_add_assignment(default_date=d)

    # ================= 统计页 =================

    def _build_stats_tab(self, nb: ttk.Notebook) -> None:
        frame = ttk.Frame(nb, padding=12)
        nb.add(frame, text="统计")

        cards = ttk.Frame(frame)
        cards.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        self._stat_vars: dict[str, tk.StringVar] = {}
        for key, label in (("total", "作业总数"), ("pending", "待完成"),
                           ("done", "已完成"), ("overdue", "已逾期"),
                           ("rate", "完成率")):
            lf = ttk.LabelFrame(cards, text=label)
            lf.pack(side="left", padx=(0, 12), ipadx=18, ipady=4)
            var = tk.StringVar(value="0")
            ttk.Label(lf, textvariable=var,
                      font=("Segoe UI", 16, "bold")).pack()
            self._stat_vars[key] = var

        cols = ("course", "total", "pending", "done", "overdue", "rate")
        self.tree_stats = tree = ttk.Treeview(frame, columns=cols,
                                              show="headings", height=12)
        for cid, text, width, anchor in (
                ("course", "课程", 140, "w"), ("total", "总数", 60, "center"),
                ("pending", "待完成", 70, "center"),
                ("done", "已完成", 70, "center"),
                ("overdue", "逾期", 60, "center"),
                ("rate", "完成率", 80, "center")):
            tree.heading(cid, text=text)
            tree.column(cid, width=width, anchor=anchor)
        tree.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        sb.grid(row=1, column=1, sticky="ns")
        tree.tag_configure("overdue", foreground="#C0392B")
        frame.rowconfigure(1, weight=1)
        frame.columnconfigure(0, weight=1)

    def _refresh_stats(self) -> None:
        now = datetime.now()
        total = len(self.assignments)
        done = sum(1 for a in self.assignments if a.is_done())
        overdue = sum(1 for a in self.assignments
                      if not a.is_done() and a.due <= now)
        rate = f"{done / total * 100:.0f}%" if total else "—"
        self._stat_vars["total"].set(str(total))
        self._stat_vars["done"].set(str(done))
        self._stat_vars["pending"].set(str(total - done))
        self._stat_vars["overdue"].set(str(overdue))
        self._stat_vars["rate"].set(rate)

        tree = self.tree_stats
        tree.delete(*tree.get_children())
        groups: dict[str | None, list] = {}
        for a in self.assignments:
            groups.setdefault(a.course_id, []).append(a)
        rows = [(c, groups.pop(c.id, [])) for c in self.courses]
        ungrouped = groups.pop(None, [])
        for _, rest in groups.items():   # 已删除课程的遗留作业
            ungrouped.extend(rest)
        if ungrouped:
            rows.append((None, ungrouped))
        for course, items in rows:
            d = sum(1 for a in items if a.is_done())
            ovd = sum(1 for a in items
                      if not a.is_done() and a.due <= now)
            pct = f"{d / len(items) * 100:.0f}%" if items else "—"
            tree.insert("", "end", tags=("overdue",) if ovd else (),
                        values=(course.name if course else "未分类",
                                len(items), len(items) - d, d, ovd, pct))

    def _build_course_tab(self, nb: ttk.Notebook) -> None:
        frame = ttk.Frame(nb, padding=8)
        nb.add(frame, text="课程管理")

        top = ttk.Frame(frame)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        for text, cmd in (("新建课程", self._on_add_course),
                          ("编辑", self._on_edit_course_sel),
                          ("删除", self._on_delete_course)):
            ttk.Button(top, text=text, command=cmd).pack(side="left", padx=3)

        cols = ("name", "teacher", "location", "weekday", "time", "weeks")
        self.tree_course = tree = ttk.Treeview(frame, columns=cols, show="headings")
        for cid, text, width in (("name", "课程名", 160), ("teacher", "教师", 110),
                                 ("location", "地点", 150),
                                 ("weekday", "星期", 70), ("time", "时间", 120),
                                 ("weeks", "周次", 130)):
            tree.heading(cid, text=text)
            tree.column(cid, width=width, anchor="w")
        tree.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        sb.grid(row=1, column=1, sticky="ns")
        tree.bind("<Double-Button-1>", lambda e: self._on_edit_course_sel())

        frame.rowconfigure(1, weight=1)
        frame.columnconfigure(0, weight=1)

    # ================= 数据读写枢纽 =================
    def _mutate(self, fn) -> None:
        """所有数据变更必经此处：锁内修改 + 存盘 + 全量刷新。"""
        with self._lock:
            fn()
            try:
                save_data(self.courses, self.assignments, self.settings)
            except OSError as e:
                messagebox.showerror("保存失败", f"数据写入失败：{e}")
        self._refresh_all()

    def _save_silent(self) -> None:
        with self._lock:
            try:
                save_data(self.courses, self.assignments, self.settings)
            except OSError:
                pass

    def _assignment_snapshot(self) -> list[Assignment]:
        with self._lock:
            return [replace(a, reminded=list(a.reminded),
                            remind_before=list(a.remind_before))
                    for a in self.assignments]

    def _course_of(self, course_id: str | None) -> Course | None:
        if not course_id:
            return None
        return next((c for c in self.courses if c.id == course_id), None)

    def _course_name_of(self, course_id: str | None) -> str | None:
        c = self._course_of(course_id)
        return c.name if c else None

    # ================= 作业列表页 =================
    def _selected_assignment(self) -> Assignment | None:
        sel = self.tree_assign.selection()
        if not sel:
            return None
        return next((a for a in self.assignments if a.id == sel[0]), None)

    def _on_add_assignment(self, default_date=None) -> None:
        dlg = AssignmentDialog(self, None, self._sorted_courses(),
                               default_date=default_date)
        if dlg.result:
            new_a, _ = dlg.result
            self._mutate(lambda: self.assignments.append(new_a))

    def _on_edit_assignment(self) -> None:
        a = self._selected_assignment()
        if not a:
            return
        idx = self.assignments.index(a)
        dlg = AssignmentDialog(self, a, self._sorted_courses())
        if dlg.result:
            new_a, reset = dlg.result
            if reset:
                new_a.reminded = []

            def apply() -> None:
                self.assignments[idx] = new_a
            self._mutate(apply)

    def _on_toggle_done(self) -> None:
        a = self._selected_assignment()
        if not a:
            return

        def apply() -> None:
            a.status = STATUS_DONE if not a.is_done() else STATUS_PENDING
        self._mutate(apply)

    def _on_delete_assignment(self) -> None:
        a = self._selected_assignment()
        if not a:
            return
        if not messagebox.askyesno("删除作业", f"确定删除作业「{a.title}」吗？"):
            return

        def apply() -> None:
            self.assignments.remove(a)
        self._mutate(apply)

    def _mark_done(self, assignment_id: str) -> None:
        a = next((x for x in self.assignments if x.id == assignment_id), None)
        if a and not a.is_done():
            self._mutate(lambda: setattr(a, "status", STATUS_DONE))

    def _refresh_assignments(self) -> None:
        tree = self.tree_assign
        sel = set(tree.selection())
        tree.delete(*tree.get_children())
        filt = self.var_filter.get()
        now = datetime.now()
        rows = []
        for a in self.assignments:
            if filt == "待完成" and a.is_done():
                continue
            if filt == "已完成" and not a.is_done():
                continue
            if filt == "即将截止" and (a.is_done() or a.due - now > timedelta(hours=24)):
                continue
            rows.append(a)
        rows.sort(key=lambda a: a.due)
        for a in rows:
            course = self._course_of(a.course_id)
            if a.is_done():
                tag, remaining = ("done", "—")
            elif a.due <= now:
                tag, remaining = ("overdue", format_remaining(a.due - now))
            elif a.due - now <= timedelta(hours=24):
                tag, remaining = ("soon", format_remaining(a.due - now))
            else:
                tag, remaining = (), format_remaining(a.due - now)
            if not a.is_done() and a.priority == "high":
                tag = tuple(tag) + ("hipri",)
            tree.insert("", "end", iid=a.id, tags=tag, values=(
                a.title, course.name if course else "未分类", a.kind_name(),
                a.due.strftime("%m-%d %H:%M"), remaining, a.priority_name(),
                "已完成" if a.is_done() else "待完成", a.note))
        keep = [iid for iid in sel if tree.exists(iid)]
        if keep:
            tree.selection_set(keep)

    def _on_export_csv(self) -> None:
        """把作业清单导出为 CSV（UTF-8 BOM，Excel 可直接打开）。"""
        path = filedialog.asksaveasfilename(
            parent=self, title="导出作业清单", defaultextension=".csv",
            filetypes=[("CSV 文件", "*.csv")],
            initialfile=f"作业清单-{datetime.now():%Y%m%d}.csv")
        if not path:
            return
        now = datetime.now()
        rows = sorted(self.assignments, key=lambda a: a.due)
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(["标题", "课程", "类型", "优先级", "截止时间",
                            "状态", "剩余", "备注"])
                for a in rows:
                    course = self._course_of(a.course_id)
                    done = a.is_done()
                    w.writerow([
                        a.title, course.name if course else "未分类",
                        a.kind_name(), a.priority_name(),
                        a.due.strftime("%Y-%m-%d %H:%M"),
                        "已完成" if done else "待完成",
                        "—" if done else format_remaining(a.due - now),
                        a.note])
        except OSError as e:
            messagebox.showerror("导出失败", f"无法写入文件：{e}", parent=self)
            return
        messagebox.showinfo(
            "导出成功", f"已导出 {len(rows)} 条作业到：\n{path}", parent=self)

    # ================= 周课表页 =================
    def _semester_monday(self):
        """开学周一（date），未设置返回 None。"""
        s = self.settings.get("semester_start")
        if not s:
            return None
        try:
            d = date.fromisoformat(str(s))
        except ValueError:
            return None
        return d - timedelta(days=d.weekday())

    def _week_monday(self, week_no: int):
        """第 week_no 教学周的周一日期。开学未设置时以本周为第 1 周。"""
        base = self._semester_monday()
        today = date.today()
        if base is None:
            return today - timedelta(days=today.weekday())
        return base + timedelta(weeks=week_no - 1)

    def _view_week(self) -> int:
        """当前查看的教学周号（1 起，1~MAX_WEEK）。"""
        today = date.today()
        cur = semester_week(self._semester_monday(), today)
        return max(1, min(MAX_WEEK, cur + self._week_offset))

    def _shift_week(self, delta: int) -> None:
        self._week_offset += delta
        self._refresh_timetable()

    def _reset_week(self) -> None:
        self._week_offset = 0
        self._refresh_timetable()

    def _on_set_semester(self) -> None:
        text = self.e_semester.get().strip()
        try:
            d = date.fromisoformat(text)
        except ValueError:
            messagebox.showerror("输入有误", "开学日期格式应为 YYYY-MM-DD", parent=self)
            return
        d = d - timedelta(days=d.weekday())   # 归一到周一
        self.e_semester.delete(0, "end")
        self.e_semester.insert(0, d.isoformat())
        self._week_offset = 0
        self._mutate(lambda: self.settings.update(semester_start=d.isoformat()))

    def _refresh_timetable(self) -> None:
        now = datetime.now()
        week_no = self._view_week()
        self.timetable.render(self.courses, now, week_no=week_no,
                              week_monday=self._week_monday(week_no))
        start = self._week_monday(week_no)
        end = start + timedelta(days=6)
        cur = semester_week(self._semester_monday(), now.date())
        mark = "" if self._week_offset == 0 else f"（本周为第 {cur} 周）"
        self.var_week.set(f"第 {week_no} 周　{start.month}/{start.day} – {end.month}/{end.day} {mark}")
        if not self.e_semester.get():
            base = self._semester_monday() or (now.date() - timedelta(days=now.weekday()))
            self.e_semester.insert(0, base.isoformat())

    # ================= 课程管理页 =================
    def _sorted_courses(self) -> list[Course]:
        return sorted(self.courses, key=lambda c: (c.weekday, c.start))

    def _selected_course(self) -> Course | None:
        sel = self.tree_course.selection()
        if not sel:
            return None
        return next((c for c in self.courses if c.id == sel[0]), None)

    def _on_add_course(self) -> None:
        dlg = CourseDialog(self)
        if dlg.result:
            self._mutate(lambda: self.courses.append(dlg.result))

    def _on_add_course_preset(self, weekday: int, start: str) -> None:
        dlg = CourseDialog(self, preset=(weekday, start))
        if dlg.result:
            self._mutate(lambda: self.courses.append(dlg.result))

    def _on_edit_course_sel(self) -> None:
        c = self._selected_course()
        if c:
            self._on_edit_course_obj(c)

    def _on_edit_course_obj(self, course: Course) -> None:
        idx = self.courses.index(course)
        dlg = CourseDialog(self, course)
        if dlg.result:
            new_course = dlg.result

            def apply() -> None:
                self.courses[idx] = new_course
            self._mutate(apply)

    def _on_delete_course(self) -> None:
        c = self._selected_course()
        if not c:
            return
        linked = [a for a in self.assignments if a.course_id == c.id]

        def apply_course_only() -> None:
            self.courses.remove(c)

        if not linked:
            if not messagebox.askyesno("删除课程", f"确定删除课程「{c.name}」吗？"):
                return
            self._mutate(apply_course_only)
            return
        if messagebox.askyesno(
                "删除课程",
                f"课程「{c.name}」下有 {len(linked)} 个作业。\n"
                "是否同时删除这些作业？\n"
                "（选“否”则作业保留，显示为未分类）"):
            def apply_both() -> None:
                self.courses.remove(c)
                self.assignments = [a for a in self.assignments
                                    if a.course_id != c.id]
            self._mutate(apply_both)
        else:
            if not messagebox.askyesno(
                    "删除课程",
                    f"确定删除课程「{c.name}」吗？（其作业将保留为未分类）"):
                return
            self._mutate(apply_course_only)

    def _refresh_courses(self) -> None:
        tree = self.tree_course
        sel = set(tree.selection())
        tree.delete(*tree.get_children())
        for c in self._sorted_courses():
            tree.insert("", "end", iid=c.id, values=(
                c.name, c.teacher, c.location,
                c.weekday_name(), f"{c.start}-{c.end}", c.weeks_label()))
        keep = [iid for iid in sel if tree.exists(iid)]
        if keep:
            tree.selection_set(keep)

    # ================= 提醒接收（主线程） =================
    def _poll_reminder_queue(self) -> None:
        while True:
            try:
                evt = self._queue.get_nowait()
            except queue.Empty:
                break
            a = next((x for x in self.assignments if x.id == evt.assignment_id), None)
            if a is None or a.is_done():
                continue
            if evt.level not in a.reminded:
                self._mutate(lambda: a.reminded.append(evt.level))
            try:
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            except Exception:
                pass
            popup = ReminderPopup(
                self, evt,
                on_done=lambda aid=a.id: self._mark_done(aid),
                cascade=len(self._popups),
            )
            self._popups.append(popup)
            popup.bind("<Destroy>",
                       lambda e, p=popup: self._popups.remove(p)
                       if p in self._popups and e.widget is p else None)
            remaining = format_remaining(evt.due - datetime.now())
            send_windows_toast(
                "作业已逾期" if evt.level == "overdue" else "作业即将截止",
                f"{evt.title}（{evt.course_name or '未分类'}）"
                f" 截止 {evt.due:%m-%d %H:%M}，{remaining}")
            self._refresh_all()
        self.after(500, self._poll_reminder_queue)

    # ================= 刷新与状态栏 =================
    def _refresh_all(self) -> None:
        self._refresh_assignments()
        self._refresh_timetable()
        self._refresh_month()
        self._refresh_courses()
        self._refresh_stats()
        self._update_statusbar()

    def _update_statusbar(self) -> None:
        pending = [a for a in self.assignments if not a.is_done()]
        text = f"共 {len(self.courses)} 门课程 · {len(pending)} 个待完成"
        if pending:
            next_a = min(pending, key=lambda a: a.due)
            text += (f" · 最近截止: {next_a.title}"
                     f"({format_remaining(next_a.due - datetime.now())})")
        self.var_status.set(text)

    def _minute_tick(self) -> None:
        self._refresh_assignments()
        self._refresh_timetable()
        self._refresh_month()
        self._refresh_stats()
        self._update_statusbar()
        self.after(60_000, self._minute_tick)

    def _on_close(self) -> None:
        self._service.stop()
        self.tray.stop()
        self._save_silent()
        self.destroy()

    # ================= 系统托盘 =================
    def _minimize_to_tray(self) -> None:
        """点窗口关闭按钮：托盘可用则最小化到托盘（提醒继续），否则直接退出。"""
        if not self.tray.ok:
            self._on_close()
            return
        self.withdraw()
        if not self._tray_balloon_shown:
            self._tray_balloon_shown = True
            self.tray.show_balloon(
                "课程作业管理",
                "已最小化到系统托盘，作业截止提醒将继续运行。\n"
                "右键托盘图标可退出程序。")

    def _poll_tray_queue(self) -> None:
        while True:
            try:
                evt = self._tray_events.get_nowait()
            except queue.Empty:
                break
            if evt == ("show",):
                self.deiconify()
                self.lift()
                self.focus_force()
            elif evt == ("quit",):
                self._on_close()
                return
        self.after(200, self._poll_tray_queue)


def main() -> None:
    _enable_dpi_awareness()
    App().mainloop()


if __name__ == "__main__":
    main()
