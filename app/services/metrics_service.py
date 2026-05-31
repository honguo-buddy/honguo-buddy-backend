"""Redis 高性能计数器中心：支持异步 Redis 原子自增、定时刷盘到 MySQL、批量灌注到列表卡片。

核心设计：
- 热点写入全走 Redis HINCRBY（O(1) 原子操作，零锁竞争）
- 活跃实体通过 Redis 分布式 Set 追踪（跨 Worker 安全）
- 定时异步相对增量回写 MySQL（增量累加模式，彻底隔离重置覆盖天坑）
- 列表读取走 混合对账双端合流 机制（MySQL 历史基准 + Redis 临时增量，消灭数据断层）
- 静态参数化批量绑定（完全遵循数据库安全红线，彻底消灭 SQL 注入风险）
- 彻底放开缓存侧负数限制，支持合法的负数净增量下发，并在 MySQL 端通过 GREATEST 优雅兜底。
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from sqlalchemy import text, select, bindparam
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Goods, Post  # 导入 Post 和 Goods 模型以进行 ID 验证

logger = logging.getLogger(__name__)

# Redis 分布式集合 key 常量
_ACTIVE_POSTS_SET = "metrics:active_posts_set"
_ACTIVE_GOODS_SET = "metrics:active_goods_set"


class MetricsService:
    """高性能多实体计数器中心。"""

    # ------------------------------------------------------------------
    # Redis 原子自增入口（放开负数限制，允许合法负数净增量沉淀）
    # ------------------------------------------------------------------

    @staticmethod
    async def incr_post_view(redis_client, post_id: int) -> None:
        """浏览次数 +1（纯 Redis，不查不写 MySQL）。"""
        logger.debug(f"[Metrics Debug] Detected post detail access, triggering atomic increment for post_{post_id}")
        key = f"metrics:post:{post_id}"
        await redis_client.hincrby(key, "view", 1)
        await redis_client.sadd(_ACTIVE_POSTS_SET, post_id)

    @staticmethod
    async def incr_post_favorite(redis_client, post_id: int, delta: int = 1) -> None:
        """收藏数增减。delta=1 为收藏，delta=-1 为取消收藏。允许产生负数临时增量。"""
        key = f"metrics:post:{post_id}"
        await redis_client.hincrby(key, "favorite", delta)
        await redis_client.sadd(_ACTIVE_POSTS_SET, post_id)

    @staticmethod
    async def incr_post_comment(redis_client, post_id: int, delta: int = 1) -> None:
        """评论数增减。delta=-1 为删除评论。"""
        key = f"metrics:post:{post_id}"
        await redis_client.hincrby(key, "comment", delta)
        await redis_client.sadd(_ACTIVE_POSTS_SET, post_id)

    @staticmethod
    async def incr_post_upvote(redis_client, post_id: int, delta: int = 1) -> None:
        """点赞数增减。"""
        key = f"metrics:post:{post_id}"
        await redis_client.hincrby(key, "upvote", delta)
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
        await redis_client.hincrby(key, "favorite", delta)
        await redis_client.sadd(_ACTIVE_GOODS_SET, goods_id)

    @staticmethod
    async def incr_goods_comment(redis_client, goods_id: int, delta: int = 1) -> None:
        """商品评论数增减。"""
        key = f"metrics:goods:{goods_id}"
        await redis_client.hincrby(key, "comment", delta)
        await redis_client.sadd(_ACTIVE_GOODS_SET, goods_id)

    # ------------------------------------------------------------------
    # 读链路重构：双端对账实时合流（Total = MySQL Base + Redis Increment）
    # ------------------------------------------------------------------

    @staticmethod
    async def hydrate_posts_with_metrics(
        db: AsyncSession,
        redis_client,
        items: list[dict[str, Any]],
        post_ids: list[int],
        id_key: str = "post_id",
    ) -> None:
        """帖子指标混合反哺：结合 MySQL 历史大盘与 Redis 动态增量，确保精确计算。"""
        if not items or not post_ids:
            return

        pipe = redis_client.pipeline()
        for pid in post_ids:
            pipe.hgetall(f"metrics:post:{pid}")
        redis_results = await pipe.execute()

        redis_map = {}
        for pid, raw in zip(post_ids, redis_results):
            if raw:
                redis_map[pid] = {
                    "view": int(raw.get("view") or raw.get(b"view") or 0),
                    "favorite": int(raw.get("favorite") or raw.get(b"favorite") or 0),
                    "comment": int(raw.get("comment") or raw.get(b"comment") or 0),
                }

        db_map = {}
        try:
            sql = text("""
                SELECT post_id, view_count, favorite_count, comment_count 
                FROM post_metrics 
                WHERE post_id IN :pids
            """).bindparams(bindparam("pids", expanding=True))
            
            res = await db.execute(sql, {"pids": post_ids})
            for row in res.mappings():
                db_map[row["post_id"]] = {
                    "view": int(row["view_count"] or 0),
                    "favorite": int(row["favorite_count"] or 0),
                    "comment": int(row["comment_count"] or 0)
                }
        except Exception:
            logger.exception("[Metrics Hydrate] Failed to fetch post metrics baseline from database")

        for item in items:
            pid = item.get(id_key)
            if pid is None:
                continue
            
            db_base = db_map.get(pid, {"view": 0, "favorite": 0, "comment": 0})
            redis_incr = redis_map.get(pid, {"view": 0, "favorite": 0, "comment": 0})
            
            # 允许负数增量参与求和运算，读链路数据达到动态绝对一致
            item["view_count"] = max(0, db_base["view"] + redis_incr["view"])
            item["favorite_count"] = max(0, db_base["favorite"] + redis_incr["favorite"])
            item["comment_count"] = max(0, db_base["comment"] + redis_incr["comment"])

    @staticmethod
    async def hydrate_goods_with_metrics(
        db: AsyncSession,
        redis_client,
        items: list[dict[str, Any]],
        goods_ids: list[int],
    ) -> None:
        """商品指标混合反哺：结合 MySQL 历史大盘与 Redis 动态增量，确保精确计算。"""
        if not items or not goods_ids:
            return

        pipe = redis_client.pipeline()
        for gid in goods_ids:
            pipe.hgetall(f"metrics:goods:{gid}")
        results = await pipe.execute()

        redis_map = {}
        for gid, raw in zip(goods_ids, results):
            if raw:
                redis_map[gid] = {
                    "view": int(raw.get("view") or raw.get(b"view") or 0),
                    "favorite": int(raw.get("favorite") or raw.get(b"favorite") or 0),
                    "comment": int(raw.get("comment") or raw.get(b"comment") or 0),
                }

        db_map = {}
        try:
            sql = text("""
                SELECT goods_id, view_count, favorite_count, comment_count 
                FROM goods_metrics 
                WHERE goods_id IN :gids
            """).bindparams(bindparam("gids", expanding=True))
            
            res = await db.execute(sql, {"gids": goods_ids})
            for row in res.mappings():
                db_map[row["goods_id"]] = {
                    "view": int(row["view_count"] or 0),
                    "favorite": int(row["favorite_count"] or 0),
                    "comment": int(row["comment_count"] or 0)
                }
        except Exception:
            logger.exception("[Metrics Hydrate] Failed to fetch goods metrics baseline from database")

        for item in items:
            gid = item.get("goods_id") or item.get("target_id")
            if gid is None:
                continue
            
            db_base = db_map.get(gid, {"view": 0, "favorite": 0, "comment": 0})
            redis_incr = redis_map.get(gid, {"view": 0, "favorite": 0, "comment": 0})
            
            item["view_count"] = max(0, db_base["view"] + redis_incr["view"])
            item["favorite_count"] = max(0, db_base["favorite"] + redis_incr["favorite"])
            item["comment_count"] = max(0, db_base["comment"] + redis_incr["comment"])

    # ------------------------------------------------------------------
    # Redis 分布式锁（SET NX EX 原子模式，跨进程安全）
    # ------------------------------------------------------------------

    @staticmethod
    async def _acquire_flush_lock(redis_client, lock_key: str, ttl: int = 60) -> Optional[str]:
        """Redis 分布式锁：SET NX EX 原子获取，返回专属 token 供安全解锁。"""
        token = str(uuid.uuid4())
        acquired = await redis_client.set(lock_key, token, nx=True, ex=ttl)
        return token if acquired else None

    @staticmethod
    async def _release_flush_lock(redis_client, lock_key: str, token: str) -> None:
        """Lua 脚本原子释放锁：严格校验 token 匹配性，防止跨进程误删他人锁。"""
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
        """定时任务刷盘总调度入口。"""
        await MetricsService._flush_post_metrics(db, redis_client)
        await MetricsService._flush_goods_metrics(db, redis_client)

    # ------------------------------------------------------------------
    # 写链路异步刷盘区（100% 预编译静态绑定 + MySQL GREATEST 安全卡位）
    # ------------------------------------------------------------------

    @staticmethod
    async def _flush_post_metrics(db: AsyncSession, redis_client) -> None:
        """批量同步帖子并发计数器（原生参数化批量处理，彻底清除 SQL 注入隐患）。"""
        lock_key = "metrics:flush_post_lock"
        token = await MetricsService._acquire_flush_lock(redis_client, lock_key)
        if token is None:
            logger.debug("[Metrics Sync] Another worker is currently executing post metrics flush, skipping this round")
            return
        try:
            raw_post_ids = await redis_client.smembers(_ACTIVE_POSTS_SET)
            if not raw_post_ids:
                return

            post_ids = [int(pid) for pid in raw_post_ids]

            # 幽灵脏数据内审清洗滤网
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
                logger.warning(f"[Metrics Clean] Intercepted and wiped out post ghost IDs from cache: {ghost_ids}")

            if not valid_ids:
                return

            pipe = redis_client.pipeline()
            for pid in valid_ids:
                pipe.hgetall(f"metrics:post:{pid}")
            results = await pipe.execute()

            # 构建标准预编译批处理参数字典列表
            bind_params_list = []
            for pid, raw in zip(valid_ids, results):
                if not raw:
                    continue
                view = int(raw.get("view") or raw.get(b"view") or 0)
                favorite = int(raw.get("favorite") or raw.get(b"favorite") or 0)
                comment = int(raw.get("comment") or raw.get(b"comment") or 0)
                
                if view == 0 and favorite == 0 and comment == 0:
                    continue

                bind_params_list.append({
                    "pid": pid,
                    "view_count": view,
                    "favorite_count": favorite,
                    "comment_count": comment
                })

            if not bind_params_list:
                return

            #  纯静态 SQL 模板契约：在 ON DUPLICATE KEY UPDATE 中引入 GREATEST(0, ...) 终极卡位防御
            sql = text("""
                INSERT INTO post_metrics (post_id, view_count, favorite_count, comment_count, create_time, update_time)
                VALUES (:pid, :view_count, :favorite_count, :comment_count, 
                        CONVERT_TZ(NOW(), @@session.time_zone, '+08:00'), 
                        CONVERT_TZ(NOW(), @@session.time_zone, '+08:00'))
                ON DUPLICATE KEY UPDATE
                    view_count = GREATEST(0, view_count + VALUES(view_count)),
                    favorite_count = GREATEST(0, favorite_count + VALUES(favorite_count)),
                    comment_count = GREATEST(0, comment_count + VALUES(comment_count)),
                    update_time = CONVERT_TZ(NOW(), @@session.time_zone, '+08:00')
            """)

            try:
                await db.execute(sql, bind_params_list)
                await db.commit()
                
                # 相对流水账增量原子对账销账
                pipe_deduct = redis_client.pipeline()
                for p in bind_params_list:
                    key = f"metrics:post:{p['pid']}"
                    pipe_deduct.hincrby(key, "view", -p["view_count"])
                    pipe_deduct.hincrby(key, "favorite", -p["favorite_count"])
                    pipe_deduct.hincrby(key, "comment", -p["comment_count"])
                await pipe_deduct.execute()

                await redis_client.srem(_ACTIVE_POSTS_SET, *[p["pid"] for p in bind_params_list])
                logger.info(f"[Metrics Sync] Successfully completed parameterized flush batch for posts: {[p['pid'] for p in bind_params_list]}")
            except Exception:
                await db.rollback()
                logger.exception("[Metrics Sync] Critical database exception during post metrics executemany flush")
        finally:
            await MetricsService._release_flush_lock(redis_client, lock_key, token)

    @staticmethod
    async def _flush_goods_metrics(db: AsyncSession, redis_client) -> None:
        """批量同步商品并发计数器（原生参数化批量处理，彻底清除 SQL 注入隐患）。"""
        lock_key = "metrics:flush_goods_lock"
        token = await MetricsService._acquire_flush_lock(redis_client, lock_key)
        if token is None:
            logger.debug("[Metrics Sync] Another worker is currently executing goods metrics flush, skipping this round")
            return
        try:
            raw_goods_ids = await redis_client.smembers(_ACTIVE_GOODS_SET)
            if not raw_goods_ids:
                return

            goods_ids = [int(gid) for gid in raw_goods_ids]

            # 商品幽灵 ID 清洗
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
                logger.warning(f"[Metrics Clean] Intercepted and wiped out goods ghost IDs from cache: {ghost_ids}")

            if not valid_ids:
                return

            pipe = redis_client.pipeline()
            for gid in valid_ids:
                pipe.hgetall(f"metrics:goods:{gid}")
            results = await pipe.execute()

            # 构建商品域原生批量参数化字典数组
            bind_params_list = []
            for gid, raw in zip(valid_ids, results):
                if not raw:
                    continue
                view = int(raw.get("view") or raw.get(b"view") or 0)
                favorite = int(raw.get("favorite") or raw.get(b"favorite") or 0)
                comment = int(raw.get("comment") or raw.get(b"comment") or 0)
                
                if view == 0 and favorite == 0 and comment == 0:
                    continue

                bind_params_list.append({
                    "gid": gid,
                    "view_count": view,
                    "favorite_count": favorite,
                    "comment_count": comment
                })

            if not bind_params_list:
                return

            # 纯静态 SQL 模板契约：引入 GREATEST(0, ...) 终极卡位防御
            sql = text("""
                INSERT INTO goods_metrics (goods_id, view_count, favorite_count, comment_count, create_time, update_time)
                VALUES (:gid, :view_count, :favorite_count, :comment_count, 
                        CONVERT_TZ(NOW(), @@session.time_zone, '+08:00'), 
                        CONVERT_TZ(NOW(), @@session.time_zone, '+08:00'))
                ON DUPLICATE KEY UPDATE
                    view_count = GREATEST(0, view_count + VALUES(view_count)),
                    favorite_count = GREATEST(0, favorite_count + VALUES(favorite_count)),
                    comment_count = GREATEST(0, comment_count + VALUES(comment_count)),
                    update_time = CONVERT_TZ(NOW(), @@session.time_zone, '+08:00')
            """)

            try:
                await db.execute(sql, bind_params_list)
                await db.commit()
                
                # 流水账增量原子对账扣减
                pipe_deduct = redis_client.pipeline()
                for p in bind_params_list:
                    key = f"metrics:goods:{p['gid']}"
                    pipe_deduct.hincrby(key, "view", -p["view_count"])
                    pipe_deduct.hincrby(key, "favorite", -p["favorite_count"])
                    pipe_deduct.hincrby(key, "comment", -p["comment_count"])
                await pipe_deduct.execute()

                await redis_client.srem(_ACTIVE_GOODS_SET, *[p["gid"] for p in bind_params_list])
                logger.info(f"[Metrics Sync] Successfully completed parameterized flush batch for goods: {[p['gid'] for p in bind_params_list]}")
            except Exception:
                await db.rollback()
                logger.exception("[Metrics Sync] Critical database exception during goods metrics executemany flush")
        finally:
            await MetricsService._release_flush_lock(redis_client, lock_key, token)