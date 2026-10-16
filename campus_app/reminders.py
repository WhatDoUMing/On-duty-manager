"""One delivery per local day; each delivery previews the following day."""
from datetime import datetime, timedelta
import logging
import threading


class Reminders:
    def __init__(self, repository, service, launch, clock=datetime.now, alive=lambda: False):
        self.repository, self.service, self.launch, self.clock, self.alive = repository, service, launch, clock, alive
        self.stop_event = threading.Event()

    def check(self):
        now = self.clock()
        _, state = self.repository.read()
        settings = state["settings"]
        if settings["paused"] or self.alive():
            return False
        tomorrow = (now.date()+timedelta(days=1)).isoformat()
        if not self.service.days(tomorrow, tomorrow)[1][0]["areas"]:
            return False
        token = self.repository.claim_reminder(now, settings["reminder_time"])
        if not token:
            return False
        try:
            self.launch(now.date().isoformat(), token, settings["reminder_mode"])
        except Exception:
            self.repository.reminder_action(now.date().isoformat(), token, "snooze", now)
            raise
        return True

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.check()
            except Exception:
                logging.exception("校园提醒检查失败")
            self.stop_event.wait(15)
