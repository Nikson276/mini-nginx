"""Unit tests for ConnectionLimitManager and Semaphore limits."""

import asyncio
import pytest
from proxy.limits import ConnectionLimitManager, ConnectionLimits
from proxy.upstream_pool import Upstream


class TestConnectionLimits:
    """Tests for ConnectionLimits configuration."""
    
    def test_default_limits(self):
        """Test default limit values."""
        limits = ConnectionLimits()
        assert limits.max_client_conns == 1000
        assert limits.max_conns_per_upstream == 100
    
    def test_custom_limits(self):
        """Test custom limit values."""
        limits = ConnectionLimits(
            max_client_conns=500,
            max_conns_per_upstream=50
        )
        assert limits.max_client_conns == 500
        assert limits.max_conns_per_upstream == 50


class TestConnectionLimitManager:
    """Tests for ConnectionLimitManager Semaphore functionality."""
    
    def test_manager_creation(self):
        """Test creating connection limit manager."""
        limits = ConnectionLimits(
            max_client_conns=10,
            max_conns_per_upstream=5
        )
        manager = ConnectionLimitManager(limits)
        assert manager.limits == limits
    
    def test_client_semaphore_creation(self):
        """Test that client semaphore is created with correct limit."""
        limits = ConnectionLimits(max_client_conns=100)
        manager = ConnectionLimitManager(limits)
        
        # Проверяем, что семафор создан с правильным лимитом
        stats = manager.get_stats()
        assert stats['client_connections_limit'] == 100
        assert stats['client_connections_available'] == 100
    
    @pytest.mark.asyncio
    async def test_client_connection_limit(self):
        """Test that client connection limit works correctly."""
        limits = ConnectionLimits(max_client_conns=2)  # Только 2 разрешения
        manager = ConnectionLimitManager(limits)
        
        # Первые два соединения должны пройти сразу
        async with manager.client_connection():
            async with manager.client_connection():
                # Третье соединение должно ждать
                # Создаем задачу, которая попытается получить третье соединение
                third_connection_started = asyncio.Event()
                
                async def third_connection():
                    third_connection_started.set()
                    async with manager.client_connection():
                        await asyncio.sleep(0.1)
                
                # Запускаем третье соединение
                task = asyncio.create_task(third_connection())
                
                # Ждем, пока оно начнет ждать
                await third_connection_started.wait()
                await asyncio.sleep(0.01)  # Даем время на acquire()
                
                # Проверяем, что третье соединение ждет (семафор занят)
                stats = manager.get_stats()
                assert stats['client_connections_available'] == 0
                
                # Завершаем задачу
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
    
    @pytest.mark.asyncio
    async def test_upstream_connection_limit(self):
        """Test that upstream connection limit works correctly."""
        limits = ConnectionLimits(max_conns_per_upstream=2)
        manager = ConnectionLimitManager(limits)
        
        upstream1 = Upstream(host='127.0.0.1', port=9001)
        upstream2 = Upstream(host='127.0.0.1', port=9002)
        
        # Получаем семафоры для разных upstream
        sem1 = await manager.upstream_connection(upstream1)
        sem2 = await manager.upstream_connection(upstream2)
        
        # Семафоры должны быть разными для разных upstream
        assert sem1 is not sem2
        
        # Но одинаковые для одного и того же upstream
        sem1_again = await manager.upstream_connection(upstream1)
        assert sem1 is sem1_again
    
    @pytest.mark.asyncio
    async def test_upstream_semaphore_limit_enforcement(self):
        """Test that upstream semaphore enforces connection limit."""
        limits = ConnectionLimits(max_conns_per_upstream=2)
        manager = ConnectionLimitManager(limits)
        
        upstream = Upstream(host='127.0.0.1', port=9001)
        
        # Первые два соединения должны пройти
        sem = await manager.upstream_connection(upstream)
        
        async with sem:
            async with sem:
                # Третье соединение должно ждать
                third_started = asyncio.Event()
                
                async def third_connection():
                    third_started.set()
                    async with sem:
                        await asyncio.sleep(0.1)
                
                task = asyncio.create_task(third_connection())
                await third_started.wait()
                await asyncio.sleep(0.01)
                
                # Проверяем статистику
                stats = manager.get_stats()
                upstream_stats = stats['upstream_semaphores'][str((upstream.host, upstream.port))]
                assert upstream_stats['available'] == 0
                
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
    
    @pytest.mark.asyncio
    async def test_semaphore_release_on_exit(self):
        """Test that semaphore is released when exiting context manager."""
        limits = ConnectionLimits(max_client_conns=1)
        manager = ConnectionLimitManager(limits)
        
        # Используем соединение и выходим
        async with manager.client_connection():
            stats = manager.get_stats()
            assert stats['client_connections_available'] == 0
        
        # После выхода соединение должно быть освобождено
        stats = manager.get_stats()
        assert stats['client_connections_available'] == 1
    
    def test_get_stats(self):
        """Test getting connection statistics."""
        limits = ConnectionLimits(
            max_client_conns=100,
            max_conns_per_upstream=50
        )
        manager = ConnectionLimitManager(limits)
        
        stats = manager.get_stats()
        assert 'client_connections_available' in stats
        assert 'client_connections_limit' in stats
        assert 'upstream_semaphores' in stats
        assert stats['client_connections_limit'] == 100
