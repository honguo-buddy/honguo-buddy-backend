"""Redis 高性能计数器中心：支持异步 Redis 原子自增、定时刷盘到 MySQL、批量灌注到列表卡片。

核心设计：
- 热点写入全走 Redis HINCRBY（O(1) 原子操作，零锁竞争）
- 定时异步回写 MySQL（ON DUPLICATE KEY UPDATE 批量刷盘）
- 列表读取走 Pipeline 批量灌水（单次网络往返，消灭 N+1）
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Post, PostMetrics

logger = logging.getLogger(__name__)

# 内存活跃键池（供定时刷盘扫描）
_active_post_metric_keys: set[int] = set()
_active_goods_metric_keys: set[int] = set()

_METRICS_FLUSH_LOCK = asyncio.Lock()


class MetricsService:
    """高性能多实体计数器中心。"""

    # ------------------------------------------------------------------
    # Redis 原子自增入口
    # ------------------------------------------------------------------

    @staticmethod
    async def incr_post_view(redis_client, post_id: int) -> None:
        """浏览次数 +1（纯 Redis，不查不写 MySQL）。"""
        key = f"metrics:post:{post_id}"
        await redis_client.hincrby(key, "view", 1)
        _active_post_metric_keys.add(post_id)

    @staticmethod
    async def incr_post_favorite(redis_client, post_id: int, delta: int = 1) -> None:
        """收藏数增减。delta=1 为收藏，delta=-1 为取消收藏。"""
        key = f"metrics:post:{post_id}"
        await redis_client.hincrby(key, "favorite", delta)
        _active_post_metric_keys.add(post_id)

    @staticmethod
    async def incr_post_comment(redis_client, post_id: int, delta: int = 1) -> None:
        """评论数增减。"""
        key = f"metrics:post:{post_id}"
        await redis_client.hincrby(key, "comment", delta)
        _active_post_metric_keys.add(post_id)

    @staticmethod
    async def incr_post_upvote(redis_client, post_id: int, delta: int = 1) -> None:
        """点赞数增减。"""
        key = f"metrics:post:{post_id}"
        await redis_client.hincrby(key, "upvote", delta)
        _active_post_metric_keys.add(post_id)

    @staticmethod
    async def incr_goods_view(redis_client, goods_id: int) -> None:
        """商品浏览 +1。"""
        key = f"metrics:goods:{goods_id}"
        await redis_client.hincrby(key, "view", 1)
        _active_goods_metric_keys.add(goods_id)

    @staticmethod
    async def incr_goods_favorite(redis_client, goods_id: int, delta: int = 1) -> None:
        """商品收藏数增减。"""
        key = f"metrics:goods:{goods_id}"
        await redis_client.hincrby(key, "favorite", delta)
        _active_goods_metric_keys.add(goods_id)

    @staticmethod
    async def incr_goods_comment(redis_client, goods_id: int, delta: int = 1) -> None:
        """商品评论数增减。"""
        key = f"metrics:goods:{goods_id}"
        await redis_client.hincrby(key, "comment", delta)
        _active_goods_metric_keys.add(goods_id)

    # ------------------------------------------------------------------
    # 批量灌水反哺（Anti-N+1 Pipeline）
    # ------------------------------------------------------------------

    @staticmethod
    async def hydrate_posts_with_metrics(
        redis_client,
        items: list[dict[str, Any]],
        post_ids: list[int],
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
            pid = item.get("post_id")
            if pid is None:
                continue
            metrics = metrics_map.get(pid, {})
            item.setdefault("view_count", int(metrics.get("view", 0)))
            item.setdefault("favorite_count", int(metrics.get("favorite", 0)))
            item.setdefault("comment_count", int(metrics.get("comment", 0)))
            # upvote 暂不灌入卡片，保留给未来扩展

    @staticmethod
    async def hydrate_posts_counter(
        redis_client,
        items: list[dict[str, Any]],
        prefix: str = "post",
        field_map: dict[str, str] | None = None,
    ) -> None:
        """通用批量灌水接口：一次性从 Redis Pipeline 捞取指定实体的计数器字段。

        参数：
            redis_client: Redis 客户端
            items: 待注入的字典列表（会原地修改）
            prefix: Redis Key 前缀，如 "post" 或 "goods"
            field_map: Redis field -> Python key 的映射字典，如 {"view": "view_count", "favorite": "favorite_count"}
        """
        if not items or not field_map:
            return

        ids = [it.get(f"{prefix}_id" if prefix != "post" else "post_id") for it in items if it.get(f"{prefix}_id" if prefix != "post" else "post_id")]
        ids = [i for i in ids if i is not None]
        if not ids:
            return

        pipe = redis_client.pipeline()
        for entity_id in ids:
            pipe.hgetall(f"metrics:{prefix}:{entity_id}")
        results = await pipe.execute()

        for item in items:
            pid = item.get(f"{prefix}_id" if prefix != "post" else "post_id")
            if pid is None:
                continue
            idx = ids.index(pid) if pid in ids else -1
            if idx >= 0 and results[idx]:
                raw_data = results[idx]
                for redis_field, python_key in field_map.items():
                    item.setdefault(python_key, int(raw_data.get(redis_field, 0)))

    # ------------------------------------------------------------------
    # 异步写回（Write-Back）：定时将 Redis 计数器刷入 MySQL
    # ------------------------------------------------------------------

    @staticmethod
    async def flush_metrics_to_db(db: AsyncSession, redis_client) -> None:
        """每分钟一次，扫描活跃变动的 ID 池，批量刷盘到 MySQL。

        使用 ON DUPLICATE KEY UPDATE 原生 SQL 批量写入 PostsMetrics 表。
        异步锁保护避免并发重复执行。
        """
        async with _METRICS_FLUSH_LOCK:
            await _flush_post_metrics(db, redis_client)
            await _flush_goods_metrics(db, redis_client)

            _active_post_metric_keys.clear()
            _active_goods_metric_keys.clear()


async def _flush_post_metrics(db: AsyncSession, redis_client) -> None:
    """批量刷盘帖子计数器。"""
    post_ids = list(_active_post_metric_keys)
    if not post_ids:
        return

    pipe = redis_client.pipeline()
    for pid in post_ids:
        pipe.hgetall(f"metrics:post:{pid}")
    results = await pipe.execute()

    values_clauses = []
    for pid, raw in zip(post_ids, results):
        if not raw:
            continue
        view = int(raw.get("view", 0))
        favorite = int(raw.get("favorite", 0))
        comment = int(raw.get("comment", 0))
        upvote = int(raw.get("upvote", 0))
        values_clauses.append(f"({pid}, {view}, {favorite}, {comment}, {upvote})")

    if not values_clauses:
        return

    sql = text("""
        INSERT INTO post_metrics (post_id, view_count, favorite_count, comment_count, upvote_count)
        VALUES {values}
        ON DUPLICATE KEY UPDATE
            view_count = view_count + VALUES(view_count),
            favorite_count = favorite_count + VALUES(favorite_count),
            comment_count = comment_count + VALUES(comment_count),
            upvote_count = upvote_count + VALUES(upvote_count)
    """.format(values=", ".join(values_clauses)))

    try:
        await db.execute(sql)
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("刷盘 post_metrics 失败")


async def _flush_goods_metrics(db: AsyncSession, redis_client) -> None:
    """批量刷盘商品计数器。"""
    goods_ids = list(_active_goods_metric_keys)
    if not goods_ids:
        return

    pipe = redis_client.pipeline()
    for gid in goods_ids:
        pipe.hgetall(f"metrics:goods:{gid}")
    results = await pipe.execute()

    values_clauses = []
    for gid, raw in zip(goods_ids, results):
        if not raw:
            continue
        view = int(raw.get("view", 0))
        favorite = int(raw.get("favorite", 0))
        comment = int(raw.get("comment", 0))
        sales = int(raw.get("sales", 0))
        cart = int(raw.get("cart", 0))
        values_clauses.append(f"({gid}, {view}, {favorite}, {comment}, {sales}, {cart})")

    if not values_clauses:
        return

    sql = text("""
        INSERT INTO goods_metrics (goods_id, view_count, favorite_count, comment_count, sales_count, cart_count)
        VALUES {values}
        ON DUPLICATE KEY UPDATE
            view_count = view_count + VALUES(view_count),
            favorite_count = favorite_count + VALUES(favorite_count),
            comment_count = comment_count + VALUES(comment_count),
            sales_count = sales_count + VALUES(sales_count),
            cart_count = cart_count + VALUES(cart_count)
    """.format(values=", ".join(values_clauses)))

    try:
        await db.execute(sql)
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("刷盘 goods_metrics 失败")
