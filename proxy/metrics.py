"""
Minimal metrics for the proxy: counters and request duration.
Exposed on a separate port in Prometheus text format.
"""

import asyncio
import time
from typing import Dict

# All counters and sums; guarded by _lock for consistency.
_lock: asyncio.Lock = asyncio.Lock()
_requests_total: int = 0
_requests_parse_errors_total: int = 0
_response_total_by_class: Dict[str, int] = {"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0}
_request_duration_sum: float = 0.0
_request_duration_count: int = 0
_bytes_sent_total: int = 0
_bytes_received_total: int = 0
_upstream_requests_total: Dict[str, int] = {}
_upstream_errors_total: Dict[str, int] = {}
_timeout_errors_total: Dict[str, int] = {"connect": 0, "read": 0, "write": 0, "total": 0}


def _status_class(status: int) -> str:
    if status < 300:
        return "2xx"
    if status < 400:
        return "3xx"
    if status < 500:
        return "4xx"
    return "5xx"


def _upstream_key(host: str, port: int) -> str:
    return f"{host}:{port}"


async def record_request_start() -> float:
    """Record request start; returns start time for duration."""
    async with _lock:
        global _requests_total
        _requests_total += 1
    return time.monotonic()


async def record_request_done(start_time: float, status: int, upstream_host: str, upstream_port: int, bytes_sent: int) -> None:
    """Record successful request completion (status from upstream or 502/504)."""
    duration = time.monotonic() - start_time
    key = _upstream_key(upstream_host, upstream_port)
    async with _lock:
        global _request_duration_sum, _request_duration_count, _response_total_by_class, _bytes_sent_total, _upstream_requests_total
        _request_duration_sum += duration
        _request_duration_count += 1
        _response_total_by_class[_status_class(status)] = _response_total_by_class.get(_status_class(status), 0) + 1
        _bytes_sent_total += bytes_sent
        _upstream_requests_total[key] = _upstream_requests_total.get(key, 0) + 1


async def record_parse_error() -> None:
    async with _lock:
        global _requests_parse_errors_total
        _requests_parse_errors_total += 1


async def record_upstream_error(upstream_host: str, upstream_port: int, error_type: str) -> None:
    """error_type: 'timeout', 'connection_refused', 'other'."""
    key = _upstream_key(upstream_host, upstream_port)
    async with _lock:
        global _upstream_errors_total
        if key not in _upstream_errors_total:
            _upstream_errors_total[key] = {}
        _upstream_errors_total[key][error_type] = _upstream_errors_total[key].get(error_type, 0) + 1


async def record_timeout_error(timeout_type: str) -> None:
    """timeout_type: 'connect', 'read', 'write', 'total'."""
    async with _lock:
        global _timeout_errors_total
        _timeout_errors_total[timeout_type] = _timeout_errors_total.get(timeout_type, 0) + 1


async def record_response_status(status: int) -> None:
    """Record response status (e.g. when we return 502/504)."""
    async with _lock:
        global _response_total_by_class
        _response_total_by_class[_status_class(status)] = _response_total_by_class.get(_status_class(status), 0) + 1


def _render_prometheus_sync() -> str:
    """Render from current in-memory state (caller must hold _lock or single-threaded read)."""
    lines = []
    lines.append("# TYPE proxy_requests_total counter")
    lines.append(f"proxy_requests_total {_requests_total}")
    lines.append("# TYPE proxy_requests_parse_errors_total counter")
    lines.append(f"proxy_requests_parse_errors_total {_requests_parse_errors_total}")
    lines.append("# TYPE proxy_responses_total counter")
    for cls in ["2xx", "3xx", "4xx", "5xx"]:
        lines.append(f'proxy_responses_total{{status_class="{cls}"}} {_response_total_by_class.get(cls, 0)}')
    lines.append("# TYPE proxy_request_duration_seconds summary")
    lines.append(f"proxy_request_duration_seconds_sum {_request_duration_sum:.6f}")
    lines.append(f"proxy_request_duration_seconds_count {_request_duration_count}")
    lines.append("# TYPE proxy_bytes_sent_total counter")
    lines.append(f"proxy_bytes_sent_total {_bytes_sent_total}")
    lines.append("# TYPE proxy_upstream_requests_total counter")
    for key, val in sorted(_upstream_requests_total.items()):
        host, _, port = key.rpartition(":")
        lines.append(f'proxy_upstream_requests_total{{upstream="{key}"}} {val}')
    lines.append("# TYPE proxy_upstream_errors_total counter")
    for up, by_type in sorted(_upstream_errors_total.items()):
        for typ, val in sorted(by_type.items()):
            lines.append(f'proxy_upstream_errors_total{{upstream="{up}",type="{typ}"}} {val}')
    lines.append("# TYPE proxy_timeout_errors_total counter")
    for typ in ["connect", "read", "write", "total"]:
        lines.append(f'proxy_timeout_errors_total{{type="{typ}"}} {_timeout_errors_total.get(typ, 0)}')
    return "\n".join(lines) + "\n"


async def get_metrics_prometheus() -> str:
    """Return current metrics in Prometheus text format (thread-safe)."""
    async with _lock:
        return _render_prometheus_sync()


async def run_metrics_server(host: str, port: int) -> None:
    """Run a minimal HTTP server on (host, port) serving GET /metrics."""
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await reader.readline()
            if not request_line:
                writer.close()
                return
            # Read remaining headers until \r\n\r\n
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
            # Request line: "GET /metrics HTTP/1.1" -> path is second token
            parts = request_line.decode("utf-8", errors="replace").split(None, 2) if request_line else []
            path = (parts[1].split("?")[0] if len(parts) >= 2 else "")
            if path == "/metrics":
                body = await get_metrics_prometheus()
                response = (
                    "HTTP/1.1 200 OK\r\n"
                    "Content-Type: text/plain; charset=utf-8\r\n"
                    f"Content-Length: {len(body.encode('utf-8'))}\r\n"
                    "Connection: close\r\n\r\n"
                ) + body
            else:
                response = "HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n"
            writer.write(response.encode("utf-8"))
            await writer.drain()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    srv = await asyncio.start_server(handler, host, port, reuse_address=True)
    async with srv:
        await srv.serve_forever()
