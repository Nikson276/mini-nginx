# Новый файл: proxy/connection_pool.py
import asyncio
import aiohttp
from typing import Dict, Optional
from dataclasses import dataclass
from proxy.upstream_pool import Upstream
from proxy.logger import get_logger

logger = get_logger()

@dataclass
class ConnectionPoolConfig:
    """Configuration for connection pool."""
    max_size: int = 50                    # Max connections per upstream
    max_connections_per_host: int = 25    # Max connections per upstream host
    idle_timeout: float = 15.0            # Close idle connections after 15s
    connect_timeout: float = 2.0          # Connection timeout
    read_timeout: float = 10.0            # Read timeout


class ConnectionPool:
    """HTTP/1.1 Keep-alive connection pool for upstream servers."""
    
    def __init__(self, config: Optional[ConnectionPoolConfig] = None):
        self.config = config or ConnectionPoolConfig()
        self._pools: Dict[str, aiohttp.ClientSession] = {}
        self._lock = asyncio.Lock()
        
    async def get_session(self, upstream: Upstream) -> aiohttp.ClientSession:
        """Get or create a keep-alive session for an upstream."""
        key = f"{upstream.host}:{upstream.port}"
        
        async with self._lock:
            if key not in self._pools or self._pools[key].closed:
                # Create new session with keep-alive
                connector = aiohttp.TCPConnector(
                    limit=self.config.max_size,
                    limit_per_host=self.config.max_connections_per_host,
                    force_close=False,  # КРИТИЧЕСКОЕ: enable keep-alive
                    enable_cleanup_closed=True,
                    ttl_dns_cache=300,
                )
                
                timeout = aiohttp.ClientTimeout(
                    total=self.config.connect_timeout + self.config.read_timeout,
                    connect=self.config.connect_timeout,
                    sock_read=self.config.read_timeout,
                )
                
                self._pools[key] = aiohttp.ClientSession(
                    connector=connector,
                    timeout=timeout,
                    headers={
                        "User-Agent": "Mini-Nginx-Proxy/1.0",
                        "Accept": "*/*",
                    }
                )
                
                await logger.debug(f"Created new keep-alive session for {key}")
                
            else:
                await logger.debug(
                    f"Reusing keep-alive session for {key}, \n"
                    f"active sessions in pool: {len(self._pools)}"
                )

            return self._pools[key]
    
    async def close_all(self):
        """Close all sessions (call on shutdown)."""
        async with self._lock:
            for key, session in self._pools.items():
                if not session.closed:
                    await session.close()
                    await logger.debug(f"Closed session for {key}")
            self._pools.clear()
