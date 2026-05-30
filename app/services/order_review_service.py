"""订单评价服务：支持双盲首评、追评、回评与可见性解封。"""

from __future__ import annotations

import time
from datetime import timedelta

import logging

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import BusinessHTTPException, ResourceHTTPException, get_now_naive, settings
from app.core.delay_queue import REVIEW_DOUBLE_BLIND_QUEUE_KEY, enqueue_delayed_task
from app.models import AttachmentTargetType, Order, OrderReview, ReviewType, OrderStatus
from app.services.attachment_service import AttachmentService

logger = logging.getLogger(__name__)


class OrderReviewService:
    """订单评价业务服务。"""

    @staticmethod
    def _normalize_review_type(review_type: str) -> ReviewType:
        normalized = str(review_type or "").strip().upper()
        try:
            return ReviewType[normalized]
        except KeyError as exc:
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg=f"不支持的评价类型: {review_type}") from exc

    @staticmethod
    async def _get_order(db: AsyncSession, order_id: int) -> Order:
        stmt = select(Order).where(Order.order_id == order_id, Order.is_deleted == False)
        res = await db.execute(stmt)
        order = res.scalars().first()
        if not order:
            raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="订单不存在")
        return order

    @staticmethod
    async def _get_existing_initial_review(db: AsyncSession, order_id: int, reviewer_id: int) -> OrderReview | None:
        stmt = select(OrderReview).where(
            OrderReview.order_id == order_id,
            OrderReview.reviewer_id == reviewer_id,
            OrderReview.review_type == ReviewType.INITIAL,
        )
        res = await db.execute(stmt)
        return res.scalars().first()

    @staticmethod
    async def _release_reviews_if_ready(db: AsyncSession, order: Order) -> bool:
        """当双盲条件满足时，将订单下所有评价一次性公开。"""

        initial_count_stmt = select(func.count()).select_from(OrderReview).where(
            OrderReview.order_id == order.order_id,
            OrderReview.review_type == ReviewType.INITIAL,
        )
        initial_count_res = await db.execute(initial_count_stmt)
        initial_count = int(initial_count_res.scalar_one() or 0)

        should_release = initial_count >= 2
        if not should_release and order.status == OrderStatus.COMPLETED:
            completed_at = order.update_time or order.create_time
            if completed_at is not None and (get_now_naive() - completed_at) >= timedelta(days=settings.REVIEW_DOUBLE_BLIND_DAYS):
                should_release = True

        if not should_release:
            return False

        reviews_stmt = select(OrderReview).where(OrderReview.order_id == order.order_id)
        reviews_res = await db.execute(reviews_stmt)
        for review in reviews_res.scalars().all():
            review.is_visible = True
        return True

    @staticmethod
    async def create_review(
        db: AsyncSession,
        *,
        current_user_id: int,
        order_id: int,
        reviewee_id: int,
        review_type: str,
        parent_id: int | None = None,
        rating: int | None = None,
        content: str | None = None,
        is_anonymous: bool = False,
        attachment_ids: list[int] | None = None,
        redis_client=None,
    ) -> OrderReview:
        """创建订单评价并在满足条件时自动公开。"""

        order = await OrderReviewService._get_order(db, order_id)
        if order.status != OrderStatus.COMPLETED:
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="只有已完成订单才允许评价")

        if current_user_id not in {order.buyer_id, order.seller_id}:
            raise BusinessHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="仅订单相关方可评价")

        expected_reviewee_id = order.seller_id if current_user_id == order.buyer_id else order.buyer_id
        if int(reviewee_id) != int(expected_reviewee_id):
            raise BusinessHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="被评价人必须是订单另一方")

        review_type_enum = OrderReviewService._normalize_review_type(review_type)
        if review_type_enum == ReviewType.INITIAL:
            if rating is None:
                raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="首评必须填写评分")
            if parent_id is not None:
                raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="首评不允许关联父评价")
        else:
            if rating is not None:
                raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="追评/回评不允许填写评分")
            if parent_id is None:
                raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="追评/回评必须关联父评价")

        if review_type_enum == ReviewType.INITIAL:
            existed = await OrderReviewService._get_existing_initial_review(db, order_id, current_user_id)
            if existed:
                raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="同一订单下同一用户只能发布一次首评")

        if parent_id is not None:
            parent_stmt = select(OrderReview).where(
                OrderReview.review_id == parent_id,
                OrderReview.order_id == order_id,
            )
            parent_res = await db.execute(parent_stmt)
            parent_review = parent_res.scalars().first()
            if not parent_review:
                raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="父评价不存在")

        review = OrderReview(
            order_id=order_id,
            reviewer_id=current_user_id,
            reviewee_id=reviewee_id,
            review_type=review_type_enum,
            parent_id=parent_id,
            rating=rating if review_type_enum == ReviewType.INITIAL else None,
            content=content,
            is_anonymous=is_anonymous,
            is_visible=False,
        )
        db.add(review)
        await db.flush()

        if attachment_ids:
            await AttachmentService.bind_attachments_to_target(
                db=db,
                attachment_ids=attachment_ids,
                target_type=AttachmentTargetType.ORDERREVIEW.value,
                target_id=review.review_id,
                creator_id=current_user_id,
            )

        if review_type_enum == ReviewType.INITIAL:
            initial_count_stmt = select(func.count()).select_from(OrderReview).where(
                OrderReview.order_id == order_id,
                OrderReview.review_type == ReviewType.INITIAL,
            )
            initial_count_res = await db.execute(initial_count_stmt)
            initial_count = int(initial_count_res.scalar_one() or 0)
            if initial_count == 1 and redis_client is not None:
                try:
                    delayed_score = time.time() + settings.REVIEW_DOUBLE_BLIND_DAYS * 86400
                    await enqueue_delayed_task(redis_client, REVIEW_DOUBLE_BLIND_QUEUE_KEY, order_id, delayed_score)
                except Exception as exc:
                    logger.error(f"Failed to enqueue review delayed task for order {order_id}: {exc}")

        await OrderReviewService._release_reviews_if_ready(db, order)
        await db.commit()
        await db.refresh(review)
        return review

    @staticmethod
    async def release_double_blind_reviews_for_order(db: AsyncSession, order_id: int) -> bool:
        """针对单个订单执行双盲评价解封。"""

        order = await OrderReviewService._get_order(db, order_id)
        released = await OrderReviewService._release_reviews_if_ready(db, order)
        if released:
            await db.commit()
        return released

    @staticmethod
    async def list_reviews_for_order(
        db: AsyncSession,
        *,
        order_id: int,
        current_user_id: int | None = None,
        is_admin: bool = False,
    ) -> list[OrderReview]:
        order = await OrderReviewService._get_order(db, order_id)
        if not is_admin and current_user_id not in {None, order.buyer_id, order.seller_id}:
            raise BusinessHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="仅订单相关方可查看评价")

        stmt = select(OrderReview).where(OrderReview.order_id == order_id)
        if not is_admin:
            stmt = stmt.where(OrderReview.is_visible == True)
        stmt = stmt.order_by(OrderReview.create_time.asc(), OrderReview.review_id.asc())
        res = await db.execute(stmt)
        return res.scalars().all()

    @staticmethod
    async def auto_release_expired_double_blind_reviews(db: AsyncSession) -> int:
        """定时任务存根：解封超出双盲期的订单评价。"""

        threshold = get_now_naive() - timedelta(days=settings.REVIEW_DOUBLE_BLIND_DAYS)
        stmt = select(Order).where(
            Order.status == OrderStatus.COMPLETED,
            Order.update_time <= threshold,
            Order.is_deleted == False,
        )
        res = await db.execute(stmt)
        released = 0
        for order in res.scalars().all():
            if await OrderReviewService._release_reviews_if_ready(db, order):
                released += 1
        if released:
            await db.commit()
        return released
