"""Unit tests for TimeoutPolicy."""

import asyncio
import pytest
from proxy.timeouts import TimeoutPolicy, DEFAULT_TIMEOUT_POLICY


class TestTimeoutPolicy:
    """Tests for TimeoutPolicy class."""
    
    def test_default_timeout_values(self):
        """Test that default timeout values are correct."""
        policy = DEFAULT_TIMEOUT_POLICY
        assert policy.connect_ms == 1000
        assert policy.read_ms == 15000
        assert policy.write_ms == 15000
        assert policy.total_ms == 30000
    
    def test_timeout_conversion_to_seconds(self):
        """Test conversion from milliseconds to seconds."""
        policy = TimeoutPolicy(
            connect_ms=2000,
            read_ms=30000,
            write_ms=45000,
            total_ms=60000
        )
        assert policy.connect_timeout() == 2.0
        assert policy.read_timeout() == 30.0
        assert policy.write_timeout() == 45.0
        assert policy.total_timeout() == 60.0
    
    @pytest.mark.asyncio
    async def test_connect_timeout_success(self):
        """Test that connect timeout allows fast operations."""
        policy = TimeoutPolicy(connect_ms=1000)
        
        # Быстрая операция должна завершиться успешно
        async def fast_operation():
            await asyncio.sleep(0.1)
            return "success"
        
        result = await policy.with_connect_timeout(fast_operation())
        assert result == "success"
    
    @pytest.mark.asyncio
    async def test_connect_timeout_failure(self):
        """Test that connect timeout raises TimeoutError for slow operations."""
        policy = TimeoutPolicy(connect_ms=100)  # Очень короткий таймаут
        
        # Медленная операция должна вызвать TimeoutError
        async def slow_operation():
            await asyncio.sleep(1.0)  # Больше чем таймаут
            return "success"
        
        with pytest.raises(asyncio.TimeoutError):
            await policy.with_connect_timeout(slow_operation())
    
    @pytest.mark.asyncio
    async def test_read_timeout(self):
        """Test read timeout functionality."""
        policy = TimeoutPolicy(read_ms=200)
        
        async def fast_read():
            await asyncio.sleep(0.1)
            return b"data"
        
        result = await policy.with_read_timeout(fast_read())
        assert result == b"data"
        
        async def slow_read():
            await asyncio.sleep(0.5)
            return b"data"
        
        with pytest.raises(asyncio.TimeoutError):
            await policy.with_read_timeout(slow_read())
    
    @pytest.mark.asyncio
    async def test_write_timeout(self):
        """Test write timeout functionality."""
        policy = TimeoutPolicy(write_ms=200)
        
        async def fast_write():
            await asyncio.sleep(0.1)
            return "written"
        
        result = await policy.with_write_timeout(fast_write())
        assert result == "written"
        
        async def slow_write():
            await asyncio.sleep(0.5)
            return "written"
        
        with pytest.raises(asyncio.TimeoutError):
            await policy.with_write_timeout(slow_write())
    
    @pytest.mark.asyncio
    async def test_total_timeout(self):
        """Test total timeout wraps entire operation."""
        policy = TimeoutPolicy(total_ms=200)
        
        async def fast_total():
            await asyncio.sleep(0.1)
            return "done"
        
        result = await policy.with_total_timeout(fast_total())
        assert result == "done"
        
        async def slow_total():
            await asyncio.sleep(0.5)
            return "done"
        
        with pytest.raises(asyncio.TimeoutError):
            await policy.with_total_timeout(slow_total())
