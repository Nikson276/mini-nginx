import asyncio
import time
from typing import Optional, Callable, Any
from dataclasses import dataclass
from enum import Enum
from proxy.logger import get_logger

logger = get_logger()


class CircuitState(Enum):
    CLOSED = "closed"      # Все работает нормально
    OPEN = "open"          # Circuit открыт, быстро отказываем
    HALF_OPEN = "half_open" # Пробуем один запрос


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5        # 5 ошибок → открыть circuit
    recovery_timeout: float = 10.0    # Ждать 10 секунд перед half-open
    half_open_max_requests: int = 1   # Только 1 запрос в half-open состоянии
    timeout: float = 2.0              # Таймаут для каждого запроса


class CircuitBreaker:
    """Circuit breaker для защиты от медленных/недоступных upstream."""
    
    def __init__(self, name: str, config: Optional[CircuitBreakerConfig] = None):
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time: Optional[float] = None
        self.half_open_requests = 0
        self._lock = asyncio.Lock()
    
    async def execute(self, coro: Callable, *args, **kwargs) -> Any:
        """Выполнить операцию с защитой circuit breaker."""
        # Проверяем состояние circuit
        if self.state == CircuitState.OPEN:
            # Проверяем, не пора ли перейти в half-open
            if (self.last_failure_time and 
                time.time() - self.last_failure_time > self.config.recovery_timeout):
                async with self._lock:
                    if self.state == CircuitState.OPEN:
                        self.state = CircuitState.HALF_OPEN
                        self.half_open_requests = 0
                        await logger.info(f"Circuit {self.name} переход в HALF_OPEN")
            else:
                raise CircuitOpenError(f"Circuit {self.name} is OPEN")
        
        elif self.state == CircuitState.HALF_OPEN:
            async with self._lock:
                if self.half_open_requests >= self.config.half_open_max_requests:
                    raise CircuitOpenError(f"Circuit {self.name} is HALF_OPEN, max requests reached")
                self.half_open_requests += 1
        
        # Выполняем запрос с таймаутом
        try:
            result = await asyncio.wait_for(coro(*args, **kwargs), timeout=self.config.timeout)
            
            # Успех - сбрасываем счетчики
            async with self._lock:
                if self.state == CircuitState.HALF_OPEN:
                    # Успешный запрос в half-open -> закрываем circuit
                    self.state = CircuitState.CLOSED
                    self.failure_count = 0
                    self.half_open_requests = 0
                    await logger.info(f"Circuit {self.name} переход в CLOSED (успешный запрос)")
                else:
                    self.failure_count = 0
            
            return result
            
        except asyncio.TimeoutError:
            await self._record_failure(f"Timeout after {self.config.timeout}s")
            raise
        except Exception as e:
            await self._record_failure(str(e))
            raise
    
    async def _record_failure(self, error_msg: str):
        """Записать ошибку и обновить состояние circuit."""
        async with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.time()
            
            await logger.warning(
                f"Circuit {self.name} ошибка: {error_msg} "
                f"(failures: {self.failure_count}/{self.config.failure_threshold})"
            )
            
            if self.state == CircuitState.HALF_OPEN:
                # Ошибка в half-open -> снова открываем
                self.state = CircuitState.OPEN
                self.half_open_requests = 0
                await logger.error(f"Circuit {self.name} переход в OPEN (ошибка в half-open)")
            
            elif (self.state == CircuitState.CLOSED and 
                  self.failure_count >= self.config.failure_threshold):
                # Достигли порога ошибок -> открываем circuit
                self.state = CircuitState.OPEN
                await logger.error(f"Circuit {self.name} переход в OPEN (превышен порог ошибок)")


class CircuitBreakerManager:
    """Менеджер circuit breakers для всех upstream."""
    
    def __init__(self):
        self._breakers: Dict[str, CircuitBreaker] = {}
        self._lock = asyncio.Lock()
    
    def get_breaker(self, upstream_host: str, upstream_port: int) -> CircuitBreaker:
        """Получить или создать circuit breaker для upstream."""
        key = f"{upstream_host}:{upstream_port}"
        
        if key not in self._breakers:
            self._breakers[key] = CircuitBreaker(name=key)
        
        return self._breakers[key]


class CircuitOpenError(Exception):
    """Исключение когда circuit breaker открыт."""
    pass
