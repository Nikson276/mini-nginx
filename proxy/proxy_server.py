import logging
import sys
import asyncio
from asyncio.streams import StreamReader, StreamWriter

from proxy.client_handler import ClientConnectionHandler
from proxy.timeouts import TimeoutPolicy, DEFAULT_TIMEOUT_POLICY
from proxy.upstream_pool import UpstreamPool, Upstream, DEFAULT_UPSTREAM_POOL
from proxy.limits import ConnectionLimitManager, ConnectionLimits, DEFAULT_LIMITS


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(logging.StreamHandler(stream=sys.stdout))


# Upstream pool with round-robin load balancing
# Можно настроить несколько upstream серверов для балансировки нагрузки
# По умолчанию используется один upstream, но можно добавить больше:
UPSTREAM_POOL = UpstreamPool([
    Upstream(host='127.0.0.1', port=9001),
    Upstream(host='127.0.0.1', port=9002),
])

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
    
    # Ограничение количества одновременных клиентских соединений
    # Если достигнут лимит max_client_conns, новые клиенты будут ждать
    # Это защищает прокси от перегрузки при большом количестве запросов
    async with CONNECTION_LIMITS.client_connection():
        logger.info('Client connected: %s', address)
        
        # Create handler with timeout policy
        # This ensures all operations (connect, read, write) have timeouts
        handler = ClientConnectionHandler(
            reader,
            writer,
            timeout_policy=TIMEOUT_POLICY,
            limit_manager=CONNECTION_LIMITS,
        )

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
            # This handles:
            # - Connection to upstream (with upstream connection limit)
            # - Sending request (headers + body streaming)
            # - Receiving response and forwarding to client (streaming)
            # - Backpressure handling via drain()
            await handler.proxy_to_upstream(
                request,
                upstream=upstream,
            )
        
        except asyncio.CancelledError:
            logger.warning('Client connection cancelled: %s', address)
            raise
        
        except Exception as e:
            logger.error('Error handling client %s: %s', address, e, exc_info=True)
        
        finally:
            # Clean up client connection
            # Semaphore автоматически освободится при выходе из async with блока
            logger.info('Client disconnected: %s', address)
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
