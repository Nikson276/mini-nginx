"""Main entry point for the mini-nginx proxy server."""

import pyroscope
import asyncio
import logging
import os
import sys

from proxy.logger import TraceIdFormatter
from proxy.proxy_server import main as run_server
from proxy import metrics


def init_pyroscope():
    """Простая инициализация Pyroscope"""
    try:
        app_name = os.getenv("PYROSCOPE_APPLICATION_NAME", "proxy-service")
        server = os.getenv("PYROSCOPE_SERVER", "http://pyroscope:4040")
        
        print(f"Initializing Pyroscope: app={app_name}, server={server}")
        
        pyroscope.configure(
            application_name=app_name,
            server_address=server,
        )
        
        print("Pyroscope initialized successfully!")
    except Exception as e:
        print(f"Pyroscope init error: {e}")

def setup_logging():
    """Configure logging for the application (with optional trace_id from context)."""
    fmt = "%(asctime)s - %(name)s - %(levelname)s - %(message)s%(trace_id_fmt)s"
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(TraceIdFormatter(fmt))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    # Ensure all log records have trace_id_fmt (Formatter sets it)
    logging.getLogger().handlers[0].setFormatter(TraceIdFormatter(fmt))


if __name__ == '__main__':
    setup_logging()
    init_pyroscope()
    
    # Default: localhost for local dev; in Docker set PROXY_LISTEN_HOST=0.0.0.0
    host = os.environ.get('PROXY_LISTEN_HOST', '127.0.0.1')
    port = int(os.environ.get('PROXY_LISTEN_PORT', '8080'))
    
    # Allow override via command line arguments
    if len(sys.argv) > 1:
        host = sys.argv[1]
    if len(sys.argv) > 2:
        port = int(sys.argv[2])
    
    metrics_host = os.environ.get('METRICS_LISTEN_HOST', '127.0.0.1')
    metrics_port = int(os.environ.get('METRICS_LISTEN_PORT', '8081'))
    logging.info('Starting proxy server on %s:%d, metrics on %s:%d', host, port, metrics_host, metrics_port)

    async def run_all():
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
        logging.info('Server stopped by user')
    except Exception as e:
        logging.error('Server error: %s', e, exc_info=True)
        sys.exit(1)
