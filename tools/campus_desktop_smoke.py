"""Real tray/widget/dialog checks, with isolated data and browser interception."""
import ctypes as C
from ctypes import wintypes as W
from datetime import date, datetime
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def host(root):
    import pystray
    import webbrowser
    from campus_app.native import WidgetHost
    from campus_app.desktop import run_desktop
    original, start = pystray.Icon, WidgetHost.start
    def opened(url):
        with (root/"opened.txt").open("a", encoding="utf-8") as file:
            file.write("opened\n")
        return True
    webbrowser.open = opened
    def widget_start(self):
        start(self)
        (root/"widget.json").write_text(json.dumps({"hwnd": self.window.hwnd}))
    WidgetHost.start = widget_start
    def icon_factory(*args, **kwargs):
        icon = original(*args, **kwargs)
        def stop():
            while not (root/"stop").exists():
                time.sleep(.2)
            icon.stop()
        threading.Thread(target=stop, daemon=True).start()
        (root/"ready").touch()
        return icon
    pystray.Icon = icon_factory
    run_desktop(root, port=0, background=True)


def main():
    import psutil
    from PIL import ImageGrab
    from campus_app.engine import empty_state, make_config
    from campus_app.storage import Repository
    runtime = ROOT/".runtime"
    output = runtime/"qa"
    output.mkdir(parents=True, exist_ok=True)
    user = C.WinDLL("user32")
    user.GetForegroundWindow.restype = W.HWND
    user.GetWindowLongW.argtypes = [W.HWND, C.c_int]
    user.GetWindowLongW.restype = W.LONG
    user.SendMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
    user.GetWindowRect.argtypes = [W.HWND, C.POINTER(W.RECT)]
    user.IsWindowVisible.argtypes = [W.HWND]
    user.GetWindow.argtypes, user.GetWindow.restype = [W.HWND, W.UINT], W.HWND
    flags = subprocess.CREATE_NO_WINDOW
    exe = Path(sys.executable).with_name("pythonw.exe")
    executable = str(exe) if exe.exists() else sys.executable
    with tempfile.TemporaryDirectory(prefix="中文 校园桌面-", dir=runtime) as temp:
        root = Path(temp)
        repo = Repository(root/"campus.sqlite3")
        state = empty_state()
        state["configs"] = [make_config({"class_name": "高一（1）班", "mode": "group",
              "students": [{"id": str(i), "name": name} for i, name in enumerate(("林予安", "陈星野", "沈知夏", "周亦辰"))],
              "areas": [{"id": "room", "name": "教室清扫", "groups": [
                  {"id": "a", "name": "第一组", "student_ids": ["0", "1"]},
                  {"id": "b", "name": "第二组", "student_ids": ["2", "3"]}]}]}, date.today().isoformat())]
        state["settings"]["paused"] = True
        repo.mutate(0, lambda _: state)
        foreground = user.GetForegroundWindow()
        process = subprocess.Popen([executable, __file__, "--host", str(root)], cwd=ROOT,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=flags)
        try:
            for _ in range(100):
                if (root/"ready").exists(): break
                if process.poll() is not None: raise AssertionError(process.stderr.read().decode(errors="replace"))
                time.sleep(.1)
            assert (root/"ready").exists(), "tray did not start"
            hwnd = json.loads((root/"widget.json").read_text())["hwnd"]
            assert user.IsWindowVisible(hwnd)
            exstyle = user.GetWindowLongW(hwnd, -20)
            assert exstyle & 0x08000000 and not exstyle & 0x8, "widget activated or topmost"
            assert user.GetForegroundWindow() == foreground, "widget stole foreground"
            ImageGrab.grab(window=hwnd).save(output/"campus-widget.png")
            rect = W.RECT()
            user.GetWindowRect(hwnd, C.byref(rect))
            original_height = rect.bottom-rect.top
            client = W.RECT()
            user.GetClientRect(C.c_void_p(hwnd), C.byref(client))
            user.SendMessageW(hwnd, 0x202, 0, (15 << 16) | (client.right-80))
            collapsed = W.RECT()
            user.GetWindowRect(hwnd, C.byref(collapsed))
            assert collapsed.bottom-collapsed.top < original_height, "collapse failed"
            user.SendMessageW(hwnd, 0x202, 0, (15 << 16) | (client.right-80))
            duplicate = subprocess.run([executable, "-m", "campus_app", "--data-dir", str(root), "--background"],
                                       cwd=ROOT, capture_output=True, timeout=12, creationflags=flags)
            assert duplicate.returncode == 0, duplicate.stderr
            assert (root/"opened.txt").read_text().count("opened") == 1
            measured = psutil.Process(process.pid)
            assert not measured.children(recursive=True)
            cpu = measured.cpu_percent(interval=20)
            memory = measured.memory_info()
            delivery = date.today().isoformat()
            token = repo.claim_reminder(datetime.now(), "00:00")
            popup = subprocess.Popen([executable, "-m", "campus_app", "--data-dir", str(root), "--popup", "--day", delivery, "--token", token],
                                     cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=flags)
            popup_handle = []
            enum_proc = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
            user.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
            def found(handle, _):
                pid = W.DWORD()
                user.GetWindowThreadProcessId(handle, C.byref(pid))
                if pid.value == popup.pid and user.IsWindowVisible(handle): popup_handle.append(handle)
                return True
            callback = enum_proc(found)
            try:
                for _ in range(100):
                    user.EnumWindows(callback, 0)
                    if popup_handle: break
                    if popup.poll() is not None: raise AssertionError(popup.stderr.read().decode(errors="replace"))
                    time.sleep(.1)
                assert popup_handle, "popup did not show"
                time.sleep(.3)
                user.GetDlgItem.argtypes, user.GetDlgItem.restype = [W.HWND, C.c_int], W.HWND
                for button_id in (101, 102, 103):
                    button = user.GetDlgItem(popup_handle[0], button_id)
                    button_rect, parent_rect = W.RECT(), W.RECT()
                    user.GetWindowRect(button, C.byref(button_rect))
                    user.GetWindowRect(popup_handle[0], C.byref(parent_rect))
                    assert user.IsWindowVisible(button) and button_rect.top > parent_rect.top+100
                    assert button_rect.bottom < parent_rect.bottom, "reminder button outside client area"
                ImageGrab.grab(window=popup_handle[0]).save(output/"campus-popup.png")
                # The notification is a native dialog, with no audio/WinRT sender process.
                user.SendMessageW(popup_handle[0], 0x111, 102, 0)
                popup.wait(timeout=6)
                assert popup.returncode == 0, popup.stderr.read()
                assert repo.reminder(delivery)[0] == "snoozed"
            finally:
                if popup.poll() is None:
                    popup.terminate()
                    popup.wait(timeout=4)
                popup.stderr.close()
            result = {"result": "passed", "idle_cpu_percent": cpu, "private_mb": round(memory.private/1024**2, 2),
                      "working_set_mb": round(memory.rss/1024**2, 2), "measurement_seconds": 20,
                      "resident_children": len(measured.children(recursive=True)),
                      "checks": ["native tray", "widget no focus/topmost", "widget collapse", "position persistence",
                                 "single instance activation", "Chinese/spaced path", "silent native dialog", "snooze", "dialog process exit"]}
            assert repo.get_meta("widget_layout") is not None
            (output/"campus-desktop-metrics.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
            print(json.dumps(result))
        finally:
            (root/"stop").touch()
            try: process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)
            stderr = process.stderr.read()
            process.stderr.close()
            if stderr: print(stderr.decode(errors="replace"), file=sys.stderr)
        assert not (root/"runtime.json").exists()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--host": host(Path(sys.argv[2]))
    else: main()
