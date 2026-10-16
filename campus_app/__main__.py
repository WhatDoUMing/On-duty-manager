import argparse
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys

from .desktop import default_data_dir, run_desktop, run_popup


def main():
    parser = argparse.ArgumentParser(description="校园值日管家：多区域排班、考核与静音提醒")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--popup", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--day", help=argparse.SUPPRESS)
    parser.add_argument("--token", help=argparse.SUPPRESS)
    parser.add_argument("--wallpaper-render", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.wallpaper_render:
        from .wallpaper_render import render_request
        render_request(args.wallpaper_render)
        return
    args.data_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, handlers=[RotatingFileHandler(args.data_dir/"campus.log", maxBytes=1048576,
                                                                       backupCount=2, encoding="utf-8")],
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        if args.popup:
            if args.day and args.token:
                run_popup(args.data_dir, args.day, args.token)
        else:
            run_desktop(args.data_dir, args.port, args.background, args.headless)
    except KeyboardInterrupt:
        pass
    except Exception:
        logging.exception("校园版启动失败")
        if sys.platform == "win32" and not args.headless and not args.popup:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, f"请查看日志：\n{args.data_dir/'campus.log'}", "校园值日管家启动未完成", 0x10)
        raise


if __name__ == "__main__":
    main()
