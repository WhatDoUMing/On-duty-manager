"""Small Win32 windows: no browser engine, GUI toolkit, audio or busy loop.

Each window owns its message loop. UI calls stay on its thread; other threads
only post refresh/close messages. The widget is non-activating and stays below
ordinary application windows. Reminder dialogs live in a separate process.
"""
import ctypes as C
from ctypes import wintypes as W
import logging
import threading

LRESULT = C.c_ssize_t
WNDPROC = C.WINFUNCTYPE(LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)


class WNDCLASS(C.Structure):
    _fields_ = [("style", W.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", C.c_int),
                ("cbWndExtra", C.c_int), ("hInstance", W.HINSTANCE), ("hIcon", W.HICON),
                ("hCursor", W.HANDLE), ("hbrBackground", W.HBRUSH), ("lpszMenuName", W.LPCWSTR), ("lpszClassName", W.LPCWSTR)]


class PAINTSTRUCT(C.Structure):
    _fields_ = [("hdc", W.HDC), ("fErase", W.BOOL), ("rcPaint", W.RECT), ("fRestore", W.BOOL),
                ("fIncUpdate", W.BOOL), ("rgbReserved", C.c_byte*32)]


class SCROLLINFO(C.Structure):
    _fields_ = [("cbSize", W.UINT), ("fMask", W.UINT), ("nMin", C.c_int), ("nMax", C.c_int),
                ("nPage", W.UINT), ("nPos", C.c_int), ("nTrackPos", C.c_int)]


class MONITORINFO(C.Structure):
    _fields_ = [("cbSize", W.DWORD), ("rcMonitor", W.RECT), ("rcWork", W.RECT), ("dwFlags", W.DWORD)]


def _rgb(r, g, b):
    return r | g << 8 | b << 16


class Window:
    REFRESH, STOP = 0x8001, 0x8002

    def __init__(self, data, widget=False, layout=None, save=lambda x: None,
                 visible=lambda: True, hide=lambda: None, action=lambda x: None):
        self.u, self.g, self.k = C.WinDLL("user32", use_last_error=True), C.WinDLL("gdi32", use_last_error=True), C.WinDLL("kernel32", use_last_error=True)
        self._signatures()
        try:
            self.u.SetThreadDpiAwarenessContext(C.c_void_p(-4))
        except AttributeError:
            pass
        self.data, self.widget, self.save, self.visible, self.hide, self.action = data, widget, save, visible, hide, action
        self.payload, self.offset, self.buttons, self.hwnd = data(), 0, [], None
        self.layout = layout if isinstance(layout, dict) else {}
        self.collapsed = bool(self.layout.get("collapsed", False)) if widget else False
        work = W.RECT()
        self.u.SystemParametersInfoW(0x30, 0, C.byref(work), 0)
        self.scale = self.u.GetDpiForSystem()/96
        width = round((350 if widget else 660)*self.scale)
        count = len(self.payload.get("today" if widget else "tomorrow", {}).get("areas", []))
        height = round(min(120+max(1, count)*100, (work.bottom-work.top)/self.scale*.45)*self.scale) if widget else round(min(230+max(1, count)*100, (work.bottom-work.top)/self.scale*.75)*self.scale)
        self.full_height = self.layout.get("full_height", height)
        x = work.right-width-round(16*self.scale) if widget else (work.right-width)//2
        y = work.top+round(16*self.scale) if widget else work.top+max(0, (work.bottom-work.top-height)//2)
        if widget:
            width = max(round(260*self.scale), min(int(self.layout.get("width", width)), work.right-work.left))
            height = max(round(100*self.scale), min(int(self.layout.get("height", height)), work.bottom-work.top))
            x, y = int(self.layout.get("x", x)), int(self.layout.get("y", y))
            rect = W.RECT(x, y, x+width, y+height)
            monitor = self.u.MonitorFromRect(C.byref(rect), 2)
            info = MONITORINFO(C.sizeof(MONITORINFO))
            self.u.GetMonitorInfoW(monitor, C.byref(info))
            width, height = min(width, info.rcWork.right-info.rcWork.left), min(height, info.rcWork.bottom-info.rcWork.top)
            x = max(info.rcWork.left, min(x, info.rcWork.right-width))
            y = max(info.rcWork.top, min(y, info.rcWork.bottom-height))
        self.fonts = []
        self.brush = self.g.CreateSolidBrush(_rgb(249, 248, 243))
        self.rule = self.g.CreateSolidBrush(_rgb(226, 228, 217))
        self.instance = self.k.GetModuleHandleW(None)
        self.proc = WNDPROC(self._procedure)
        self.class_name = f"CampusDutyWindow-{threading.get_ident()}"
        wc = WNDCLASS(0, self.proc, 0, 0, self.instance, None, self.u.LoadCursorW(None, C.c_void_p(32512)), self.brush, None, self.class_name)
        if not self.u.RegisterClassW(C.byref(wc)):
            raise C.WinError(C.get_last_error())
        ex = 0x08000080 if widget else 0x00000080  # NOACTIVATE | TOOLWINDOW
        style = 0x80000000 | 0x00040000 | 0x00200000 | 0x02000000  # POPUP | THICKFRAME | VSCROLL | CLIPCHILDREN
        self.hwnd = self.u.CreateWindowExW(ex, self.class_name, "今日值日 · 校园值日管家" if widget else "明日值日提醒 · 校园值日管家",
                                         style, x, y, width, height, None, None, self.instance, None)
        if not self.hwnd:
            raise C.WinError(C.get_last_error())
        self._fonts()
        if not widget:
            for i, label in enumerate(("打开展示页", "十分钟后提醒", "关闭本次提醒")):
                control = self.u.CreateWindowExW(0, "BUTTON", label, 0x50010000,
                                                 0, 0, 100, 40, self.hwnd, C.c_void_p(101+i), self.instance, None)
                self.u.SendMessageW(control, 0x30, self.fonts[1], 1)
                self.buttons.append(control)
        self.refresh()
        if widget:
            self.u.SetWindowPos(self.hwnd, C.c_void_p(1), 0, 0, 0, 0, 0x13)  # BOTTOM, NOACTIVATE, NOMOVE, NOSIZE
        else:
            self.u.ShowWindow(self.hwnd, 1)
        self.u.SetTimer(self.hwnd, 1, 30000, None)

    def _signatures(self):
        # Explicit pointer signatures are essential on 64-bit Windows.
        signatures = {
            "CreateWindowExW": (W.HWND, [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, C.c_int, C.c_int, C.c_int, C.c_int, W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID]),
            "DefWindowProcW": (LRESULT, [W.HWND, W.UINT, W.WPARAM, W.LPARAM]),
            "RegisterClassW": (W.ATOM, [C.POINTER(WNDCLASS)]), "UnregisterClassW": (W.BOOL, [W.LPCWSTR, W.HINSTANCE]),
            "LoadCursorW": (W.HANDLE, [W.HINSTANCE, W.LPVOID]), "BeginPaint": (W.HDC, [W.HWND, C.POINTER(PAINTSTRUCT)]),
            "EndPaint": (W.BOOL, [W.HWND, C.POINTER(PAINTSTRUCT)]), "GetClientRect": (W.BOOL, [W.HWND, C.POINTER(W.RECT)]),
            "GetWindowRect": (W.BOOL, [W.HWND, C.POINTER(W.RECT)]), "FillRect": (C.c_int, [W.HDC, C.POINTER(W.RECT), W.HBRUSH]),
            "DrawTextW": (C.c_int, [W.HDC, W.LPCWSTR, C.c_int, C.POINTER(W.RECT), W.UINT]),
            "ShowWindow": (W.BOOL, [W.HWND, C.c_int]), "DestroyWindow": (W.BOOL, [W.HWND]),
            "SetWindowPos": (W.BOOL, [W.HWND, W.HWND, C.c_int, C.c_int, C.c_int, C.c_int, W.UINT]),
            "SendMessageW": (LRESULT, [W.HWND, W.UINT, W.WPARAM, W.LPARAM]),
            "PostMessageW": (W.BOOL, [W.HWND, W.UINT, W.WPARAM, W.LPARAM]),
            "InvalidateRect": (W.BOOL, [W.HWND, C.POINTER(W.RECT), W.BOOL]),
            "MoveWindow": (W.BOOL, [W.HWND, C.c_int, C.c_int, C.c_int, C.c_int, W.BOOL]),
            "SetTimer": (C.c_size_t, [W.HWND, C.c_size_t, W.UINT, W.LPVOID]),
            "KillTimer": (W.BOOL, [W.HWND, C.c_size_t]),
            "GetMessageW": (W.BOOL, [C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT]),
            "TranslateMessage": (W.BOOL, [C.POINTER(W.MSG)]), "DispatchMessageW": (LRESULT, [C.POINTER(W.MSG)]),
            "SetScrollInfo": (C.c_int, [W.HWND, C.c_int, C.POINTER(SCROLLINFO), W.BOOL]),
            "MonitorFromRect": (W.HANDLE, [C.POINTER(W.RECT), W.DWORD]), "GetMonitorInfoW": (W.BOOL, [W.HANDLE, C.POINTER(MONITORINFO)])}
        for name, (result, args) in signatures.items():
            fn = getattr(self.u, name)
            fn.restype, fn.argtypes = result, args
        for name, result, args in (
            ("CreateSolidBrush", W.HBRUSH, [W.DWORD]), ("DeleteObject", W.BOOL, [W.HANDLE]),
            ("SelectObject", W.HANDLE, [W.HDC, W.HANDLE]), ("SetTextColor", W.DWORD, [W.HDC, W.DWORD]),
            ("SetBkMode", C.c_int, [W.HDC, C.c_int]), ("SaveDC", C.c_int, [W.HDC]),
            ("RestoreDC", W.BOOL, [W.HDC, C.c_int]), ("IntersectClipRect", C.c_int, [W.HDC, C.c_int, C.c_int, C.c_int, C.c_int]),
            ("CreateFontW", W.HANDLE, [C.c_int]*5+[W.DWORD]*8+[W.LPCWSTR])):
            fn = getattr(self.g, name)
            fn.restype, fn.argtypes = result, args
        self.k.GetModuleHandleW.restype, self.k.GetModuleHandleW.argtypes = W.HMODULE, [W.LPCWSTR]

    def _fonts(self):
        for font in self.fonts:
            self.g.DeleteObject(font)
        self.fonts = [self.g.CreateFontW(-round(size*self.scale), 0, 0, 0, weight, 0, 0, 0, 1, 0, 0, 5, 0, "Microsoft YaHei UI")
                      for size, weight in ((22 if self.widget else 30, 650), (14 if self.widget else 17, 400), (12, 400))]

    def refresh(self):
        self.payload = self.data()
        if self.widget:
            self.u.ShowWindow(self.hwnd, 4 if self.visible() else 0)
            self.u.SetWindowPos(self.hwnd, C.c_void_p(1), 0, 0, 0, 0, 0x13)
        self.u.InvalidateRect(self.hwnd, None, True)

    def _draw_text(self, dc, value, rect, font=1, dim=False, measure=False):
        self.g.SelectObject(dc, self.fonts[font])
        self.g.SetTextColor(dc, _rgb(119, 129, 115) if dim else _rgb(36, 55, 45))
        self.u.DrawTextW(dc, value, len(value), C.byref(rect), 0x10 | 0x800 | (0x400 if measure else 0))
        return rect.bottom-rect.top

    def _paint(self, hwnd):
        if not self.fonts:
            return
        ps, rect = PAINTSTRUCT(), W.RECT()
        dc = self.u.BeginPaint(hwnd, C.byref(ps))
        try:
            self.u.GetClientRect(hwnd, C.byref(rect))
            self.u.FillRect(dc, C.byref(rect), self.brush)
            self.g.SetBkMode(dc, 1)
            pad = round(18*self.scale)
            title = "今日值日" if self.widget else "明日值日 · 提前准备"
            self._draw_text(dc, title, W.RECT(pad, pad, rect.right-pad, pad+round(44*self.scale)), 0)
            target = self.payload.get("today" if self.widget else "tomorrow", {})
            subtitle = f'{target.get("date", "")}  ·  {self.payload.get("class_name", "校园值日管家")}'
            self._draw_text(dc, subtitle, W.RECT(pad, pad+round(40*self.scale), rect.right-pad, pad+round(67*self.scale)), 2, True)
            if self.widget:
                self._draw_text(dc, "展开" if self.collapsed else "收起", W.RECT(rect.right-110, 7, rect.right-60, 30), 2, True)
                self._draw_text(dc, "隐藏", W.RECT(rect.right-56, 7, rect.right-6, 30), 2, True)
            if self.collapsed:
                return
            header, footer = round(92*self.scale), (0 if self.widget else round(72*self.scale))
            self.g.SaveDC(dc)
            self.g.IntersectClipRect(dc, 0, header, rect.right, max(header, rect.bottom-footer))
            top = header-self.offset
            areas = target.get("areas", [])
            if not areas:
                self._draw_text(dc, "尚未开始排班，请从托盘打开管理。", W.RECT(pad, top, rect.right-pad, top+80), 1, True)
                top += 80
            for area in areas:
                group = " · "+area["group_name"] if area.get("group_name") else ""
                label = area["area_name"]+group
                self._draw_text(dc, label, W.RECT(pad, top, rect.right-pad, top+round(29*self.scale)), 1)
                top += round(32*self.scale)
                names = "、".join(s["name"]+("（请假）" if s.get("absent") else "") for s in area["students"]) or "暂无出勤人员"
                measured = W.RECT(pad, top, rect.right-pad, top+2000)
                height = self._draw_text(dc, names, measured, 1, True, True)
                self._draw_text(dc, names, measured, 1, True)
                top += height+round(24*self.scale)
                line = W.RECT(pad, top-8, rect.right-pad, top-7)
                self.u.FillRect(dc, C.byref(line), self.rule)
            self.g.RestoreDC(dc, -1)
            content = max(0, top+self.offset-header)
            page = max(1, rect.bottom-footer-header)
            self.max_offset = max(0, content-page)
            self.offset = min(self.offset, self.max_offset)
            info = SCROLLINFO(C.sizeof(SCROLLINFO), 0x7, 0, max(0, content-1), page, self.offset, 0)
            self.u.SetScrollInfo(hwnd, 1, C.byref(info), True)
        finally:
            self.u.EndPaint(hwnd, C.byref(ps))

    def _save(self):
        rect = W.RECT()
        self.u.GetWindowRect(self.hwnd, C.byref(rect))
        self.save({"x": rect.left, "y": rect.top, "width": rect.right-rect.left, "height": rect.bottom-rect.top,
                   "collapsed": self.collapsed, "full_height": self.full_height})

    def _procedure(self, hwnd, message, wp, lp):
        try:
            if message == 0x14:
                return 1
            if message == 0xF:
                self._paint(hwnd)
                return 0
            if message == 0x21 and self.widget:  # WM_MOUSEACTIVATE
                return 3  # MA_NOACTIVATE
            if message == 0x84 and self.widget:  # Custom drag handle, while preserving resize edges.
                hit = self.u.DefWindowProcW(hwnd, message, wp, lp)
                if hit != 1:
                    return hit
                rect = W.RECT()
                self.u.GetWindowRect(hwnd, C.byref(rect))
                x, y = C.c_short(lp & 0xffff).value-rect.left, C.c_short((lp >> 16) & 0xffff).value-rect.top
                if y < round(70*self.scale) and x < rect.right-rect.left-120:
                    return 2
                return 1
            if message == 0x202 and self.widget:
                rect = W.RECT()
                self.u.GetClientRect(hwnd, C.byref(rect))
                x, y = lp & 0xffff, (lp >> 16) & 0xffff
                if y < 35 and x > rect.right-60:
                    self.hide()
                elif y < 35 and x > rect.right-115:
                    outer = W.RECT()
                    self.u.GetWindowRect(hwnd, C.byref(outer))
                    if not self.collapsed:
                        self.full_height = outer.bottom-outer.top
                    self.collapsed = not self.collapsed
                    height = round(100*self.scale) if self.collapsed else self.full_height
                    self.u.SetWindowPos(hwnd, C.c_void_p(1), 0, 0, outer.right-outer.left, height, 0x12)
                    self._save()
                    self.u.InvalidateRect(hwnd, None, True)
                return 0
            if message in (0x115, 0x20A):  # native scrollbar / mouse wheel
                if message == 0x20A:
                    delta = -C.c_short((wp >> 16) & 0xffff).value//120*round(50*self.scale)
                    self.offset += delta
                else:
                    kind = wp & 0xffff
                    if kind in (4, 5):
                        self.offset = (wp >> 16) & 0xffff
                    else:
                        self.offset += {0: -30, 1: 30, 2: -180, 3: 180}.get(kind, 0)
                self.offset = max(0, min(self.offset, getattr(self, "max_offset", 0)))
                self.u.InvalidateRect(hwnd, None, True)
                return 0
            if message == 0x5 and self.buttons:
                rect = W.RECT()
                self.u.GetClientRect(hwnd, C.byref(rect))
                gap, pad, height = round(10*self.scale), round(18*self.scale), round(40*self.scale)
                width = max(50, (rect.right-2*pad-2*gap)//3)
                for i, button in enumerate(self.buttons):
                    self.u.MoveWindow(button, pad+i*(width+gap), rect.bottom-height-pad, width, height, True)
            if message == 0x111 and not self.widget:
                action = {101: "open", 102: "snooze", 103: "dismiss"}.get(wp & 0xffff)
                if action:
                    self.action(action)
                    self.u.DestroyWindow(hwnd)
                    return 0
            if message == 0x232 and self.widget:
                self._save()
                self.u.SetWindowPos(hwnd, C.c_void_p(1), 0, 0, 0, 0, 0x13)
                return 0
            if message in (0x113, self.REFRESH):
                self.refresh()
                return 0
            if message == 0x2E0:  # WM_DPICHANGED
                self.scale = (wp & 0xffff)/96
                self._fonts()
                proposed = C.cast(lp, C.POINTER(W.RECT)).contents
                self.u.SetWindowPos(hwnd, C.c_void_p(1) if self.widget else None, proposed.left, proposed.top,
                                    proposed.right-proposed.left, proposed.bottom-proposed.top, 0x10 if self.widget else 0x4)
                return 0
            if message in (0x10, self.STOP):
                if self.widget and message != self.STOP:
                    self.hide()
                else:
                    if not self.widget and message == 0x10:
                        self.action("dismiss")
                    self.u.DestroyWindow(hwnd)
                return 0
            if message == 0x2:
                self.u.KillTimer(hwnd, 1)
                self.u.PostQuitMessage(0)
                return 0
        except Exception:
            logging.exception("校园原生窗口操作失败")
        return self.u.DefWindowProcW(hwnd, message, wp, lp)

    def run(self):
        # Trigger initial layout after button creation.
        self.u.PostMessageW(self.hwnd, 5, 0, 0)
        message = W.MSG()
        try:
            while self.u.GetMessageW(C.byref(message), None, 0, 0) > 0:
                self.u.TranslateMessage(C.byref(message))
                self.u.DispatchMessageW(C.byref(message))
        finally:
            for obj in self.fonts+[self.brush, self.rule]:
                self.g.DeleteObject(obj)
            self.u.UnregisterClassW(self.class_name, self.instance)


class WidgetHost:
    def __init__(self, service):
        self.service, self.window, self.error = service, None, None
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, name="campus-widget", daemon=True)

    def _run(self):
        try:
            repo = self.service.repository

            def visible():
                _, state = repo.read()
                return bool(state["configs"]) and state["settings"]["widget_visible"]

            def hide():
                revision, _ = repo.read()
                self.service.edit("settings", {"revision": revision, "widget_visible": False})

            self.window = Window(self.service.display, widget=True, layout=repo.get_meta("widget_layout"),
                                 save=lambda value: repo.set_meta("widget_layout", value), visible=visible, hide=hide)
            self.ready.set()
            self.window.run()
        except Exception as exc:
            self.error = exc
            logging.exception("桌面名单启动失败")
            self.ready.set()

    def start(self):
        self.thread.start()
        self.ready.wait(5)
        if self.error:
            raise self.error

    def refresh(self):
        if self.window:
            self.window.u.PostMessageW(self.window.hwnd, Window.REFRESH, 0, 0)

    def stop(self):
        if self.window:
            self.window.u.PostMessageW(self.window.hwnd, Window.STOP, 0, 0)
        self.thread.join(timeout=4)
