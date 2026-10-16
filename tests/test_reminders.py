from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from duty_app.reminders import Reminders
from duty_app.storage import Repository
from test_engine import fixture


class ReminderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.temp.name)/"duty.sqlite3")
        self.repo.mutate(0, lambda _: fixture())
        self.now = datetime(2026,10,13,21,59)
        self.shown = []
        self.worker = Reminders(self.repo, lambda d,t: self.shown.append((d,t)), clock=lambda: self.now)

    def tearDown(self):
        self.temp.cleanup()

    def test_exact_time_and_deduplication(self):
        self.assertFalse(self.worker.check())
        self.now += timedelta(minutes=1)
        self.assertTrue(self.worker.check())
        self.assertFalse(self.worker.check())
        ds, token = self.shown[-1]
        self.assertTrue(self.repo.reminder_action(ds, token, "dismiss", self.now))
        self.now += timedelta(minutes=30)
        self.assertFalse(self.worker.check())

    def test_snooze_persists_across_restart(self):
        self.now = datetime(2026,10,13,22,3)
        self.worker.check()
        ds, token = self.shown[-1]
        self.repo.reminder_action(ds, token, "snooze", self.now)
        restarted = Reminders(Repository(self.repo.path), self.worker.launch, clock=lambda: self.now)
        self.now += timedelta(minutes=9)
        self.assertFalse(restarted.check())
        self.now += timedelta(minutes=1)
        self.assertTrue(restarted.check())

    def test_resume_only_notifies_current_day(self):
        self.now = datetime(2026,10,16,23,0)
        self.assertTrue(self.worker.check())
        self.assertEqual(self.shown[-1][0], "2026-10-16")
        self.assertIsNone(self.repo.reminder("2026-10-15"))

    def test_pause_empty_schedule_and_existing_popup(self):
        def pause(state):
            state["settings"]["paused"] = True
            return state
        self.repo.mutate(1, pause)
        self.now = datetime(2026,10,13,23,0)
        self.assertFalse(self.worker.check())
        self.repo.mutate(2, lambda _: fixture())
        self.worker.alive = lambda: True
        self.assertFalse(self.worker.check())

    def test_popup_failure_retries_after_snooze(self):
        self.now = datetime(2026,10,13,22,0)
        def fail(*_):
            raise OSError("popup")
        self.worker.launch = fail
        with self.assertRaises(OSError):
            self.worker.check()
        self.assertEqual(self.repo.reminder("2026-10-13")[0], "snoozed")

    def test_clock_backwards_does_not_duplicate_acknowledged_day(self):
        self.now = datetime(2026,10,13,23,0)
        self.worker.check()
        ds, token = self.shown[-1]
        self.repo.reminder_action(ds, token, "dismiss", self.now)
        self.now -= timedelta(minutes=30)
        self.assertFalse(self.worker.check())


if __name__ == "__main__":
    unittest.main()
