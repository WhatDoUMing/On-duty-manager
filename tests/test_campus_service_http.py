from datetime import date, datetime, timedelta
from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
import threading
import unittest

from campus_app.engine import RuleError, empty_state
from campus_app.server import AppServer
from campus_app.service import DutyService, ScheduleConflict
from campus_app.startup import Startup
from campus_app.storage import Repository, RevisionConflict
from test_campus_engine import config, state


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/"中文 班级"/"campus.sqlite3"
        self.repo = Repository(self.path)
        self.repo.mutate(0, lambda _: state())
        self.service = DutyService(self.repo, today=lambda: date(2026, 10, 12))

    def tearDown(self):
        self.temp.cleanup()

    def test_restart_and_request_deduplication_with_old_revision(self):
        body = {"revision": 1, "request_id": "repeat", "area_id": "room", "target_kind": "group",
                "target_id": "A", "reason": "missed", "penalty": "duty", "days": 3, "date": "2026-10-12"}
        first = self.service.edit("marks", body)
        second = self.service.edit("marks", body)
        self.assertEqual(first["revision"], second["revision"])
        self.assertEqual(len(Repository(self.path).read()[1]["marks"]), 1)
        with self.assertRaises(RuleError):
            self.service.edit("marks", {**body, "days": 2})

    def test_concurrent_edits_one_wins(self):
        result = []
        def edit(value):
            try:
                self.service.edit("settings", {"revision": 1, "request_id": value, "reminder_time": "20:00" if value == "a" else "21:00"})
                result.append("saved")
            except RevisionConflict:
                result.append("stale")
        threads = [threading.Thread(target=edit, args=(v,)) for v in ("a", "b")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertCountEqual(result, ["saved", "stale"])

    def test_preview_conflict_and_atomic_confirmation(self):
        self.service.edit("swaps", {"revision": 1, "area_id": "room", "first": "2026-10-13", "second": "2026-10-14"})
        cfg = config()
        cfg["areas"][0]["groups"].pop(0)
        body = {"revision": 2, "config": cfg}
        self.assertEqual(len(self.service.preview(body)["conflicts"]), 1)
        with self.assertRaises(ScheduleConflict):
            self.service.edit("config", body)
        self.assertEqual(self.repo.read()[0], 2)
        result = self.service.edit("config", {**body, "cancel_conflicts": True})
        self.assertEqual(result["next_config"]["effective"], "2026-10-13")
        self.assertEqual(result["today_duty"]["areas"][0]["group_id"], "A")

    def test_invalid_edit_rolls_back(self):
        with self.assertRaises(RuleError):
            self.service.edit("settings", {"revision": 1, "reminder_time": "28:00"})
        self.assertEqual(self.repo.read()[0], 1)

    def test_export_csv_formula_escaping_and_display_whitelist(self):
        self.service.edit("marks", {"revision": 1, "target_kind": "group", "target_id": "A", "area_id": "room",
                                    "reason": "poor", "penalty": "other", "note": "=SECRET()"})
        value = self.service.report_csv().decode("utf-8-sig")
        self.assertIn("'=SECRET()", value)
        display = json.dumps(self.service.display())
        for secret in ("marks", "password", "note", "penalty_ids", "SECRET"):
            self.assertNotIn(secret, display)

    def test_settings_no_registry_side_effect_on_revision_conflict(self):
        class Fake:
            calls = []
            def enabled(self): return False
            def set_enabled(self, value): self.calls.append(value)
        fake = Fake()
        self.service.startup = fake
        with self.assertRaises(RevisionConflict):
            self.service.edit("settings", {"revision": 0, "autostart": True})
        self.assertEqual(fake.calls, [])

    def test_startup_identity_and_unicode_path(self):
        self.assertEqual(Startup.NAME, "CampusDutyManager")
        command = Startup.default_command(Path(self.temp.name)/"中文 路径")
        self.assertIn("run_campus.pyw", command)
        self.assertIn("--background", command)
        self.assertIn("中文 路径", command)

    def test_removed_overlap_confirmation_does_not_mask_reintroduction(self):
        from campus_app.engine import overlaps
        original = self.repo.read()[1]
        original["configs"][0]["areas"][0]["groups"][1]["student_ids"].append("1")
        key = overlaps(original["configs"][0])[0]["key"]
        original["overlap_confirmations"] = [key]
        self.repo.mutate(1, lambda _: original)
        self.service.edit("config", {"revision": 2, "config": config()})
        self.assertEqual(self.repo.read()[1]["overlap_confirmations"], [])
        self.service.edit("config", {"revision": 3, "config": original["configs"][0]})
        self.assertEqual(len(self.service.view()["overlaps"]), 1)


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.temp.name)/"campus.sqlite3")
        self.service = DutyService(self.repo, today=lambda: date(2026, 10, 12))
        self.server = AppServer(("127.0.0.1", 0), self.service, Path(__file__).resolve().parent.parent,
                                "instance", clock=lambda: datetime(2026, 10, 12, 22))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.cookie = ""

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, method, path, body=None, cookie=None, headers=None):
        client = HTTPConnection("127.0.0.1", self.server.server_address[1])
        request_headers = {"Content-Type": "application/json", "Cookie": self.cookie if cookie is None else cookie, **(headers or {})}
        client.request(method, path, json.dumps(body).encode() if body is not None else None, request_headers)
        reply = client.getresponse()
        data = reply.read()
        response_cookie = reply.getheader("Set-Cookie")
        result = json.loads(data) if reply.getheader("Content-Type", "").startswith("application/json") else data
        status = reply.status
        client.close()
        return status, result, response_cookie

    def setup(self):
        response = self.request("POST", "/api/setup", {"token": self.server.auth.bootstrap, "password": "classroom123",
                                                        "config": config(), "effective": "2026-10-12"})
        self.assertEqual(response[0], 200, response)
        self.cookie = response[2].split(";", 1)[0]
        self.assertTrue(self.cookie.startswith("campus_session="))

    def test_setup_atomic_and_auth_boundaries(self):
        self.assertEqual(self.request("GET", "/api/state")[0], 401)
        bad = self.request("POST", "/api/setup", {"token": self.server.auth.bootstrap, "password": "classroom123", "config": {}})
        self.assertEqual(bad[0], 400)
        self.assertIsNone(self.repo.get_meta("password"))
        self.assertEqual(self.repo.read()[0], 0)
        self.setup()
        self.assertEqual(self.request("GET", "/api/state")[0], 200)
        self.assertEqual(self.request("POST", "/api/setup", {"token": self.server.auth.bootstrap, "password": "classroom123", "config": config()})[0], 400)

    def test_display_ticket_separate_role_and_one_use(self):
        self.setup()
        ticket = self.server.display_auth.ticket()
        response = self.request("POST", "/api/display-login", {"token": ticket}, cookie="")
        display_cookie = response[2].split(";", 1)[0]
        self.assertTrue(display_cookie.startswith("campus_display_session="))
        self.assertEqual(self.request("GET", "/api/display", cookie=display_cookie)[0], 200)
        self.assertEqual(self.request("GET", "/api/state", cookie=display_cookie)[0], 401)
        self.assertEqual(self.request("POST", "/api/settings", {"revision": 1, "paused": True}, cookie=display_cookie)[0], 401)
        self.assertEqual(self.request("POST", "/api/display-login", {"token": ticket}, cookie="")[0], 400)

    def test_internal_and_origin_boundaries(self):
        self.assertEqual(self.request("POST", "/internal/display-entry", {})[0], 403)
        self.assertEqual(self.request("POST", "/internal/display-entry", {}, headers={"X-Instance-Key": "instance"})[0], 200)
        self.assertEqual(self.request("POST", "/api/login", {"password": "x"}, headers={"Origin": "http://evil.test"})[0], 403)
        self.assertEqual(self.request("GET", "/api/status", headers={"Host": "evil.test:8766"})[0], 403)
        self.assertEqual(self.request("GET", "/../campus_app/storage.py")[0], 404)

    def test_reminder_capability_and_csv(self):
        self.setup()
        now = datetime(2026, 10, 12, 22)
        token = self.repo.claim_reminder(now, "22:00")
        body = {"day": "2026-10-12", "token": token, "action": "shown"}
        self.assertEqual(self.request("POST", "/api/reminder-action", {**body, "token": "bad"})[0], 400)
        self.assertTrue(self.request("POST", "/api/reminder-action", body)[1]["ok"])
        self.assertTrue(self.request("POST", "/api/reminder-action", {**body, "action": "snooze"})[1]["ok"])
        self.assertFalse(self.request("POST", "/api/reminder-action", {**body, "action": "snooze"})[1]["ok"])
        self.assertEqual(self.request("POST", "/api/reminder-action", {**body, "day": "2026-10-11"})[0], 400)
        self.assertEqual(self.request("GET", "/api/assessments.csv?period=month&anchor=2026-10-12")[0], 200)

    def test_import_preview_revision_and_login_persistence(self):
        self.setup()
        self.assertEqual(self.request("POST", "/api/config/preview", {"revision": 1, "config": config()})[0], 200)
        self.assertEqual(self.request("POST", "/api/config/preview", {"revision": 0, "config": config()})[0], 409)
        self.assertEqual(self.request("POST", "/api/import", {"revision": 1, "config": {"config": config()}})[0], 200)
        self.assertEqual(self.request("POST", "/api/logout", {})[0], 200)
        self.assertEqual(self.request("GET", "/api/state")[0], 401)
        self.assertEqual(self.request("POST", "/api/login", {"password": "classroom123"})[0], 200)

    def test_wallpaper_settings_preview_and_authentication(self):
        self.assertEqual(self.request("GET", "/api/wallpaper.png")[0], 401)
        self.setup()
        self.assertEqual(self.request("GET", "/api/wallpaper.png")[0], 404)
        self.assertEqual(self.request("POST", "/api/settings", {"revision":1,"wallpaper_enabled":"yes"})[0], 400)
        self.assertEqual(self.request("POST", "/api/settings", {"revision":1,"wallpaper_enabled":True})[0], 200)
        self.assertEqual(self.request("GET", "/api/wallpaper-status")[1]["phase"], "pending")
        folder = self.repo.path.parent/"wallpapers"
        folder.mkdir()
        picture = folder/"duty-test.png"
        picture.write_bytes(b"test-image")
        self.repo.set_meta("wallpaper_status", {"phase":"active","preview":str(picture),"day":"2026-10-12"})
        self.assertEqual(self.request("GET", "/api/wallpaper.png")[1], b"test-image")
        self.repo.set_meta("wallpaper_status", {"phase":"active","preview":str(self.repo.path)})
        self.assertEqual(self.request("GET", "/api/wallpaper.png")[0], 404)
        display_cookie = "campus_display_session="+self.server.display_auth.new_session()
        self.assertEqual(self.request("GET", "/api/wallpaper.png", cookie=display_cookie)[0], 401)


if __name__ == "__main__":
    unittest.main()
