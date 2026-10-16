# Configuration only: build only after the user explicitly requests 打包.
from pathlib import Path

root = Path(SPECPATH).parent
a = Analysis(
    [str(root / 'run_campus.pyw')], pathex=[str(root)], binaries=[],
    datas=[(str(root / 'campus.html'), '.'), (str(root / 'display.html'), '.'),
           (str(root / 'assets'), 'assets'), (str(root / 'docs' / 'THIRD_PARTY_NOTICES.md'), 'docs')],
    hiddenimports=['pystray._win32', 'PIL._imaging', 'PIL._imagingft'], hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['pytest', 'psutil', 'websocket', 'unittest', 'paddleocr', 'torch', 'numpy',
              'pandas', 'matplotlib', 'PyQt5', 'PySide6', 'tkinter', '_tkinter'], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='CampusDutyManager',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='CampusDutyManager')
