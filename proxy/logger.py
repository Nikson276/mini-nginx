"""Logging setup with optional trace_id in log format via context."""

import logging
import contextvars

# Context variable for trace_id; set per-request in client_connected.
trace_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")


class TraceIdFormatter(logging.Formatter):
    """Formatter that appends trace_id from context when non-empty."""

    def format(self, record: logging.LogRecord) -> str:
        trace_id = trace_id_ctx.get()
        setattr(record, "trace_id_fmt", (" trace_id=" + trace_id) if trace_id else "")
        return super().format(record)
