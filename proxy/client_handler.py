"""Client connection handler for processing HTTP requests."""

import asyncio
import logging
from typing import Optional, Tuple
from asyncio.streams import StreamReader, StreamWriter

from proxy.utils.http import HTTPRequest


logger = logging.getLogger(__name__)


class ClientConnectionHandler:
    """Handles client connections and parses HTTP requests."""
    
    def __init__(self, reader: StreamReader, writer: StreamWriter):
        self.reader = reader
        self.writer = writer
        self.address = writer.get_extra_info('peername')
    
    async def proxy_to_upstream(
        self,
        request: HTTPRequest,
        upstream_host: str,
        upstream_port: int,
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
            upstream_host: Upstream server hostname/IP
            upstream_port: Upstream server port
        """
        upstream_reader = None
        upstream_writer = None
        
        try:
            logger.info(
                'Connecting to upstream %s:%d for %s %s',
                upstream_host,
                upstream_port,
                request.method,
                request.path
            )
            
            # 1. Connect to upstream server
            # asyncio.open_connection creates a TCP connection and returns
            # StreamReader/StreamWriter pair for bidirectional communication
            upstream_reader, upstream_writer = await asyncio.open_connection(
                upstream_host,
                upstream_port
            )
            
            logger.info('Connected to upstream %s:%d', upstream_host, upstream_port)
            
            # 2. Send request to upstream
            # This writes: start line + headers + empty line + body (streamed)
            logger.debug('Sending request to upstream: %s %s', request.method, request.path)
            await request.write_to_upstream(upstream_writer)
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
            first_chunk = True
            
            # Read until upstream closes connection (EOF)
            # For HTTP/1.1 with Connection: close, upstream should close after response
            try:
                while True:
                    # Read chunk from upstream
                    # read() will return empty bytes (b'') when upstream closes connection
                    # or when at_eof() is True
                    chunk = await upstream_reader.read(chunk_size)
                    
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
                    self.writer.write(chunk)
                    # drain() ensures backpressure: if client's receive buffer is full,
                    # we wait here instead of buffering everything in memory
                    # This prevents memory exhaustion on large responses
                    await self.writer.drain()
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
