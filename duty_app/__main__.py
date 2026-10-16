import argparse
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .desktop import default_data_dir, run_desktop, run_popup


def main():
    parser = argparse.ArgumentParser(description="值日管家：托盘提醒和浏览器管理")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--headless", action="store_true", help="仅运行服务，用于诊断")
    parser.add_argument("--popup", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--day", help=argparse.SUPPRESS)
    parser.add_argument("--token", help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.data_dir.mkdir(parents=True, exist_ok=True)
    log = RotatingFileHandler(args.data_dir/"duty.log", maxBytes=1048576, backupCount=2, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[log], format="%(asctime)s %(levelname)s %(message)s")
    try:
        if args.popup:
            if args.day and args.token:
                run_popup(args.data_dir, args.day, args.token)
        else:
            run_desktop(args.data_dir, args.port, args.background, args.headless)
    except KeyboardInterrupt:
        pass
    except Exception:
        logging.exception("程序启动失败")
        raise


if __name__ == "__main__":
    main()
