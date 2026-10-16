"""Authenticated campus HTTP API with a separate read-only display audience."""
from datetime import datetime
from http.cookies import SimpleCookie
import hmac
import json
import logging
from pathlib import Path
import secrets
from urllib.parse import parse_qs, urlencode, urlsplit

from duty_app.auth import Auth
from duty_app.server import AppServer as BaseServer, Handler as BaseHandler
from . import __version__
from .engine import RuleError
from .service import ScheduleConflict
from .storage import RevisionConflict


class AppServer(BaseServer):
    def __init__(self, address, service, assets: Path, instance_key="", on_open=lambda: None, clock=datetime.now):
        super().__init__(address, service, assets, instance_key, on_open)
        self.RequestHandlerClass = Handler
        self.display_auth = Auth(service.repository)
        self.clock = clock

    def display_url(self, delivery=None, token=None):
        fragment = {"ticket": self.display_auth.ticket()}
        if delivery and token:
            fragment.update(day=delivery, token=token)
        return f'http://127.0.0.1:{self.server_address[1]}/display#{urlencode(fragment)}'


class Handler(BaseHandler):
    server_version = "CampusDutyManager"

    def send_header(self, keyword, value):
        if keyword.lower() == "set-cookie":
            value = value.replace("duty_session=", getattr(self, "_cookie_name", "campus_session")+"=", 1)
        super().send_header(keyword, value)

    def _token(self, name="campus_session"):
        try:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            return cookie[name].value if name in cookie else ""
        except Exception:
            return ""

    def _display_access(self):
        return (self.server.display_auth.authenticated(self._token("campus_display_session"))
                or self.server.auth.authenticated(self._token()))

    def do_GET(self):
        if not self._host_valid():
            return self._reply(403, {"error": "请使用本机或局域网地址访问"})
        target = urlsplit(self.path)
        try:
            if target.path == "/api/status":
                return self._reply(200, {"initialized": self.server.auth.initialized(),
                                         "authenticated": self.server.auth.authenticated(self._token()),
                                         "display_authenticated": self._display_access(),
                                         "local": self._local(), "version": __version__})
            if target.path == "/api/display":
                if not self._display_access():
                    return self._reply(401, {"error": "请从本机托盘打开展示页，或先登录管理端"})
                return self._reply(200, self.server.service.display())
            if target.path.startswith("/api/"):
                if not self.server.auth.authenticated(self._token()):
                    return self._reply(401, {"error": "请先登录管理端"})
                service = self.server.service
                query = {k: v[0] for k, v in parse_qs(target.query).items()}
                if target.path == "/api/wallpaper-status":
                    return self._reply(200, service.wallpaper_status())
                if target.path == "/api/wallpaper.png":
                    status = service.repository.get_meta("wallpaper_status") or {}
                    path = Path(status.get("preview", "")).resolve()
                    folder = (service.repository.path.parent/"wallpapers").resolve()
                    if path.parent != folder or not path.name.startswith("duty-") or path.suffix != ".png" or not path.is_file():
                        return self._reply(404, {"error": "开启值日壁纸并等待生成后，即可查看图片"})
                    return self._reply(200, path.read_bytes(), "image/png")
                if target.path == "/api/state":
                    self.server.refresh_addresses()
                    result = service.view()
                    result["lan_urls"] = [f"http://{ip}:{self.server.server_address[1]}" for ip in self.server.addresses]
                    return self._reply(200, result)
                if target.path == "/api/schedule":
                    revision, days = service.days(query.get("start", ""), query.get("end", ""))
                    return self._reply(200, {"revision": revision, "days": days})
                if target.path in ("/api/assessments", "/api/assessments.csv"):
                    kwargs = {k: query[k] for k in ("period", "anchor", "area_id", "target_kind", "reason", "target_id") if k in query}
                    if target.path.endswith(".csv"):
                        return self._reply(200, service.report_csv(**kwargs), "text/csv; charset=utf-8")
                    return self._reply(200, service.report(**kwargs))
                if target.path == "/api/export":
                    value = service.view()
                    return self._reply(200, {"format": "campus-duty-config-v1", "config": value["next_config"] or value["config"]})
                return self._reply(404, {"error": "页面不存在"})
            files = {"/": ("campus.html", "text/html; charset=utf-8"),
                     "/display": ("display.html", "text/html; charset=utf-8"),
                     "/assets/campus.js": ("assets/campus.js", "text/javascript; charset=utf-8"),
                     "/assets/display.js": ("assets/display.js", "text/javascript; charset=utf-8"),
                     "/assets/campus.css": ("assets/campus.css", "text/css; charset=utf-8"),
                     "/favicon.svg": ("assets/campus-favicon.svg", "image/svg+xml")}
            if target.path not in files:
                return self._reply(404, {"error": "页面不存在"})
            filename, mime = files[target.path]
            path = self.server.assets/filename
            return self._reply(200, path.read_bytes(), mime) if path.is_file() else self._reply(503, {"error": "界面尚未就绪"})
        except RuleError as exc:
            return self._reply(400, {"error": str(exc)})
        except Exception:
            logging.exception("校园读取请求失败")
            return self._reply(500, {"error": "读取失败，请重试"})

    def do_POST(self):
        if not self._host_valid():
            return self._reply(403, {"error": "访问地址有误"})
        origin = self.headers.get("Origin")
        if origin and origin != "http://"+self.headers.get("Host", ""):
            return self._reply(403, {"error": "请在校园管理页面中操作"})
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
        route, auth = urlsplit(self.path).path, self.server.auth
        try:
            if route in ("/internal/activate", "/internal/display-entry"):
                if (not self._local() or not self.server.instance_key or
                        not hmac.compare_digest(self.headers.get("X-Instance-Key", ""), self.server.instance_key)):
                    return self._reply(403, {"error": "本机入口校验失败"})
                if route.endswith("activate"):
                    self.server.on_open()
                    return self._reply(200, {"ok": True})
                return self._reply(200, {"url": self.server.display_url(body.get("day"), body.get("token"))})
            if route == "/api/setup":
                if not self._local() or not isinstance(body.get("token"), str) or not hmac.compare_digest(body["token"], auth.bootstrap):
                    return self._reply(403, {"error": "请从本机托盘进入首次设置"})
                password = body.get("password")
                if not isinstance(password, str) or not 8 <= len(password) <= 128:
                    raise RuleError("管理口令请设置为 8 至 128 个字符")
                state = self.server.service.initial_state(body)
                salt = secrets.token_hex(16)
                self.server.service.repository.initialize(state, {"salt": salt, "hash": auth._hash(password, salt)})
                self.server.service.on_change()
                return self._reply(200, {"ok": True}, cookie=auth.new_session())
            if route == "/api/login":
                return self._reply(200, {"ok": True}, cookie=auth.login(body.get("password"), self.client_address[0]))
            if route in ("/api/local-login", "/api/display-login"):
                if not self._local() or not auth.initialized():
                    return self._reply(403, {"error": "请先在本机完成初始化"})
                audience = self.server.display_auth if route.endswith("display-login") else auth
                session = audience.redeem(body.get("token"))
                if route.endswith("display-login"):
                    self._cookie_name = "campus_display_session"
                return self._reply(200, {"ok": True}, cookie=session)
            if route == "/api/reminder-action":
                if not self._display_access():
                    return self._reply(401, {"error": "请重新打开展示页"})
                ds, token, action = body.get("day"), body.get("token"), body.get("action")
                row = self.server.service.repository.reminder(ds) if isinstance(ds, str) else None
                if (ds != self.server.clock().date().isoformat() or not isinstance(token, str) or not row
                        or not hmac.compare_digest(row[2], token) or action not in ("shown", "snooze", "dismiss")):
                    raise RuleError("本次提醒已过期")
                repo = self.server.service.repository
                ok = ((repo.mark_notified(ds, token) or row[0] == "notified") if action == "shown"
                      else repo.reminder_action(ds, token, action, self.server.clock()))
                return self._reply(200, {"ok": ok})
            if not auth.authenticated(self._token()):
                return self._reply(401, {"error": "登录已过期，请重新登录"})
            if route == "/api/logout":
                auth.logout(self._token())
                return self._reply(200, {"ok": True}, cookie="")
            if route == "/api/config/preview":
                return self._reply(200, self.server.service.preview(body))
            if not route.startswith("/api/"):
                return self._reply(404, {"error": "操作不存在"})
            return self._reply(200, self.server.service.edit(route[5:], body))
        except ScheduleConflict as exc:
            return self._reply(409, {"error": str(exc), "conflicts": exc.conflicts})
        except RevisionConflict as exc:
            return self._reply(409, {"error": str(exc), "stale": True})
        except (RuleError, TypeError, KeyError) as exc:
            return self._reply(400, {"error": str(exc) if isinstance(exc, RuleError) else "输入内容格式有误"})
        except Exception:
            logging.exception("校园保存请求失败")
            return self._reply(500, {"error": "保存失败，请重试"})
