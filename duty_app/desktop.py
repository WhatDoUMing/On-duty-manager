from __future__ import annotations

import ctypes
from ctypes import wintypes
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
    from .notifications import deliver
    return deliver(data_dir, target, token)


def run_desktop(data_dir, port=8765, background=False, headless=False, activate_on_duplicate=True):
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    guard = SingleInstance(data_dir) if sys.platform == "win32" else None
    if guard and not guard.acquired:
        if not activate_on_duplicate:
            guard.close()
            return
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
