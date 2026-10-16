from datetime import datetime, timedelta
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock
from xml.etree import ElementTree as ET

from duty_app.notifications import (PS_SCRIPT, action_uri, activate, deliver,
                                    ensure_registered, identity, powershell,
                                    protocol_command, toast_xml)
from duty_app.reminders import Reminders
from duty_app.storage import Repository
from test_engine import fixture


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="通知测试-")
        self.directory = Path(self.temp.name)
        self.repo = Repository(self.directory/"duty.sqlite3")
        self.repo.mutate(0, lambda _: fixture())
        self.now = datetime(2026,10,13,22,0)
        self.token = self.repo.claim_reminder(self.now, "22:00")
        self.sender, self.register = MagicMock(), MagicMock()

    def tearDown(self):
        self.temp.cleanup()

    def deliver(self):
        return deliver(self.directory,"2026-10-13",self.token,clock=lambda:self.now,
                       sender=self.sender,register=self.register)

    def test_xml_is_native_toast_with_actions_and_escaped_member(self):
        xml = toast_xml(self.directory,"2026-10-13",self.token,
                        {"name":"甲<&\"乙", "mop":True,"carried":True})
        root = ET.fromstring(xml)
        self.assertEqual(root.attrib["activationType"],"protocol")
        self.assertEqual(root.find("visual/binding").attrib["template"],"ToastGeneric")
        self.assertEqual(root.find("visual/binding/text").text,'今日值日：甲<&"乙')
        self.assertEqual([a.attrib["content"] for a in root.findall("actions/action")],
                         ["打开管理","十分钟后提醒","关闭本次"])
        self.assertTrue(all(a.attrib["arguments"].startswith(identity(self.directory)[1]+":")
                            for a in root.findall("actions/action")))

    def test_send_acknowledgement_prevents_repeat_and_retry_after_timeout(self):
        self.assertTrue(self.deliver())
        self.assertEqual(self.repo.reminder("2026-10-13")[0],"notified")
        self.now+=timedelta(hours=1)
        worker=Reminders(self.repo,MagicMock(),clock=lambda:self.now)
        self.assertFalse(worker.check())
        self.assertFalse(self.deliver())
        self.sender.assert_called_once()
        self.assertIsNone(self.repo.claim_reminder(self.now,"22:00"))

    def test_snooze_protocol_persists_and_is_one_time(self):
        self.deliver()
        uri=action_uri(self.directory,"2026-10-13",self.token,"snooze")
        self.assertEqual(activate(self.directory,uri,clock=lambda:self.now),"snooze")
        self.assertIsNone(activate(self.directory,uri,clock=lambda:self.now))
        restarted=Repository(self.directory/"duty.sqlite3")
        self.assertIsNone(restarted.claim_reminder(self.now+timedelta(minutes=9),"22:00"))
        self.assertIsNotNone(restarted.claim_reminder(self.now+timedelta(minutes=10),"22:00"))

    def test_open_and_dismiss_are_accepted(self):
        self.deliver()
        uri=action_uri(self.directory,"2026-10-13",self.token,"open")
        self.assertEqual(activate(self.directory,uri,clock=lambda:self.now),"open")
        self.assertEqual(self.repo.reminder("2026-10-13")[0],"dismissed")

    def test_invalid_or_old_notification_does_not_consume_reminder(self):
        self.deliver()
        valid=action_uri(self.directory,"2026-10-13",self.token,"snooze")
        for uri in (valid.replace("reminder?","other?"),valid+"&extra=yes",valid+"#bad",
                    valid.replace(self.token,"a"*32),valid.replace("snooze","delete"),
                    "http://example.test/", "x"*1025):
            self.assertIsNone(activate(self.directory,uri,clock=lambda:self.now))
        self.assertIsNone(activate(self.directory,valid,clock=lambda:self.now+timedelta(days=1)))
        self.assertEqual(self.repo.reminder("2026-10-13")[0],"notified")

    def test_failed_transport_snoozes_instead_of_acknowledging(self):
        self.sender.side_effect=OSError("transport")
        with self.assertRaises(OSError):
            self.deliver()
        self.assertEqual(self.repo.reminder("2026-10-13")[0],"snoozed")

    def test_payload_does_not_interpolate_member_into_shell_code(self):
        malicious="'; Write-Output 'not-code"
        payload={"xml":malicious,"app_id":"test","operation":"show"}
        runner=MagicMock(return_value=subprocess.CompletedProcess([],0,b"sent\r\n",b""))
        self.assertEqual(powershell(payload,runner=runner),"sent")
        args,options=runner.call_args
        self.assertNotIn(malicious,args[0])
        self.assertEqual(args[0][-1],PS_SCRIPT)
        self.assertEqual(json.loads(options["input"]),payload)
        self.assertNotIn("shell",options)
        self.assertIn("Hidden",args[0])

    def test_registry_is_per_user_and_uri_argument_is_quoted(self):
        registry=MagicMock()
        ensure_registered(self.directory,registry=registry)
        self.assertEqual(registry.CreateKeyEx.call_count,3)
        for call in registry.CreateKeyEx.call_args_list:
            self.assertEqual(call.args[0],registry.HKEY_CURRENT_USER)
            self.assertTrue(call.args[1].startswith("Software\\Classes\\"))
        command=protocol_command(self.directory)
        self.assertTrue(command.endswith(' --toast-activation "%1"'))
        self.assertIn("--data-dir",command)

    def test_expiration_is_midnight_and_new_day_stays_quiet_before_due(self):
        self.deliver()
        payload=self.sender.call_args.args[0]
        self.assertTrue(payload["expires"].startswith("2026-10-14T00:00:00"))
        self.now+=timedelta(hours=3)
        self.assertFalse(Reminders(self.repo,MagicMock(),clock=lambda:self.now).check())


if __name__ == "__main__":
    unittest.main()
