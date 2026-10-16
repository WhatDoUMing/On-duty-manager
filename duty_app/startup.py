"""Per-user startup only. Tests use a fake registry, never the real Run key."""
from pathlib import Path
import os
import subprocess
import sys


class Startup:
    KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
    NAME = "DutyManager"

    def __init__(self, registry=None, command=None):
        if registry is None:
            import winreg
            registry = winreg
        self.registry = registry
        self.command = command or self.default_command()

    @staticmethod
    def default_command(data_dir=None):
        if getattr(sys, "frozen", False):
            args = [sys.executable, "--background"]
        else:
            executable = Path(sys.executable).with_name("pythonw.exe")
            if not executable.exists():
                executable = Path(sys.executable)
            args = [str(executable), str(Path(__file__).resolve().parent.parent / "run_duty.pyw"), "--background"]
        default = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "DutyManager"
        if data_dir is not None and Path(data_dir).resolve() != default.resolve():
            args += ["--data-dir", str(Path(data_dir).resolve())]
        if getattr(sys, "frozen", False):
            # Match the installer's always-quoted executable path.
            return '"'+args[0]+'" '+subprocess.list2cmdline(args[1:])
        return subprocess.list2cmdline(args)

    def enabled(self):
        r = self.registry
        try:
            with r.OpenKey(r.HKEY_CURRENT_USER, self.KEY) as key:
                value, _ = r.QueryValueEx(key, self.NAME)
                return value == self.command
        except FileNotFoundError:
            return False

    def set_enabled(self, enabled):
        r = self.registry
        with r.CreateKeyEx(r.HKEY_CURRENT_USER, self.KEY, 0, r.KEY_SET_VALUE) as key:
            if enabled:
                r.SetValueEx(key, self.NAME, 0, r.REG_SZ, self.command)
            else:
                try:
                    r.DeleteValue(key, self.NAME)
                except FileNotFoundError:
                    pass
