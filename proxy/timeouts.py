"""Timeout policy for proxy operations."""

import asyncio
from typing import Optional
from dataclasses import dataclass


@dataclass
class TimeoutPolicy:
    """
    Configurable timeout policy for proxy operations.
    
    All timeouts are in milliseconds. Use asyncio.wait_for() to apply them.
    
    Attributes:
        connect_ms: Timeout for connecting to upstream (default: 1000ms = 1s)
        read_ms: Timeout for reading data from upstream/client (default: 15000ms = 15s)
        write_ms: Timeout for writing data to upstream/client (default: 15000ms = 15s)
        total_ms: Total timeout for entire request processing (default: 30000ms = 30s)
    """
    
    connect_ms: int = 1000      # 1 second - connection should be fast
    read_ms: int = 15000         # 15 seconds - reasonable for reading response
    write_ms: int = 15000        # 15 seconds - reasonable for writing request
    total_ms: int = 30000        # 30 seconds - total request time limit
    
    def connect_timeout(self) -> float:
        """Return connect timeout in seconds for asyncio.wait_for()."""
        return self.connect_ms / 1000.0
    
    def read_timeout(self) -> float:
        """Return read timeout in seconds for asyncio.wait_for()."""
        return self.read_ms / 1000.0
    
    def write_timeout(self) -> float:
        """Return write timeout in seconds for asyncio.wait_for()."""
        return self.write_ms / 1000.0
    
    def total_timeout(self) -> float:
        """Return total timeout in seconds for asyncio.wait_for()."""
        return self.total_ms / 1000.0
    
    async def with_connect_timeout(self, coro):
        """
        Wrap a coroutine with connect timeout.
        
        Как это работает:
        1. Принимает корутину (coro) - объект, представляющий асинхронную операцию
        2. asyncio.wait_for() запускает выполнение корутины
        3. Если выполнение занимает больше connect_timeout() секунд - 
           wait_for отменяет корутину и выбрасывает TimeoutError
        4. Если выполнение завершилось вовремя - возвращает результат
        
        Пример использования:
            # asyncio.open_connection() возвращает корутину (еще не выполненную!)
            coro = asyncio.open_connection('127.0.0.1', 9001)
            
            # Передаем корутину в with_connect_timeout
            # wait_for запустит выполнение и будет ждать максимум 1 секунду
            reader, writer = await self.with_connect_timeout(coro)
        
        Args:
            coro: Coroutine to execute (typically asyncio.open_connection)
                  ВАЖНО: передается корутина, а не результат await!
            
        Returns:
            Result of the coroutine (например, (reader, writer) для open_connection)
            
        Raises:
            asyncio.TimeoutError: If connection takes longer than connect_ms
        """
        # wait_for принимает корутину и timeout в секундах
        # Внутри wait_for:
        # 1. Создает задачу (Task) из корутины
        # 2. Ждет завершения задачи с таймаутом
        # 3. Если таймаут - отменяет задачу и выбрасывает TimeoutError
        # 4. Если успешно - возвращает результат корутины
        return await asyncio.wait_for(coro, timeout=self.connect_timeout())
    
    async def with_read_timeout(self, coro):
        """
        Wrap a coroutine with read timeout.
        
        Args:
            coro: Coroutine to execute (typically reader.read())
            
        Returns:
            Result of the coroutine
            
        Raises:
            asyncio.TimeoutError: If read takes longer than read_ms
        """
        return await asyncio.wait_for(coro, timeout=self.read_timeout())
    
    async def with_write_timeout(self, coro):
        """
        Wrap a coroutine with write timeout.
        
        Args:
            coro: Coroutine to execute (typically writer.write() + drain())
            
        Returns:
            Result of the coroutine
            
        Raises:
            asyncio.TimeoutError: If write takes longer than write_ms
        """
        return await asyncio.wait_for(coro, timeout=self.write_timeout())
    
    async def with_total_timeout(self, coro):
        """
        Wrap a coroutine with total timeout.
        
        This should wrap the entire request processing to ensure
        no request takes longer than total_ms.
        
        Args:
            coro: Coroutine to execute (entire request processing)
            
        Returns:
            Result of the coroutine
            
        Raises:
            asyncio.TimeoutError: If total processing takes longer than total_ms
        """
        return await asyncio.wait_for(coro, timeout=self.total_timeout())


# Default timeout policy (can be overridden via configuration)
DEFAULT_TIMEOUT_POLICY = TimeoutPolicy()
