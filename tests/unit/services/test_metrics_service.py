"""MetricsService 单元测试。"""
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.asyncio


class FakeRedisForMetrics:
    """专门为计数器中心打造的轻量级 FakeRedis。"""
    def __init__(self):
        self._hashes = {}
        self._sets = {}
        self._pipeline_calls = []

    async def hincrby(self, key, field, amount=1):
        bucket = self._hashes.setdefault(key, {})
        current = int(bucket.get(field, 0))
        current += int(amount)
        bucket[field] = str(current)
        return current

    async def hgetall(self, key):
        return self._hashes.get(key, {}) or None

    async def hset(self, key, field=None, value=None, mapping=None):
        if mapping:
            bucket = self._hashes.setdefault(key, {})
            for k, v in mapping.items():
                bucket[k] = str(v)
            return len(mapping)
        bucket = self._hashes.setdefault(key, {})
        bucket[field] = str(value)
        return 1

    async def sadd(self, key, *values):
        s = self._sets.setdefault(key, set())
        added = 0
        for v in values:
            if v not in s:
                s.add(v)
                added += 1
        return added

    async def smembers(self, key):
        return list(self._sets.get(key, set()))

    async def srem(self, key, *values):
        s = self._sets.get(key, set())
        removed = 0
        for v in values:
            if v in s:
                s.remove(v)
                removed += 1
        return removed

    async def set(self, key, value, nx=False, ex=None):
        """Redis SET 模拟（分布式锁用）。"""
        if nx and key in self._hashes:
            return None
        self._hashes[key] = value
        return True

    async def get(self, key):
        """Redis GET 模拟。"""
        return self._hashes.get(key)

    async def delete(self, key):
        """Redis DEL 模拟。"""
        return 1 if self._hashes.pop(key, None) is not None else 0

    async def eval(self, script, numkeys, *args):
        """Redis EVAL 模拟（Lua 释放锁脚本）。"""
        # Simulate: if redis.call("GET", KEYS[1]) == ARGV[1] then DEL
        if len(args) >= 2:
            lock_key = args[0]
            token = args[1]
            stored = self._hashes.get(lock_key)
            if stored == token:
                self._hashes.pop(lock_key, None)
                return 1
        return 0

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

        def hincrby(self, key, field, amount=1):
            self._commands.append(("hincrby", key, field, amount))
            return self

        def hset(self, key, field=None, value=None, mapping=None):
            self._commands.append(("hset", key, field, value, mapping))
            return self

        def delete(self, key):
            self._commands.append(("delete", key))
            return self

        async def execute(self):
            results = []
            i = 0
            while i < len(self._commands):
                cmd = self._commands[i]
                if cmd[0] == "hgetall":
                    key = cmd[1]
                    val = self._parent._hashes.get(key, {})
                    results.append(val if val else None)
                    i += 1
                elif cmd[0] == "hincrby":
                    key, field, amount = cmd[1], cmd[2], cmd[3]
                    bucket = self._parent._hashes.setdefault(key, {})
                    current = int(bucket.get(field, 0)) + int(amount)
                    bucket[field] = str(current)
                    results.append(current)
                    i += 1
                elif cmd[0] == "hset":
                    key, field, value, mapping = cmd[1], cmd[2], cmd[3], cmd[4]
                    bucket = self._parent._hashes.setdefault(key, {})
                    if mapping:
                        for k, v in mapping.items():
                            bucket[k] = str(v)
                    elif field is not None:
                        bucket[field] = str(value)
                    i += 1
                elif cmd[0] == "delete":
                    key = cmd[1]
                    self._parent._hashes.pop(key, None)
                    i += 1
                else:
                    i += 1
            return results


class FakeMetricsQueryResult:
    """指标服务测试专用查询结果对象。"""

    def __init__(self, scalar_items=None, mapping_items=None):
        self._scalar_items = list(scalar_items or [])
        self._mapping_items = list(mapping_items or [])

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._scalar_items))

    def mappings(self):
        return list(self._mapping_items)


class FakeMetricsRoundTripDB:
    """模拟 metrics 查询与刷盘的轻量数据库对象。"""

    def __init__(self, post_rows=None, goods_rows=None):
        self.post_rows = dict(post_rows or {})
        self.goods_rows = dict(goods_rows or {})
        self.committed = False
        self.rolled_back = False

    async def execute(self, stmt, params=None):
        stmt_str = str(stmt) if stmt is not None else ""
        exec_params = params or {}

        if "SELECT post_id, view_count, favorite_count, comment_count" in stmt_str and "FROM post_metrics" in stmt_str:
            requested_post_ids = list(exec_params.get("pids", []))
            rows = []
            for post_id in requested_post_ids:
                row = self.post_rows.get(post_id)
                if row is None:
                    continue
                rows.append(
                    {
                        "post_id": post_id,
                        "view_count": row.get("view_count", 0),
                        "favorite_count": row.get("favorite_count", 0),
                        "comment_count": row.get("comment_count", 0),
                    }
                )
            return FakeMetricsQueryResult(mapping_items=rows)

        if "SELECT goods_id, view_count, favorite_count, comment_count" in stmt_str and "FROM goods_metrics" in stmt_str:
            requested_goods_ids = list(exec_params.get("gids", []))
            rows = []
            for goods_id in requested_goods_ids:
                row = self.goods_rows.get(goods_id)
                if row is None:
                    continue
                rows.append(
                    {
                        "goods_id": goods_id,
                        "view_count": row.get("view_count", 0),
                        "favorite_count": row.get("favorite_count", 0),
                        "comment_count": row.get("comment_count", 0),
                    }
                )
            return FakeMetricsQueryResult(mapping_items=rows)

        if "FROM post" in stmt_str and "post_metrics" not in stmt_str:
            return FakeMetricsQueryResult(scalar_items=list(self.post_rows.keys()))

        if "FROM goods" in stmt_str and "goods_metrics" not in stmt_str:
            return FakeMetricsQueryResult(scalar_items=list(self.goods_rows.keys()))

        if "INSERT INTO post_metrics" in stmt_str:
            for bind_item in list(exec_params or []):
                post_id = bind_item["pid"]
                current_row = self.post_rows.setdefault(
                    post_id,
                    {"view_count": 0, "favorite_count": 0, "comment_count": 0},
                )
                current_row["view_count"] = max(0, current_row["view_count"] + bind_item["view_count"])
                current_row["favorite_count"] = max(0, bind_item["favorite_count"])
                current_row["comment_count"] = max(0, bind_item["comment_count"])
            return FakeMetricsQueryResult()

        if "INSERT INTO goods_metrics" in stmt_str:
            for bind_item in list(exec_params or []):
                goods_id = bind_item["gid"]
                current_row = self.goods_rows.setdefault(
                    goods_id,
                    {"view_count": 0, "favorite_count": 0, "comment_count": 0},
                )
                current_row["view_count"] = max(0, current_row["view_count"] + bind_item["view_count"])
                current_row["favorite_count"] = max(0, bind_item["favorite_count"])
                current_row["comment_count"] = max(0, bind_item["comment_count"])
            return FakeMetricsQueryResult()

        return FakeMetricsQueryResult()

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


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
        # Set initial value to 2, then decrement to 1 (negative guard not triggered)
        await redis_fake.hset("metrics:post:1001", "favorite", 2)
        await MetricsService.incr_post_favorite(redis_fake, 1001, delta=-1)
        bucket = await redis_fake.hgetall("metrics:post:1001")
        assert bucket["favorite"] == "1"

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
        await MetricsService.hydrate_posts_with_metrics(None, redis_fake, items, [1001, 1002])

        assert items[0]["view_count"] == 42
        assert items[0]["favorite_count"] == 7
        assert items[0]["comment_count"] == 3
        assert items[1]["view_count"] == 10

    async def test_hydrate_empty_lists(self):
        from app.services.metrics_service import MetricsService

        redis_fake = FakeRedisForMetrics()
        items = []
        await MetricsService.hydrate_posts_with_metrics(None, redis_fake, items, [])
        # Should complete without error
        assert items == []

    async def test_hydrate_missing_metrics_defaults_to_zero(self):
        from app.services.metrics_service import MetricsService

        redis_fake = FakeRedisForMetrics()
        items = [{"post_id": 9999, "title": "No metrics"}]
        await MetricsService.hydrate_posts_with_metrics(None, redis_fake, items, [9999])

        assert items[0]["view_count"] == 0
        assert items[0]["favorite_count"] == 0
        assert items[0]["comment_count"] == 0
    async def test_incr_post_comment_adds_to_redis(self):
        from app.services.metrics_service import MetricsService

        redis_fake = FakeRedisForMetrics()
        await MetricsService.incr_post_comment(redis_fake, 1001, delta=1)
        bucket = await redis_fake.hgetall("metrics:post:1001")
        assert bucket["comment"] == "1"

    async def test_incr_post_comment_negative(self):
        from app.services.metrics_service import MetricsService

        redis_fake = FakeRedisForMetrics()
        # simulate add then delete
        await MetricsService.incr_post_comment(redis_fake, 1001, delta=1)
        await MetricsService.incr_post_comment(redis_fake, 1001, delta=-1)
        bucket = await redis_fake.hgetall("metrics:post:1001")
        assert bucket["comment"] == "0"

    async def test_incr_goods_view(self):
        from app.services.metrics_service import MetricsService

        redis_fake = FakeRedisForMetrics()
        await MetricsService.incr_goods_view(redis_fake, 2001)
        bucket = await redis_fake.hgetall("metrics:goods:2001")
        assert bucket["view"] == "1"

    async def test_incr_goods_favorite(self):
        from app.services.metrics_service import MetricsService

        redis_fake = FakeRedisForMetrics()
        await MetricsService.incr_goods_favorite(redis_fake, 2001, delta=1)
        await MetricsService.incr_goods_favorite(redis_fake, 2001, delta=-1)
        bucket = await redis_fake.hgetall("metrics:goods:2001")
        assert bucket["favorite"] == "0"

    async def test_hydrate_items_without_post_id_are_skipped(self):
        from app.services.metrics_service import MetricsService

        redis_fake = FakeRedisForMetrics()
        redis_fake._hashes["metrics:post:1001"] = {"view": "5"}
        items = [
            {"post_id": 1001, "title": "Has post_id"},
            {"title": "No post_id"},
            {"post_id": None, "title": "Explicit None"},
        ]
        await MetricsService.hydrate_posts_with_metrics(None, redis_fake, items, [1001])

        assert items[0]["view_count"] == 5
        # Items without post_id should be untouched (no crash)
        assert "view_count" not in items[1]
        assert "view_count" not in items[2]

    async def test_hydrate_none_post_ids_does_not_crash(self):
        from app.services.metrics_service import MetricsService

        redis_fake = FakeRedisForMetrics()
        # post_ids=None should be handled gracefully
        items = [{"post_id": 1001}]
        # Empty list should be fine
        await MetricsService.hydrate_posts_with_metrics(None, redis_fake, items, [])
        assert "view_count" not in items[0]  # empty post_ids -> no hydration

    async def test_hydrate_mixed_keys_partial_match(self):
        from app.services.metrics_service import MetricsService

        redis_fake = FakeRedisForMetrics()
        redis_fake._hashes["metrics:post:1001"] = {"view": "10", "favorite": "2", "comment": "1"}
        # 1002 has no metrics in Redis

        items = [
            {"post_id": 1001, "title": "With metrics"},
            {"post_id": 1002, "title": "Without metrics"},
        ]
        await MetricsService.hydrate_posts_with_metrics(None, redis_fake, items, [1001, 1002])

        assert items[0]["view_count"] == 10
        assert items[0]["favorite_count"] == 2
        assert items[0]["comment_count"] == 1
        assert items[1]["view_count"] == 0
        assert items[1]["favorite_count"] == 0
        assert items[1]["comment_count"] == 0

    async def test_flush_metrics_to_db_empty_pools(self):
        from app.services.metrics_service import MetricsService
        from unittest.mock import AsyncMock

        db = AsyncMock()
        redis_fake = FakeRedisForMetrics()
        await MetricsService.flush_metrics_to_db(db, redis_fake)
        # With empty Redis sets, db.execute should not be called
        db.execute.assert_not_called()
        db.commit.assert_not_called()

    async def test_flush_metrics_to_db_with_data(self, monkeypatch):
        from app.services.metrics_service import MetricsService
        from unittest.mock import AsyncMock
        from tests.unit.fake_sqlalchemy import FakeResult

        redis_fake = FakeRedisForMetrics()
        await redis_fake.sadd("metrics:active_posts_set", 1001)
        redis_fake._hashes["metrics:post:1001"] = {"view": "20", "favorite": "3", "comment": "1", "upvote": "0"}

        db = AsyncMock()
        # First execute = validation query (select existing post_ids), second = metrics insert
        db.execute = AsyncMock(side_effect=[
            FakeResult(items=[1001]),   # validation: post 1001 exists
            FakeResult(),                # metrics INSERT
        ])
        monkeypatch.setattr(
            MetricsService,
            "_get_post_truth_metrics_map",
            AsyncMock(return_value={1001: {"favorite_count": 9, "comment_count": 4}}),
        )
        await MetricsService.flush_metrics_to_db(db, redis_fake)

        assert db.execute.call_count == 2  # validation + INSERT
        db.commit.assert_called_once()
        insert_params = db.execute.await_args_list[1].args[1]
        assert insert_params == [{"pid": 1001, "view_count": 20, "favorite_count": 9, "comment_count": 4}]

        members = await redis_fake.smembers("metrics:active_posts_set")
        assert 1001 not in members
        bucket = await redis_fake.hgetall("metrics:post:1001")
        assert int(bucket["view"]) == 0
        assert int(bucket["favorite"]) == 0
        assert int(bucket["comment"]) == 0

    async def test_incr_post_favorite_negative_allowed_in_redis(self):
        """Redis 层放开负数限制，允许合法负数净增量沉淀，MySQL 端通过 GREATEST 兜底。"""
        from app.services.metrics_service import MetricsService

        redis_fake = FakeRedisForMetrics()
        # Simulate un-favorite when count is already 0
        await redis_fake.hset("metrics:post:1001", "favorite", 0)
        await MetricsService.incr_post_favorite(redis_fake, 1001, delta=-1)
        bucket = await redis_fake.hgetall("metrics:post:1001")
        assert int(bucket["favorite"]) == -1  # Redis 允许负数，不再卡位

    async def test_incr_post_comment_negative_allowed_in_redis(self):
        """Redis 层放开负数限制，允许删除评论产生负数净增量。"""
        from app.services.metrics_service import MetricsService

        redis_fake = FakeRedisForMetrics()
        await redis_fake.hset("metrics:post:1001", "comment", 0)
        await MetricsService.incr_post_comment(redis_fake, 1001, delta=-1)
        bucket = await redis_fake.hgetall("metrics:post:1001")
        assert int(bucket["comment"]) == -1  # Redis 允许负数

    async def test_incr_post_view_stores_in_active_set(self):
        from app.services.metrics_service import MetricsService, _ACTIVE_POSTS_SET

        redis_fake = FakeRedisForMetrics()
        await MetricsService.incr_post_view(redis_fake, 1001)
        members = await redis_fake.smembers(_ACTIVE_POSTS_SET)
        assert 1001 in members



    # ------------------------------------------------------------------
    # 评论生命周期测试：评论/取消评论循环
    # ------------------------------------------------------------------

    async def test_comment_lifecycle_incr_decr_cycle(self):
        """评论生命周期：10→11→10（Redis 增量模式）。"""
        from app.services.metrics_service import MetricsService

        redis_fake = FakeRedisForMetrics()
        post_id = 2001
        key = f"metrics:post:{post_id}"
        await redis_fake.hset(key, "comment", 10)
        await MetricsService.incr_post_comment(redis_fake, post_id, delta=1)
        bucket = await redis_fake.hgetall(key)
        assert int(bucket["comment"]) == 11
        await MetricsService.incr_post_comment(redis_fake, post_id, delta=-1)
        bucket = await redis_fake.hgetall(key)
        assert int(bucket["comment"]) == 10

    async def test_comment_lifecycle_incr_after_decr(self):
        """已有评论被删除后再发评论：10→9→10。"""
        from app.services.metrics_service import MetricsService

        redis_fake = FakeRedisForMetrics()
        post_id = 2002
        key = f"metrics:post:{post_id}"
        await redis_fake.hset(key, "comment", 10)
        await MetricsService.incr_post_comment(redis_fake, post_id, delta=-1)
        bucket = await redis_fake.hgetall(key)
        assert int(bucket["comment"]) == 9
        await MetricsService.incr_post_comment(redis_fake, post_id, delta=1)
        bucket = await redis_fake.hgetall(key)
        assert int(bucket["comment"]) == 10

    async def test_comment_lifecycle_hydrate_reflects_redis(self):
        """评论增减后 hydrate 能正确读取 Redis 增量。"""
        from app.services.metrics_service import MetricsService

        redis_fake = FakeRedisForMetrics()
        post_id = 2003
        key = f"metrics:post:{post_id}"
        await redis_fake.hset(key, "comment", 0)
        await MetricsService.incr_post_comment(redis_fake, post_id, delta=1)
        await MetricsService.incr_post_comment(redis_fake, post_id, delta=1)
        await MetricsService.incr_post_comment(redis_fake, post_id, delta=1)
        items = [{"post_id": post_id}]
        await MetricsService.hydrate_posts_with_metrics(None, redis_fake, items, [post_id])
        assert items[0]["comment_count"] == 3
        await MetricsService.incr_post_comment(redis_fake, post_id, delta=-1)
        items2 = [{"post_id": post_id}]
        await MetricsService.hydrate_posts_with_metrics(None, redis_fake, items2, [post_id])
        assert items2[0]["comment_count"] == 2

    # ------------------------------------------------------------------
    # 刷盘→灌水循环对账测试（Redis ↔ MySQL 数据一致性）
    # ------------------------------------------------------------------

    async def test_flush_then_hydrate_round_trip(self, monkeypatch):
        """首次 hydrate 只读 MySQL 基线，不得把基线灌回 Redis 增量桶。"""
        from app.services.metrics_service import MetricsService
        from unittest.mock import AsyncMock

        redis_fake = FakeRedisForMetrics()
        post_id = 3001
        key = f"metrics:post:{post_id}"
        db = FakeMetricsRoundTripDB(
            post_rows={post_id: {"view_count": 100, "favorite_count": 5, "comment_count": 10}}
        )
        monkeypatch.setattr(
            MetricsService,
            "_get_post_truth_metrics_map",
            AsyncMock(return_value={post_id: {"favorite_count": 5, "comment_count": 10}}),
        )

        items = [{"post_id": post_id}]
        await MetricsService.hydrate_posts_with_metrics(db, redis_fake, items, [post_id])
        assert items[0]["view_count"] == 100
        assert items[0]["favorite_count"] == 5
        assert items[0]["comment_count"] == 10
        assert await redis_fake.hgetall(key) is None

        await MetricsService.incr_post_view(redis_fake, post_id)
        items2 = [{"post_id": post_id}]
        await MetricsService.hydrate_posts_with_metrics(db, redis_fake, items2, [post_id])
        assert items2[0]["view_count"] == 101
        assert items2[0]["favorite_count"] == 5
        assert items2[0]["comment_count"] == 10

        await redis_fake.sadd("metrics:active_posts_set", post_id)
        await MetricsService.flush_metrics_to_db(db, redis_fake)
        assert db.post_rows[post_id]["view_count"] == 101
        assert db.post_rows[post_id]["favorite_count"] == 5
        assert db.post_rows[post_id]["comment_count"] == 10

        items3 = [{"post_id": post_id}]
        await MetricsService.hydrate_posts_with_metrics(db, redis_fake, items3, [post_id])
        assert items3[0]["view_count"] == 101
        assert items3[0]["favorite_count"] == 5
        assert items3[0]["comment_count"] == 10

    async def test_hydrate_goods_does_not_backfill_mysql_baseline_into_redis_increment_bucket(self):
        """商品 metrics 首次 hydrate 不得把 MySQL 基线写回 Redis 增量桶。"""
        from app.services.metrics_service import MetricsService

        redis_fake = FakeRedisForMetrics()
        goods_id = 4001
        db = FakeMetricsRoundTripDB(
            goods_rows={goods_id: {"view_count": 20, "favorite_count": 3, "comment_count": 2}}
        )

        items = [{"goods_id": goods_id}]
        await MetricsService.hydrate_goods_with_metrics(db, redis_fake, items, [goods_id])

        assert items[0]["view_count"] == 20
        assert items[0]["favorite_count"] == 3
        assert items[0]["comment_count"] == 2
        assert await redis_fake.hgetall(f"metrics:goods:{goods_id}") is None

    async def test_flush_goods_after_hydrate_and_one_new_view_only_adds_one(self, monkeypatch):
        """商品 metrics 在 hydrate 后新增一次浏览，刷盘后只能 +1。"""
        from app.services.metrics_service import MetricsService
        from unittest.mock import AsyncMock

        redis_fake = FakeRedisForMetrics()
        goods_id = 4002
        db = FakeMetricsRoundTripDB(
            goods_rows={goods_id: {"view_count": 50, "favorite_count": 8, "comment_count": 4}}
        )
        monkeypatch.setattr(
            MetricsService,
            "_get_goods_truth_metrics_map",
            AsyncMock(return_value={goods_id: {"favorite_count": 8, "comment_count": 4}}),
        )

        items = [{"goods_id": goods_id}]
        await MetricsService.hydrate_goods_with_metrics(db, redis_fake, items, [goods_id])
        assert items[0]["view_count"] == 50
        assert await redis_fake.hgetall(f"metrics:goods:{goods_id}") is None

        await MetricsService.incr_goods_view(redis_fake, goods_id)
        items2 = [{"goods_id": goods_id}]
        await MetricsService.hydrate_goods_with_metrics(db, redis_fake, items2, [goods_id])
        assert items2[0]["view_count"] == 51

        await redis_fake.sadd("metrics:active_goods_set", goods_id)
        await MetricsService.flush_metrics_to_db(db, redis_fake)
        assert db.goods_rows[goods_id]["view_count"] == 51
        assert db.goods_rows[goods_id]["favorite_count"] == 8
        assert db.goods_rows[goods_id]["comment_count"] == 4

    async def test_flush_post_metrics_overwrites_favorite_and_comment_with_truth(self, monkeypatch):
        from app.services.metrics_service import MetricsService
        from unittest.mock import AsyncMock

        redis_fake = FakeRedisForMetrics()
        post_id = 5001
        await redis_fake.sadd("metrics:active_posts_set", post_id)
        redis_fake._hashes[f"metrics:post:{post_id}"] = {"view": "7", "favorite": "32", "comment": "64"}
        db = FakeMetricsRoundTripDB(
            post_rows={post_id: {"view_count": 100, "favorite_count": 999, "comment_count": 777}}
        )
        monkeypatch.setattr(
            MetricsService,
            "_get_post_truth_metrics_map",
            AsyncMock(return_value={post_id: {"favorite_count": 2, "comment_count": 1}}),
        )

        await MetricsService.flush_metrics_to_db(db, redis_fake)

        assert db.post_rows[post_id]["view_count"] == 107
        assert db.post_rows[post_id]["favorite_count"] == 2
        assert db.post_rows[post_id]["comment_count"] == 1
        bucket = await redis_fake.hgetall(f"metrics:post:{post_id}")
        assert int(bucket["view"]) == 0
        assert int(bucket["favorite"]) == 0
        assert int(bucket["comment"]) == 0

    async def test_flush_goods_metrics_overwrites_favorite_and_comment_with_truth(self, monkeypatch):
        from app.services.metrics_service import MetricsService
        from unittest.mock import AsyncMock

        redis_fake = FakeRedisForMetrics()
        goods_id = 6001
        await redis_fake.sadd("metrics:active_goods_set", goods_id)
        redis_fake._hashes[f"metrics:goods:{goods_id}"] = {"view": "5", "favorite": "16", "comment": "8"}
        db = FakeMetricsRoundTripDB(
            goods_rows={goods_id: {"view_count": 30, "favorite_count": 888, "comment_count": 666}}
        )
        monkeypatch.setattr(
            MetricsService,
            "_get_goods_truth_metrics_map",
            AsyncMock(return_value={goods_id: {"favorite_count": 1, "comment_count": 3}}),
        )

        await MetricsService.flush_metrics_to_db(db, redis_fake)

        assert db.goods_rows[goods_id]["view_count"] == 35
        assert db.goods_rows[goods_id]["favorite_count"] == 1
        assert db.goods_rows[goods_id]["comment_count"] == 3
        bucket = await redis_fake.hgetall(f"metrics:goods:{goods_id}")
        assert int(bucket["view"]) == 0
        assert int(bucket["favorite"]) == 0
        assert int(bucket["comment"]) == 0
