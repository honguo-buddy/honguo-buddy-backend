"""Redis 原子操作辅助工具。"""

from __future__ import annotations

import asyncio
import uuid


async def set_if_absent(redis_client, key: str, value: str, *, ex: int) -> bool:
    """原子写入带过期时间的不存在键。"""
    try:
        acquired = await redis_client.set(key, value, ex=ex, nx=True)
        return bool(acquired)
    except (AttributeError, TypeError):
        existing = await redis_client.get(key)
        if existing is not None:
            return False
        await redis_client.set(key, value, ex=ex)
        return True


async def acquire_redis_lock(
    redis_client,
    lock_key: str,
    *,
    ttl_seconds: int = 5,
    wait_timeout_seconds: float = 1.0,
    retry_interval_seconds: float = 0.01,
) -> str | None:
    """获取 Redis 分布式短锁。"""
    token = str(uuid.uuid4())
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(wait_timeout_seconds, 0.0)

    while True:
        acquired = await set_if_absent(
            redis_client,
            lock_key,
            token,
            ex=ttl_seconds,
        )
        if acquired:
            return token

        if wait_timeout_seconds <= 0 or loop.time() >= deadline:
            return None

        await asyncio.sleep(retry_interval_seconds)


async def release_redis_lock(redis_client, lock_key: str, token: str | None) -> None:
    """按 token 安全释放 Redis 分布式短锁。"""
    if not token:
        return

    script = """
    if redis.call("GET", KEYS[1]) == ARGV[1] then
        return redis.call("DEL", KEYS[1])
    else
        return 0
    end
    """
    try:
        await redis_client.eval(script, 1, lock_key, token)
    except (AttributeError, TypeError):
        current = await redis_client.get(lock_key)
        if current == token:
            await redis_client.delete(lock_key)
