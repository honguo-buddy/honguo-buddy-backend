"""MetricsService 单元测试。"""
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.asyncio


class FakeRedisForMetrics:
    """专门为计数器中心打造的轻量级 FakeRedis。"""
    def __init__(self):
        self._hashes = {}
        self._pipeline_calls = []

    async def hincrby(self, key, field, amount=1):
        bucket = self._hashes.setdefault(key, {})
        current = int(bucket.get(field, 0))
        current += int(amount)
        bucket[field] = str(current)
        return current

    async def hgetall(self, key):
        return self._hashes.get(key, {}) or None

    def pipeline(self):
        """返回一个 Pipe 对象，收集命令并在 execute 时批量执行。"""
        pipe = FakeRedisForMetrics._Pipe(self)
        self._pipeline_calls.append(pipe)
        return pipe

    class _Pipe:
        def __init__(self, parent):
            self._parent = parent
            self._commands = []

        def hgetall(self, key):
            self._commands.append(("hgetall", key))
            return self

        async def execute(self):
            results = []
            for cmd, key in self._commands:
                if cmd == "hgetall":
                    val = self._parent._hashes.get(key, {})
                    results.append(val if val else None)
            return results


class TestMetricsService:
    async def test_incr_post_view_adds_to_redis(self):
        from app.services.metrics_service import MetricsService

        redis_fake = FakeRedisForMetrics()
        await MetricsService.incr_post_view(redis_fake, 1001)
        bucket = await redis_fake.hgetall("metrics:post:1001")
        assert bucket is not None
        assert bucket.get("view") == "1"

    async def test_incr_post_favorite_positive(self):
        from app.services.metrics_service import MetricsService

        redis_fake = FakeRedisForMetrics()
        await MetricsService.incr_post_favorite(redis_fake, 1001, delta=1)
        bucket = await redis_fake.hgetall("metrics:post:1001")
        assert bucket["favorite"] == "1"

    async def test_incr_post_favorite_negative(self):
        from app.services.metrics_service import MetricsService

        redis_fake = FakeRedisForMetrics()
        await MetricsService.incr_post_favorite(redis_fake, 1001, delta=-1)
        bucket = await redis_fake.hgetall("metrics:post:1001")
        assert bucket["favorite"] == "-1"

    async def test_hydrate_posts_with_metrics(self):
        from app.services.metrics_service import MetricsService

        redis_fake = FakeRedisForMetrics()
        # Pre-populate Redis with metrics
        redis_fake._hashes["metrics:post:1001"] = {"view": "42", "favorite": "7", "comment": "3"}
        redis_fake._hashes["metrics:post:1002"] = {"view": "10", "favorite": "2", "comment": "0"}

        items = [
            {"post_id": 1001, "title": "Post 1"},
            {"post_id": 1002, "title": "Post 2"},
        ]
        await MetricsService.hydrate_posts_with_metrics(redis_fake, items, [1001, 1002])

        assert items[0]["view_count"] == 42
        assert items[0]["favorite_count"] == 7
        assert items[0]["comment_count"] == 3
        assert items[1]["view_count"] == 10

    async def test_hydrate_empty_lists(self):
        from app.services.metrics_service import MetricsService

        redis_fake = FakeRedisForMetrics()
        items = []
        await MetricsService.hydrate_posts_with_metrics(redis_fake, items, [])
        # Should complete without error
        assert items == []

    async def test_hydrate_missing_metrics_defaults_to_zero(self):
        from app.services.metrics_service import MetricsService

        redis_fake = FakeRedisForMetrics()
        items = [{"post_id": 9999, "title": "No metrics"}]
        await MetricsService.hydrate_posts_with_metrics(redis_fake, items, [9999])

        assert items[0]["view_count"] == 0
        assert items[0]["favorite_count"] == 0
        assert items[0]["comment_count"] == 0
