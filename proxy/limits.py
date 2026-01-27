"""Connection limits using asyncio.Semaphore."""

import asyncio
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class ConnectionLimits:
    """
    Connection limits configuration.
    
    Attributes:
        max_client_conns: Maximum number of simultaneous client connections
        max_conns_per_upstream: Maximum number of connections per upstream server
    """
    max_client_conns: int = 1000
    max_conns_per_upstream: int = 100


class ConnectionLimitManager:
    """
    Manages connection limits using asyncio.Semaphore.
    
    Что такое Semaphore?
    ====================
    
    Semaphore (семафор) - это примитив синхронизации, который контролирует доступ
    к ограниченному ресурсу. Представьте его как счетчик разрешений:
    
    - Semaphore(5) означает, что одновременно может быть 5 "разрешений"
    - Когда корутина хочет использовать ресурс, она вызывает await semaphore.acquire()
    - Если есть свободное разрешение - корутина продолжает выполнение
    - Если разрешений нет - корутина ждет, пока кто-то освободит разрешение
    - Когда корутина закончила работу, она вызывает semaphore.release()
    
    Пример:
    -------
    
    ```python
    # Создаем семафор с 3 разрешениями
    sem = asyncio.Semaphore(3)
    
    async def worker(name):
        async with sem:  # acquire() при входе, release() при выходе
            print(f"{name} работает")
            await asyncio.sleep(1)
    
    # Запускаем 5 задач одновременно
    await asyncio.gather(
        worker("A"), worker("B"), worker("C"), worker("D"), worker("E")
    )
    
    # Результат:
    # A, B, C начинают работать одновременно (3 разрешения)
    # D и E ждут
    # Когда A закончит, D начнет работать
    # Когда B закончит, E начнет работать
    ```
    
    Зачем это нужно для прокси?
    ============================
    
    1. **Защита от перегрузки**: Если к прокси подключится 10000 клиентов одновременно,
       это может перегрузить сервер. Semaphore ограничивает количество одновременных
       соединений.
    
    2. **Защита upstream**: Если прокси откроет 1000 соединений к одному upstream,
       это может перегрузить его. Semaphore ограничивает количество соединений
       к каждому upstream.
    
    3. **Контроль ресурсов**: Semaphore помогает контролировать использование памяти
       и сетевых ресурсов.
    
    Как это работает в нашем коде:
    ===============================
    
    ```python
    # Создаем менеджер лимитов
    limits = ConnectionLimitManager(
        max_client_conns=100,      # Максимум 100 клиентских соединений
        max_conns_per_upstream=10   # Максимум 10 соединений к каждому upstream
    )
    
    # В обработчике клиента:
    async with limits.client_connection():  # Ждем свободного места для клиента
        # Обрабатываем запрос клиента
        await handle_client()
    
    # При подключении к upstream:
    async with limits.upstream_connection(upstream):  # Ждем свободного места для upstream
        # Подключаемся к upstream
        await connect_to_upstream()
    ```
    
    Если лимит достигнут:
    - Корутина будет ждать (await), пока не освободится место
    - Это не блокирует event loop - другие корутины продолжают работать
    - Как только место освободится, корутина продолжит выполнение
    """
    
    def __init__(self, limits: ConnectionLimits):
        """
        Initialize connection limit manager.
        
        Args:
            limits: Connection limits configuration
        """
        self.limits = limits
        
        # Semaphore для ограничения количества одновременных клиентских соединений
        # Если достигнут лимит, новые клиенты будут ждать
        self._client_semaphore = asyncio.Semaphore(limits.max_client_conns)
        
        # Словарь семафоров для каждого upstream
        # Ключ: (host, port) tuple, значение: Semaphore
        # Это позволяет ограничить количество соединений к каждому upstream отдельно
        self._upstream_semaphores: Dict[tuple, asyncio.Semaphore] = {}
        self._upstream_lock = asyncio.Lock()  # Для thread-safety при создании семафоров
    
    def client_connection(self):
        """
        Get semaphore for client connection limit.
        
        Использование:
            async with limits.client_connection():
                # Обработка клиентского соединения
                await handle_client()
        
        Если достигнут лимит max_client_conns, корутина будет ждать,
        пока не освободится место.
        
        Returns:
            Semaphore that can be used as async context manager
        """
        return self._client_semaphore
    
    async def upstream_connection(self, upstream):
        """
        Get semaphore for upstream connection limit.
        
        Использование:
            upstream_sem = await limits.upstream_connection(upstream)
            async with upstream_sem:
                # Подключение к upstream
                await connect_to_upstream()
        
        Если достигнут лимит max_conns_per_upstream для данного upstream,
        корутина будет ждать, пока не освободится место.
        
        Args:
            upstream: Upstream object (from upstream_pool.Upstream)
        
        Returns:
            Semaphore that can be used as async context manager
        """
        # Создаем ключ для upstream (host, port)
        upstream_key = (upstream.host, upstream.port)
        
        # Получаем или создаем семафор для этого upstream
        async with self._upstream_lock:
            if upstream_key not in self._upstream_semaphores:
                # Создаем новый семафор для этого upstream
                self._upstream_semaphores[upstream_key] = asyncio.Semaphore(
                    self.limits.max_conns_per_upstream
                )
        
        return self._upstream_semaphores[upstream_key]
    
    def get_stats(self) -> dict:
        """
        Get current connection statistics.
        
        Returns:
            Dictionary with current connection counts
        """
        return {
            'client_connections_available': self._client_semaphore._value,
            'client_connections_limit': self.limits.max_client_conns,
            'upstream_semaphores': {
                str(key): {
                    'available': sem._value,
                    'limit': self.limits.max_conns_per_upstream,
                }
                for key, sem in self._upstream_semaphores.items()
            }
        }


# Default connection limits (can be overridden via configuration)
DEFAULT_LIMITS = ConnectionLimits(
    max_client_conns=1000,
    max_conns_per_upstream=100,
)
