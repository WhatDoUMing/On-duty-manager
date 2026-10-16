from datetime import date, datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from campus_app.reminders import Reminders
from campus_app.service import DutyService
from campus_app.storage import Repository
from test_campus_engine import state


class CampusReminderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.temp.name)/"campus.sqlite3")
        self.repo.mutate(0, lambda _: state())
        self.now = datetime(2026, 10, 12, 21, 59)
        self.sent = []
        self.service = DutyService(self.repo, today=lambda: self.now.date())
        def launch(ds, token, mode):
            self.sent.append((ds, token, mode))
            self.repo.mark_notified(ds, token)
        self.worker = Reminders(self.repo, self.service, launch, clock=lambda: self.now)

    def tearDown(self):
        self.temp.cleanup()

    def test_delivery_day_deduplicated_and_target_is_tomorrow(self):
        self.assertFalse(self.worker.check())
        self.now = self.now.replace(hour=22, minute=0)
        self.assertTrue(self.worker.check())
        self.assertEqual(self.sent[0][0], "2026-10-12")
        self.assertFalse(self.worker.check())
        self.assertEqual(self.service.display()["tomorrow"]["areas"][0]["group_name"], "B")

    def test_late_start_resume_only_current_delivery_day(self):
        self.now = datetime(2026, 10, 15, 23)
        self.assertTrue(self.worker.check())
        self.assertEqual([s[0] for s in self.sent], ["2026-10-15"])

    def test_snooze_restart_and_midnight_expiry(self):
        self.now = datetime(2026, 10, 12, 22)
        self.worker.check()
        self.repo.reminder_action(self.sent[0][0], self.sent[0][1], "snooze", self.now)
        worker = Reminders(Repository(self.repo.path), self.service, self.worker.launch, clock=lambda: self.now)
        self.now += timedelta(minutes=9)
        self.assertFalse(worker.check())
        self.now += timedelta(minutes=1)
        self.assertTrue(worker.check())
        self.repo.reminder_action(self.sent[-1][0], self.sent[-1][1], "snooze", self.now)
        self.now = datetime(2026, 10, 13, 0, 1)
        self.assertFalse(worker.check())

    def test_pause_live_popup_and_browser_mode(self):
        self.now = datetime(2026, 10, 12, 22)
        self.worker.alive = lambda: True
        self.assertFalse(self.worker.check())
        self.worker.alive = lambda: False
        self.repo.mutate(1, lambda s: s | {"settings": s["settings"] | {"paused": True}})
        self.assertFalse(self.worker.check())
        self.repo.mutate(2, lambda s: s | {"settings": s["settings"] | {"paused": False, "reminder_mode": "display"}})
        self.assertTrue(self.worker.check())
        self.assertEqual(self.sent[0][2], "display")

    def test_failure_retries_after_ten_minutes(self):
        self.now = datetime(2026, 10, 12, 22)
        def fail(*args): raise OSError("browser")
        self.worker.launch = fail
        with self.assertRaises(OSError): self.worker.check()
        self.assertEqual(self.repo.reminder("2026-10-12")[0], "snoozed")
        self.assertFalse(self.worker.check())


if __name__ == "__main__":
    unittest.main()
