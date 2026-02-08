"""Main entry point for the mini-nginx proxy server."""

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

import pyroscope

from proxy.logger import TraceIdFormatter
from proxy.proxy_server import main as run_server
from proxy import metrics
from proxy.config import (
    load_config,
    get_config,
    set_config_fallback,
    build_fallback_from_env,
)


def init_pyroscope():
    """Простая инициализация Pyroscope"""
    try:
        app_name = os.getenv("PYROSCOPE_APPLICATION_NAME", "proxy-service")
        server = os.getenv("PYROSCOPE_SERVER", "http://pyroscope:4040")
        print(f"Initializing Pyroscope: app={app_name}, server={server}")
        pyroscope.configure(
            application_name=app_name,
            server_address=server,
            detect_subprocesses=True,
            # Для asyncio:
            oncpu=False,  # профилировать wall-clock, а не только CPU
        )
        print("Pyroscope initialized successfully!")
    except Exception as e:
        print(f"Pyroscope init error: {e}")


def setup_logging(level: str = "info"):
    """Configure logging (with optional trace_id from context). Level: debug, info, warning, error."""
    fmt = "%(asctime)s - %(name)s - %(levelname)s - %(message)s%(trace_id_fmt)s"
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(TraceIdFormatter(fmt))
    logging.basicConfig(level=_log_level(level), handlers=[handler])
    logging.getLogger().handlers[0].setFormatter(TraceIdFormatter(fmt))


def _log_level(level: str) -> int:
    return getattr(logging, (level or "info").upper(), logging.INFO)


def _config_path() -> Path:
    """Config path: CONFIG_PATH env, or first arg, or config.yaml in cwd."""
    path = os.environ.get("CONFIG_PATH", "").strip()
    if path:
        return Path(path)
    if len(sys.argv) > 1 and not sys.argv[1].isdigit():
        return Path(sys.argv[1])
    return Path("config.yaml")


def _apply_logging_level(level: str) -> None:
    logging.getLogger().setLevel(_log_level(level))


def _reload_config(path: Path) -> None:
    """Load config from file and apply (logging level). Called on SIGHUP or startup."""
    holder = load_config(path)
    if holder is not None:
        _apply_logging_level(holder.model.logging.level)
        logging.info("Config reloaded from %s (logging level=%s)", path, holder.model.logging.level)


if __name__ == "__main__":
    # Setup logging first (TraceIdFormatter); level may be overridden by config
    setup_logging(os.environ.get("LOG_LEVEL", "info"))

    config_path = _config_path()

    if config_path.is_file():
        holder = load_config(config_path)
        if holder is not None:
            _apply_logging_level(holder.model.logging.level)
        else:
            holder = build_fallback_from_env()
            set_config_fallback(holder)
            _apply_logging_level(holder.model.logging.level)
            logging.warning("Config file invalid or missing, using env fallback")
    else:
        holder = build_fallback_from_env()
        set_config_fallback(holder)
        _apply_logging_level(holder.model.logging.level)
        logging.info("Config from env (no file %s)", config_path)

    cfg = get_config()
    if cfg is None:
        logging.error("Config not available")
        sys.exit(1)

    init_pyroscope()

    host = cfg.model.listen_host
    port = cfg.model.listen_port
    metrics_host = cfg.model.metrics_host
    metrics_port = cfg.model.metrics_port

    # CLI override: python -m proxy.main [host] [port]
    if len(sys.argv) >= 2 and sys.argv[1].replace(".", "").isdigit():
        host = sys.argv[1]
    if len(sys.argv) >= 3 and sys.argv[2].isdigit():
        port = int(sys.argv[2])

    logging.info(
        "Starting proxy server on %s:%d, metrics on %s:%d (config: %s)",
        host, port, metrics_host, metrics_port, config_path,
    )

    async def run_all():
        # SIGHUP: hot reload config (Unix only)
        loop = asyncio.get_running_loop()
        try:
            loop.add_signal_handler(
                signal.SIGHUP,
                lambda: _reload_config(config_path),
            )
        except (NotImplementedError, ValueError):
            pass  # Windows or not in main thread

        task = asyncio.create_task(metrics.run_metrics_server(metrics_host, metrics_port))
        try:
            await run_server(host, port)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    try:
        asyncio.run(run_all())
    except KeyboardInterrupt:
        logging.info("Server stopped by user")
    except Exception as e:
        logging.error("Server error: %s", e, exc_info=True)
        sys.exit(1)
