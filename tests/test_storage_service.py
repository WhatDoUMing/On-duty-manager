from concurrent.futures import ThreadPoolExecutor
from datetime import date
import tempfile
from pathlib import Path
import unittest

from duty_app.engine import RuleError, schedule
from duty_app.service import DutyService, SwapConflict
from duty_app.storage import Repository, RevisionConflict
from test_engine import fixture


class StorageServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="值日测试-")
        self.path = Path(self.temp.name)/"值日.sqlite3"
        self.repo = Repository(self.path)
        self.repo.mutate(0, lambda _: fixture())
        self.service = DutyService(self.repo, today=lambda: date(2026, 10, 13))

    def tearDown(self):
        self.temp.cleanup()

    def test_restart_retains_data_and_duplicate_is_noop(self):
        body = {"revision": 1, "date": "2026-10-12"}
        view = self.service.edit("missed", body)
        self.assertEqual(view["today_duty"]["name"], "1号")
        restarted = Repository(self.path)
        self.assertEqual(restarted.read(), self.repo.read())
        body["revision"] = view["revision"]
        self.assertEqual(self.service.edit("missed", body)["revision"], view["revision"])

    def test_simultaneous_edits_do_not_overwrite(self):
        def edit(value):
            try:
                self.service.edit("settings", {"revision": 1, "reminder_time": value})
                return "ok"
            except RevisionConflict:
                return "conflict"
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(edit, ["21:00", "23:00"])), ["ok", "conflict"])
        self.assertEqual(self.repo.read()[0], 2)

    def test_failed_edit_rolls_back(self):
        before = self.repo.read()
        with self.assertRaises(RuleError):
            self.service.edit("settings", {"revision": 1, "reminder_time": "25:70"})
        self.assertEqual(before, self.repo.read())

    def test_new_config_next_day_and_conflict_confirmation(self):
        view = self.service.edit("swaps", {"revision": 1, "first": "2026-10-14", "second": "2026-10-15"})
        body = {"revision": view["revision"], "members": [{"id":"1","name":"1号"}], "mop_interval": 2}
        with self.assertRaises(SwapConflict):
            self.service.edit("config", body)
        self.assertEqual(self.repo.read()[0], view["revision"])
        body["cancel_conflicts"] = True
        updated = self.service.edit("config", body)
        self.assertEqual(updated["today_duty"]["name"], "2号")
        self.assertEqual(updated["next_config"]["effective"], "2026-10-14")
        self.assertTrue(updated["state"]["swaps"][0]["cancelled"])

    def test_cancel_swap_preserves_past_leg(self):
        initial = DutyService(self.repo, today=lambda: date(2026, 10, 12))
        view = initial.edit("swaps", {"revision": 1, "first": "2026-10-12", "second": "2026-10-14"})
        updated = self.service.edit("swaps/cancel", {"revision":view["revision"], "id":view["state"]["swaps"][0]["id"]})
        rows = schedule(updated["state"], "2026-10-12", "2026-10-14")
        self.assertEqual(rows[0]["name"], "3号")
        self.assertEqual(rows[2]["name"], "3号")

    def test_import_legacy_chinese_key(self):
        import base64
        import json
        payload = {"members":["甲", "乙"], "mopAnchorDate":"2026-10-12", "mopAnchorIdx":1, "mopInterval":3}
        key = "DUTY:"+base64.b64encode(json.dumps(payload, ensure_ascii=False).encode()).decode()
        view = self.service.edit("import", {"revision":1, "key":key})
        self.assertEqual(view["next_config"]["members"][0]["name"], "甲")
        with self.assertRaises(RuleError):
            self.service.edit("import", {"revision":view["revision"], "key":"DUTY:broken!"})


if __name__ == "__main__":
    unittest.main()
