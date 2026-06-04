"""单元测试 redis mock fixture."""
import pytest
from unittest.mock import AsyncMock, MagicMock


class FakeRedisUnit:
    """单元测试用最小 Redis mock — 所有方法都是 AsyncMock。"""
    def __init__(self):
        self.hincrby = AsyncMock(return_value=1)
        self.hgetall = AsyncMock(return_value={})
        self.hset = AsyncMock(return_value=1)
        self.sadd = AsyncMock(return_value=1)
        self.smembers = AsyncMock(return_value=set())
        self.srem = AsyncMock(return_value=1)
        self.set = AsyncMock(return_value=True)
        self.get = AsyncMock(return_value=None)
        self.delete = AsyncMock(return_value=1)
        self.zadd = AsyncMock(return_value=1)
        self.zrem = AsyncMock(return_value=1)
        self.zremrangebyscore = AsyncMock(return_value=0)
        self.zrevrange = AsyncMock(return_value=[])
        self.eval = AsyncMock(return_value=1)

    def pipeline(self):
        pipe = MagicMock()
        pipe.hgetall = MagicMock(return_value=pipe)
        pipe.hincrby = MagicMock(return_value=pipe)
        pipe.hset = MagicMock(return_value=pipe)
        pipe.delete = MagicMock(return_value=pipe)
        pipe.sadd = MagicMock(return_value=pipe)
        pipe.srem = MagicMock(return_value=pipe)
        pipe.execute = AsyncMock(return_value=[])
        return pipe


@pytest.fixture(autouse=True)
def patch_redis_for_unit_tests(monkeypatch):
    """所有单元测试自动 mock 掉 Redis 连接，防止挂起。"""
    fake = FakeRedisUnit()
    monkeypatch.setattr("app.db.redis", fake, raising=False)
    monkeypatch.setattr("app.db.base.redis", fake, raising=False)
    monkeypatch.setattr("app.services.comment_service.app_redis", fake, raising=False)
    monkeypatch.setattr("app.services.social_service.redis", fake, raising=False)
    monkeypatch.setattr("app.services.metrics_service.redis", fake, raising=False)
