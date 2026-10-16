from datetime import date, timedelta
import hashlib
import hmac
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

from duty_app.desktop import SingleInstance as BaseInstance, asset_dir
from .reminders import Reminders
from .server import AppServer
from .service import DutyService
from .startup import Startup
from .storage import Repository


def default_data_dir():
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home())))/"CampusDutyManager"


class SingleInstance(BaseInstance):
    # The namespace also differs when a caller explicitly supplies the same path.
    def __init__(self, data_dir):
        super().__init__(str(Path(data_dir)/"CampusDutyManager-instance"))


def internal_request(data_dir, route, body=None):
    meta = json.loads((Path(data_dir)/"runtime.json").read_text(encoding="utf-8"))
    request = urllib.request.Request(f'http://127.0.0.1:{meta["port"]}/internal/{route}',
                                     data=json.dumps(body or {}).encode(),
                                     headers={"Content-Type": "application/json", "X-Instance-Key": meta["key"]})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=4) as response:
        return json.loads(response.read())


def run_popup(data_dir, delivery, token):
    from .native import Window
    repo = Repository(Path(data_dir)/"campus.sqlite3")
    row = repo.reminder(delivery)
    if (delivery != date.today().isoformat() or not row or row[0] not in ("showing", "notified")
            or not isinstance(token, str) or not hmac.compare_digest(row[2], token)):
        return
    service = DutyService(repo)

    def action(value):
        from datetime import datetime
        if value == "open":
            url = internal_request(data_dir, "display-entry", {"day": delivery, "token": token})["url"]
            if not webbrowser.open(url):
                raise OSError("浏览器启动失败")
            return
        repo.reminder_action(delivery, token, value, datetime.now())

    window = None

    def data():
        if window is not None and date.today().isoformat() != delivery:
            window.u.PostMessageW(window.hwnd, Window.STOP, 0, 0)
        return service.display()

    window = Window(data, action=action)
    repo.mark_notified(delivery, token)
    window.run()


def run_desktop(data_dir, port=8766, background=False, headless=False):
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    guard = SingleInstance(data_dir) if sys.platform == "win32" else None
    if guard and not guard.acquired:
        try:
            for _ in range(20):
                try:
                    internal_request(data_dir, "activate")
                    break
                except (OSError, ValueError, KeyError):
                    threading.Event().wait(.2)
        finally:
            guard.close()
        return
    repo = Repository(data_dir/"campus.sqlite3")
    startup = Startup(command=Startup.default_command(data_dir)) if sys.platform == "win32" else None
    service = DutyService(repo, startup=startup)
    server = worker = widget = popup = None
    server_thread = reminder_thread = None

    def open_dashboard(*_):
        webbrowser.open(server.dashboard_url())

    def open_display(*_):
        webbrowser.open(server.display_url())

    def alive():
        return popup is not None and popup.poll() is None

    def launch(delivery, token, mode):
        nonlocal popup
        if mode == "display":
            if not webbrowser.open(server.display_url(delivery, token)):
                raise OSError("浏览器启动失败")
            return
        args = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, str(asset_dir()/"run_campus.pyw")]
        args += ["--data-dir", str(data_dir), "--popup", "--day", delivery, "--token", token]
        popup = subprocess.Popen(args, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)

    try:
        key = secrets.token_urlsafe(32)
        try:
            server = AppServer(("0.0.0.0", port), service, asset_dir(), key, open_dashboard)
        except OSError:
            server = AppServer(("0.0.0.0", 0), service, asset_dir(), key, open_dashboard)
        (data_dir/"runtime.json").write_text(json.dumps({"port": server.server_address[1], "pid": os.getpid(), "key": key}), encoding="utf-8")
        worker = Reminders(repo, service, launch, alive=alive)
        server_thread = threading.Thread(target=server.serve_forever, name="campus-http", daemon=True)
        reminder_thread = threading.Thread(target=worker.run, name="campus-reminders", daemon=True)
        server_thread.start()
        reminder_thread.start()
        if headless:
            while server_thread.is_alive():
                server_thread.join(1)
            return
        from .native import WidgetHost
        import pystray
        from PIL import Image, ImageDraw
        widget = WidgetHost(service)
        widget.start()
        service.on_change = widget.refresh
        image = Image.new("RGBA", (64, 64))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((3, 3, 61, 61), radius=16, fill="#37644c")
        draw.line((17, 32, 27, 42, 47, 21), fill="white", width=6)

        def setting(name):
            def toggle(icon, _):
                revision, state = repo.read()
                service.edit("settings", {"revision": revision, name: not state["settings"][name]})
                icon.update_menu()
            return toggle

        def addresses(*_):
            import ctypes
            server.refresh_addresses(force=True)
            urls = "\n".join(f'http://{ip}:{server.server_address[1]}' for ip in server.addresses)
            ctypes.windll.user32.MessageBoxW(None, urls or "请连接 Wi-Fi 或有线网络", "手机管理地址 · 需管理口令", 0x40)

        icon = pystray.Icon("CampusDutyManager", image, "校园值日管家", menu=pystray.Menu(
            pystray.MenuItem("打开校园值日管理", open_dashboard, default=True),
            pystray.MenuItem("打开展示页", open_display),
            pystray.MenuItem("显示今日桌面名单", setting("widget_visible"), checked=lambda _: repo.read()[1]["settings"]["widget_visible"]),
            pystray.MenuItem("查看手机管理地址", addresses),
            pystray.MenuItem("暂停提醒", setting("paused"), checked=lambda _: repo.read()[1]["settings"]["paused"]),
            pystray.Menu.SEPARATOR, pystray.MenuItem("退出", lambda tray, _: tray.stop())))

        def setup(tray):
            tray.visible = True
            if not background:
                open_dashboard()
        icon.run(setup=setup)
    finally:
        if worker:
            worker.stop_event.set()
        if widget:
            widget.stop()
        if alive():
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
