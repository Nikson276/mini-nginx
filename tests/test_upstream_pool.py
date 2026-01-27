"""Unit tests for UpstreamPool round-robin load balancing."""

import asyncio
import pytest
from proxy.upstream_pool import UpstreamPool, Upstream


class TestUpstreamPool:
    """Tests for UpstreamPool round-robin functionality."""
    
    def test_pool_creation(self):
        """Test creating upstream pool."""
        upstreams = [
            Upstream(host='127.0.0.1', port=9001),
            Upstream(host='127.0.0.1', port=9002),
        ]
        pool = UpstreamPool(upstreams)
        assert len(pool) == 2
    
    def test_pool_creation_empty_list_raises_error(self):
        """Test that empty upstream list raises ValueError."""
        with pytest.raises(ValueError, match="must contain at least one upstream"):
            UpstreamPool([])
    
    @pytest.mark.asyncio
    async def test_round_robin_single_upstream(self):
        """Test round-robin with single upstream always returns same."""
        pool = UpstreamPool([Upstream(host='127.0.0.1', port=9001)])
        
        upstream1 = await pool.get_next()
        upstream2 = await pool.get_next()
        upstream3 = await pool.get_next()
        
        assert upstream1.host == '127.0.0.1'
        assert upstream1.port == 9001
        assert upstream1 == upstream2 == upstream3
    
    @pytest.mark.asyncio
    async def test_round_robin_two_upstreams(self):
        """Test round-robin alternates between two upstreams."""
        pool = UpstreamPool([
            Upstream(host='127.0.0.1', port=9001),
            Upstream(host='127.0.0.1', port=9002),
        ])
        
        # Первый запрос → первый upstream
        upstream1 = await pool.get_next()
        assert upstream1.port == 9001
        
        # Второй запрос → второй upstream
        upstream2 = await pool.get_next()
        assert upstream2.port == 9002
        
        # Третий запрос → снова первый upstream (циклический переход)
        upstream3 = await pool.get_next()
        assert upstream3.port == 9001
        
        # Четвертый запрос → снова второй upstream
        upstream4 = await pool.get_next()
        assert upstream4.port == 9002
    
    @pytest.mark.asyncio
    async def test_round_robin_three_upstreams(self):
        """Test round-robin cycles through three upstreams."""
        pool = UpstreamPool([
            Upstream(host='127.0.0.1', port=9001),
            Upstream(host='127.0.0.1', port=9002),
            Upstream(host='127.0.0.1', port=9003),
        ])
        
        # Проверяем цикл: 9001 → 9002 → 9003 → 9001 → ...
        assert (await pool.get_next()).port == 9001
        assert (await pool.get_next()).port == 9002
        assert (await pool.get_next()).port == 9003
        assert (await pool.get_next()).port == 9001
        assert (await pool.get_next()).port == 9002
        assert (await pool.get_next()).port == 9003
    
    @pytest.mark.asyncio
    async def test_round_robin_concurrent_requests(self):
        """Test round-robin works correctly with concurrent requests."""
        pool = UpstreamPool([
            Upstream(host='127.0.0.1', port=9001),
            Upstream(host='127.0.0.1', port=9002),
        ])
        
        # Делаем несколько параллельных запросов
        results = await asyncio.gather(
            pool.get_next(),
            pool.get_next(),
            pool.get_next(),
            pool.get_next(),
        )
        
        ports = [upstream.port for upstream in results]
        
        # Должно быть равномерное распределение между двумя upstream
        assert ports.count(9001) == 2
        assert ports.count(9002) == 2
    
    def test_pool_repr(self):
        """Test string representation of pool."""
        pool = UpstreamPool([
            Upstream(host='127.0.0.1', port=9001),
            Upstream(host='127.0.0.1', port=9002),
        ])
        
        repr_str = repr(pool)
        assert 'UpstreamPool' in repr_str
        assert '2 upstreams' in repr_str
