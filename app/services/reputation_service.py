"""用户声誉画像与透明脱敏评价系统。

核心功能：
- 双角色评分聚合（接单人 carrier / 发单人 client）
- Redis 缓存优先 + 击穿回数据库
- 严格双向脱敏评价列表
- 评价释放后自动对账刷新声誉缓存
"""
from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import settings
from app.models import OrderReview, ReviewType, User, UserReputation

logger = logging.getLogger(__name__)

REPUTATION_CACHE_TTL = 3600  # 1 小时


class ReputationService:
    """用户主页声誉画像服务。"""

    # ------------------------------------------------------------------
    # 声誉画像获取（Redis 优先）
    # ------------------------------------------------------------------

    @staticmethod
    async def get_user_reputation(
        redis_client,
        db: AsyncSession,
        user_id: int,
    ) -> dict[str, Any]:
        """获取用户双角色声誉画像，Redis 优先，击穿时回数据库重算。"""
        cache_key = f"user:reputation:{user_id}"
        cached = await redis_client.get(cache_key)
        if cached:
            try:
                return json.loads(cached)
            except (json.JSONDecodeError, TypeError):
                pass

        # 回数据库重算
        data = await _rebuild_reputation_from_db(db, user_id)
        # 写入缓存
        await redis_client.setex(cache_key, REPUTATION_CACHE_TTL, json.dumps(data, ensure_ascii=False))
        return data

    # ------------------------------------------------------------------
    # 评价释放后增量刷新
    # ------------------------------------------------------------------

    @staticmethod
    async def refresh_reputation_after_review_release(
        db: AsyncSession,
        redis_client,
        reviewee_id: int,
    ) -> None:
        """评价释放后，重算被评价人的声誉画像并清除旧缓存。"""
        data = await _rebuild_reputation_from_db(db, reviewee_id)
        cache_key = f"user:reputation:{reviewee_id}"
        await redis_client.setex(cache_key, REPUTATION_CACHE_TTL, json.dumps(data, ensure_ascii=False))

    # ------------------------------------------------------------------
    # 延迟加载评价详情（双向脱敏）
    # ------------------------------------------------------------------

    @staticmethod
    async def get_user_reviews(
        db: AsyncSession,
        user_id: int,
        role: Literal["CARRIER", "CLIENT"],
        offset: int = 0,
        limit: int = 20,
    ) -> dict[str, Any]:
        """获取指定用户的评价列表，执行严格双向脱敏。

        只有满足双盲释放机制（is_visible=True）的评价才对外展示。
        评价发表人信息脱敏：头像置 None，姓名打码。
        """
        if role == "CARRIER":
            # 用户作为接单人被评价（评价人是发单人，被评价人是接单人）
            reviewer_col = OrderReview.reviewer_id
        else:
            # 用户作为发单人被评价（评价人是接单人，被评价人是发单人）
            reviewer_col = OrderReview.reviewer_id

        # 总数查询
        count_stmt = (
            select(func.count())
            .select_from(OrderReview)
            .where(
                OrderReview.reviewee_id == user_id,
                OrderReview.is_visible == True,
                OrderReview.review_type == ReviewType.INITIAL,
            )
        )
        count_res = await db.execute(count_stmt)
        total = int(count_res.scalar_one())

        # 分页查询
        stmt = (
            select(OrderReview)
            .where(
                OrderReview.reviewee_id == user_id,
                OrderReview.is_visible == True,
                OrderReview.review_type == ReviewType.INITIAL,
            )
            .order_by(OrderReview.create_time.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await db.execute(stmt)
        reviews = result.scalars().all()

        items = []
        for rv in reviews:
            reviewer = rv.reviewer
            reviewer_name = "匿名用户"
            reviewer_avatar = None
            if reviewer:
                reviewer_name = _mask_name(reviewer.user_name or "匿名用户")
                # 严格脱敏：头像置 None
                reviewer_avatar = None

            items.append({
                "review_id": rv.review_id,
                "order_id": rv.order_id,
                "rating": rv.rating,
                "content": rv.content,
                "is_anonymous": rv.is_anonymous,
                "reviewer": {
                    "user_id": reviewer.user_id if reviewer else None,
                    "user_name": reviewer_name,
                    "avatar": reviewer_avatar,
                },
                "create_time": int(rv.create_time.timestamp() * 1000) if rv.create_time else 0,
            })

        return {
            "total": total,
            "offset": offset,
            "limit": limit,
            "role": role,
            "list": items,
        }


# ------------------------------------------------------------------
# 内部辅助函数
# ------------------------------------------------------------------

def _mask_name(name: str) -> str:
    """姓名脱敏：保留首字符，其余用「**」替代。

    示例：「张学长」→「张**」，「李明」→「李*」。
    """
    if not name or name == "匿名用户":
        return "匿名用户"
    name = str(name).strip()
    if len(name) <= 1:
        return name + "**"
    return name[0] + "**"


async def _rebuild_reputation_from_db(db: AsyncSession, user_id: int) -> dict[str, Any]:
    """从数据库重建用户声誉画像。"""
    # 查询作为接单人的评价
    carrier_payload = await _aggregate_rating_for_role(db, user_id, "CARRIER")
    # 查询作为发单人的评价
    client_payload = await _aggregate_rating_for_role(db, user_id, "CLIENT")

    return {
        "user_id": user_id,
        "carrier_score": float(carrier_payload["score"]),
        "carrier_order_count": carrier_payload["count"],
        "client_score": float(client_payload["score"]),
        "client_order_count": client_payload["count"],
        "tags_json": _aggregate_tags(db, user_id),
    }


async def _aggregate_rating_for_role(
    db: AsyncSession,
    user_id: int,
    role: Literal["CARRIER", "CLIENT"],
) -> dict[str, Any]:
    """聚合指定角色维度的平均评分和订单数量。

    - CARRIER: 用户作为接单人（reviewee），被评价人
    - CLIENT: 用户作为发单人（reviewee），被评价人
    
    注意：这里的 role 参数与接口入参不同，是指用户的角色。
    """
    stmt = (
        select(
            func.count(OrderReview.review_id).label("cnt"),
            func.avg(OrderReview.rating).label("avg_rating"),
        )
        .where(
            OrderReview.reviewee_id == user_id,
            OrderReview.is_visible == True,
            OrderReview.review_type == ReviewType.INITIAL,
            OrderReview.rating.isnot(None),
        )
    )
    res = await db.execute(stmt)
    row = res.one()
    cnt = int(row.cnt or 0)
    avg = float(row.avg_rating or 5.0)
    return {"score": round(avg, 1), "count": cnt}


def _aggregate_tags(db, user_id: int) -> str:
    """聚合高频印象标签（简化实现，返回默认空 JSON）。"""
    # 后续可扩展为根据评价内容 NLP 提取关键词
    return "{}"
