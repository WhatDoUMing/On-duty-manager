from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time

from .engine import RuleError


class Auth:
    def __init__(self, repository, clock=time.monotonic):
        self.repository = repository
        self.clock = clock
        self.lock = threading.Lock()
        self.sessions, self.tickets, self.attempts = {}, {}, {}
        self.bootstrap = secrets.token_urlsafe(32)

    def initialized(self):
        return self.repository.get_meta("password") is not None

    def setup(self, password, token):
        if not isinstance(token, str) or not hmac.compare_digest(token, self.bootstrap):
            raise RuleError("请从本机托盘打开首次设置")
        if not isinstance(password, str) or not 8 <= len(password) <= 128:
            raise RuleError("管理口令请设置为 8 至 128 个字符")
        salt = secrets.token_hex(16)
        value = {"salt": salt, "hash": self._hash(password, salt)}
        if not self.repository.set_meta_if_absent("password", value):
            raise RuleError("管理口令已设置，请登录")
        return self.new_session()

    @staticmethod
    def _hash(password, salt):
        return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 260000).hex()

    def login(self, password, address):
        now = self.clock()
        with self.lock:
            self.attempts = {k: v for k, v in self.attempts.items() if now-v[0] < 600}
            if len(self.attempts) > 1000:
                self.attempts.clear()
            since, attempts = self.attempts.get(address, (now, 0))
            if attempts >= 10:
                raise RuleError("尝试次数较多，请十分钟后再试")
            self.attempts[address] = (since, attempts+1)
        stored = self.repository.get_meta("password")
        if (not stored or not isinstance(password, str) or len(password) > 128
                or not hmac.compare_digest(stored["hash"], self._hash(password, stored["salt"]))):
            raise RuleError("管理口令有误")
        with self.lock:
            self.attempts.pop(address, None)
        return self.new_session()

    def new_session(self):
        token = secrets.token_urlsafe(32)
        with self.lock:
            now = self.clock()
            self.sessions = {k: expiry for k, expiry in self.sessions.items() if expiry > now}
            self.sessions[token] = now+12*3600
        return token

    def authenticated(self, token):
        with self.lock:
            return self.sessions.get(token, 0) > self.clock()

    def logout(self, token):
        with self.lock:
            self.sessions.pop(token, None)

    def ticket(self):
        token = secrets.token_urlsafe(32)
        with self.lock:
            now = self.clock()
            self.tickets = {k: v for k, v in self.tickets.items() if v > now}
            self.tickets[token] = now+60
        return token

    def redeem(self, token):
        with self.lock:
            expires = self.tickets.pop(token, 0) if isinstance(token, str) else 0
        if expires <= self.clock():
            raise RuleError("本机入口已过期，请从托盘重新打开")
        return self.new_session()
