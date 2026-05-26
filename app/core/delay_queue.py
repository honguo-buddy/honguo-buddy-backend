"""Redis ZSET 延迟任务队列基础设施。"""

from __future__ import annotations

from typing import Any

ORDER_AUTO_CONFIRM_QUEUE_KEY = "queue:order_auto_confirm"
REVIEW_DOUBLE_BLIND_QUEUE_KEY = "queue:review_double_blind"


async def enqueue_delayed_task(redis_client: Any, queue_key: str, member: int | str, score: float) -> None:
    """将单个任务投递到指定 ZSET 延迟队列。"""

    await redis_client.zadd(queue_key, {str(member): float(score)})