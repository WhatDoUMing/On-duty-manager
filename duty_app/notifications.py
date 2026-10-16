"""Windows toast transport with short-lived, data-only PowerShell calls.

Uses the WinRT/PowerShell approach demonstrated by winotify (MIT), rather
than loading a browser or keeping a second GUI process alive. Toast actions
activate our per-user URL protocol; their random capability is checked in DB.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import parse_qs, urlencode, urlsplit
from xml.etree import ElementTree as ET

from .engine import day, schedule
from .storage import Repository


GROUP = "DutyReminder"
PS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$payload = [Console]::In.ReadToEnd() | ConvertFrom-Json
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.UI.Notifications.ToastNotifier, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.UI.Notifications.NotificationSetting, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null
$manager = [Windows.UI.Notifications.ToastNotificationManager]
if ($payload.operation -eq 'clear') {
    $manager::History.Clear([string]$payload.app_id)
} elseif ($payload.operation -eq 'history') {
    @($manager::History.GetHistory([string]$payload.app_id) | ForEach-Object { $_.Tag }) | ConvertTo-Json -Compress
} else {
    $notifier = $manager::CreateToastNotifier([string]$payload.app_id)
    if ($null -ne $notifier.Setting -and $notifier.Setting.ToString() -ne 'Enabled') {
        throw 'Windows notifications are disabled for this application.'
    }
    $document = New-Object Windows.Data.Xml.Dom.XmlDocument
    $document.LoadXml([string]$payload.xml)
    $toast = [Windows.UI.Notifications.ToastNotification]::new($document)
    $toast.Tag = [string]$payload.tag
    $toast.Group = 'DutyReminder'
    $toast.ExpirationTime = [DateTimeOffset]::Parse([string]$payload.expires)
    $notifier.Show($toast)
    'sent'
}
"""


def identity(data_dir):
    directory = Path(data_dir).resolve()
    default = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "DutyManager"
    suffix = "" if directory == default.resolve() else "-"+hashlib.sha256(str(directory).lower().encode()).hexdigest()[:16]
    return "DutyManager"+suffix, "dutymanager"+suffix


def protocol_command(data_dir):
    if getattr(sys, "frozen", False):
        args = [sys.executable]
    else:
        executable = Path(sys.executable).with_name("pythonw.exe")
        args = [str(executable if executable.is_file() else Path(sys.executable)),
                str(Path(__file__).resolve().parent.parent / "run_duty.pyw")]
    # Always quote the untrusted URI placeholder. Never dispatch it to a shell.
    args += ["--data-dir", str(Path(data_dir).resolve()), "--toast-activation"]
    return subprocess.list2cmdline(args)+' "%1"'


def ensure_registered(data_dir, registry=None):
    if registry is None:
        import winreg
        registry = winreg
    r = registry
    app_id, scheme = identity(data_dir)
    definitions = {
        rf"Software\Classes\{scheme}": {"": "URL:值日管家", "URL Protocol": ""},
        rf"Software\Classes\{scheme}\shell\open\command": {"": protocol_command(data_dir)},
        rf"Software\Classes\AppUserModelId\{app_id}": {"DisplayName": "值日管家"},
    }
    for path, values in definitions.items():
        with r.CreateKeyEx(r.HKEY_CURRENT_USER, path, 0, r.KEY_SET_VALUE) as key:
            for name, value in values.items():
                r.SetValueEx(key, name, 0, r.REG_SZ, value)


def action_uri(data_dir, target, token, action):
    _, scheme = identity(data_dir)
    return f"{scheme}://reminder?"+urlencode({"day": target, "token": token, "action": action})


def toast_xml(data_dir, target, token, info):
    toast = ET.Element("toast", {"activationType": "protocol", "duration": "long",
                                 "launch": action_uri(data_dir, target, token, "open")})
    binding = ET.SubElement(ET.SubElement(toast, "visual"), "binding", {"template": "ToastGeneric"})
    ET.SubElement(binding, "text").text = f"今日值日：{info['name']}"
    ET.SubElement(binding, "text").text = target+" · "+("倒垃圾 + 拖地" if info["mop"] else "倒垃圾 · 今日无需拖地")
    if info["carried"]:
        ET.SubElement(binding, "text").text = "昨日任务，今日继续补做。"
    actions = ET.SubElement(toast, "actions")
    for label, action in (("打开管理", "open"), ("十分钟后提醒", "snooze"), ("关闭本次", "dismiss")):
        ET.SubElement(actions, "action", {"activationType": "protocol", "content": label,
                                          "arguments": action_uri(data_dir, target, token, action)})
    ET.SubElement(toast, "audio", {"src": "ms-winsoundevent:Notification.Default"})
    return ET.tostring(toast, encoding="unicode")


def powershell(payload, runner=subprocess.run):
    executable = Path(os.environ.get("SystemRoot", r"C:\Windows"))/"System32/WindowsPowerShell/v1.0/powershell.exe"
    result = runner([str(executable), "-NoLogo", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
                     "-Command", PS_SCRIPT], input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        # Error text is diagnostic only; reminder titles/tokens are never logged.
        error = RuntimeError(f"Windows 系统通知发送失败（代码 {result.returncode}），请检查系统通知设置或 duty.log")
        error.details = result.stderr.decode("utf-8", errors="replace")
        raise error
    return result.stdout.decode("utf-8-sig").strip()


def deliver(data_dir, target, token, clock=datetime.now, sender=powershell, register=ensure_registered):
    repository = Repository(Path(data_dir)/"duty.sqlite3")
    now = clock()
    reminder = repository.reminder(target)
    if (not reminder or not isinstance(token, str) or not hmac.compare_digest(reminder[2], token)
            or reminder[0] != "showing" or target != now.date().isoformat()):
        return False
    _, state = repository.read()
    info = schedule(state, target, target)[0]
    if not info["member_id"]:
        repository.reminder_action(target, token, "dismiss", now)
        return False
    try:
        register(data_dir)
        expiration = datetime.combine(now.date()+timedelta(days=1), time.min).astimezone()
        sender({"operation": "show", "app_id": identity(data_dir)[0], "tag": target,
                "xml": toast_xml(data_dir, target, token, info), "expires": expiration.isoformat()})
        repository.mark_notified(target, token)
    except Exception:
        repository.reminder_action(target, token, "snooze", now)
        raise
    return True


def activate(data_dir, uri, clock=datetime.now):
    """Consume one authenticated action. Unknown/expired URIs do nothing."""
    if not isinstance(uri, str) or len(uri) > 1024:
        return None
    try:
        parsed = urlsplit(uri)
        if (parsed.scheme != identity(data_dir)[1] or parsed.netloc != "reminder"
                or parsed.path not in ("", "/") or parsed.fragment):
            return None
        fields = parse_qs(parsed.query, strict_parsing=True, max_num_fields=3)
        if set(fields) != {"day", "token", "action"} or any(len(v) != 1 for v in fields.values()):
            return None
        target, token, action = (fields[key][0] for key in ("day", "token", "action"))
        now = clock()
        if (day(target) != now.date() or action not in ("open", "snooze", "dismiss")
                or not re.fullmatch(r"[A-Za-z0-9_-]{32}", token)):
            return None
    except (ValueError, TypeError):
        return None
    repository = Repository(Path(data_dir)/"duty.sqlite3")
    return action if repository.reminder_action(target, token, action, now) else None


def clear(data_dir):
    return powershell({"operation": "clear", "app_id": identity(data_dir)[0]})


def cleanup_test_registration(data_dir):
    """Only test profiles can be removed here. Production cleanup is installer-owned."""
    import winreg as r
    app_id, scheme = identity(data_dir)
    if scheme == "dutymanager":
        raise ValueError("请使用隔离的测试数据目录")
    clear(data_dir)
    for path in (rf"Software\Classes\{scheme}\shell\open\command",
                 rf"Software\Classes\{scheme}\shell\open", rf"Software\Classes\{scheme}\shell",
                 rf"Software\Classes\{scheme}", rf"Software\Classes\AppUserModelId\{app_id}"):
        try:
            r.DeleteKey(r.HKEY_CURRENT_USER, path)
        except FileNotFoundError:
            pass
