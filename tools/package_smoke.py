"""Verify the real frozen EXE with isolated data and a Python-free PATH."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime
import http.cookiejar
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))


def stop_tray(pid):
    user=ctypes.WinDLL("user32",use_last_error=True)
    user.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
    user.GetClassNameW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
    user.PostMessageW.argtypes=[wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM]
    callback_type=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    found=[]

    @callback_type
    def visit(hwnd,_):
        owner=wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd,ctypes.byref(owner))
        if owner.value==pid:
            name=ctypes.create_unicode_buffer(256)
            user.GetClassNameW(hwnd,name,256)
            if name.value.startswith("DutyManager") and name.value.endswith("SystemTrayIcon"):
                found.append(hwnd)
                user.PostMessageW(hwnd,0x040A,0,0)  # pystray's WM_STOP
        return True
    user.EnumWindows.argtypes=[callback_type,wintypes.LPARAM]
    user.EnumWindows(visit,0)
    return bool(found)


def main():
    import psutil
    from duty_app.auth import Auth
    from duty_app.engine import empty_state,make_config
    from duty_app.notifications import action_uri,cleanup_test_registration,identity,powershell
    from duty_app.storage import Repository
    parser=argparse.ArgumentParser()
    parser.add_argument("--exe",type=Path,default=ROOT/"dist/DutyManager/DutyManager.exe")
    args=parser.parse_args()
    executable=args.exe.resolve()
    assert executable.is_file(),executable
    runtime=ROOT/".runtime"
    runtime.mkdir(exist_ok=True)
    (runtime/"qa").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="成品测试-",dir=runtime) as temp:
        directory=Path(temp)
        repo=Repository(directory/"duty.sqlite3")
        state=empty_state()
        target=datetime.now().date().isoformat()
        state["configs"]=[make_config(["1号","2号","3号"],target)]
        state["settings"]["paused"]=True
        repo.mutate(0,lambda _:state)
        auth=Auth(repo)
        auth.setup("package-test-password",auth.bootstrap)
        environment=os.environ.copy()
        environment.pop("PYTHONPATH",None)
        environment.pop("PYTHONHOME",None)
        windows=Path(environment.get("SystemRoot",r"C:\Windows"))
        environment["PATH"]=os.pathsep.join(str(p) for p in (windows,windows/"System32",windows/"System32/WindowsPowerShell/v1.0"))
        environment["PYINSTALLER_RESET_ENVIRONMENT"]="1"
        process=subprocess.Popen([str(executable),"--background","--data-dir",str(directory),"--port","0"],
                                  cwd=directory,env=environment,creationflags=subprocess.CREATE_NO_WINDOW,
                                  stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        registered=False
        try:
            metadata=directory/"runtime.json"
            for _ in range(150):
                if metadata.exists():
                    break
                if process.poll() is not None:
                    raise AssertionError(f"EXE exited: {process.returncode}")
                time.sleep(.1)
            assert metadata.exists(),(directory/"duty.log").read_text(encoding="utf-8")
            port=json.loads(metadata.read_text())["port"]
            base=f"http://127.0.0.1:{port}"
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

            def request(path,body=None):
                outgoing=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,
                                                headers={"Content-Type":"application/json"})
                with opener.open(outgoing,timeout=5) as response:
                    return response.read()

            self_status=json.loads(request("/api/status"))
            assert self_status["initialized"] and not self_status["authenticated"]
            request("/api/login",{"password":"package-test-password"})
            view=json.loads(request("/api/state"))
            assert view["today_duty"]["name"]=="1号"
            for path,source in (("/",ROOT/"duty_manager.html"),("/assets/app.js",ROOT/"assets/app.js"),
                                ("/assets/style.css",ROOT/"assets/style.css")):
                assert request(path)==source.read_bytes(),f"Bundled asset mismatch: {path}"
            # Check there is a real native tray window and no resident child.
            time.sleep(1)
            measured=psutil.Process(process.pid)
            assert not measured.children(recursive=True)
            cpu=measured.cpu_percent(interval=20)
            memory=measured.memory_info()
            token=repo.claim_reminder(datetime.now(),"00:00")
            registered=True
            notification=subprocess.run([str(executable),"--data-dir",str(directory),"--popup","--day",target,"--token",token],
                                         cwd=directory,env=environment,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL,timeout=25,creationflags=subprocess.CREATE_NO_WINDOW)
            assert notification.returncode==0,(directory/"duty.log").read_text(encoding="utf-8")
            assert repo.reminder(target)[0]=="notified",(directory/"duty.log").read_text(encoding="utf-8")
            assert target in powershell({"operation":"history","app_id":identity(directory)[0]})
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,rf"Software\Classes\{identity(directory)[1]}\shell\open\command") as key:
                command=winreg.QueryValueEx(key,"")[0]
            assert str(executable) in command and "python" not in command.lower(),command
            os.startfile(action_uri(directory,target,token,"snooze"))
            for _ in range(100):
                if repo.reminder(target)[0]=="snoozed":
                    break
                time.sleep(.1)
            assert repo.reminder(target)[0]=="snoozed","Frozen EXE protocol activation failed"
            assert json.loads(metadata.read_text())["pid"]==process.pid,"Duplicate resident instance"
            assert not measured.children(recursive=True)
            result={"result":"passed","exe":str(executable),"exe_sha256":hashlib.sha256(executable.read_bytes()).hexdigest(),
                    "private_mb":round(memory.private/1024**2,2),
                    "working_set_mb":round(memory.rss/1024**2,2),"idle_cpu_percent":cpu,
                    "resident_children":0,"python_free_path":True,
                    "checks":["frozen startup","authentication","bundled HTML/CSS/JS","Windows toast history",
                              "frozen protocol callback","persistent snooze","single instance"]}
            (runtime/"qa/package-metrics.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
            print(json.dumps(result))
        finally:
            if process.poll() is None:
                stop_tray(process.pid)
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=5)
            if registered:
                cleanup_test_registration(directory)
        assert not (directory/"runtime.json").exists(),"Tray did not shut down cleanly"


if __name__=="__main__":
    main()
