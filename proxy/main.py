"""Main entry point for the mini-nginx proxy server."""

import asyncio
import os
import signal
import sys
from pathlib import Path

import pyroscope

from proxy.logger import setup_aiologger, get_logger, set_logging_level
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
            oncpu=False,
        )
        print("Pyroscope initialized successfully!")
    except Exception as e:
        print(f"Pyroscope init error: {e}")


def _config_path() -> Path:
    """Config path: CONFIG_PATH env, or first arg, or config.yaml in cwd."""
    path = os.environ.get("CONFIG_PATH", "").strip()
    if path:
        return Path(path)
    if len(sys.argv) > 1 and not sys.argv[1].replace(".", "").isdigit():
        return Path(sys.argv[1])
    return Path("config.yaml")


def _apply_logging_level(level: str) -> None:
    """Apply logging level from config (file or env)."""
    set_logging_level(level)


def _reload_config(path: Path) -> None:
    """Load config from file and apply (logging level). Called on SIGHUP or startup."""
    holder = load_config(path)
    if holder is not None:
        _apply_logging_level(holder.model.logging.level)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                get_logger().info(
                    "Config reloaded from %s (logging level=%s)"
                    % (str(path), holder.model.logging.level)
                )
            )
        except RuntimeError:
            pass  # no event loop (e.g. during shutdown)


if __name__ == "__main__":
    import logging
    # Sync logging for config load (runs before event loop; aiologger needs loop).
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)

    config_path = _config_path()

    # Load config first (sync _load_log in config; aiologger setup after)
    holder = None
    if config_path.is_file():
        holder = load_config(config_path)
    if holder is None:
        holder = build_fallback_from_env()
        set_config_fallback(holder)
    level = holder.model.logging.level

    # Setup async logger with level from config (file or env)
    setup_aiologger(level=level)
    _apply_logging_level(level)

    cfg = get_config()
    if cfg is None:
        print("Config not available", file=sys.stderr)
        sys.exit(1)

    init_pyroscope()

    host = cfg.model.listen_host
    port = cfg.model.listen_port
    metrics_host = cfg.model.metrics_host
    metrics_port = cfg.model.metrics_port

    if len(sys.argv) >= 2 and sys.argv[1].replace(".", "").isdigit():
        host = sys.argv[1]
    if len(sys.argv) >= 3 and sys.argv[2].isdigit():
        port = int(sys.argv[2])

    async def run_all():
        logger = get_logger()
        await logger.info(
            "Starting proxy server on %s:%d, metrics on %s:%d (config: %s)"
            % (host, port, metrics_host, metrics_port, config_path)
        )
        if not config_path.is_file():
            await logger.info("Config from env (no file %s)" % (str(config_path),))
        else:
            c = get_config()
            if c is not None:
                await logger.info(
                    "Config loaded from %s (listen=%s, upstreams=%d)"
                    % (str(config_path), c.model.listen, len(c.model.upstreams))
                )

        loop = asyncio.get_running_loop()
        try:
            loop.add_signal_handler(
                signal.SIGHUP,
                lambda: _reload_config(config_path),
            )
        except (NotImplementedError, ValueError):
            pass

        task = asyncio.create_task(metrics.run_metrics_server(metrics_host, metrics_port))
        try:
            await run_server(host, port)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            await logger.shutdown()

    try:
        asyncio.run(run_all())
    except KeyboardInterrupt:
        print("Server stopped by user", file=sys.stderr)
    except Exception as e:
        print(f"Server error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
