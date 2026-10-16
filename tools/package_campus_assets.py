"""Campus release resources; invoke only as part of an explicitly requested build."""
from importlib.metadata import distribution
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parent.parent


def main():
    destination = ROOT/"dist/CampusDutyManager"
    if not (destination/"CampusDutyManager.exe").is_file():
        raise SystemExit("Build CampusDutyManager with PyInstaller first")
    shutil.copy2(ROOT/"packaging/campus-readme.txt", destination/"CampusDuty-Readme.txt")
    notices = destination/"licenses"
    notices.mkdir(exist_ok=True)
    shutil.copy2(ROOT/"docs/THIRD_PARTY_NOTICES.md", notices/"THIRD_PARTY_NOTICES.md")
    for name in ("pystray", "Pillow", "six"):
        package = distribution(name)
        for relative in package.files or []:
            if "license" in str(relative).lower() or "copying" in str(relative).lower():
                target = notices/name/Path(relative).name
                target.parent.mkdir(exist_ok=True)
                shutil.copy2(package.locate_file(relative), target)
    for filename in ("LICENSE_PYTHON.txt", "LICENSE.txt"):
        source = Path(sys.base_prefix)/filename
        if source.is_file():
            shutil.copy2(source, notices/"Python-LICENSE.txt")
            break


if __name__ == "__main__":
    main()
