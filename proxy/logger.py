"""
Async logging via aiologger (non-blocking for event loop).
Trace_id from context; level from config (file or env).
"""

import contextvars
import sys
from typing import Optional

from aiologger import Logger
from aiologger.formatters.base import Formatter
from aiologger.handlers.streams import AsyncStreamHandler
from aiologger.levels import LogLevel

# Context variable for trace_id; set per-request in client_connected.
trace_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")

_root_logger: Optional[Logger] = None


async def _noop():
    pass


class _LoggerProxy:
    """
    Proxy that delegates to _root_logger at call time.
    Used so that modules doing `logger = get_logger()` at import time
    (before setup_aiologger(level=...) from config) still get the
    correctly configured logger when they actually log.
    """

    async def info(self, msg, *args, **kwargs):
        if _root_logger is not None:
            return await _root_logger.info(msg, *args, **kwargs)
        return _noop()

    async def error(self, msg, *args, **kwargs):
        if _root_logger is not None:
            return await _root_logger.error(msg, *args, **kwargs)
        return _noop()

    async def warning(self, msg, *args, **kwargs):
        if _root_logger is not None:
            return await _root_logger.warning(msg, *args, **kwargs)
        return _noop()

    async def debug(self, msg, *args, **kwargs):
        if _root_logger is not None:
            return await _root_logger.debug(msg, *args, **kwargs)
        return _noop()

    async def shutdown(self):
        if _root_logger is not None:
            return await _root_logger.shutdown()
        return _noop()


_logger_proxy: Optional[_LoggerProxy] = None

# Default format (same as before)
DEFAULT_FMT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s%(trace_id_fmt)s"


class TraceIdFormatter(Formatter):
    """Formatter that appends trace_id from context when non-empty."""

    def format(self, record):
        trace_id = trace_id_ctx.get()
        setattr(record, "trace_id_fmt", (" trace_id=" + trace_id) if trace_id else "")
        return super().format(record)


def _level_from_str(level: str) -> int:
    """Convert 'info'/'debug'/... to LogLevel value."""
    level = (level or "info").strip().upper()
    return getattr(LogLevel, level, LogLevel.INFO)


def setup_aiologger(level: str = "info", name: str = "proxy") -> Logger:
    """
    Create and set the root aiologger Logger with TraceIdFormatter.
    Level is taken from config (logging.level). Call once at startup.
    """
    global _root_logger
    fmt = DEFAULT_FMT
    formatter = TraceIdFormatter(fmt=fmt)
    handler = AsyncStreamHandler(
        stream=sys.stdout,
        level=LogLevel.DEBUG,
        formatter=formatter,
    )
    _root_logger = Logger(name=name, level=_level_from_str(level))
    _root_logger.add_handler(handler)
    return _root_logger


def get_logger():
    """
    Return a logger that delegates to the root aiologger at call time.
    setup_aiologger(level=...) must be called from main (after loading config)
    so that the level from config is used; modules that do logger = get_logger()
    at import time will still use that level when they log.
    """
    global _logger_proxy
    if _logger_proxy is None:
        _logger_proxy = _LoggerProxy()
    return _logger_proxy


def set_logging_level(level: str) -> None:
    """Update logger level (e.g. on config reload). Level from config: debug, info, warning, error."""
    global _root_logger
    if _root_logger is not None:
        _root_logger.level = _level_from_str(level)
