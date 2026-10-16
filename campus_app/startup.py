from pathlib import Path
import os
import subprocess
import sys

from duty_app.startup import Startup as BaseStartup


class Startup(BaseStartup):
    NAME = "CampusDutyManager"

    @staticmethod
    def default_command(data_dir=None):
        if getattr(sys, "frozen", False):
            args = [sys.executable, "--background"]
        else:
            pythonw = Path(sys.executable).with_name("pythonw.exe")
            args = [str(pythonw if pythonw.exists() else sys.executable),
                    str(Path(__file__).resolve().parent.parent/"run_campus.pyw"), "--background"]
        default = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))/"CampusDutyManager"
        if data_dir is not None and Path(data_dir).resolve() != default.resolve():
            args += ["--data-dir", str(Path(data_dir).resolve())]
        return ('"'+args[0]+'" '+subprocess.list2cmdline(args[1:]) if getattr(sys, "frozen", False)
                else subprocess.list2cmdline(args))
