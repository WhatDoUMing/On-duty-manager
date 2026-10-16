"""Opt-in daily wallpaper: one sleeping worker, renderer exits after changes.

Original images are copied before any Windows mutation; Windows may overwrite
its own TranscodedWallpaper cache. Restore only wallpaper still owned by us.
"""
from datetime import datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from uuid import uuid4


class Wallpapers:
    def __init__(self, service, data_dir, backend_factory=None, renderer=None, clock=datetime.now):
        self.service, self.repo = service, service.repository
        self.directory = Path(data_dir)/"wallpapers"
        self.backend_factory = backend_factory or self._windows
        self.renderer = renderer or self._render
        self.clock, self.backend = clock, None
        self.stop_event, self.wake_event = threading.Event(), threading.Event()
        self.thread = threading.Thread(target=self.run, name="campus-wallpaper", daemon=True)
        self.process, self.retry_after = None, 0

    @staticmethod
    def _windows():
        from .wallpaper_windows import WindowsWallpaper
        return WindowsWallpaper()

    def start(self):
        self.thread.start()

    def wake(self):
        self.retry_after = 0
        self.wake_event.set()

    def stop(self):
        self.stop_event.set()
        self.wake_event.set()
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
            except OSError:
                pass
        if self.thread.ident:
            self.thread.join(timeout=6)

    def _status(self, phase, **values):
        self.repo.set_meta("wallpaper_status", {"phase": phase, **values})

    def _backup_images(self, snapshot):
        folder = self.directory/"originals"
        folder.mkdir(parents=True, exist_ok=True)
        for monitor in snapshot["monitors"]:
            path = Path(monitor["path"])
            if monitor["path"] and not path.is_file():
                raise OSError("原壁纸文件已失效，请在 Windows 设置中重新选择壁纸后再开启")
            if monitor["path"] and path.is_file():
                with path.open("rb") as file:
                    digest = hashlib.file_digest(file, "sha256").hexdigest()
                suffix = path.suffix if path.suffix.lower() in (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff") else ".jpg"
                saved = folder/(digest+suffix)
                if not saved.exists():
                    shutil.copyfile(path, saved)
                monitor["source_path"], monitor["path"] = monitor["path"], str(saved)
        return snapshot

    def check(self):
        _, state = self.repo.read()
        enabled = state["settings"].get("wallpaper_enabled", False)
        record = self.repo.get_meta("wallpaper_backup")
        if not enabled and not record:
            status = self.repo.get_meta("wallpaper_status")
            if status and status.get("phase") != "disabled":
                self._status("disabled")
            if self.backend:
                self.backend.close()
                self.backend = None
            return False
        if self.backend is None:
            self.backend = self.backend_factory()
        if not enabled:
            self.backend.restore(record["snapshot"], record.get("owned", {}))
            self.repo.set_meta("wallpaper_backup", None)
            self.repo.set_meta("wallpaper_applied", None)
            self._status("disabled")
            self.backend.close()
            self.backend = None
            return True
        payload = self.service.display()
        # Tomorrow and assessment-only updates do not regenerate today's image.
        today = {"class_name": payload["class_name"], "today": payload["today"]}
        monitors = self.backend.monitors()
        if not monitors:
            raise OSError("暂未检测到可用显示器，稍后会自动重试")
        key = hashlib.sha256(json.dumps([today, [{k: m[k] for k in ("id", "width", "height")} for m in monitors]],
                                       ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        applied = self.repo.get_meta("wallpaper_applied")
        if (applied and applied["key"] == key and all(Path(p).is_file() for p in applied["files"].values())
                and all(os.path.normcase(m["path"]) == os.path.normcase(applied["files"].get(m["id"], "")) for m in monitors)):
            return False
        self.directory.mkdir(parents=True, exist_ok=True)
        jobs, files = [], {}
        for monitor in monitors:
            mid = hashlib.sha256(monitor["id"].encode()).hexdigest()[:12]
            path = self.directory/f'duty-{key[:20]}-{mid}.png'
            jobs.append({"path": str(path), "width": monitor["width"], "height": monitor["height"]})
            files[monitor["id"]] = str(path)
        self._status("updating", day=today["today"]["date"])
        self.renderer(today, jobs)
        if self.stop_event.is_set():
            return False
        # The user may turn the option off while rendering. Read again before
        # touching the desktop, and let the next check process the latest data.
        if not self.repo.read()[1]["settings"].get("wallpaper_enabled", False):
            if record is None:
                self._status("disabled")
            return False
        if record is None:
            record = {"snapshot": self._backup_images(self.backend.snapshot()), "owned": {}}
        else:
            known = {m["id"] for m in record["snapshot"]["monitors"]}
            added = [m for m in self.backend.snapshot()["monitors"] if m["id"] not in known]
            if added:
                record["snapshot"]["monitors"].extend(self._backup_images({"monitors": added})["monitors"])
        # Write recovery metadata before mutation, including partially applied
        # updates. Keep the last two image paths per monitor for ownership tests.
        for mid, path in files.items():
            record["owned"][mid] = list(dict.fromkeys(record["owned"].get(mid, [])+[path]))[-2:]
        self.repo.set_meta("wallpaper_backup", record)
        self.backend.apply(files, record["snapshot"])
        self.repo.set_meta("wallpaper_applied", {"key": key, "files": files})
        self._status("active", day=today["today"]["date"], updated=self.clock().isoformat(timespec="seconds"),
                     preview=next(iter(files.values())))
        keep = {Path(p).resolve() for values in record["owned"].values() for p in values}
        for path in self.directory.glob("duty-*.png"):
            if path.resolve().parent == self.directory.resolve() and path.resolve() not in keep:
                path.unlink(missing_ok=True)
        return True

    def _render(self, payload, jobs):
        request = self.directory/f'.render-{uuid4().hex}.json'
        request.write_text(json.dumps({"payload": payload, "jobs": jobs}, ensure_ascii=False), encoding="utf-8")
        if getattr(sys, "frozen", False):
            args = [sys.executable]
        else:
            args = [sys.executable, str(Path(__file__).resolve().parent.parent/"run_campus.pyw")]
        args += ["--wallpaper-render", str(request)]
        try:
            self.process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            try:
                _, error = self.process.communicate(timeout=45)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.communicate()
                raise OSError("壁纸生成超时，将自动重试") from None
            if self.process.returncode:
                logging.error("壁纸生成进程：%s", error.decode("utf-8", errors="replace")[-2000:])
                raise OSError("壁纸生成失败，将自动重试")
        finally:
            self.process = None
            request.unlink(missing_ok=True)

    def run(self):
        try:
            while not self.stop_event.is_set():
                self.wake_event.clear()
                if time.monotonic() >= self.retry_after:
                    try:
                        self.check()
                    except Exception as exc:
                        if not self.stop_event.is_set():
                            logging.exception("值日壁纸更新失败")
                            self._status("error", message=str(exc) if isinstance(exc, OSError) else "更新失败，将自动重试")
                            self.retry_after = time.monotonic()+300
                self.wake_event.wait(30)
        finally:
            if self.backend:
                self.backend.close()
