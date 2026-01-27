"""Upstream pool with round-robin load balancing."""

import asyncio
from dataclasses import dataclass
from typing import List, Optional
from collections.abc import Iterator


@dataclass
class Upstream:
    """
    Represents a single upstream server.
    
    Attributes:
        host: Upstream server hostname or IP address
        port: Upstream server port
    """
    host: str
    port: int
    
    def __repr__(self) -> str:
        return f"{self.host}:{self.port}"


class UpstreamPool:
    """
    Pool of upstream servers with round-robin load balancing.
    
    Round-robin означает, что запросы распределяются по upstream серверам
    последовательно: первый запрос идет на первый upstream, второй - на второй,
    третий - на третий, четвертый - снова на первый, и так далее по кругу.
    
    Это простой и эффективный способ балансировки нагрузки, который:
    - Равномерно распределяет запросы между всеми upstream
    - Не требует сложной логики выбора
    - Работает хорошо, когда все upstream имеют одинаковую производительность
    
    Пример использования:
        pool = UpstreamPool([
            Upstream('127.0.0.1', 9001),
            Upstream('127.0.0.1', 9002),
        ])
        
        # Получить следующий upstream по round-robin
        upstream = pool.get_next()
        # upstream = Upstream(host='127.0.0.1', port=9001)
        
        upstream = pool.get_next()
        # upstream = Upstream(host='127.0.0.1', port=9002)
        
        upstream = pool.get_next()
        # upstream = Upstream(host='127.0.0.1', port=9001) - снова первый
    """
    
    def __init__(self, upstreams: List[Upstream]):
        """
        Initialize upstream pool.
        
        Args:
            upstreams: List of upstream servers
            
        Raises:
            ValueError: If upstreams list is empty
        """
        if not upstreams:
            raise ValueError("Upstream pool must contain at least one upstream")
        
        self.upstreams = upstreams
        # Индекс текущего upstream для round-robin
        # Используем asyncio.Lock для thread-safety в многопоточной среде
        self._current_index = 0
        self._lock = asyncio.Lock()
    
    async def get_next(self) -> Upstream:
        """
        Get next upstream server using round-robin algorithm.
        
        Round-robin работает так:
        1. Берем текущий индекс
        2. Возвращаем upstream по этому индексу
        3. Увеличиваем индекс на 1
        4. Если индекс >= количество upstream, сбрасываем на 0 (циклический переход)
        
        Это гарантирует, что запросы распределяются равномерно по всем upstream.
        
        Returns:
            Next upstream server in round-robin order
        """
        # Используем lock для thread-safety в asyncio
        # В asyncio несколько корутин могут вызывать get_next() одновременно
        # Lock гарантирует атомарность операции чтения и изменения индекса
        async with self._lock:
            upstream = self.upstreams[self._current_index]
            # Переходим к следующему upstream (циклически)
            self._current_index = (self._current_index + 1) % len(self.upstreams)
            return upstream
    
    def __len__(self) -> int:
        """Return number of upstream servers in pool."""
        return len(self.upstreams)
    
    def __repr__(self) -> str:
        return f"UpstreamPool({len(self.upstreams)} upstreams: {self.upstreams})"


# Default upstream pool (can be overridden via configuration)
# For now, we'll use a single upstream, but pool supports multiple
DEFAULT_UPSTREAM_POOL = UpstreamPool([
    Upstream(host='127.0.0.1', port=9001),
])
