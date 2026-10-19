"""作业截止提醒：后台检查线程 + Windows 系统通知（零依赖）。"""
from __future__ import annotations

import base64
import queue
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

from models import OVERDUE_LEVEL, Assignment

_TOAST_APPID = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe"


@dataclass
class ReminderEvent:
    assignment_id: str
    level: str            # "24h" | "1h" | "overdue"
    title: str
    course_name: str | None
    due: datetime


class ReminderService(threading.Thread):
    """后台守护线程：每 CHECK_INTERVAL 秒检查一次快照，命中级别投递事件到队列。

    线程内不触碰任何 tkinter 对象；只读 provider() 返回的快照，只写 out_queue。
    """

    CHECK_INTERVAL = 30  # 秒

    def __init__(self, provider, out_queue: "queue.Queue[ReminderEvent]",
                 course_name_of=None):
        super().__init__(daemon=True, name="ReminderService")
        self.provider = provider
        self.out_queue = out_queue
        self.course_name_of = course_name_of or (lambda cid: None)
        self._stop = threading.Event()
        self._emitted: set[tuple[str, str]] = set()  # (assignment_id, level) 防投递窗口期重复

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.wait(self.CHECK_INTERVAL):
            try:
                self._check()
            except Exception:
                pass  # 提醒线程故障不应影响主程序

    def _check(self) -> None:
        now = datetime.now()
        for a in self.provider():
            if a.is_done():
                continue
            level = self._due_level(a, now)
            if level and (a.id, level) not in self._emitted:
                self._emitted.add((a.id, level))
                self.out_queue.put(ReminderEvent(
                    assignment_id=a.id,
                    level=level,
                    title=a.title,
                    course_name=self.course_name_of(a.course_id),
                    due=a.due,
                ))

    @staticmethod
    def _due_level(a: Assignment, now: datetime) -> str | None:
        """判定当前应提醒的级别。

        候选：已逾期（overdue，最紧急）与作业配置的各"截止前 N 分钟"偏移中
        当前适用的（0 < 剩余时间 <= N）。取最紧急（N 最小）的一个：
        已提醒过则返回 None——不降级到更宽松的级别重复提醒。
        """
        cands: list[tuple[int, str]] = []
        if now >= a.due:
            cands.append((0, OVERDUE_LEVEL))
        else:
            delta = a.due - now
            for m in set(a.remind_before):
                if m > 0 and delta <= timedelta(minutes=m):
                    cands.append((m, f"b{m}"))
        if not cands:
            return None
        cands.sort(key=lambda t: t[0])
        level = cands[0][1]
        return None if level in a.reminded else level


def _xml_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def send_windows_toast(title: str, message: str) -> bool:
    """通过 PowerShell 调 WinRT 发出系统 toast。非阻塞，失败返回 False（应用内弹窗兜底）。"""
    title = _xml_escape(title.replace("\r", " ").replace("\n", " "))
    message = _xml_escape(message.replace("\r", " ").replace("\n", " "))
    ps = "\n".join([
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications,"
        " ContentType = WindowsRuntime] | Out-Null",
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument,"
        " ContentType = WindowsRuntime] | Out-Null",
        "$x = New-Object Windows.Data.Xml.Dom.XmlDocument",
        '$xmlText = @"',
        f'<toast><visual><binding template="ToastGeneric">'
        f"<text>{title}</text><text>{message}</text></binding></visual></toast>",
        '"@',
        "$x.LoadXml($xmlText)",
        "$t = New-Object Windows.Data.Notifications.ToastNotification $x",
        f'$appid = "{_TOAST_APPID}"',
        "[Windows.UI.Notifications.ToastNotificationManager]"
        "::CreateToastNotifier($appid).Show($t)",
    ])
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand",
             base64.b64encode(ps.encode("utf-16-le")).decode("ascii")],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return True
    except OSError:
        return False
