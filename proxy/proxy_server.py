import logging
import os
import sys
import asyncio
import uuid
from asyncio.streams import StreamReader, StreamWriter

from proxy.client_handler import ClientConnectionHandler
from proxy.timeouts import TimeoutPolicy, DEFAULT_TIMEOUT_POLICY
from proxy.upstream_pool import UpstreamPool, Upstream
from proxy.limits import ConnectionLimitManager, ConnectionLimits, DEFAULT_LIMITS
from proxy.logger import trace_id_ctx
from proxy import metrics


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(logging.StreamHandler(stream=sys.stdout))


def _build_upstream_pool():
    """
    Build upstream pool from environment or use defaults.
    
    Env: UPSTREAM_HOSTS = "host1:port1,host2:port2,..."
    Example: UPSTREAM_HOSTS=upstream1:9001,upstream2:9002 (for Docker Compose)
    """
    hosts_str = os.environ.get('UPSTREAM_HOSTS', '').strip()
    if hosts_str:
        upstreams = []
        for part in hosts_str.split(','):
            part = part.strip()
            if ':' in part:
                host, port_str = part.rsplit(':', 1)
                try:
                    port = int(port_str)
                    upstreams.append(Upstream(host=host.strip(), port=port))
                except ValueError:
                    logger.warning('Invalid upstream entry %r, skip', part)
        if upstreams:
            return UpstreamPool(upstreams)
    # Defaults (localhost, for local dev)
    return UpstreamPool([
        Upstream(host='127.0.0.1', port=9001),
        Upstream(host='127.0.0.1', port=9002),
    ])


# Upstream pool with round-robin load balancing
# Configurable via UPSTREAM_HOSTS env (e.g. Docker: upstream1:9001,upstream2:9002)
UPSTREAM_POOL = _build_upstream_pool()

# Timeout policy (can be loaded from config later)
# Default values: connect=1s, read=15s, write=15s, total=30s
TIMEOUT_POLICY = DEFAULT_TIMEOUT_POLICY

# Connection limits (can be loaded from config later)
# Default values: max_client_conns=1000, max_conns_per_upstream=100
CONNECTION_LIMITS = ConnectionLimitManager(ConnectionLimits(
    max_client_conns=500,
    max_conns_per_upstream=1
))


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
    address = writer.get_extra_info('peername')
    trace_id = str(uuid.uuid4())
    token = trace_id_ctx.set(trace_id)

    # Ограничение количества одновременных клиентских соединений
    async with CONNECTION_LIMITS.client_connection():
        logger.info('Client connected: %s', address)

        handler = ClientConnectionHandler(
            reader,
            writer,
            timeout_policy=TIMEOUT_POLICY,
            limit_manager=CONNECTION_LIMITS,
            trace_id=trace_id,
        )

        try:
            # 1. Parse HTTP request from client
            request = await handler.parse_request()
            
            if not request:
                await metrics.record_parse_error()
                logger.warning('Failed to parse request from %s', address)
                return

            start_time = await metrics.record_request_start()
            logger.info(
                'Request: %s %s %s from %s',
                request.method,
                request.path,
                request.version,
                address,
            )
            logger.debug('Headers: %s', request.headers)
            
            # 2. Select upstream using round-robin load balancing
            # Round-robin распределяет запросы равномерно по всем upstream серверам
            # Первый запрос → первый upstream, второй запрос → второй upstream,
            # третий запрос → снова первый upstream, и так далее по кругу
            upstream = await UPSTREAM_POOL.get_next()
            logger.info(
                'Selected upstream %s:%d for %s %s (round-robin)',
                upstream.host,
                upstream.port,
                request.method,
                request.path
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
            logger.warning('Client connection cancelled: %s', address)
            raise

        except Exception as e:
            logger.error('Error handling client %s: %s', address, e, exc_info=True)

        finally:
            logger.info('Client disconnected: %s', address)
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
        reuse_port=True 
    )

    async with srv:
        await srv.serve_forever()


if __name__ == '__main__':
    asyncio.run(main('127.0.0.1', 8080))
