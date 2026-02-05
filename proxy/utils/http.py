"""HTTP utilities for parsing requests."""

from typing import Dict, Optional
from asyncio.streams import StreamReader, StreamWriter


class HTTPRequest:
    """Represents a parsed HTTP request."""

    def __init__(
        self,
        method: str,
        path: str,
        version: str,
        headers: Dict[str, str],
        reader: StreamReader,
        trace_id: Optional[str] = None,
    ):
        self.method = method
        self.path = path
        self.version = version
        self.headers = headers
        self.reader = reader  # Raw stream for body
        self.trace_id = trace_id
    
    def __repr__(self) -> str:
        return f"HTTPRequest(method={self.method!r}, path={self.path!r}, version={self.version!r})"
    
    async def write_to_upstream(self, writer: StreamWriter) -> None:
        """
        Write HTTP request to upstream connection.
        
        This method:
        1. Writes the start line (method, path, version)
        2. Writes all headers
        3. Writes empty line (CRLF) to signal end of headers
        4. Streams the body from self.reader to writer
        
        Args:
            writer: StreamWriter to write the request to
        """
        # 1. Write start line: METHOD PATH VERSION
        start_line = f"{self.method} {self.path} {self.version}\r\n"
        writer.write(start_line.encode())
        
        # 2. Write headers
        # Headers are stored in lowercase, but we need to preserve original format
        # For simplicity, we'll write them as-is (they were already parsed)
        # In a real proxy, you might want to preserve original header names
        
        # Add/modify headers for upstream request
        headers_to_send = dict(self.headers)
        
        # Add Connection: close to ensure upstream closes connection after response
        # This simplifies our response reading logic
        if 'connection' not in headers_to_send:
            headers_to_send['connection'] = 'close'
        elif headers_to_send.get('connection', '').lower() != 'close':
            # Override keep-alive to close for simplicity
            headers_to_send['connection'] = 'close'

        # Propagate trace_id to upstream for distributed tracing
        if self.trace_id:
            headers_to_send['x-trace-id'] = self.trace_id

        # Host header is required for HTTP/1.1
        # We'll use the original host from client request
        # (in a real proxy, you might want to modify this)
        if 'host' not in headers_to_send:
            # If no host header, this might cause issues, but we'll proceed
            pass
        
        for name, value in headers_to_send.items():
            # Capitalize first letter of each word in header name for HTTP standard
            # e.g., "content-type" -> "Content-Type"
            header_name = '-'.join(word.capitalize() for word in name.split('-'))
            header_line = f"{header_name}: {value}\r\n"
            writer.write(header_line.encode())
        
        # 3. Write empty line (CRLF) to signal end of headers
        writer.write(b"\r\n")
        await writer.drain()  # Ensure headers are sent before body
        
        # 4. Stream body from client to upstream (if body exists)
        # Check if request has a body by looking at Content-Length or Transfer-Encoding
        # For methods like GET, HEAD, OPTIONS - no body expected
        has_body = False
        content_length = self.headers.get('content-length')
        transfer_encoding = self.headers.get('transfer-encoding')
        
        if content_length:
            # Explicit Content-Length header
            try:
                body_size = int(content_length)
                has_body = body_size > 0
            except ValueError:
                has_body = False
        elif transfer_encoding and 'chunked' in transfer_encoding.lower():
            # Chunked encoding - body is present
            has_body = True
        elif self.method.upper() in ('POST', 'PUT', 'PATCH'):
            # For these methods, body might be present even without headers
            # We'll try to read, but won't block forever
            has_body = True
        
        if has_body:
            # Stream body from client to upstream
            # Read in chunks and immediately write to upstream (streaming, not buffering)
            # This is important for large requests and backpressure handling
            chunk_size = 8192  # 8KB chunks - good balance between efficiency and responsiveness
            
            if content_length:
                # We know exact size - read exactly that much
                remaining = int(content_length)
                while remaining > 0:
                    read_size = min(chunk_size, remaining)
                    chunk = await self.reader.read(read_size)
                    if not chunk:  # EOF before expected size
                        break
                    writer.write(chunk)
                    await writer.drain()
                    remaining -= len(chunk)
            else:
                # Unknown size - read until EOF (but this should be limited)
                # For chunked encoding or unknown size
                while True:
                    chunk = await self.reader.read(chunk_size)
                    if not chunk:  # EOF - no more data
                        break
                    writer.write(chunk)
                    # drain() ensures we don't overwhelm the upstream with data
                    # It waits until the write buffer is ready for more data (backpressure)
                    await writer.drain()
        
        # Ensure all data is flushed to upstream
        await writer.drain()
