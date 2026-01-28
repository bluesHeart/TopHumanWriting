# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import logging
import logging.config
import os
from pathlib import Path
import socket
import traceback
import urllib.parse
import urllib.request
import webbrowser


def _resolve_data_dir(base_dir: Path) -> Path:
    raw = (os.environ.get("TOPHUMANWRITING_DATA_DIR") or os.environ.get("AIWORDDETECTOR_DATA_DIR") or "").strip()
    if raw:
        try:
            p = Path(raw)
            if not p.is_absolute():
                p = base_dir.joinpath(p)
            p.mkdir(parents=True, exist_ok=True)
            return p
        except Exception:
            pass

    thw = base_dir.joinpath("TopHumanWriting_data")
    legacy = base_dir.joinpath("AIWordDetector_data")
    if legacy.exists() and not thw.exists():
        return legacy
    return thw


def _resolve_log_path(base_dir: Path) -> Path:
    raw = (os.environ.get("AIW_LOG_FILE") or "").strip()
    if raw:
        try:
            p = Path(raw)
            if not p.is_absolute():
                p = base_dir.joinpath(p)
            p.parent.mkdir(parents=True, exist_ok=True)
            return p
        except Exception:
            pass
    p = _resolve_data_dir(base_dir).joinpath("logs", "launch.log")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return p


def _build_log_config(log_path: Path) -> dict:
    handlers: dict = {
        "file": {
            "class": "logging.FileHandler",
            "formatter": "default",
            "filename": str(log_path),
            "encoding": "utf-8",
        }
    }
    handler_names = ["file"]
    # pythonw has no console; only add stderr when available.
    try:
        import sys

        if getattr(sys, "stderr", None) is not None:
            handlers["stderr"] = {
                "class": "logging.StreamHandler",
                "formatter": "default",
                "stream": "ext://sys.stderr",
            }
            handler_names.append("stderr")
    except Exception:
        pass

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"default": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
        "handlers": handlers,
        "loggers": {
            "uvicorn": {"handlers": handler_names, "level": "INFO", "propagate": False},
            "uvicorn.error": {"handlers": handler_names, "level": "INFO", "propagate": False},
            "uvicorn.access": {"handlers": handler_names, "level": "INFO", "propagate": False},
        },
        "root": {"handlers": handler_names, "level": "INFO"},
    }


def _health_ok(host: str, port: int) -> bool:
    url = f"http://{host}:{port}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=0.4) as resp:
            if getattr(resp, "status", 200) != 200:
                return False
            raw = resp.read()
        data = json.loads(raw.decode("utf-8", errors="replace"))
        return bool(data.get("ok"))
    except Exception:
        return False


def _port_available(host: str, port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((host, port))
        return True
    except OSError:
        return False


def main() -> None:
    host = os.environ.get("AIW_HOST", "127.0.0.1")
    preferred_port = int(os.environ.get("AIW_PORT", "7860"))
    max_tries = int(os.environ.get("AIW_PORT_MAX_TRIES", "20"))
    base_dir = Path(__file__).resolve().parent.parent
    log_path = _resolve_log_path(base_dir)
    log_config = _build_log_config(log_path)
    try:
        logging.config.dictConfig(log_config)
    except Exception:
        logging.basicConfig(level=logging.INFO)
    log = logging.getLogger("aiw.launch")
    log.info("Launching TopHumanWriting Web")
    log.info("base_dir=%s", str(base_dir))
    log.info("preferred_port=%s max_tries=%s", preferred_port, max_tries)
    log.info("log_path=%s", str(log_path))

    # If already running, just open it.
    for candidate in range(preferred_port, preferred_port + max_tries):
        if _health_ok(host, candidate):
            url = f"http://{host}:{candidate}/"
            log.info("Already running: %s", url)
            try:
                webbrowser.open(url)
            except Exception:
                pass
            return

    # Otherwise, find a free port.
    port = None
    for candidate in range(preferred_port, preferred_port + max_tries):
        if _port_available(host, candidate):
            port = candidate
            break
    if port is None:
        port = preferred_port

    url = f"http://{host}:{port}/"
    log.info("Selected URL: %s", url)
    try:
        loading_path = Path(__file__).parent.joinpath("static", "loading.html").resolve()
        timeout_ms = int(os.environ.get("AIW_LOADING_TIMEOUT_MS", "180000") or 180000)
        qs = urllib.parse.urlencode(
            {
                "host": host,
                "port": str(preferred_port),
                "tries": str(max_tries),
                "timeout_ms": str(timeout_ms),
                "log": log_path.as_uri(),
            }
        )
        loading_url = f"{loading_path.as_uri()}?{qs}"
        webbrowser.open(loading_url)
    except Exception:
        # Fallback: open the target URL directly.
        try:
            webbrowser.open(url)
        except Exception:
            pass

    # Run uvicorn with a server handle so the app can request a graceful exit.
    try:
        import uvicorn  # lazy import (faster splash screen)
        from webapp import app as app_module  # lazy import (may be heavy)

        app = app_module.app
        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level=os.environ.get("AIW_LOG_LEVEL", "info"),
            log_config=log_config,
        )
        server = uvicorn.Server(config)
        try:
            app.state.uvicorn_server = server
            app.state.launched_by_launcher = True
        except Exception:
            pass
        server.run()
    except Exception:
        log.error("Failed to start server:\n%s", traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
