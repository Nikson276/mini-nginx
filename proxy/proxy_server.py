import sys
import asyncio
import uuid
from asyncio.streams import StreamReader, StreamWriter

from proxy.client_handler import ClientConnectionHandler
from proxy.upstream_pool import Upstream
from proxy.logger import get_logger, trace_id_ctx
from proxy import metrics
from proxy.config import get_config


logger = get_logger()


async def client_connected(reader: StreamReader, writer: StreamWriter):
    """
    Handle incoming client connection and proxy HTTP requests to upstream.
    
    Flow:
    1. Acquire client connection slot (wait if limit reached)
    2. Parse HTTP request from client
    3. Connect to upstream server (with upstream connection limit)
    4. Forward request to upstream (headers + body stream)
    5. Forward response from upstream to client (stream)
    6. Close connections properly
    7. Release client connection slot
    """
    cfg = get_config()
    if cfg is None:
        await logger.error("Config not initialized")
        writer.close()
        return

    address = writer.get_extra_info('peername')
    trace_id = str(uuid.uuid4())
    token = trace_id_ctx.set(trace_id)

    async with cfg.connection_limits.client_connection():
        await logger.info('Client connected: %s' % (address,))

        handler = ClientConnectionHandler(
            reader,
            writer,
            timeout_policy=cfg.timeout_policy,
            limit_manager=cfg.connection_limits,
            trace_id=trace_id,
        )

        try:
            request = await handler.parse_request()
            if not request:
                await metrics.record_parse_error()
                await logger.warning('Failed to parse request from %s' % (address,))
                return

            start_time = await metrics.record_request_start()
            await logger.info(
                'Request: %s %s %s from %s'
                % (request.method, request.path, request.version, address)
            )
            await logger.debug('Headers: %s' % (request.headers,))

            upstream = await cfg.upstream_pool.get_next()
            await logger.info(
                'Selected upstream %s:%d for %s %s (round-robin)'
                % (upstream.host, upstream.port, request.method, request.path)
            )
            
            # 3. Proxy request to selected upstream
            result = await handler.proxy_to_upstream(
                request,
                upstream=upstream,
            )
            if result is not None:
                status, bytes_sent = result
                await metrics.record_request_done(
                    start_time, status, upstream.host, upstream.port, bytes_sent
                )

        except asyncio.CancelledError:
            await logger.warning('Client connection cancelled: %s' % (address,))
            raise

        except Exception as e:
            await logger.error('Error handling client %s: %s' % (address, e), exc_info=True)

        finally:
            await logger.info('Client disconnected: %s' % (address,))
            trace_id_ctx.reset(token)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


async def main(host: str, port: int):
    srv = await asyncio.start_server(
        client_connected,
        host,
        port,
        reuse_address=True,
        reuse_port=True,
        limit=256*1024,      # 256KB вместо 64KB
        backlog=65535
    )

    async with srv:
        await srv.serve_forever()


if __name__ == '__main__':
    asyncio.run(main('127.0.0.1', 8080))
