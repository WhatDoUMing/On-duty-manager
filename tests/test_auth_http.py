from datetime import date
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from duty_app.auth import Auth
from duty_app.engine import RuleError
from duty_app.server import AppServer
from duty_app.service import DutyService
from duty_app.storage import Repository


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Repository(Path(self.temp.name)/"duty.sqlite3")
        self.server = AppServer(("127.0.0.1", 0), DutyService(self.repo, today=lambda:date(2026,10,13)),
                                Path(__file__).resolve().parent.parent, instance_key="local-key")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.cookie = ""

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        outgoing = {"Content-Type":"application/json", "Cookie":self.cookie}
        outgoing.update(headers or {})
        connection.request(method, path, json.dumps(body).encode() if body is not None else None, outgoing)
        response = connection.getresponse()
        cookie = response.getheader("Set-Cookie")
        if cookie:
            self.cookie = cookie.split(";")[0]
        raw = response.read()
        status = response.status
        content_type = response.getheader("Content-Type")
        connection.close()
        return status, json.loads(raw) if "application/json" in content_type else raw

    def setup_auth(self):
        status, _ = self.request("POST", "/api/setup-auth", {"password":"test-password", "token":self.server.auth.bootstrap})
        self.assertEqual(status, 200)

    def test_unauthenticated_requests_and_bootstrap(self):
        self.assertEqual(self.request("GET", "/api/state")[0], 401)
        self.assertEqual(self.request("POST", "/api/setup-auth", {"password":"test-password", "token":"wrong"})[0], 400)
        self.setup_auth()
        self.assertEqual(self.request("GET", "/api/state")[0], 200)
        self.assertNotIn("test-password", json.dumps(self.repo.get_meta("password")))

    def test_full_api_sequence_with_revision_check(self):
        self.setup_auth()
        status, view = self.request("POST", "/api/config", {"revision":0,"members":["甲","乙","丙"],
                                     "effective":"2026-10-12", "first_mop":"2026-10-14"})
        self.assertEqual(status, 200)
        self.assertEqual(view["today_duty"]["name"], "乙")
        status, view = self.request("POST", "/api/missed", {"revision":1, "date":"2026-10-12"})
        self.assertEqual(status, 200)
        self.assertEqual(view["today_duty"]["name"], "甲")
        self.assertEqual(self.request("POST", "/api/settings", {"revision":1,"paused":True})[0], 409)
        status, result = self.request("GET", "/api/schedule?start=2026-10-13&end=2026-10-16")
        self.assertEqual(status, 200)
        self.assertEqual([r["date"] for r in result["days"] if r["mop"]], ["2026-10-15"])

    def test_origin_host_and_path_boundaries(self):
        self.setup_auth()
        self.assertEqual(self.request("POST", "/api/settings", {"revision":0}, {"Origin":"http://other.example"})[0], 403)
        self.assertEqual(self.request("GET", "/api/state", headers={"Host":"other.example:8765"})[0], 403)
        self.assertEqual(self.request("GET", "/../../.git/config")[0], 404)
        self.assertEqual(self.request("POST", "/api/settings", {}, {"Content-Type":"text/plain"})[0], 415)

    def test_logout_and_local_ticket_one_use(self):
        self.setup_auth()
        self.request("POST", "/api/logout", {})
        self.assertEqual(self.request("GET", "/api/state")[0], 401)
        ticket = self.server.auth.ticket()
        self.assertEqual(self.request("POST", "/api/local-login", {"token":ticket})[0], 200)
        self.assertEqual(self.request("POST", "/api/local-login", {"token":ticket})[0], 400)

    def test_local_activation_requires_instance_key(self):
        opened = []
        self.server.on_open = lambda: opened.append(True)
        self.assertEqual(self.request("POST", "/internal/activate", {})[0], 403)
        self.assertEqual(self.request("POST", "/internal/activate", {}, {"X-Instance-Key":"local-key"})[0], 200)
        self.assertEqual(opened, [True])

    def test_auth_rate_limit_and_ticket_expiry(self):
        self.setup_auth()
        for _ in range(10):
            self.assertEqual(self.request("POST", "/api/login", {"password":"wrong"})[0], 400)
        status, result = self.request("POST", "/api/login", {"password":"test-password"})
        self.assertEqual(status, 400)
        self.assertIn("十分钟", result["error"])

    def test_ticket_expiry_and_password_persistence(self):
        self.setup_auth()
        now=[0.0]
        auth=Auth(self.repo,clock=lambda:now[0])
        self.assertTrue(auth.initialized())
        token=auth.ticket()
        now[0]=61
        with self.assertRaises(RuleError):
            auth.redeem(token)
        session=auth.login("test-password","local")
        self.assertTrue(auth.authenticated(session))
        now[0]+=12*3600+1
        self.assertFalse(auth.authenticated(session))


if __name__ == "__main__":
    unittest.main()
