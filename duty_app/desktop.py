from __future__ import annotations

import ctypes
from ctypes import wintypes
from datetime import datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import urllib.request
import webbrowser

from .engine import schedule
from .reminders import Reminders
from .server import AppServer
from .service import DutyService
from .startup import Startup
from .storage import Repository


def default_data_dir():
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "DutyManager"


def asset_dir():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


class SingleInstance:
    def __init__(self, data_dir):
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel.CreateMutexW.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        name = "Local\\DutyManager-"+hashlib.sha256(str(Path(data_dir).resolve()).lower().encode()).hexdigest()[:20]
        self.handle = self.kernel.CreateMutexW(None, False, name)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.acquired = ctypes.get_last_error() != 183

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def activate_existing(data_dir):
    try:
        metadata = json.loads((Path(data_dir)/"runtime.json").read_text(encoding="utf-8"))
        request = urllib.request.Request(f'http://127.0.0.1:{metadata["port"]}/internal/activate',
                                         data=b"{}", headers={"Content-Type": "application/json",
                                                               "X-Instance-Key": metadata["key"]})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=4) as response:
            return response.status == 200
    except (OSError, ValueError, KeyError):
        return False


def run_popup(data_dir, target, token):
    # This module intentionally does not import Tk/Pillow/pystray in the parent.
    import tkinter as tk
    from tkinter import ttk
    repository = Repository(Path(data_dir)/"duty.sqlite3")
    reminder = repository.reminder(target)
    if not reminder or reminder[2] != token or target != datetime.now().date().isoformat():
        return
    _, state = repository.read()
    info = schedule(state, target, target)[0]
    if not info["member_id"]:
        repository.reminder_action(target, token, "dismiss", datetime.now())
        return
    root = tk.Tk()
    root.title("值日管家 · 今日提醒")
    root.configure(bg="#f5f3ed")
    root.resizable(False, False)
    root.attributes("-topmost", True)
    width, height = 440, 370 if len(info["name"]) > 10 else 320
    root.geometry(f"{width}x{height}+{(root.winfo_screenwidth()-width)//2}+{(root.winfo_screenheight()-height)//2}")
    frame = tk.Frame(root, bg="#f5f3ed", padx=28, pady=24)
    frame.pack(fill="both", expand=True)
    tk.Label(frame, text=f"{target}  ·  今日值日", bg="#f5f3ed", fg="#777", font=("Microsoft YaHei UI", 11)).pack(anchor="w")
    tk.Label(frame, text=info["name"], bg="#f5f3ed", fg="#2c2c2c", wraplength=384, justify="left",
             font=("Microsoft YaHei UI", 20 if len(info["name"]) > 10 else 30, "bold")).pack(anchor="w", pady=(16, 6))
    tk.Label(frame, text="倒垃圾 + 拖地" if info["mop"] else "倒垃圾 · 今日无需拖地", bg="#f5f3ed", fg="#c5483d", font=("Microsoft YaHei UI", 13)).pack(anchor="w")
    tk.Label(frame, text="补做任务，请记得完成" if info["carried"] else "花一点时间，让公共空间保持整洁。", bg="#f5f3ed", fg="#777", font=("Microsoft YaHei UI", 10)).pack(anchor="w", pady=(12, 22))

    def finish(action):
        repository.reminder_action(target, token, action, datetime.now())
        root.destroy()
        if action == "open":
            activate_existing(data_dir)

    buttons = tk.Frame(frame, bg="#f5f3ed")
    buttons.pack(fill="x")
    for label, action in (("打开管理", "open"), ("十分钟后提醒", "snooze"), ("关闭本次", "dismiss")):
        ttk.Button(buttons, text=label, command=lambda a=action: finish(a)).pack(side="left", padx=(0, 8))
    root.protocol("WM_DELETE_WINDOW", lambda: finish("dismiss"))

    def check_date():
        if target != datetime.now().date().isoformat():
            root.destroy()
        else:
            root.after(15000, check_date)
    root.after(15000, check_date)
    root.mainloop()


def run_desktop(data_dir, port=8765, background=False, headless=False):
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    guard = SingleInstance(data_dir) if sys.platform == "win32" else None
    if guard and not guard.acquired:
        # The first process may still be binding its socket.
        for _ in range(20):
            if activate_existing(data_dir):
                break
            threading.Event().wait(.2)
        guard.close()
        return
    repository = Repository(data_dir/"duty.sqlite3")
    startup = Startup(command=Startup.default_command(data_dir)) if sys.platform == "win32" else None
    service = DutyService(repository, startup=startup)
    instance_key = secrets.token_urlsafe(32)
    server = None
    popup = None

    def open_dashboard(*_):
        webbrowser.open(server.dashboard_url())

    def popup_alive():
        return popup is not None and popup.poll() is None

    def launch(target, token):
        nonlocal popup
        args = ([sys.executable] if getattr(sys, "frozen", False)
                else [sys.executable, str(asset_dir()/"run_duty.pyw")])
        args += ["--data-dir", str(data_dir), "--popup", "--day", target, "--token", token]
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
        popup = subprocess.Popen(args, **options)

    worker = Reminders(repository, launch, alive=popup_alive)
    server_thread = reminder_thread = None
    try:
        try:
            server = AppServer(("0.0.0.0", port), service, asset_dir(), instance_key, open_dashboard)
        except OSError:
            server = AppServer(("0.0.0.0", 0), service, asset_dir(), instance_key, open_dashboard)
        runtime = {"port": server.server_address[1], "pid": os.getpid(), "key": instance_key}
        (data_dir/"runtime.json").write_text(json.dumps(runtime), encoding="utf-8")
        server_thread = threading.Thread(target=server.serve_forever, name="duty-http", daemon=True)
        reminder_thread = threading.Thread(target=worker.run, name="duty-reminders", daemon=True)
        server_thread.start()
        reminder_thread.start()
        if headless:
            logging.info("服务运行于端口 %s", runtime["port"])
            while server_thread.is_alive():
                server_thread.join(1)
            return
        # Only desktop mode needs tray dependencies. No browser engine is loaded.
        import pystray
        from PIL import Image, ImageDraw
        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((4, 4, 60, 60), radius=16, fill="#d9453a")
        draw.line((19, 33, 28, 42, 46, 22), fill="white", width=6)

        def pause(icon, _item):
            revision, state = repository.read()
            service.edit("settings", {"revision": revision, "paused": not state["settings"]["paused"]})
            icon.update_menu()

        def show_addresses(*_):
            server.refresh_addresses(force=True)
            message = "\n".join(f"http://{ip}:{runtime['port']}" for ip in server.addresses)
            if sys.platform == "win32":
                ctypes.windll.user32.MessageBoxW(None, message or "请先连接 Wi-Fi 或有线网络", "手机管理地址 · 需输入管理口令", 0x40)

        icon = pystray.Icon("DutyManager", image, "值日管家 · 每日提醒", menu=pystray.Menu(
            pystray.MenuItem("打开值日管理", open_dashboard, default=True),
            pystray.MenuItem("查看手机管理地址", show_addresses),
            pystray.MenuItem("暂停提醒", pause, checked=lambda _: repository.read()[1]["settings"]["paused"]),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", lambda tray, _: tray.stop())))

        def setup(tray):
            tray.visible = True
            if not background:
                open_dashboard()
        icon.run(setup=setup)
    finally:
        worker.stop_event.set()
        if popup_alive():
            popup.terminate()
            try:
                popup.wait(timeout=3)
            except subprocess.TimeoutExpired:
                popup.kill()
        if server:
            if server_thread and server_thread.is_alive():
                server.shutdown()
            server.server_close()
        if reminder_thread:
            reminder_thread.join(timeout=2)
        (data_dir/"runtime.json").unlink(missing_ok=True)
        if guard:
            guard.close()
