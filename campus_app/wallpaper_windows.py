"""Thread-affine IDesktopWallpaper adapter. No optional COM package required.

Interface layout and identifiers follow Microsoft's ShObjIdl_core.h. Callers
create and close this object on the same worker thread. Tests read the actual
desktop, but substitute the adapter for any operation that changes wallpaper.
"""
import ctypes as C
from ctypes import wintypes as W
from contextlib import contextmanager
import os
from pathlib import Path
from uuid import UUID


class GUID(C.Structure):
    _fields_ = [("data", C.c_ubyte*16)]

    @classmethod
    def parse(cls, value):
        return cls((C.c_ubyte*16).from_buffer_copy(UUID(value).bytes_le))


def _call(pointer, slot, types, *args):
    table = C.cast(pointer, C.POINTER(C.POINTER(C.c_void_p))).contents
    function = C.WINFUNCTYPE(C.c_long, C.c_void_p, *types)(table[slot])
    result = function(pointer, *args)
    if result < 0:
        error = OSError(f"Windows 壁纸接口返回 0x{result & 0xffffffff:08X}")
        error.hresult = result & 0xffffffff
        raise error
    return result


def _release(pointer):
    if pointer:
        _call(pointer, 2, [])


class WindowsWallpaper:
    def __init__(self):
        self.ole = C.WinDLL("ole32")
        self.ole.CoInitializeEx.argtypes = [C.c_void_p, W.DWORD]
        self.ole.CoInitializeEx.restype = C.c_long
        self.ole.CoCreateInstance.argtypes = [C.POINTER(GUID), C.c_void_p, W.DWORD, C.POINTER(GUID), C.POINTER(C.c_void_p)]
        self.ole.CoCreateInstance.restype = C.c_long
        self.ole.CoTaskMemFree.argtypes = [C.c_void_p]
        result = self.ole.CoInitializeEx(None, 2)
        if result < 0:
            raise OSError("Windows 桌面接口初始化失败")
        self.pointer = C.c_void_p()
        clsid = GUID.parse("C2CF3110-460E-4FC1-B9D0-8A1C0C9CC4BD")
        iid = GUID.parse("B92B56A9-8B55-4E14-9A89-0199BBB6F93B")
        result = self.ole.CoCreateInstance(C.byref(clsid), None, 23, C.byref(iid), C.byref(self.pointer))
        if result < 0:
            self.ole.CoUninitialize()
            raise OSError(f"Windows 桌面接口启动失败 0x{result & 0xffffffff:08X}")

    def _string(self, pointer, slot, types, *args):
        value = C.c_void_p()
        _call(pointer, slot, [*types, C.POINTER(C.c_void_p)], *args, C.byref(value))
        try:
            return C.wstring_at(value) if value.value else ""
        finally:
            self.ole.CoTaskMemFree(value)

    def monitors(self):
        count = W.UINT()
        _call(self.pointer, 6, [C.POINTER(W.UINT)], C.byref(count))
        result = []
        for index in range(count.value):
            monitor_id = self._string(self.pointer, 5, [W.UINT], index)
            rect = W.RECT()
            try:
                attached = _call(self.pointer, 7, [W.LPCWSTR, C.POINTER(W.RECT)], monitor_id, C.byref(rect))
            except OSError as exc:
                # Some Windows builds return E_FAIL for a remembered monitor
                # that has been unplugged, rather than documented S_FALSE.
                if getattr(exc, "hresult", 0) in (0x80004005, 0x80070057):
                    continue
                raise
            if attached != 0:
                continue
            path = self._string(self.pointer, 4, [W.LPCWSTR], monitor_id)
            if rect.right > rect.left and rect.bottom > rect.top:
                result.append({"id": monitor_id, "width": rect.right-rect.left, "height": rect.bottom-rect.top, "path": path})
        return result

    def snapshot(self):
        position, color, status = W.UINT(), W.DWORD(), W.UINT()
        _call(self.pointer, 11, [C.POINTER(W.UINT)], C.byref(position))
        _call(self.pointer, 9, [C.POINTER(W.DWORD)], C.byref(color))
        _call(self.pointer, 17, [C.POINTER(W.UINT)], C.byref(status))
        snapshot = {"monitors": self.monitors(), "position": position.value, "color": color.value, "status": status.value}
        if status.value & 2:
            array = C.c_void_p()
            _call(self.pointer, 13, [C.POINTER(C.c_void_p)], C.byref(array))
            try:
                count, paths = W.DWORD(), []
                _call(array, 7, [C.POINTER(W.DWORD)], C.byref(count))
                for index in range(count.value):
                    item = C.c_void_p()
                    _call(array, 8, [W.DWORD, C.POINTER(C.c_void_p)], index, C.byref(item))
                    try:
                        paths.append(self._string(item, 5, [W.UINT], 0x80058000))  # SIGDN_FILESYSPATH
                    finally:
                        _release(item)
                flags, interval = W.UINT(), W.UINT()
                _call(self.pointer, 15, [C.POINTER(W.UINT), C.POINTER(W.UINT)], C.byref(flags), C.byref(interval))
                snapshot["slideshow"] = {"paths": paths, "flags": flags.value, "interval": interval.value}
            finally:
                _release(array)
        return snapshot

    def apply(self, files, snapshot):
        if snapshot.get("slideshow"):
            # SetWallpaper selects a still image. Preserve the original slide
            # list and defer its timer while this app owns the desktop.
            try:
                _call(self.pointer, 14, [W.UINT, W.UINT], snapshot["slideshow"]["flags"], 0xffffffff)
            except OSError:
                _call(self.pointer, 14, [W.UINT, W.UINT], snapshot["slideshow"]["flags"], 86400000)
        _call(self.pointer, 10, [W.UINT], 3)  # DWPOS_FIT: never crop the duty list.
        _call(self.pointer, 18, [W.BOOL], True)
        for monitor_id, path in files.items():
            _call(self.pointer, 3, [W.LPCWSTR, W.LPCWSTR], monitor_id, str(path))

    @contextmanager
    def _shell_items(self, paths):
        shell = C.WinDLL("shell32")
        shell.SHParseDisplayName.argtypes = [W.LPCWSTR, C.c_void_p, C.POINTER(C.c_void_p), W.DWORD, C.c_void_p]
        shell.SHParseDisplayName.restype = C.c_long
        shell.SHCreateShellItemArrayFromIDLists.argtypes = [W.UINT, C.POINTER(C.c_void_p), C.POINTER(C.c_void_p)]
        shell.SHCreateShellItemArrayFromIDLists.restype = C.c_long
        ids, array = [], C.c_void_p()
        try:
            for path in paths:
                item = C.c_void_p()
                if shell.SHParseDisplayName(path, None, C.byref(item), 0, None) < 0:
                    raise OSError("原幻灯片目录已变化，请在 Windows 设置中重新选择")
                ids.append(item)
            values = (C.c_void_p*len(ids))(*[i.value for i in ids])
            if shell.SHCreateShellItemArrayFromIDLists(len(ids), values, C.byref(array)) < 0:
                raise OSError("原幻灯片列表恢复失败")
            yield array
        finally:
            _release(array)
            for item in ids:
                self.ole.CoTaskMemFree(item)

    def restore(self, snapshot, owned):
        current = {m["id"]: m for m in self.monitors()}
        matching = [m for m in snapshot["monitors"] if m["id"] in current
                    and os.path.normcase(current[m["id"]]["path"]) in {os.path.normcase(p) for p in owned.get(m["id"], [])}]
        if not matching:
            return
        all_owned = len(matching) == len(current)
        if all_owned and not any(m["path"] for m in matching):
            _call(self.pointer, 18, [W.BOOL], False)
            _call(self.pointer, 8, [W.DWORD], snapshot["color"])
            _call(self.pointer, 10, [W.UINT], snapshot["position"])
            return
        for monitor in matching:
            _call(self.pointer, 3, [W.LPCWSTR, W.LPCWSTR], monitor["id"], monitor["path"])
        if all_owned:
            _call(self.pointer, 10, [W.UINT], snapshot["position"])
            _call(self.pointer, 8, [W.DWORD], snapshot["color"])
            slideshow = snapshot.get("slideshow")
            if slideshow and slideshow["paths"]:
                with self._shell_items(slideshow["paths"]) as items:
                    _call(self.pointer, 12, [C.c_void_p], items)
                _call(self.pointer, 14, [W.UINT, W.UINT], slideshow["flags"], slideshow["interval"])
            _call(self.pointer, 18, [W.BOOL], bool(snapshot["status"] & 1))

    def close(self):
        if self.pointer:
            _release(self.pointer)
            self.pointer = None
            self.ole.CoUninitialize()
