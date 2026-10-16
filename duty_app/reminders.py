"""One cheap, sleep-based scheduler. Tk is imported only by the popup child."""
from datetime import datetime
import logging
import threading

from .engine import schedule


class Reminders:
    def __init__(self, repository, launch, clock=datetime.now, alive=lambda: False):
        self.repository, self.launch, self.clock, self.alive = repository, launch, clock, alive
        self.stop_event = threading.Event()

    def check(self):
        now = self.clock()
        _, state = self.repository.read()
        if state["settings"]["paused"] or not state["configs"] or self.alive():
            return False
        ds = now.date().isoformat()
        existing = self.repository.reminder(ds)
        if existing and existing[0] == "dismissed":
            return False
        if not existing and now.strftime("%H:%M") < state["settings"]["reminder_time"]:
            return False
        info = schedule(state, ds, ds)[0]
        if not info["member_id"]:
            return False
        token = self.repository.claim_reminder(now, state["settings"]["reminder_time"])
        if not token:
            return False
        try:
            self.launch(ds, token)
        except Exception:
            self.repository.reminder_action(ds, token, "snooze", now)
            raise
        return True

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.check()
            except Exception:
                logging.exception("提醒检查失败")
            self.stop_event.wait(15)
