"""Exercise real Windows tray, instance mutex and toast without user settings.

The probe replaces browser opening with a counter and uses an isolated toast
identity. Data and measurements are isolated in .runtime.
"""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def host(data_dir):
    import webbrowser
    import pystray
    from duty_app.desktop import run_desktop
    original = pystray.Icon

    def opened(_url):
        with (data_dir/"opened.txt").open("a",encoding="utf-8") as file:
            file.write("opened\n")
        return True
    webbrowser.open = opened

    def icon_factory(*args, **kwargs):
        icon = original(*args, **kwargs)
        def stop_when_requested():
            while not (data_dir/"stop").exists():
                time.sleep(.2)
            icon.stop()
        threading.Thread(target=stop_when_requested, daemon=True).start()
        (data_dir/"ready").write_text("ready")
        return icon
    pystray.Icon = icon_factory
    run_desktop(data_dir, port=0, background=True)


def popup_test(data_dir, target, token):
    from duty_app.desktop import run_popup
    from duty_app.notifications import action_uri
    from duty_app.storage import Repository
    assert run_popup(data_dir, target, token)
    repository=Repository(data_dir/"duty.sqlite3")
    assert repository.reminder(target)[0]=="notified"
    os.startfile(action_uri(data_dir,target,token,"snooze"))
    for _ in range(80):
        if repository.reminder(target)[0]=="snoozed":
            return
        time.sleep(.1)
    raise AssertionError("Native toast snooze activation failed")


def main():
    if sys.platform != "win32":
        raise SystemExit("Desktop smoke test requires Windows")
    import psutil
    from duty_app.engine import empty_state, make_config
    from duty_app.storage import Repository
    runtime = ROOT/".runtime"
    runtime.mkdir(exist_ok=True)
    (runtime/"qa").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="中文托盘测试-",dir=runtime) as temp:
        data_dir = Path(temp)
        repo = Repository(data_dir/"duty.sqlite3")
        state = empty_state()
        state["configs"] = [make_config(["1号","2号","3号"],datetime.now().date().isoformat())]
        state["settings"]["paused"] = True
        repo.mutate(0, lambda _: state)
        flags = subprocess.CREATE_NO_WINDOW
        windowless = Path(sys.executable).with_name("pythonw.exe")
        executable = str(windowless) if windowless.exists() else sys.executable
        process = subprocess.Popen([executable,__file__,"--host",str(data_dir)],cwd=ROOT,
                                    stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,creationflags=flags)
        try:
            for _ in range(100):
                if (data_dir/"ready").exists():
                    break
                if process.poll() is not None:
                    raise AssertionError(process.stderr.read().decode(errors="replace"))
                time.sleep(.1)
            assert (data_dir/"ready").exists(), "tray startup timed out"
            time.sleep(1)
            measured = psutil.Process(process.pid)
            children = measured.children(recursive=True)
            assert not children, f"unexpected resident children: {children}"
            cpu = measured.cpu_percent(interval=20)
            memory = measured.memory_info()
            duplicate = subprocess.run([executable,"-m","duty_app","--data-dir",str(data_dir),"--background"],
                                        cwd=ROOT,capture_output=True,timeout=12,creationflags=flags)
            assert duplicate.returncode==0, duplicate.stderr
            assert (data_dir/"opened.txt").read_text().count("opened")==1
            target = datetime.now().date().isoformat()
            token = repo.claim_reminder(datetime.now(), "00:00")
            popup = subprocess.run([executable,__file__,"--popup-test",str(data_dir),target,token],
                                    cwd=ROOT,capture_output=True,timeout=15,creationflags=flags)
            assert popup.returncode==0, popup.stderr
            assert repo.reminder(target)[0]=="snoozed", "popup did not persist snooze"
            assert (data_dir/"opened.txt").read_text().count("opened")==1, "snooze unexpectedly opened browser"
            assert not measured.children(recursive=True), "popup process still resident"
            result = {"result":"passed","private_mb":round(memory.private/1024**2,2),
                      "working_set_mb":round(memory.rss/1024**2,2),"idle_cpu_percent":cpu,
                      "measurement_seconds":20,"resident_children":0,
                      "checks":["native tray","single instance activation","unicode paths",
                                "Windows toast","native snooze activation","notification process exit"]}
            (runtime/"qa/desktop-metrics.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
            print(json.dumps(result))
        finally:
            (data_dir/"stop").touch()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)
            stderr=process.stderr.read()
            process.stderr.close()
            if stderr:
                print(stderr.decode(errors="replace"),file=sys.stderr)
            from duty_app.notifications import cleanup_test_registration
            cleanup_test_registration(data_dir)
        assert not (data_dir/"runtime.json").exists(), "runtime discovery file not cleaned up"


if __name__ == "__main__":
    if len(sys.argv)>1 and sys.argv[1]=="--host":
        host(Path(sys.argv[2]))
    elif len(sys.argv)>1 and sys.argv[1]=="--popup-test":
        popup_test(Path(sys.argv[2]),sys.argv[3],sys.argv[4])
    else:
        main()
