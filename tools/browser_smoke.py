"""Real Chromium smoke test; uses temporary data, no normal-user configuration.

Run explicitly: python tools/browser_smoke.py
Requires requirements-dev.txt and an installed Chrome or Edge. No downloaded
browser or Node dependencies. Screenshots go to .runtime/qa/.
"""
from __future__ import annotations

import base64
from datetime import date
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import websocket
from duty_app.server import AppServer
from duty_app.service import DutyService
from duty_app.storage import Repository


class FakeStartup:
    value = False

    def enabled(self):
        return self.value

    def set_enabled(self, value):
        self.value = value


class CDP:
    def __init__(self, url):
        self.ws = websocket.create_connection(url, timeout=12, suppress_origin=True,
                                              http_no_proxy=["127.0.0.1", "localhost"])
        self.sequence, self.errors = 0, []

    def send(self, method, params=None):
        self.sequence += 1
        self.ws.send(json.dumps({"id":self.sequence,"method":method,"params":params or {}}))
        return self.sequence

    def call(self, method, params=None):
        wanted = self.send(method, params)
        while True:
            response = json.loads(self.ws.recv())
            if response.get("method") == "Page.javascriptDialogOpening":
                self.send("Page.handleJavaScriptDialog", {"accept":True})
            if response.get("method") == "Runtime.exceptionThrown":
                self.errors.append(response["params"]["exceptionDetails"])
            if response.get("id") == wanted:
                if "error" in response:
                    raise AssertionError(response["error"])
                return response.get("result", {})

    def evaluate(self, expression):
        result = self.call("Runtime.evaluate", {"expression":expression,"returnByValue":True,
                                                "awaitPromise":True,"userGesture":True})
        if result.get("exceptionDetails"):
            raise AssertionError(result["exceptionDetails"])
        return result.get("result", {}).get("value")

    def wait(self, expression, timeout=12):
        until = time.monotonic()+timeout
        while time.monotonic() < until:
            if self.evaluate(expression):
                return
            time.sleep(.1)
        raise AssertionError(f"Timed out: {expression}\n"+str(self.evaluate("document.body.innerText")))

    def screenshot(self, destination):
        metrics = self.call("Page.getLayoutMetrics")
        size = metrics["cssContentSize"]
        image = self.call("Page.captureScreenshot", {"format":"png","captureBeyondViewport":True,
                           "clip":{"x":0,"y":0,"width":size["width"],"height":size["height"],"scale":1}})
        destination.write_bytes(base64.b64decode(image["data"]))


def main():
    runtime = ROOT/".runtime"
    runtime.mkdir(exist_ok=True)
    output = runtime/"qa"
    output.mkdir(exist_ok=True)
    candidates = [Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))/"Google/Chrome/Application/chrome.exe",
                  Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)"))/"Microsoft/Edge/Application/msedge.exe"]
    browser = next((p for p in candidates if p.is_file()), None)
    if browser is None:
        raise RuntimeError("请安装 Chrome 或 Edge 后运行浏览器验证")
    with tempfile.TemporaryDirectory(prefix="browser-", dir=runtime) as temp:
        root = Path(temp)
        repository = Repository(root/"duty.sqlite3")
        server = AppServer(("127.0.0.1", 0), DutyService(repository, today=lambda:date(2026,10,13),startup=FakeStartup()), ROOT)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        process = subprocess.Popen([str(browser), "--headless=new", "--disable-gpu", "--no-first-run",
                                    "--no-default-browser-check", "--remote-debugging-port=0",
                                    "--remote-debugging-address=127.0.0.1", f"--user-data-dir={root/'profile'}", "about:blank"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=="win32" else 0)
        cdp = None
        try:
            active = root/"profile/DevToolsActivePort"
            for _ in range(100):
                if active.exists():
                    break
                if process.poll() is not None:
                    raise AssertionError("浏览器退出")
                time.sleep(.1)
            port = active.read_text().splitlines()[0]
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(f"http://127.0.0.1:{port}/json", timeout=5) as response:
                targets = json.load(response)
            target = next(t for t in targets if t["type"] == "page")
            cdp = CDP(target["webSocketDebuggerUrl"])
            cdp.call("Page.enable")
            cdp.call("Runtime.enable")
            cdp.call("Emulation.setDeviceMetricsOverride", {"width":1440,"height":1100,"deviceScaleFactor":1,"mobile":False})
            cdp.call("Page.navigate", {"url":server.dashboard_url()})
            cdp.wait("document.querySelector('#authScreen') && !document.querySelector('#authScreen').hidden")
            cdp.evaluate("document.querySelector('#password').value='smoke-password';document.querySelector('#confirmPassword').value='smoke-password';document.querySelector('#authForm').requestSubmit()")
            cdp.wait("document.querySelector('#settingsDialog').open")
            cdp.evaluate("document.querySelector('#memberNames').value='1号\\n2号\\n3号\\n4号';document.querySelector('#memberNames').dispatchEvent(new Event('input'));document.querySelector('#startDate').value='2026-10-12';document.querySelector('#firstMop').value='2026-10-14';document.querySelector('#configForm').requestSubmit()")
            cdp.wait("document.querySelector('#todayName').textContent==='2号' && !document.querySelector('#settingsDialog').open")
            cdp.evaluate("document.querySelector('#missedButton').click()")
            cdp.wait("document.querySelector('#todayName').textContent==='1号'")
            assert cdp.evaluate("document.querySelector('[aria-label=\"2026-10-15 3号 拖地\"]')!==null")
            cdp.screenshot(output/"desktop.png")
            cdp.evaluate("document.querySelector('#missedButton').click()")
            cdp.wait("document.querySelector('#todayName').textContent==='2号'")
            cdp.evaluate("document.querySelector('#swapOpen').click()")
            cdp.wait("document.querySelector('#swapDialog').open")
            cdp.evaluate("document.querySelector('#swapFirst').value='2026-10-14';document.querySelector('#swapSecond').value='2026-10-15';document.querySelector('#swapForm').requestSubmit()")
            cdp.wait("document.querySelector('[data-cancel-swap]')!==null")
            assert len(repository.read()[1]["swaps"]) == 1
            cdp.evaluate("document.querySelector('[data-cancel-swap]').click()")
            cdp.wait("document.querySelector('[data-cancel-swap]')===null")
            third = repository.read()[1]["configs"][0]["members"][2]["id"]
            cdp.evaluate(f"document.querySelector('[data-member=\"{third}\"]').click()")
            cdp.wait(f"document.querySelector('[data-member=\"{third}\"]').textContent==='已返校'")
            cdp.evaluate(f"document.querySelector('[data-member=\"{third}\"]').click()")
            cdp.wait("document.querySelector('#members').textContent.includes('明日起优先值日')")
            cdp.evaluate("document.querySelector('#monthView').click()")
            cdp.wait("document.querySelector('#monthView').getAttribute('aria-pressed')==='true'")
            cdp.evaluate("document.querySelector('#next').click()")
            cdp.wait("document.querySelector('#calendarRange').textContent.includes('11 月')")
            cdp.evaluate("document.querySelector('#goToday').click()")
            cdp.wait("document.querySelector('#calendarRange').textContent.includes('10 月')")
            cdp.evaluate("document.querySelector('#settingsOpen').click()")
            cdp.evaluate("document.querySelector('#reminderInput').value='21:45';document.querySelector('#reminderForm').requestSubmit()")
            cdp.wait("document.querySelector('#reminderTime').textContent==='21:45'")
            cdp.call("Emulation.setDeviceMetricsOverride", {"width":390,"height":844,"deviceScaleFactor":1,"mobile":True})
            cdp.wait("window.innerWidth===390")
            assert cdp.evaluate("document.documentElement.scrollWidth<=window.innerWidth"), "手机页面横向溢出"
            cdp.screenshot(output/"mobile.png")
            cdp.evaluate("document.querySelector('#settingsOpen').click()")
            cdp.wait("document.querySelector('#settingsDialog').open")
            assert cdp.evaluate("document.querySelector('#settingsDialog').getBoundingClientRect().width<=390")
            cdp.screenshot(output/"mobile-settings.png")
            cdp.evaluate("document.querySelector('#settingsDialog [data-close]').click();document.querySelector('#logout').click()")
            cdp.wait("!document.querySelector('#authScreen').hidden")
            cdp.evaluate("document.querySelector('#password').value='smoke-password';document.querySelector('#authForm').requestSubmit()")
            cdp.wait("!document.querySelector('#dashboard').hidden")
            assert not cdp.errors, cdp.errors
            print(json.dumps({"result":"passed", "desktop":"1440x1100", "mobile":"390x844", "javascript_errors":0,
                              "checks":["首次设置","忘做及撤销","拖地顺延","临时换班及撤销","回家返校","月视图导航","提醒设置","手机布局","登录退出"],
                              "screenshots":str(output)}, ensure_ascii=False))
        finally:
            if cdp:
                cdp.ws.close()
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    main()
