"""Copy release instructions and locally installed dependency license texts."""
from importlib.metadata import distribution
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parent.parent


def main():
    destination=ROOT/"dist/DutyManager"
    if not (destination/"DutyManager.exe").is_file():
        raise SystemExit("请先运行 PyInstaller")
    shutil.copy2(ROOT/"packaging/使用说明.txt",destination/"使用说明.txt")
    notices=destination/"licenses"
    notices.mkdir(exist_ok=True)
    shutil.copy2(ROOT/"docs/THIRD_PARTY_NOTICES.md",notices/"THIRD_PARTY_NOTICES.md")
    for name in ("pystray","Pillow","six"):
        package=distribution(name)
        for relative in package.files or []:
            if "license" in str(relative).lower() or "copying" in str(relative).lower():
                target=notices/name/Path(relative).name
                target.parent.mkdir(exist_ok=True)
                shutil.copy2(package.locate_file(relative),target)
    python_license=Path(sys.base_prefix)/"LICENSE_PYTHON.txt"
    if not python_license.is_file():
        python_license=Path(sys.base_prefix)/"LICENSE.txt"
    if python_license.is_file():
        shutil.copy2(python_license,notices/"Python-LICENSE.txt")
    print("Release instructions and license texts copied")


if __name__=="__main__":
    main()
