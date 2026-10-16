from copy import deepcopy
from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from campus_app.service import DutyService
from campus_app.storage import Repository
from campus_app.wallpaper import Wallpapers
from test_campus_engine import state


class FakeDesktop:
    def __init__(self, path):
        self.screens = [{"id": "screen-1", "width": 1920, "height": 1080, "path": str(path)}]
        self.applied, self.restored, self.closed = [], [], False

    def monitors(self):
        return deepcopy(self.screens)

    def snapshot(self):
        return {"monitors": self.monitors(), "position": 4, "color": 123, "status": 1}

    def apply(self, files, snapshot):
        self.applied.append(dict(files))
        for monitor in self.screens:
            monitor["path"] = files[monitor["id"]]

    def restore(self, snapshot, owned):
        self.restored.append(deepcopy(snapshot))
        for monitor in self.screens:
            if monitor["path"] in owned.get(monitor["id"], []):
                original = next(m for m in snapshot["monitors"] if m["id"] == monitor["id"])
                monitor["path"] = original["path"]

    def close(self):
        self.closed = True


class WallpaperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)/"中文 路径"
        self.repo = Repository(self.root/"campus.sqlite3")
        self.repo.mutate(0, lambda _: state())
        self.today = date(2026,10,12)
        self.service = DutyService(self.repo, today=lambda: self.today)
        self.original = self.root/"original.jpg"
        self.original.write_bytes(b"original-wallpaper")
        self.desktop = FakeDesktop(self.original)
        self.renders = []
        def renderer(payload, jobs):
            self.renders.append(deepcopy(payload))
            for job in jobs:
                Path(job["path"]).write_bytes(b"rendered-wallpaper")
        self.worker = Wallpapers(self.service, self.root, backend_factory=lambda:self.desktop, renderer=renderer)

    def tearDown(self):
        self.temp.cleanup()

    def enable(self, value=True):
        revision, _ = self.repo.read()
        return self.service.edit("settings", {"revision": revision, "wallpaper_enabled": value})

    def test_default_and_older_settings_do_not_touch_desktop(self):
        self.worker.backend_factory = lambda: self.fail("wallpaper adapter opened without opt-in")
        self.assertFalse(self.worker.check())
        rev, s = self.repo.read()
        s["settings"].pop("wallpaper_enabled", None)
        self.repo.mutate(rev, lambda _: s)
        self.assertFalse(self.worker.check())
        self.assertEqual(self.renders, [])

    def test_enable_render_once_restart_and_day_rollover(self):
        self.enable()
        self.assertTrue(self.worker.check())
        self.assertEqual(self.renders[0]["today"]["date"], "2026-10-12")
        self.assertFalse(self.worker.check())
        restarted = Wallpapers(self.service, self.root, backend_factory=lambda:self.desktop, renderer=self.worker.renderer)
        self.assertFalse(restarted.check())
        self.today = date(2026,10,13)
        self.assertTrue(restarted.check())
        self.assertEqual(self.renders[-1]["today"]["areas"][0]["group_name"], "B")
        self.assertEqual(len(self.desktop.applied), 2)

    def test_assessment_only_changes_skip_render_but_absence_refreshes(self):
        self.enable(); self.worker.check()
        rev, _ = self.repo.read()
        self.service.edit("marks", {"revision":rev,"area_id":"room","target_kind":"group","target_id":"A","reason":"poor","penalty":"other"})
        self.assertFalse(self.worker.check())
        rev, _ = self.repo.read()
        self.service.edit("absence", {"revision":rev,"student_id":"1","away":True})
        self.assertTrue(self.worker.check())
        self.assertTrue(self.renders[-1]["today"]["areas"][0]["students"][0]["absent"])
        self.assertNotIn("marks", self.renders[-1])

    def test_original_cache_copied_before_apply_and_disable_restores(self):
        self.enable(); self.worker.check()
        self.original.write_bytes(b"Windows-overwrote-its-cache")
        self.enable(False); self.assertTrue(self.worker.check())
        path = Path(self.desktop.screens[0]["path"])
        self.assertEqual(path.read_bytes(), b"original-wallpaper")
        self.assertIsNone(self.repo.get_meta("wallpaper_backup"))
        self.assertEqual(self.repo.get_meta("wallpaper_status")["phase"], "disabled")
        self.assertTrue(self.desktop.closed)

    def test_manual_wallpaper_after_enable_preserved_on_disable(self):
        self.enable(); self.worker.check()
        self.desktop.screens[0]["path"] = "new-manual-image.jpg"
        self.enable(False); self.worker.check()
        self.assertEqual(self.desktop.screens[0]["path"], "new-manual-image.jpg")

    def test_render_failure_and_disable_during_render_leave_original(self):
        self.enable()
        def fail(*_): raise OSError("font")
        self.worker.renderer = fail
        with self.assertRaises(OSError): self.worker.check()
        self.assertEqual(self.desktop.applied, [])
        self.assertIsNone(self.repo.get_meta("wallpaper_backup"))
        def turn_off(*_): self.enable(False)
        self.worker.renderer = turn_off
        self.assertFalse(self.worker.check())
        self.assertEqual(self.desktop.applied, [])
        self.assertEqual(self.repo.get_meta("wallpaper_status")["phase"], "disabled")

    def test_new_monitor_and_size_change_regenerate(self):
        self.enable(); self.worker.check()
        self.desktop.screens.append({"id":"portrait","width":1080,"height":1920,"path":str(self.original)})
        self.assertTrue(self.worker.check())
        self.assertEqual(len(self.desktop.applied[-1]), 2)
        backup = self.repo.get_meta("wallpaper_backup")
        self.assertEqual(len(backup["snapshot"]["monitors"]), 2)
        self.desktop.screens[0]["width"] = 2560
        self.assertTrue(self.worker.check())

    def test_missing_original_file_aborts_before_wallpaper_change(self):
        self.original.unlink()
        self.enable()
        with self.assertRaises(OSError): self.worker.check()
        self.assertEqual(self.desktop.applied, [])
        self.assertIsNone(self.repo.get_meta("wallpaper_backup"))

    def test_disable_after_failed_render_clears_status_and_releases_adapter(self):
        self.enable()
        def fail(*_): raise OSError("font")
        self.worker.renderer = fail
        with self.assertRaises(OSError): self.worker.check()
        self.worker._status("error", message="font")
        self.enable(False)
        self.assertFalse(self.worker.check())
        self.assertEqual(self.service.wallpaper_status()["phase"], "disabled")
        self.assertTrue(self.desktop.closed)
        self.assertIsNone(self.worker.backend)

    def test_native_restore_preserves_manual_and_solid_background(self):
        from campus_app.wallpaper_windows import WindowsWallpaper
        native = WindowsWallpaper.__new__(WindowsWallpaper)
        native.pointer = 1
        native.monitors = lambda:[{"id":"a","path":"manual.jpg"}]
        snapshot = {"monitors":[{"id":"a","path":"original.jpg"}],"position":4,"color":42,"status":1}
        with patch("campus_app.wallpaper_windows._call") as call:
            native.restore(snapshot, {"a":["ours.png"]})
            call.assert_not_called()
            native.monitors = lambda:[{"id":"a","path":"ours.png"}]
            snapshot["monitors"][0]["path"] = ""
            native.restore(snapshot, {"a":["ours.png"]})
            self.assertIn(18, [args.args[1] for args in call.call_args_list])
            self.assertNotIn(3, [args.args[1] for args in call.call_args_list])


class WallpaperRenderingTests(unittest.TestCase):
    def test_multi_area_layout_and_portrait_fit(self):
        from PIL import Image, ImageChops
        from campus_app.wallpaper_render import render
        with tempfile.TemporaryDirectory() as temp:
            repo = Repository(Path(temp)/"campus.sqlite3")
            repo.mutate(0, lambda _: state())
            payload = DutyService(repo, today=lambda:date(2026,10,12)).display()
            for size in ((1920,1080),(1080,1920),(800,600)):
                image = Path(temp)/"wallpaper.png"
                result = render(payload,*size,image)
                with Image.open(image) as picture:
                    self.assertEqual(picture.size,size)
                    shortcut_strip = picture.crop((0, 0, size[0]//3, size[1]))
                    blank = Image.new("RGB", shortcut_strip.size, "#f4f3ed")
                    self.assertIsNone(ImageChops.difference(shortcut_strip, blank).getbbox())
                for left,top,right,bottom in result["cards"]:
                    self.assertTrue(0<=left<right<=size[0] and 0<=top<bottom<=size[1])
            payload["today"]["areas"] *= 30
            result = render(payload,1920,1080,Path(temp)/"many.png")
            self.assertEqual(result["areas"],30)
            self.assertEqual(len(result["cards"]),30)

    def test_renderer_rejects_external_output(self):
        import json
        from campus_app.wallpaper_render import render_request
        with tempfile.TemporaryDirectory() as temp:
            request = Path(temp)/"request.json"
            request.write_text(json.dumps({"jobs":[{"path":str(Path(temp).parent/"duty-outside.png"),"width":1920,"height":1080}],"payload":{}}))
            with self.assertRaises(ValueError): render_request(request)
