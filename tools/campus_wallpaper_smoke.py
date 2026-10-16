"""Exercise actual renderer processes with a fake desktop setter.

Reads the real Windows monitor layout; never calls real apply/restore methods.
Creates landscape/portrait previews, checks restart/date changes and restoration.
"""
from copy import deepcopy
from datetime import date, timedelta
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class PreviewDesktop:
    def __init__(self, original, dimensions=((1920,1080),(1080,1920))):
        self.items = [{"id":f"test-{i}","width":w,"height":h,"path":str(original)} for i,(w,h) in enumerate(dimensions)]
        self.applications = 0

    def monitors(self): return deepcopy(self.items)
    def snapshot(self): return {"monitors":self.monitors(),"position":4,"color":0,"status":1}
    def apply(self, files, snapshot):
        self.applications += 1
        for item in self.items: item["path"] = files[item["id"]]
    def restore(self, snapshot, owned):
        for item in self.items:
            if item["path"] in owned.get(item["id"], []):
                item["path"] = next(m["path"] for m in snapshot["monitors"] if m["id"]==item["id"])
    def close(self): pass


def main():
    import psutil
    from PIL import Image
    from campus_app.engine import empty_state, make_config
    from campus_app.service import DutyService
    from campus_app.storage import Repository
    from campus_app.wallpaper import Wallpapers
    from campus_app.wallpaper_windows import WindowsWallpaper
    actual = WindowsWallpaper()
    try: before = actual.snapshot()
    finally: actual.close()
    output = ROOT/".runtime/qa"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="中文 壁纸测试-", dir=ROOT/".runtime") as temp:
        root = Path(temp)
        original = root/"original.png"
        Image.new("RGB",(16,16),"#c1d1bb").save(original)
        repository = Repository(root/"campus.sqlite3")
        students = [{"id":f"s{i}","name":f"同学{i+1:02d}","number":str(i+1)} for i in range(60)]
        areas = [{"id":f"area{a}","name":name,"groups":[
            {"id":f"g{a}-{g}","name":f"第{g+1}组","student_ids":[f"s{i}" for i in range(a*15+g*5,a*15+g*5+5)]}
            for g in range(3)]} for a,name in enumerate(("教室清扫","走廊与公共区","操场清洁区","黑板与讲台"))]
        state = empty_state()
        today = date.today()
        state["configs"] = [make_config({"mode":"group","class_name":"高一（1）班","students":students,"areas":areas},today.isoformat())]
        state["settings"]["wallpaper_enabled"] = True
        repository.mutate(0, lambda _:state)
        service = DutyService(repository, today=lambda:today)
        desktop = PreviewDesktop(original)
        worker = Wallpapers(service, root, backend_factory=lambda:desktop)
        started = time.monotonic()
        assert worker.check()
        assert worker.process is None
        applied = repository.get_meta("wallpaper_applied")["files"]
        for index, path in enumerate(applied.values()):
            with Image.open(path) as image:
                assert image.size == ((1920,1080) if index == 0 else (1080,1920))
            shutil.copyfile(path, output/("campus-wallpaper.png" if index == 0 else "campus-wallpaper-portrait.png"))
        assert not worker.check(), "unchanged schedule rendered again"
        today += timedelta(days=1)
        assert worker.check(), "day rollover did not update"
        revision, _ = repository.read()
        service.edit("settings", {"revision":revision,"wallpaper_enabled":False})
        assert worker.check()
        for item in desktop.items:
            assert Path(item["path"]).read_bytes() == original.read_bytes()
        assert not psutil.Process().children(recursive=True), "renderer remained resident"
        elapsed = round(time.monotonic()-started,2)
    actual = WindowsWallpaper()
    try: after = actual.snapshot()
    finally: actual.close()
    assert before == after, "real wallpaper changed during dry-run"
    result = {"result":"passed","real_windows_wallpaper_changed":False,"real_monitor_count":len(before["monitors"]),
              "students":60,"areas":4,"rendered_sizes":["1920x1080","1080x1920"],"elapsed_seconds":elapsed,
              "renderer_children_after_completion":0,
              "checks":["read-only Windows COM","short-lived renderer","Chinese path","daily rollover","unchanged schedule dedupe","original-image restore with fake setter"]}
    (output/"campus-wallpaper-metrics.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__": main()
