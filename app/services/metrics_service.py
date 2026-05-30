"""Redis 高性能计数器中心：支持异步 Redis 原子自增、定时刷盘到 MySQL、批量灌注到列表卡片。

核心设计：
- 热点写入全走 Redis HINCRBY（O(1) 原子操作，零锁竞争）
- 活跃实体通过 Redis 分布式 Set 追踪（跨 Worker 安全）
- 定时异步回写 MySQL（ON DUPLICATE KEY UPDATE 批量刷盘）
- 列表读取走 Pipeline 批量灌水（单次网络往返，消灭 N+1）
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Goods, Post  # 确保导入 Post 和 Goods 模型以进行 ID 验证

logger = logging.getLogger(__name__)

# Redis 分布式集合 key 常量（替代 Python 内存 set，跨 Worker 安全）
_ACTIVE_POSTS_SET = "metrics:active_posts_set"
_ACTIVE_GOODS_SET = "metrics:active_goods_set"



class MetricsService:
    """高性能多实体计数器中心。"""

    # ------------------------------------------------------------------
    # Redis 原子自增入口（带负数卡位防御）
    # ------------------------------------------------------------------

    @staticmethod
    async def incr_post_view(redis_client, post_id: int) -> None:
        """浏览次数 +1（纯 Redis，不查不写 MySQL）。"""
        logger.debug(f"🔥 [Metrics Debug] 侦测到详情页正向点击！正在为 post_{post_id} 触发 Redis 原子自增...")
        key = f"metrics:post:{post_id}"
        await redis_client.hincrby(key, "view", 1)
        await redis_client.sadd(_ACTIVE_POSTS_SET, post_id)

    @staticmethod
    async def incr_post_favorite(redis_client, post_id: int, delta: int = 1) -> None:
        """收藏数增减。delta=1 为收藏，delta=-1 为取消收藏。"""
        key = f"metrics:post:{post_id}"
        current = await redis_client.hincrby(key, "favorite", delta)
        if current < 0:
            await redis_client.hset(key, "favorite", 0)
        await redis_client.sadd(_ACTIVE_POSTS_SET, post_id)

    @staticmethod
    async def incr_post_comment(redis_client, post_id: int, delta: int = 1) -> None:
        """评论数增减。"""
        key = f"metrics:post:{post_id}"
        current = await redis_client.hincrby(key, "comment", delta)
        if current < 0:
            await redis_client.hset(key, "comment", 0)
        await redis_client.sadd(_ACTIVE_POSTS_SET, post_id)

    @staticmethod
    async def incr_post_upvote(redis_client, post_id: int, delta: int = 1) -> None:
        """点赞数增减。"""
        key = f"metrics:post:{post_id}"
        current = await redis_client.hincrby(key, "upvote", delta)
        if current < 0:
            await redis_client.hset(key, "upvote", 0)
        await redis_client.sadd(_ACTIVE_POSTS_SET, post_id)

    @staticmethod
    async def incr_goods_view(redis_client, goods_id: int) -> None:
        """商品浏览 +1。"""
        key = f"metrics:goods:{goods_id}"
        await redis_client.hincrby(key, "view", 1)
        await redis_client.sadd(_ACTIVE_GOODS_SET, goods_id)

    @staticmethod
    async def incr_goods_favorite(redis_client, goods_id: int, delta: int = 1) -> None:
        """商品收藏数增减。"""
        key = f"metrics:goods:{goods_id}"
        current = await redis_client.hincrby(key, "favorite", delta)
        if current < 0:
            await redis_client.hset(key, "favorite", 0)
        await redis_client.sadd(_ACTIVE_GOODS_SET, goods_id)

    @staticmethod
    async def incr_goods_comment(redis_client, goods_id: int, delta: int = 1) -> None:
        """商品评论数增减。"""
        key = f"metrics:goods:{goods_id}"
        current = await redis_client.hincrby(key, "comment", delta)
        if current < 0:
            await redis_client.hset(key, "comment", 0)
        await redis_client.sadd(_ACTIVE_GOODS_SET, goods_id)

    # ------------------------------------------------------------------
    # 批量灌水反哺（Anti-N+1 Pipeline）
    # ------------------------------------------------------------------

    @staticmethod
    async def hydrate_posts_with_metrics(
        redis_client,
        items: list[dict[str, Any]],
        post_ids: list[int],
        id_key: str = "post_id",
    ) -> None:
        """在循环体外批量（单次网络往返）捞出 Redis 哈希桶，注入卡片载荷。

        直接修改传入的 items 列表，无返回值（O(1) 内存操作）。
        """
        if not items or not post_ids:
            return

        pipe = redis_client.pipeline()
        for pid in post_ids:
            pipe.hgetall(f"metrics:post:{pid}")
        results = await pipe.execute()

        metrics_map: dict[int, dict[str, str]] = {}
        for pid, raw in zip(post_ids, results):
            if raw:
                metrics_map[pid] = raw

        for item in items:
            pid = item.get(id_key)
            if pid is None:
                continue
            metrics = metrics_map.get(pid, {})
            item["view_count"] = int(metrics.get("view", 0))
            item["favorite_count"] = int(metrics.get("favorite", 0))
            item["comment_count"] = int(metrics.get("comment", 0))


    @staticmethod
    async def hydrate_goods_with_metrics(
        redis_client,
        items: list[dict[str, Any]],
        goods_ids: list[int],
    ) -> None:
        """Goods-marketplace card batch hydration. Structural symmetry with hydrate_posts_with_metrics.

        Supports lobby, my-published, favorites, and history wall goods cards.
        Keys on goods_id with target_id fallback for polymorphic contexts.
        """
        if not items or not goods_ids:
            return

        pipe = redis_client.pipeline()
        for gid in goods_ids:
            pipe.hgetall(f"metrics:goods:{gid}")
        results = await pipe.execute()

        metrics_map: dict[int, dict[str, str]] = {}
        for gid, raw in zip(goods_ids, results):
            if raw:
                metrics_map[gid] = raw

        for item in items:
            gid = item.get("goods_id") or item.get("target_id")
            if gid is None:
                continue
            metrics = metrics_map.get(gid, {})
            v = metrics.get("view") or metrics.get(b"view") or 0
            f = metrics.get("favorite") or metrics.get(b"favorite") or 0
            c = metrics.get("comment") or metrics.get(b"comment") or 0
            item["view_count"] = int(v)
            item["favorite_count"] = int(f)
            item["comment_count"] = int(c)

    @staticmethod

    # ------------------------------------------------------------------
    # Redis 分布式锁（SET NX EX 原子模式，跨 Worker 安全）
    # ------------------------------------------------------------------

    @staticmethod
    async def _acquire_flush_lock(redis_client, lock_key: str, ttl: int = 60):
        """Redis 分布式锁：SET NX EX 原子获取，返回 token 供安全释放。"""
        token = str(uuid.uuid4())
        acquired = await redis_client.set(lock_key, token, nx=True, ex=ttl)
        return token if acquired else None

    @staticmethod
    async def _release_flush_lock(redis_client, lock_key: str, token: str) -> None:
        """Lua 原子释放：仅当 token 匹配时才删除 key，防止误删他人锁。"""
        script = """
        if redis.call("GET", KEYS[1]) == ARGV[1] then
            return redis.call("DEL", KEYS[1])
        else
            return 0
        end
        """
        await redis_client.eval(script, 1, lock_key, token)
    @staticmethod
    async def flush_metrics_to_db(db: AsyncSession, redis_client) -> None:
        """每分钟一次，从 Redis 分布式 Set 扫描活跃 ID，批量刷盘到 MySQL。

        使用 ON DUPLICATE KEY UPDATE 原生 SQL 批量写入。
        分布式锁由各 _flush_* 方法内部独立管理。
        刷盘成功后原子清除 Redis 活跃集合，确保数据不丢。
        """
        await MetricsService._flush_post_metrics(db, redis_client)
        await MetricsService._flush_goods_metrics(db, redis_client)

    @staticmethod
    async def _flush_post_metrics(db: AsyncSession, redis_client) -> None:
        """批量刷盘帖子计数器（分布式锁 + 参数化 SQL，跨 Worker 安全）。"""
        lock_key = "metrics:flush_post_lock"
        token = await MetricsService._acquire_flush_lock(redis_client, lock_key)
        if token is None:
            logger.debug("\u23ed [Metrics Sync] 另一 Worker 正在执行帖子刷盘，本轮跳过")
            return
        try:
            raw_post_ids = await redis_client.smembers(_ACTIVE_POSTS_SET)
            if not raw_post_ids:
                return

            post_ids = [int(pid) for pid in raw_post_ids]

            stmt = select(Post.post_id).where(Post.post_id.in_(post_ids))
            res = await db.execute(stmt)
            existing_ids = set(res.scalars().all())

            valid_ids = [pid for pid in post_ids if pid in existing_ids]
            ghost_ids = [pid for pid in post_ids if pid not in existing_ids]

            if ghost_ids:
                await redis_client.srem(_ACTIVE_POSTS_SET, *ghost_ids)
                pipe_clean = redis_client.pipeline()
                for g_id in ghost_ids:
                    pipe_clean.delete(f"metrics:post:{g_id}")
                await pipe_clean.execute()
                logger.warning(f"\u26a0 [Metrics Clean] 成功拦截并无声蒸发了帖子测试幽灵脏数据 ID: {ghost_ids}")

            if not valid_ids:
                return

            pipe = redis_client.pipeline()
            for pid in valid_ids:
                pipe.hgetall(f"metrics:post:{pid}")
            results = await pipe.execute()

            # 参数化绑定，杜绝 SQL 注入风险
            bind_params = {}
            placeholders = []
            for i, (pid, raw) in enumerate(zip(valid_ids, results)):
                if not raw:
                    continue
                view = int(raw.get("view") or raw.get(b"view") or 0)
                favorite = int(raw.get("favorite") or raw.get(b"favorite") or 0)
                comment = int(raw.get("comment") or raw.get(b"comment") or 0)
                placeholders.append(
                    f"(:pid_{i}, :view_{i}, :fav_{i}, :com_{i}, "
                    f"CONVERT_TZ(NOW(), @@session.time_zone, '+08:00'), "
                    f"CONVERT_TZ(NOW(), @@session.time_zone, '+08:00'))"
                )
                bind_params[f"pid_{i}"] = pid
                bind_params[f"view_{i}"] = view
                bind_params[f"fav_{i}"] = favorite
                bind_params[f"com_{i}"] = comment

            if not placeholders:
                return

            sql = text(f"""
                INSERT INTO post_metrics (post_id, view_count, favorite_count, comment_count, create_time, update_time)
                VALUES {", ".join(placeholders)}
                ON DUPLICATE KEY UPDATE
                    view_count = VALUES(view_count),
                    favorite_count = VALUES(favorite_count),
                    comment_count = VALUES(comment_count),
                    update_time = CONVERT_TZ(NOW(), @@session.time_zone, '+08:00')
            """)

            try:
                await db.execute(sql, bind_params)
                await db.commit()
                await redis_client.srem(_ACTIVE_POSTS_SET, *valid_ids)
                logger.info(f"\u2728 [Metrics Sync] 成功将活跃帖子 {valid_ids} 的最新计数完美对齐到 MySQL 物理表！")
            except Exception:
                await db.rollback()
                logger.exception("\u274c [Metrics Sync] 刷盘 post_metrics 发生严重硬伤失败")
        finally:
            await MetricsService._release_flush_lock(redis_client, lock_key, token)

    @staticmethod
    async def _flush_goods_metrics(db: AsyncSession, redis_client) -> None:
        """批量刷盘商品计数器（分布式锁 + 参数化 SQL，跨 Worker 安全）。"""
        lock_key = "metrics:flush_goods_lock"
        token = await MetricsService._acquire_flush_lock(redis_client, lock_key)
        if token is None:
            logger.debug("\u23ed [Metrics Sync] 另一 Worker 正在执行商品刷盘，本轮跳过")
            return
        try:
            raw_goods_ids = await redis_client.smembers(_ACTIVE_GOODS_SET)
            if not raw_goods_ids:
                return

            goods_ids = [int(gid) for gid in raw_goods_ids]

            stmt = select(Goods.goods_id).where(Goods.goods_id.in_(goods_ids))
            res = await db.execute(stmt)
            existing_ids = set(res.scalars().all())

            valid_ids = [gid for gid in goods_ids if gid in existing_ids]
            ghost_ids = [gid for gid in goods_ids if gid not in existing_ids]

            if ghost_ids:
                await redis_client.srem(_ACTIVE_GOODS_SET, *ghost_ids)
                pipe_clean = redis_client.pipeline()
                for g_id in ghost_ids:
                    pipe_clean.delete(f"metrics:goods:{g_id}")
                await pipe_clean.execute()
                logger.warning(f"\u26a0 [Metrics Clean] 成功拦截并无声蒸发了商品测试幽灵脏数据 ID: {ghost_ids}")

            if not valid_ids:
                return

            pipe = redis_client.pipeline()
            for gid in valid_ids:
                pipe.hgetall(f"metrics:goods:{gid}")
            results = await pipe.execute()

            # 参数化绑定，杜绝 SQL 注入风险
            bind_params = {}
            placeholders = []
            for i, (gid, raw) in enumerate(zip(valid_ids, results)):
                if not raw:
                    continue
                view = int(raw.get("view") or raw.get(b"view") or 0)
                favorite = int(raw.get("favorite") or raw.get(b"favorite") or 0)
                comment = int(raw.get("comment") or raw.get(b"comment") or 0)
                placeholders.append(
                    f"(:gid_{i}, :view_{i}, :fav_{i}, :com_{i}, "
                    f"CONVERT_TZ(NOW(), @@session.time_zone, '+08:00'), "
                    f"CONVERT_TZ(NOW(), @@session.time_zone, '+08:00'))"
                )
                bind_params[f"gid_{i}"] = gid
                bind_params[f"view_{i}"] = view
                bind_params[f"fav_{i}"] = favorite
                bind_params[f"com_{i}"] = comment

            if not placeholders:
                return

            sql = text(f"""
                INSERT INTO goods_metrics (goods_id, view_count, favorite_count, comment_count, create_time, update_time)
                VALUES {", ".join(placeholders)}
                ON DUPLICATE KEY UPDATE
                    view_count = VALUES(view_count),
                    favorite_count = VALUES(favorite_count),
                    comment_count = VALUES(comment_count),
                    update_time = CONVERT_TZ(NOW(), @@session.time_zone, '+08:00')
            """)

            try:
                await db.execute(sql, bind_params)
                await db.commit()
                await redis_client.srem(_ACTIVE_GOODS_SET, *valid_ids)
                logger.info(f"\u2728 [Metrics Sync] 成功将活跃商品 {valid_ids} 的最新计数完美对齐到 MySQL 物理表！")
            except Exception:
                await db.rollback()
                logger.exception("\u274c [Metrics Sync] 刷盘 goods_metrics 发生严重硬伤失败")
        finally:
            await MetricsService._release_flush_lock(redis_client, lock_key, token)
