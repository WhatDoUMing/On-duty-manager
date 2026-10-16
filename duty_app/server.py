"""LAN server with session authentication, same-origin edits and bounded bodies."""
from __future__ import annotations

from datetime import date
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import ipaddress
import json
import logging
from pathlib import Path
import socket
import time
from urllib.parse import parse_qs, urlsplit

from . import __version__
from .auth import Auth
from .engine import RuleError, schedule
from .service import SwapConflict
from .storage import RevisionConflict


def lan_addresses():
    addresses = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            value = info[4][0]
            if not ipaddress.ip_address(value).is_loopback:
                addresses.add(value)
    except OSError:
        pass
    return sorted(addresses)


class AppServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 16

    def __init__(self, address, service, assets: Path, instance_key="", on_open=lambda: None):
        self.service, self.assets = service, Path(assets)
        self.auth = Auth(service.repository)
        self.instance_key, self.on_open = instance_key, on_open
        self.addresses = lan_addresses()
        self.hosts = {"localhost", "127.0.0.1", *self.addresses}
        self.addresses_checked = time.monotonic()
        super().__init__(address, Handler)

    def refresh_addresses(self, force=False):
        if force or time.monotonic()-self.addresses_checked > 60:
            self.addresses = lan_addresses()
            self.hosts = {"localhost", "127.0.0.1", *self.addresses}
            self.addresses_checked = time.monotonic()

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(10)
        return connection, address

    def dashboard_url(self):
        port = self.server_address[1]
        if not self.auth.initialized():
            return f"http://127.0.0.1:{port}/#setup={self.auth.bootstrap}"
        return f"http://127.0.0.1:{port}/#ticket={self.auth.ticket()}"


class Handler(BaseHTTPRequestHandler):
    server_version = "DutyManager"
    sys_version = ""

    def log_message(self, fmt, *args):
        # Never log cookies, passwords, tickets, request bodies or URL fragments.
        logging.debug("HTTP %s %s", self.command, urlsplit(self.path).path)

    def _reply(self, status, value, content_type="application/json; charset=utf-8", cookie=None):
        data = json.dumps(value, ensure_ascii=False).encode("utf-8") if isinstance(value, (dict, list)) else value
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if cookie is not None:
            self.send_header("Set-Cookie", f"duty_session={cookie}; HttpOnly; SameSite=Strict; Path=/; Max-Age={43200 if cookie else 0}")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _token(self):
        try:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            return cookie["duty_session"].value if "duty_session" in cookie else ""
        except Exception:
            return ""

    def _local(self):
        return ipaddress.ip_address(self.client_address[0]).is_loopback

    def _host_valid(self):
        host = self.headers.get("Host", "")
        try:
            parsed = urlsplit("http://"+host)
            if parsed.hostname not in self.server.hosts:
                self.server.refresh_addresses()
            return (parsed.hostname in self.server.hosts and parsed.port == self.server.server_address[1]
                    and not parsed.username and parsed.path == "")
        except ValueError:
            return False

    def do_GET(self):
        if not self._host_valid():
            return self._reply(403, {"error": "请使用本机或局域网地址访问"})
        target = urlsplit(self.path)
        try:
            if target.path == "/api/status":
                return self._reply(200, {"initialized": self.server.auth.initialized(),
                                         "authenticated": self.server.auth.authenticated(self._token()),
                                         "local": self._local(), "version": __version__})
            if target.path.startswith("/api/"):
                if not self.server.auth.authenticated(self._token()):
                    return self._reply(401, {"error": "请先登录管理端"})
                if target.path == "/api/state":
                    self.server.refresh_addresses()
                    result = self.server.service.view()
                    result["lan_urls"] = [f"http://{ip}:{self.server.server_address[1]}" for ip in self.server.addresses]
                    return self._reply(200, result)
                if target.path == "/api/schedule":
                    query = parse_qs(target.query)
                    revision, state = self.server.service.repository.read()
                    return self._reply(200, {"revision": revision, "days": schedule(state, query.get("start", [""])[0], query.get("end", [""])[0])})
                return self._reply(404, {"error": "页面不存在"})
            files = {"/": ("duty_manager.html", "text/html; charset=utf-8"),
                     "/duty_manager.html": ("duty_manager.html", "text/html; charset=utf-8"),
                     "/assets/app.js": ("assets/app.js", "text/javascript; charset=utf-8"),
                     "/assets/style.css": ("assets/style.css", "text/css; charset=utf-8"),
                     "/favicon.svg": ("assets/favicon.svg", "image/svg+xml")}
            if target.path not in files:
                return self._reply(404, {"error": "页面不存在"})
            filename, mime = files[target.path]
            path = self.server.assets / filename
            if not path.is_file():
                return self._reply(503, {"error": "管理界面尚未就绪"})
            return self._reply(200, path.read_bytes(), mime)
        except RuleError as exc:
            return self._reply(400, {"error": str(exc)})
        except Exception:
            logging.exception("读取请求失败")
            return self._reply(500, {"error": "读取失败，请重试"})

    def do_POST(self):
        if not self._host_valid():
            return self._reply(403, {"error": "访问地址有误"})
        origin = self.headers.get("Origin")
        if origin and origin != "http://"+self.headers.get("Host", ""):
            return self._reply(403, {"error": "请在管理页面中操作"})
        if self.headers.get_content_type() != "application/json" or self.headers.get("Transfer-Encoding"):
            return self._reply(415, {"error": "请提交 JSON 数据"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1048576:
                return self._reply(413, {"error": "请求内容过大或为空"})
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError
        except (ValueError, UnicodeError):
            return self._reply(400, {"error": "请求数据格式有误"})
        route = urlsplit(self.path).path
        auth = self.server.auth
        try:
            if route == "/internal/activate":
                key = self.headers.get("X-Instance-Key", "")
                if (not self._local() or not self.server.instance_key
                        or not hmac.compare_digest(key, self.server.instance_key)):
                    return self._reply(403, {"error": "本机入口校验失败"})
                self.server.on_open()
                return self._reply(200, {"ok": True})
            session = None
            if route == "/api/setup-auth":
                if not self._local():
                    return self._reply(403, {"error": "请先在电脑托盘完成首次设置"})
                session = auth.setup(body.get("password"), body.get("token"))
            elif route == "/api/login":
                session = auth.login(body.get("password"), self.client_address[0])
            elif route == "/api/local-login":
                if not self._local() or not auth.initialized():
                    return self._reply(403, {"error": "请先设置管理口令"})
                session = auth.redeem(body.get("token"))
            if session:
                return self._reply(200, {"ok": True}, cookie=session)
            if not auth.authenticated(self._token()):
                return self._reply(401, {"error": "登录已过期，请重新登录"})
            if route == "/api/logout":
                auth.logout(self._token())
                return self._reply(200, {"ok": True}, cookie="")
            if not route.startswith("/api/"):
                return self._reply(404, {"error": "操作不存在"})
            result = self.server.service.edit(route[5:], body)
            result["lan_urls"] = [f"http://{ip}:{self.server.server_address[1]}" for ip in self.server.addresses]
            return self._reply(200, result)
        except SwapConflict as exc:
            return self._reply(409, {"error": str(exc), "conflicts": exc.conflicts})
        except RevisionConflict as exc:
            return self._reply(409, {"error": str(exc), "stale": True})
        except RuleError as exc:
            return self._reply(400, {"error": str(exc)})
        except Exception:
            logging.exception("更新请求失败")
            return self._reply(500, {"error": "保存失败，请重试"})
