"""Small SQLite repository. Each edit is one optimistic, atomic transaction."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
import secrets
import sqlite3

from .engine import empty_state


class RevisionConflict(ValueError):
    pass


class Repository:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS state (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    revision INTEGER NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reminders (
                    day TEXT PRIMARY KEY, status TEXT NOT NULL, due TEXT,
                    token TEXT NOT NULL, claimed TEXT NOT NULL);
            """)
            db.execute("INSERT OR IGNORE INTO state VALUES(1,0,?)", (json.dumps(empty_state()),))

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            yield db
        finally:
            db.close()

    def read(self):
        with self.connection() as db:
            revision, payload = db.execute("SELECT revision,payload FROM state WHERE singleton=1").fetchone()
        return revision, json.loads(payload)

    def mutate(self, revision: int, transform):
        if isinstance(revision, bool) or not isinstance(revision, int):
            raise RevisionConflict("页面版本已变化，请刷新后重试")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                actual, payload = db.execute("SELECT revision,payload FROM state WHERE singleton=1").fetchone()
                if actual != revision:
                    raise RevisionConflict("其他终端已更新排班，请刷新后重试")
                state = json.loads(payload)
                before = deepcopy(state)
                updated = transform(state)
                if updated != before:
                    actual += 1
                    db.execute("UPDATE state SET revision=?,payload=? WHERE singleton=1",
                               (actual, json.dumps(updated, ensure_ascii=False)))
                db.execute("COMMIT")
                return actual, updated
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def get_meta(self, key):
        with self.connection() as db:
            row = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def set_meta_if_absent(self, key, value):
        with self.connection() as db:
            result = db.execute("INSERT OR IGNORE INTO metadata VALUES(?,?)", (key, json.dumps(value)))
            return result.rowcount == 1

    def claim_reminder(self, now: datetime, scheduled_time: str):
        ds = now.date().isoformat()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status,due,token,claimed FROM reminders WHERE day=?", (ds,)).fetchone()
            if row:
                status, due, _, claimed = row
                if status in ("dismissed", "notified") or (status == "snoozed" and datetime.fromisoformat(due) > now):
                    db.execute("COMMIT")
                    return None
                if status == "showing" and now - datetime.fromisoformat(claimed) < timedelta(minutes=5):
                    db.execute("COMMIT")
                    return None
            elif now.strftime("%H:%M") < scheduled_time:
                db.execute("COMMIT")
                return None
            token = secrets.token_urlsafe(24)
            db.execute("INSERT OR REPLACE INTO reminders VALUES(?,?,?,?,?)",
                       (ds, "showing", None, token, now.isoformat()))
            db.execute("COMMIT")
            return token

    def reminder_action(self, target: str, token: str, action: str, now: datetime):
        status = "snoozed" if action == "snooze" else "dismissed"
        due = (now + timedelta(minutes=10)).isoformat() if action == "snooze" else None
        with self.connection() as db:
            result = db.execute("UPDATE reminders SET status=?,due=? WHERE day=? AND token=? AND status IN ('showing','notified')",
                                (status, due, target, token))
        return result.rowcount == 1

    def mark_notified(self, target: str, token: str):
        with self.connection() as db:
            return db.execute("UPDATE reminders SET status='notified' WHERE day=? AND token=? AND status='showing'",
                              (target, token)).rowcount == 1

    def reminder(self, target: str):
        with self.connection() as db:
            row = db.execute("SELECT status,due,token,claimed FROM reminders WHERE day=?", (target,)).fetchone()
        return row
