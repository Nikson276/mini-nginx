"""Client connection handler for processing HTTP requests."""

import asyncio
from typing import Optional, Tuple
from asyncio.streams import StreamReader, StreamWriter

from proxy.utils.http import HTTPRequest
from proxy.timeouts import TimeoutPolicy, DEFAULT_TIMEOUT_POLICY
from proxy.limits import ConnectionLimitManager
from proxy import metrics
from proxy.logger import get_logger


logger = get_logger()


class ClientConnectionHandler:
    """Handles client connections and parses HTTP requests."""

    def __init__(
        self,
        reader: StreamReader,
        writer: StreamWriter,
        timeout_policy: Optional[TimeoutPolicy] = None,
        limit_manager: Optional[ConnectionLimitManager] = None,  # ConnectionLimitManager, optional
        trace_id: Optional[str] = None,
    ):
        self.reader = reader
        self.writer = writer
        self.address = writer.get_extra_info('peername')
        self.timeout_policy = timeout_policy or DEFAULT_TIMEOUT_POLICY
        self.limit_manager = limit_manager
        self.trace_id = trace_id or ''
    
    async def proxy_to_upstream(
        self,
        request: HTTPRequest,
        upstream,  # Upstream object from upstream_pool
    ) -> Optional[Tuple[int, int]]:
        """
        Proxy HTTP request to upstream server with bidirectional streaming.
        Returns (status_code, bytes_sent) on success, or raises on error.
        
        This method:
        1. Connects to upstream server
        2. Sends request headers and streams body to upstream
        3. Streams response from upstream back to client
        4. Handles backpressure using drain() to prevent memory issues
        
        Args:
            request: Parsed HTTP request from client
            upstream: Upstream object (from upstream_pool.UpstreamPool)
        """
        # Import here to avoid circular dependency
        from proxy.upstream_pool import Upstream
        
        if not isinstance(upstream, Upstream):
            raise TypeError(f"Expected Upstream object, got {type(upstream)}")
        
        # Extract host and port from Upstream object
        upstream_host = upstream.host
        upstream_port = upstream.port
        
        # Wrap entire proxy operation in total timeout
        # This ensures no request takes longer than total_ms
        return await self.timeout_policy.with_total_timeout(
            self._proxy_to_upstream_internal(request, upstream)
        )
    
    def _parse_status_from_chunk(self, chunk: bytes) -> int:
        """Parse HTTP status code from first line of response chunk (e.g. HTTP/1.1 200 OK)."""
        try:
            first_line = chunk.split(b"\r\n")[0].decode("utf-8", errors="replace")
            parts = first_line.split(None, 2)
            if len(parts) >= 2:
                return int(parts[1])
        except (ValueError, IndexError):
            pass
        return 200

    async def _proxy_to_upstream_internal(
        self,
        request: HTTPRequest,
        upstream,  # Upstream object
    ) -> Tuple[int, int]:
        """
        Internal method that does the actual proxying.
        Called from proxy_to_upstream which wraps it in total timeout.
        """
        upstream_reader = None
        upstream_writer = None
        
        # Extract host and port from Upstream object
        upstream_host = upstream.host
        upstream_port = upstream.port
        
        try:
            await logger.info(
                'Connecting to upstream %s:%d for %s %s'
                % (upstream_host, upstream_port, request.method, request.path)
            )
            
            # 1. Connect to upstream server with CONNECT timeout and connection limit
            # Ограничение количества соединений к upstream через Semaphore
            # Если достигнут лимит max_conns_per_upstream, корутина будет ждать
            # Это защищает upstream от перегрузки
            
            # Получаем семафор для этого upstream (если лимиты включены)
            upstream_semaphore = None
            if self.limit_manager:
                upstream_semaphore = await self.limit_manager.upstream_connection(upstream)
            
            # Подключаемся к upstream с учетом лимита соединений
            # async with автоматически вызывает acquire() при входе и release() при выходе
            # Если лимит достигнут, корутина будет ждать здесь, пока не освободится место
            try:
                if upstream_semaphore:
                    # Используем семафор для ограничения соединений
                    async with upstream_semaphore:
                        upstream_reader, upstream_writer = await self.timeout_policy.with_connect_timeout(
                            asyncio.open_connection(upstream_host, upstream_port)
                        )
                else:
                    # Лимиты не включены, подключаемся без ограничений
                    upstream_reader, upstream_writer = await self.timeout_policy.with_connect_timeout(
                        asyncio.open_connection(upstream_host, upstream_port)
                    )
            except asyncio.TimeoutError:
                await metrics.record_timeout_error("connect")
                await metrics.record_upstream_error(upstream_host, upstream_port, "timeout")
                await logger.error(
                    'Connection to upstream %s:%d timed out after %dms'
                    % (upstream_host, upstream_port, self.timeout_policy.connect_ms)
                )
                raise
            except (ConnectionRefusedError, OSError, ConnectionError) as e:
                # Upstream is not available (connection refused, network unreachable, etc.)
                # Это происходит когда upstream выключен или порт закрыт
                # Пробрасываем ошибку наверх - там она будет обработана и залогирована
                raise
            
            await logger.info('Connected to upstream %s:%d' % (upstream_host, upstream_port))
            
            # 2. Send request to upstream with WRITE timeout
            # This writes: start line + headers + empty line + body (streamed)
            # If upstream is slow to accept data, we timeout
            await logger.debug('Sending request to upstream: %s %s' % (request.method, request.path))
            try:
                await self.timeout_policy.with_write_timeout(
                    request.write_to_upstream(upstream_writer)
                )
            except asyncio.TimeoutError:
                await logger.error(
                    'Write to upstream %s:%d timed out after %dms trace_id=%s'
                    % (upstream_host, upstream_port, self.timeout_policy.write_ms, self.trace_id)
                )
                raise
            await logger.debug('Request sent to upstream, waiting for response...')
            
            # 3. Stream response from upstream to client
            # We read the entire response (headers + body) and forward it
            # In a more advanced implementation, we could parse headers first
            # and stream body separately, but for MVP this is sufficient
            
            # Since we send Connection: close to upstream, it should close the connection
            # after sending the response. However, some servers might not close immediately.
            # We'll read until EOF, which works when upstream closes connection.
            
            chunk_size = 8192  # 8KB chunks
            total_bytes = 0
            response_status = 200  # default if no chunk

            # Read response in chunks and immediately forward to client
            first_chunk = True
            
            # Read until upstream closes connection (EOF)
            # For HTTP/1.1 with Connection: close, upstream should close after response
            try:
                while True:
                    # Read chunk from upstream with READ timeout
                    # If upstream is slow to send data, we timeout
                    try:
                        chunk = await self.timeout_policy.with_read_timeout(
                            upstream_reader.read(chunk_size)
                        )
                    except asyncio.TimeoutError:
                        await logger.error(
                            'Read from upstream %s:%d timed out after %dms'
                            % (upstream_host, upstream_port, self.timeout_policy.read_ms)
                        )
                        await metrics.record_timeout_error("read")
                        await metrics.record_upstream_error(upstream_host, upstream_port, "timeout")
                        raise
                    
                    if not chunk:  # No data received
                        # Check if connection is closed
                        if upstream_reader.at_eof():
                            await logger.debug('Upstream connection closed (EOF)')
                            break
                        # If not EOF but no data, might be keep-alive waiting
                        # Since we sent Connection: close, this shouldn't happen
                        # But let's break anyway to avoid infinite loop
                        await logger.warning('No data from upstream but connection not closed')
                        break
                    
                    if first_chunk:
                        response_status = self._parse_status_from_chunk(chunk)
                        await logger.debug(
                            'Received first chunk from upstream (%d bytes): %s'
                            % (len(chunk), chunk[:100] if len(chunk) > 100 else chunk)
                        )
                        first_chunk = False

                    total_bytes += len(chunk)
                    
                    # Write chunk to client immediately
                    # Note: We don't timeout client writes - if client is slow,
                    # drain() will naturally handle backpressure
                    self.writer.write(chunk)
                    # drain() ensures backpressure: if client's receive buffer is full,
                    # we wait here instead of buffering everything in memory
                    # This prevents memory exhaustion on large responses
                    await self.writer.drain()
            except asyncio.TimeoutError:
                # Re-raise timeout errors (already logged above)
                raise
            except Exception as e:
                await logger.error('Error reading response from upstream: %s' % (e,), exc_info=True)
                raise

            # Ensure all data is sent to client
            await self.writer.drain()
            
            await logger.info(
                'Finished proxying %s %s to %s:%d (%d bytes)(%d response_status)'
                % (request.method, request.path, upstream_host, upstream_port, total_bytes, response_status)
            )
            return (response_status, total_bytes)

        except asyncio.TimeoutError:
            await metrics.record_response_status(504)
            await metrics.record_timeout_error("total")
            try:
                error_response = (
                    f"{request.version} 504 Gateway Timeout\r\n"
                    "Content-Type: text/plain\r\n"
                    "Connection: close\r\n"
                    "\r\n"
                    "Upstream timeout"
                )
                self.writer.write(error_response.encode())
                await self.writer.drain()
            except Exception:
                pass
            raise

        except (ConnectionRefusedError, OSError, ConnectionError) as e:
            await metrics.record_response_status(502)
            err_type = "connection_refused" if isinstance(e, ConnectionRefusedError) else "other"
            await metrics.record_upstream_error(upstream_host, upstream_port, err_type)
            await logger.error(
                'Cannot connect to upstream %s:%d: %s' % (upstream_host, upstream_port, e)
            )
            try:
                error_response = (
                    f"{request.version} 502 Bad Gateway\r\n"
                    "Content-Type: text/plain\r\n"
                    "Connection: close\r\n"
                    "\r\n"
                    f"Upstream unavailable: {str(e)}"
                )
                self.writer.write(error_response.encode())
                await self.writer.drain()
            except Exception:
                pass  # Client might have disconnected
            raise
        
        except asyncio.CancelledError:
            await logger.warning('Proxy task cancelled for %s %s' % (request.method, request.path))
            raise
        
        except Exception as e:
            await metrics.record_response_status(502)
            await metrics.record_upstream_error(upstream_host, upstream_port, "other")
            await logger.error(
                'Error proxying to %s:%d: %s' % (upstream_host, upstream_port, e),
                exc_info=True,
            )
            try:
                error_response = (
                    f"{request.version} 502 Bad Gateway\r\n"
                    "Content-Type: text/plain\r\n"
                    "Connection: close\r\n"
                    "\r\n"
                    f"Upstream error: {str(e)}"
                )
                self.writer.write(error_response.encode())
                await self.writer.drain()
            except Exception:
                pass  # Client might have disconnected
        
        finally:
            # 4. Clean up upstream connection
            # Always close connections properly to avoid resource leaks
            if upstream_writer:
                upstream_writer.close()
                try:
                    await upstream_writer.wait_closed()
                except Exception:
                    pass
    
    async def parse_request(self) -> Optional[HTTPRequest]:
        """
        Parse HTTP request: start line (method, path, version) + headers.
        Body is available as raw stream through the returned request object.
        
        Returns:
            HTTPRequest object or None if connection is closed or invalid.
        """
        try:
            # Read start line
            start_line = await self._read_line()
            if not start_line:
                return None
            
            # Parse start line: METHOD PATH VERSION
            parts = start_line.split()
            if len(parts) != 3:
                await logger.warning("Invalid start line from %s: %s" % (self.address, start_line))
                return None
            
            method, path, version = parts
            
            # Read headers until empty line (CRLF)
            headers = await self._read_headers()
            if headers is None:
                return None
            
            # Create request object with raw stream for body
            return HTTPRequest(
                method=method,
                path=path,
                version=version,
                headers=headers,
                reader=self.reader,
                trace_id=self.trace_id,
            )
        
        except Exception as e:
            await logger.error("Error parsing request from %s: %s" % (self.address, e))
            return None
    
    async def _read_line(self) -> Optional[str]:
        """Read a line ending with CRLF."""
        line_bytes = b''
        
        while True:
            chunk = await self.reader.read(1)
            if not chunk:
                return None
            
            line_bytes += chunk
            
            # Check for CRLF
            if len(line_bytes) >= 2 and line_bytes[-2:] == b'\r\n':
                return line_bytes[:-2].decode('utf-8', errors='replace')
    
    async def _read_headers(self) -> Optional[dict]:
        """Read headers until empty line (CRLF)."""
        headers = {}
        
        while True:
            line = await self._read_line()
            if line is None:
                return None
            
            # Empty line means end of headers
            if not line:
                break
            
            # Parse header: "Name: Value"
            if ':' not in line:
                await logger.warning("Invalid header line from %s: %s" % (self.address, line))
                continue
            
            name, value = line.split(':', 1)
            headers[name.strip().lower()] = value.strip()
        
        return headers
