from __future__ import annotations

import argparse
import ctypes
import json
import logging
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen
import webbrowser


APP_NAME = "ScholarAgent"
APP_VERSION = "0.2.0"
HOST = "127.0.0.1"


def _resource_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))


def _data_root() -> Path:
    local_app_data = os.getenv("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local_app_data) / APP_NAME


def _runtime_file(name: str) -> Path:
    path = _data_root() / "runtime" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _configure_environment(api_port: int, browser_port: int) -> None:
    root = _data_root()
    storage = root / "data"
    uploads = root / "uploads"
    logs = root / "logs"
    for path in (storage, uploads, logs):
        path.mkdir(parents=True, exist_ok=True)
    defaults = {
        "SCHOLAR_ENV": "desktop",
        "SCHOLAR_DESKTOP_MODE": "true",
        "SCHOLAR_DISABLE_DEMO_USERS": "true",
        "SCHOLAR_STORAGE_BACKEND": "sqlite",
        "SCHOLAR_STORAGE_DIR": str(storage),
        "SCHOLAR_UPLOAD_DIR": str(uploads),
        "SCHOLAR_RUNTIME_CONFIG_PATH": str(storage / "runtime_config.json"),
        "SCHOLAR_RAG_INDEX_BACKEND": "chromadb",
        "SCHOLAR_RAG_EMBEDDING_PROVIDER": "lexical",
        "SCHOLAR_TASK_EXECUTION_MODE": "inline",
        "SCHOLAR_CHECKPOINT_BACKEND": "sqlite",
        "SCHOLAR_REDIS_URL": "",
        "SCHOLAR_MCP_URL": "",
        "SCHOLAR_BROWSER_CHANNEL": "msedge",
        "SCHOLAR_BROWSER_WORKER_URL": f"http://{HOST}:{browser_port}",
        "SCHOLAR_CORS_ALLOW_ORIGINS": f"http://{HOST}:{api_port}",
        "SCHOLAR_FRONTEND_DIR": str(_resource_root() / "frontend" / "dist"),
        "SCHOLAR_ALLOW_MOCK_DATA": "false",
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)


def _configure_logging() -> None:
    log_path = _data_root() / "logs" / "desktop.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        encoding="utf-8",
    )


def _free_port(preferred: int) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((HOST, preferred))
            return preferred
        except OSError:
            probe.bind((HOST, 0))
            return int(probe.getsockname()[1])


def _healthy(url: str, timeout: float = 0.8) -> bool:
    try:
        with urlopen(url, timeout=timeout) as response:
            return response.status == 200
    except (OSError, URLError):
        return False


def _read_endpoint() -> dict[str, Any] | None:
    path = _runtime_file("endpoint.json")
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write_endpoint(api_port: int, browser_port: int) -> None:
    _runtime_file("endpoint.json").write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "version": APP_VERSION,
                "url": f"http://{HOST}:{api_port}/app.html?desktop=1",
                "health_url": f"http://{HOST}:{api_port}/health",
                "browser_port": browser_port,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _open_existing(no_browser: bool = False) -> bool:
    endpoint = _read_endpoint()
    if not endpoint or not _healthy(str(endpoint.get("health_url") or "")):
        return False
    if not no_browser:
        webbrowser.open(str(endpoint["url"]), new=1)
    return True


def _stop_existing(show_message: bool = True) -> int:
    endpoint = _read_endpoint() or {}
    try:
        pid = int(endpoint.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    if pid > 0 and pid != os.getpid():
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    _runtime_file("endpoint.json").unlink(missing_ok=True)
    if show_message:
        _message("ScholarAgent 已停止。", APP_NAME)
    return 0


def _message(text: str, title: str, error: bool = False) -> None:
    flags = 0x10 if error else 0x40
    try:
        ctypes.windll.user32.MessageBoxW(0, text, title, flags)
    except Exception:
        logging.getLogger(__name__).error("%s: %s", title, text)


def _serve(app: Any, port: int, log_level: str = "warning"):
    import uvicorn

    config = uvicorn.Config(
        app,
        host=HOST,
        port=port,
        log_level=log_level,
        access_log=False,
        log_config=None,
    )
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None
    server.run()


def _wait_and_open(url: str, health_url: str, no_browser: bool) -> None:
    for _ in range(120):
        if _healthy(health_url):
            if not no_browser:
                webbrowser.open(url, new=1)
            return
        time.sleep(0.25)
    _message(
        f"ScholarAgent 启动超时。请查看日志：{_data_root() / 'logs' / 'desktop.log'}",
        APP_NAME,
        error=True,
    )


def run(no_browser: bool = False) -> int:
    if _open_existing(no_browser=no_browser):
        return 0
    api_port = _free_port(8000)
    browser_port = _free_port(8002)
    _configure_environment(api_port, browser_port)
    _configure_logging()
    resource_root = _resource_root()
    os.chdir(resource_root)
    if str(resource_root) not in sys.path:
        sys.path.insert(0, str(resource_root))
    try:
        from browser_worker.server import app as browser_app
        from app.main import app as backend_app

        browser_thread = threading.Thread(
            target=_serve,
            args=(browser_app, browser_port),
            name="scholar-browser-worker",
            daemon=True,
        )
        browser_thread.start()
        _write_endpoint(api_port, browser_port)
        url = f"http://{HOST}:{api_port}/app.html?desktop=1"
        health_url = f"http://{HOST}:{api_port}/health"
        threading.Thread(
            target=_wait_and_open,
            args=(url, health_url, no_browser),
            name="scholar-browser-opener",
            daemon=True,
        ).start()
        _serve(backend_app, api_port, "info")
        return 0
    except Exception as exc:
        logging.exception("Desktop startup failed")
        _message(
            f"ScholarAgent 无法启动：{exc}\n\n日志：{_data_root() / 'logs' / 'desktop.log'}",
            APP_NAME,
            error=True,
        )
        return 1
    finally:
        endpoint = _read_endpoint() or {}
        if int(endpoint.get("pid") or 0) == os.getpid():
            _runtime_file("endpoint.json").unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--version", action="store_true")
    args, _ = parser.parse_known_args()
    if args.version:
        _message(f"{APP_NAME} {APP_VERSION}", APP_NAME)
        return 0
    if args.stop:
        return _stop_existing(show_message=not args.quiet)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    return run(no_browser=args.no_browser)


if __name__ == "__main__":
    raise SystemExit(main())
