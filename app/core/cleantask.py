import asyncio
import logging
import time

logger = logging.getLogger(__name__)

async def _process_order_auto_confirm(session_factory, order_id: int) -> None:
    from app.services import OrderService

    async with session_factory() as db:
        await OrderService.auto_confirm_overdue_order_by_id(db, order_id)


async def _process_double_blind_release(session_factory, order_id: int) -> None:
    from app.services import OrderReviewService

    async with session_factory() as db:
        await OrderReviewService.release_double_blind_reviews_for_order(db, order_id)


async def process_delayed_queues_once(session_factory=None) -> bool:
    """扫描一次所有延迟队列，只消费当前已到期的队头任务。"""

    import app.db as app_db
    from app.core.delay_queue import ORDER_AUTO_CONFIRM_QUEUE_KEY, REVIEW_DOUBLE_BLIND_QUEUE_KEY

    active_session_factory = session_factory or app_db.AsyncSessionLocal

    queue_specs = (
        (ORDER_AUTO_CONFIRM_QUEUE_KEY, _process_order_auto_confirm),
        (REVIEW_DOUBLE_BLIND_QUEUE_KEY, _process_double_blind_release),
    )
    now_ts = time.time()
    did_work = False

    for queue_key, handler in queue_specs:
        due_items = await app_db.redis.zrangebyscore(queue_key, min=0, max=now_ts, start=0, num=1)
        if not due_items:
            continue

        member = due_items[0]
        removed = await app_db.redis.zrem(queue_key, member)
        if removed <= 0:
            continue

        did_work = True
        order_id = int(member)
        try:
            await handler(active_session_factory, order_id)
        except Exception as exc:
            logger.exception("Delayed queue worker failed for %s/%s: %s", queue_key, order_id, exc)

    return did_work


async def watch_delayed_queues_task():
    """常驻监听 Redis ZSET 延迟队列。"""

    logger.info("Delayed queue worker started")
    try:
        while True:
            did_work = await process_delayed_queues_once()
            if not did_work:
                await asyncio.sleep(5)
    except asyncio.CancelledError:
        logger.info("Delayed queue worker cancelled")
        raise


def create_cleanup_task():
    """向后兼容的守护任务创建入口。"""

    return asyncio.create_task(watch_delayed_queues_task())