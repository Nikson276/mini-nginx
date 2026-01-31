"""Main entry point for the mini-nginx proxy server."""

import asyncio
import logging
import os
import sys

from proxy.proxy_server import main as run_server


def setup_logging():
    """Configure logging for the application."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        stream=sys.stdout,
    )


if __name__ == '__main__':
    setup_logging()
    
    # Default: localhost for local dev; in Docker set PROXY_LISTEN_HOST=0.0.0.0
    host = os.environ.get('PROXY_LISTEN_HOST', '127.0.0.1')
    port = int(os.environ.get('PROXY_LISTEN_PORT', '8080'))
    
    # Allow override via command line arguments
    if len(sys.argv) > 1:
        host = sys.argv[1]
    if len(sys.argv) > 2:
        port = int(sys.argv[2])
    
    logging.info('Starting proxy server on %s:%d', host, port)
    
    try:
        asyncio.run(run_server(host, port))
    except KeyboardInterrupt:
        logging.info('Server stopped by user')
    except Exception as e:
        logging.error('Server error: %s', e, exc_info=True)
        sys.exit(1)
