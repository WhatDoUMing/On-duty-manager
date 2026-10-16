# Source configuration only. Run PyInstaller only after an explicit 打包 request.
from pathlib import Path

root = Path(SPECPATH).parent
a = Analysis(
    [str(root / 'run_duty.pyw')],
    pathex=[str(root)],
    binaries=[],
    datas=[(str(root / 'duty_manager.html'), '.'), (str(root / 'assets'), 'assets')],
    hiddenimports=['pystray._win32', 'PIL._imaging', 'tkinter', 'tkinter.ttk'],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['pytest', 'psutil', 'websocket', 'unittest', 'paddleocr', 'torch',
              'numpy', 'pandas', 'matplotlib', 'PyQt5', 'PySide6'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='DutyManager',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='DutyManager')
