"""Compile and install an isolated test identity; preserve normal startup data."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import time
import winreg

ROOT=Path(__file__).resolve().parent.parent
RUN_KEY=r"Software\Microsoft\Windows\CurrentVersion\Run"
TEST_NAME="DutyManagerPackagingTest"
TEST_UNINSTALL=r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{779F246E-99EE-4671-B93E-9A71A7083BD3}_is1"


def startup_value(name):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,RUN_KEY) as key:
            return winreg.QueryValueEx(key,name)
    except FileNotFoundError:
        return None


def uninstall_registered():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,TEST_UNINSTALL):
            return True
    except FileNotFoundError:
        return False


def uninstall_info():
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,TEST_UNINSTALL) as key:
        location=Path(winreg.QueryValueEx(key,"InstallLocation")[0]).resolve()
        executable=Path(winreg.QueryValueEx(key,"UninstallString")[0].strip('"')).resolve()
        display=winreg.QueryValueEx(key,"DisplayName")[0]
    assert (ROOT/".runtime").resolve() in location.parents,location
    assert executable.parent==location and executable.name.startswith("unins") and executable.suffix==".exe"
    assert "安装验证" in display
    return location,executable


def uninstall_test():
    if uninstall_registered():
        _,executable=uninstall_info()
        run([executable,"/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART"])
        for _ in range(50):
            if not uninstall_registered():
                break
            time.sleep(.1)


def run(arguments):
    result=subprocess.run([str(v) for v in arguments],capture_output=True,timeout=60,
                          creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise AssertionError(f"Exit {result.returncode}: "+result.stdout.decode(errors="replace")+result.stderr.decode(errors="replace"))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--compiler",type=Path,default=ROOT/".runtime/inno/ISCC.exe")
    args=parser.parse_args()
    previous=startup_value("DutyManager")
    runtime=ROOT/".runtime"
    with tempfile.TemporaryDirectory(prefix="安装验证-",dir=runtime) as temp:
        directory=Path(temp).resolve()
        assert runtime.resolve() in directory.parents
        run([args.compiler,"/Qp","/DPackagingTest=1",f"/O{directory}",ROOT/"packaging/installer.iss"])
        installer=directory/"DutyManager-Setup-0.1.0.exe"
        if uninstall_registered():
            # Repair a previous interrupted test before uninstalling it. All
            # paths and identities are checked before invoking the installer.
            previous_dir,previous_uninstaller=uninstall_info()
            if not previous_uninstaller.exists():
                run([installer,"/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART","/CURRENTUSER",
                     "/NOICONS",f"/DIR={previous_dir}","/TASKS=autostart"])
            uninstall_test()
        assert not uninstall_registered()
        assert startup_value(TEST_NAME) is None
        assert startup_value("DutyManager")==previous
        install_dir=directory/"应用 程序"
        installed=install_dir/"DutyManager.exe"
        reference=hashlib.sha256((ROOT/"dist/DutyManager/DutyManager.exe").read_bytes()).digest()
        try:
            for selected in (False,True):
                if not selected:
                    # An unchecked task also turns off a previous source/old
                    # installation's startup entry of the same application.
                    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,RUN_KEY,0,winreg.KEY_SET_VALUE) as key:
                        winreg.SetValueEx(key,TEST_NAME,0,winreg.REG_SZ,f'"{install_dir / "old.exe"}" --background')
                options=[installer,"/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART","/CURRENTUSER",
                         "/NOICONS",f"/DIR={install_dir}",f"/LOG={directory / ('selected.log' if selected else 'default.log')}"]
                if selected:
                    options.append("/TASKS=autostart")
                run(options)
                _,uninstaller=uninstall_info()
                assert installed.is_file() and uninstaller.is_file()
                assert hashlib.sha256(installed.read_bytes()).digest()==reference
                assert uninstall_registered()
                value=startup_value(TEST_NAME)
                if selected:
                    assert value is not None
                    assert value[0]==f'"{installed}" --background',value
                else:
                    assert value is None,"Autostart was selected by default"
                    # Simulate enabling startup later from the dashboard.
                    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,RUN_KEY,0,winreg.KEY_SET_VALUE) as key:
                        winreg.SetValueEx(key,TEST_NAME,0,winreg.REG_SZ,f'"{installed}" --background')
                assert startup_value("DutyManager")==previous,"Normal startup preference changed"
                uninstall_test()
                for _ in range(50):
                    if not installed.exists() and not uninstall_registered():
                        break
                    time.sleep(.1)
                assert not installed.exists(),"Application file survived uninstall"
                assert not uninstall_registered(),"Uninstall metadata survived cleanup"
                assert startup_value(TEST_NAME) is None,"Test autostart survived uninstall"
                assert startup_value("DutyManager")==previous
            result={"result":"passed","default_autostart":False,"selected_autostart":True,
                    "unchecked_clears_previous_autostart":True,
                    "uninstall_cleanup":True,"normal_startup_preserved":True,
                    "installed_exe_hash_matches":True,"unicode_install_path":True}
            (runtime/"qa/installer-check.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
            print(json.dumps(result))
        finally:
            uninstall_test()


if __name__=="__main__":
    main()
