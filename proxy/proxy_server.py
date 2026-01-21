import logging
import sys
import asyncio
from asyncio.streams import StreamReader, StreamWriter

from proxy.client_handler import ClientConnectionHandler


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(logging.StreamHandler(stream=sys.stdout))


# Simple upstream configuration (will be moved to config.py later)
# For now, we proxy to a single upstream server
UPSTREAM_HOST = '127.0.0.1'
UPSTREAM_PORT = 9001


async def client_connected(reader: StreamReader, writer: StreamWriter):
    """
    Handle incoming client connection and proxy HTTP requests to upstream.
    
    Flow:
    1. Parse HTTP request from client
    2. Connect to upstream server
    3. Forward request to upstream (headers + body stream)
    4. Forward response from upstream to client (stream)
    5. Close connections properly
    """
    handler = ClientConnectionHandler(reader, writer)
    address = handler.address
    logger.info('Client connected: %s', address)

    try:
        # 1. Parse HTTP request from client
        request = await handler.parse_request()
        
        if not request:
            logger.warning('Failed to parse request from %s', address)
            return
        
        logger.info(
            'Request: %s %s %s from %s',
            request.method,
            request.path,
            request.version,
            address
        )
        logger.debug('Headers: %s', request.headers)
        
        # 2. Proxy request to upstream
        # This handles:
        # - Connection to upstream
        # - Sending request (headers + body streaming)
        # - Receiving response and forwarding to client (streaming)
        # - Backpressure handling via drain()
        await handler.proxy_to_upstream(
            request,
            upstream_host=UPSTREAM_HOST,
            upstream_port=UPSTREAM_PORT,
        )
    
    except asyncio.CancelledError:
        logger.warning('Client connection cancelled: %s', address)
        raise
    
    except Exception as e:
        logger.error('Error handling client %s: %s', address, e, exc_info=True)
    
    finally:
        # 3. Clean up client connection
        logger.info('Client disconnected: %s', address)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def main(host: str, port: int):
    srv = await asyncio.start_server(
        client_connected, host, port)

    async with srv:
        await srv.serve_forever()


if __name__ == '__main__':
    asyncio.run(main('127.0.0.1', 8080))
