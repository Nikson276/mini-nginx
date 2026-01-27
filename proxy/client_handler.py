"""Client connection handler for processing HTTP requests."""

import asyncio
import logging
from typing import Optional, Tuple
from asyncio.streams import StreamReader, StreamWriter

from proxy.utils.http import HTTPRequest
from proxy.timeouts import TimeoutPolicy, DEFAULT_TIMEOUT_POLICY


logger = logging.getLogger(__name__)


class ClientConnectionHandler:
    """Handles client connections and parses HTTP requests."""
    
    def __init__(
        self,
        reader: StreamReader,
        writer: StreamWriter,
        timeout_policy: Optional[TimeoutPolicy] = None,
    ):
        self.reader = reader
        self.writer = writer
        self.address = writer.get_extra_info('peername')
        # Use provided timeout policy or default
        self.timeout_policy = timeout_policy or DEFAULT_TIMEOUT_POLICY
    
    async def proxy_to_upstream(
        self,
        request: HTTPRequest,
        upstream,  # Upstream object from upstream_pool
    ) -> None:
        """
        Proxy HTTP request to upstream server with bidirectional streaming.
        
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
        await self.timeout_policy.with_total_timeout(
            self._proxy_to_upstream_internal(request, upstream)
        )
    
    async def _proxy_to_upstream_internal(
        self,
        request: HTTPRequest,
        upstream,  # Upstream object
    ) -> None:
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
            logger.info(
                'Connecting to upstream %s:%d for %s %s',
                upstream_host,
                upstream_port,
                request.method,
                request.path
            )
            
            # 1. Connect to upstream server with CONNECT timeout
            # asyncio.open_connection creates a TCP connection and returns
            # StreamReader/StreamWriter pair for bidirectional communication
            # If upstream is unreachable or slow, we don't want to wait forever
            try:
                upstream_reader, upstream_writer = await self.timeout_policy.with_connect_timeout(
                    asyncio.open_connection(upstream_host, upstream_port)
                )
            except asyncio.TimeoutError:
                # Connect timeout - upstream не отвечает на подключение в течение 1 секунды
                logger.error(
                    'Connection to upstream %s:%d timed out after %dms (upstream may be down or unreachable)',
                    upstream_host,
                    upstream_port,
                    self.timeout_policy.connect_ms
                )
                raise
            except (ConnectionRefusedError, OSError, ConnectionError) as e:
                # Upstream is not available (connection refused, network unreachable, etc.)
                # Это происходит когда upstream выключен или порт закрыт
                # Пробрасываем ошибку наверх - там она будет обработана и залогирована
                raise
            
            logger.info('Connected to upstream %s:%d', upstream_host, upstream_port)
            
            # 2. Send request to upstream with WRITE timeout
            # This writes: start line + headers + empty line + body (streamed)
            # If upstream is slow to accept data, we timeout
            logger.debug('Sending request to upstream: %s %s', request.method, request.path)
            try:
                await self.timeout_policy.with_write_timeout(
                    request.write_to_upstream(upstream_writer)
                )
            except asyncio.TimeoutError:
                logger.error(
                    'Write to upstream %s:%d timed out after %dms',
                    upstream_host,
                    upstream_port,
                    self.timeout_policy.write_ms
                )
                raise
            logger.debug('Request sent to upstream, waiting for response...')
            
            # 3. Stream response from upstream to client
            # We read the entire response (headers + body) and forward it
            # In a more advanced implementation, we could parse headers first
            # and stream body separately, but for MVP this is sufficient
            
            # Since we send Connection: close to upstream, it should close the connection
            # after sending the response. However, some servers might not close immediately.
            # We'll read until EOF, which works when upstream closes connection.
            
            chunk_size = 8192  # 8KB chunks
            total_bytes = 0
            
            # Read response in chunks and immediately forward to client
            # This creates streaming: upstream->proxy->client
            # Each read operation has READ timeout to prevent hanging
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
                        logger.error(
                            'Read from upstream %s:%d timed out after %dms',
                            upstream_host,
                            upstream_port,
                            self.timeout_policy.read_ms
                        )
                        raise
                    
                    if not chunk:  # No data received
                        # Check if connection is closed
                        if upstream_reader.at_eof():
                            logger.debug('Upstream connection closed (EOF)')
                            break
                        # If not EOF but no data, might be keep-alive waiting
                        # Since we sent Connection: close, this shouldn't happen
                        # But let's break anyway to avoid infinite loop
                        logger.warning('No data from upstream but connection not closed')
                        break
                    
                    if first_chunk:
                        logger.debug('Received first chunk from upstream (%d bytes): %s', 
                                   len(chunk), chunk[:100] if len(chunk) > 100 else chunk)
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
                logger.error('Error reading response from upstream: %s', e, exc_info=True)
                raise
            
            # Ensure all data is sent to client
            await self.writer.drain()
            
            logger.info(
                'Finished proxying %s %s to %s:%d (%d bytes)',
                request.method,
                request.path,
                upstream_host,
                upstream_port,
                total_bytes
            )
        
        except asyncio.TimeoutError:
            # Timeout errors are already logged in specific places
            # Send timeout error response to client
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
                pass  # Client might have disconnected
            raise
        
        except (ConnectionRefusedError, OSError, ConnectionError) as e:
            # Upstream is not available (connection refused, network unreachable, etc.)
            # This happens when upstream is down or port is closed
            # This is different from timeout - upstream is simply not reachable
            logger.error(
                'Cannot connect to upstream %s:%d: %s (upstream is likely down)',
                upstream_host,
                upstream_port,
                e
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
            logger.warning('Proxy task cancelled for %s %s', request.method, request.path)
            raise
        
        except Exception as e:
            logger.error(
                'Error proxying to %s:%d: %s',
                upstream_host,
                upstream_port,
                e,
                exc_info=True
            )
            # Send error response to client if we haven't started streaming response
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
                logger.warning("Invalid start line from %s: %s", self.address, start_line)
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
                reader=self.reader,  # Body will be read from this stream
            )
        
        except Exception as e:
            logger.error("Error parsing request from %s: %s", self.address, e)
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
                logger.warning("Invalid header line from %s: %s", self.address, line)
                continue
            
            name, value = line.split(':', 1)
            headers[name.strip().lower()] = value.strip()
        
        return headers
