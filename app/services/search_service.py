"""全局搜索服务层。"""

from datetime import timedelta
from typing import Any

from sqlalchemy import String, and_, cast, func, literal, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import get_now_naive
from app.models import Attachment, Direction, Goods, GoodsMetrics, GoodsStatus, Post, PostMetrics, PostStatus, User
from app.schemas import GlobalSearchItem, SearchSort, SearchTab, SearchTime, UserRead
from app.services import MetricsService


class SearchService:
    """帖子与商品聚合搜索服务。"""

    _HIT_TIPS_LABEL_MAP = {
        "pickup_address": "取件地址",
        "delivery_address": "收件地址",
        "remark": "备注信息",
        "brand": "品牌成色",
    }

    @staticmethod
    def _time_cutoff(time_range: SearchTime):
        """根据时间范围计算北京时间截断点。"""
        if time_range == SearchTime.ONE_DAY:
            return get_now_naive() - timedelta(days=1)
        if time_range == SearchTime.SEVEN_DAYS:
            return get_now_naive() - timedelta(days=7)
        if time_range == SearchTime.HALF_YEAR:
            return get_now_naive() - timedelta(days=180)
        return None

    @staticmethod
    def _post_keyword_conditions(tokens: list[str]) -> list:
        """Post 多词根条件：每个词根在标题、描述、JSON Value 中任一命中。"""
        return [
            or_(
                Post.title.like(f"%{token}%"),
                Post.description.like(f"%{token}%"),
                func.json_search(Post.template_data, "one", f"%{token}%").isnot(None),
            )
            for token in tokens
        ]

    @staticmethod
    def _goods_keyword_conditions(tokens: list[str]) -> list:
        """Goods 多词根条件：每个词根在名称、描述、JSON Value 中任一命中。"""
        return [
            or_(
                Goods.name.like(f"%{token}%"),
                Goods.description.like(f"%{token}%"),
                func.json_search(Goods.template_data, "one", f"%{token}%").isnot(None),
            )
            for token in tokens
        ]

    @staticmethod
    def _post_select(direction: Direction, tokens: list[str], cutoff, exclude_publisher_ids: list[int] | None):
        """构造指定方向的帖子搜索查询。"""
        conditions = [
            Post.is_deleted == False,
            Post.status.in_([PostStatus.OPEN, PostStatus.SUSPENDED]),
            Post.direction == direction,
        ]
        token_conditions = SearchService._post_keyword_conditions(tokens)
        if token_conditions:
            conditions.append(and_(*token_conditions))
        if cutoff is not None:
            conditions.append(Post.create_time >= cutoff)
        if exclude_publisher_ids:
            conditions.append(Post.publisher_id.notin_(exclude_publisher_ids))

        return (
            select(
                Post.post_id.label("id"),
                literal("BUY_POST" if direction == Direction.BUY else "SELL_POST").label("item_type"),
                Post.title.label("title"),
                Post.description.label("description"),
                Post.price.label("price"),
                cast(Post.status, String).label("status"),
                Post.create_time.label("create_time"),
                Post.template_data.label("template_data"),
                func.coalesce(PostMetrics.view_count, 0).label("view_count"),
                func.coalesce(PostMetrics.favorite_count, 0).label("favorite_count"),
                func.coalesce(PostMetrics.comment_count, 0).label("comment_count"),
                Post.publisher_id.label("publisher_id"),
            )
            .select_from(Post)
            .outerjoin(PostMetrics, PostMetrics.post_id == Post.post_id)
            .where(and_(*conditions))
        )

    @staticmethod
    def _goods_select(tokens: list[str], cutoff, exclude_publisher_ids: list[int] | None):
        """构造商品搜索查询。"""
        conditions = [
            Goods.is_deleted == False,
            Goods.status == GoodsStatus.ON_SALE,
        ]
        token_conditions = SearchService._goods_keyword_conditions(tokens)
        if token_conditions:
            conditions.append(and_(*token_conditions))
        if cutoff is not None:
            conditions.append(Goods.create_time >= cutoff)
        if exclude_publisher_ids:
            conditions.append(Goods.publisher_id.notin_(exclude_publisher_ids))

        return (
            select(
                Goods.goods_id.label("id"),
                literal("GOODS").label("item_type"),
                Goods.name.label("title"),
                Goods.description.label("description"),
                Goods.price.label("price"),
                cast(Goods.status, String).label("status"),
                Goods.create_time.label("create_time"),
                Goods.template_data.label("template_data"),
                func.coalesce(GoodsMetrics.view_count, 0).label("view_count"),
                func.coalesce(GoodsMetrics.favorite_count, 0).label("favorite_count"),
                func.coalesce(GoodsMetrics.comment_count, 0).label("comment_count"),
                Goods.publisher_id.label("publisher_id"),
            )
            .select_from(Goods)
            .outerjoin(GoodsMetrics, GoodsMetrics.goods_id == Goods.goods_id)
            .where(and_(*conditions))
        )

    @staticmethod
    def _build_base_query(
        tokens: list[str],
        tab: SearchTab,
        time_range: SearchTime,
        exclude_publisher_ids: list[int] | None,
    ):
        """根据 Tab 构造分流或合流基础查询。"""
        cutoff = SearchService._time_cutoff(time_range)
        if tab == SearchTab.BUY_POST:
            return SearchService._post_select(Direction.BUY, tokens, cutoff, exclude_publisher_ids).subquery()
        if tab == SearchTab.SELL_POST:
            return SearchService._post_select(Direction.SELL, tokens, cutoff, exclude_publisher_ids).subquery()
        if tab == SearchTab.GOODS:
            return SearchService._goods_select(tokens, cutoff, exclude_publisher_ids).subquery()

        return union_all(
            SearchService._post_select(Direction.BUY, tokens, cutoff, exclude_publisher_ids),
            SearchService._post_select(Direction.SELL, tokens, cutoff, exclude_publisher_ids),
            SearchService._goods_select(tokens, cutoff, exclude_publisher_ids),
        ).subquery()

    @staticmethod
    def _order_columns(base_query, sort_by: SearchSort):
        """返回搜索排序列。"""
        if sort_by == SearchSort.FAVORITE:
            return [base_query.c.favorite_count.desc(), base_query.c.create_time.desc()]
        if sort_by == SearchSort.COMMENT:
            return [base_query.c.comment_count.desc(), base_query.c.create_time.desc()]
        if sort_by == SearchSort.VIEW:
            return [base_query.c.view_count.desc(), base_query.c.create_time.desc()]
        return [base_query.c.create_time.desc()]

    @staticmethod
    def _build_hit_tips(row: dict[str, Any], tokens: list[str]) -> str | None:
        """构造 JSON 命中提示语。"""
        if not tokens:
            return None

        primary_token = tokens[0]
        title_str = row.get("title") or ""
        desc_str = row.get("description") or ""
        if (primary_token in title_str) or (primary_token in desc_str):
            return None

        template_data = row.get("template_data") or {}
        if not isinstance(template_data, dict):
            return None

        for key, value in template_data.items():
            if primary_token in str(value):
                field_label = SearchService._HIT_TIPS_LABEL_MAP.get(key, "自定义表单")
                return f"在【{field_label}】中匹配到: {value}"
        return None

    @staticmethod
    async def search_global(
        db: AsyncSession,
        redis_client,
        *,
        tokens: list[str],
        tab: SearchTab,
        sort_by: SearchSort,
        time_range: SearchTime,
        page: int,
        page_size: int,
        exclude_publisher_ids: list[int] | None = None,
    ) -> tuple[list[GlobalSearchItem], int]:
        """执行全局聚合搜索。"""
        base_query = SearchService._build_base_query(
            tokens,
            tab,
            time_range,
            exclude_publisher_ids,
        )

        total_result = await db.execute(select(func.count()).select_from(base_query))
        total = int(total_result.scalar_one() or 0)

        result = await db.execute(
            select(base_query)
            .order_by(*SearchService._order_columns(base_query, sort_by))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = [dict(row) for row in result.mappings().all()]
        await SearchService._hydrate_search_metrics(db, redis_client, rows)
        publisher_map = await SearchService._build_publisher_map(db, rows)

        items = []
        for row in rows:
            current_tips = SearchService._build_hit_tips(row, tokens)
            items.append(
                GlobalSearchItem(
                    id=int(row["id"]),
                    item_type=str(row["item_type"]),
                    title=row["title"],
                    description=row["description"],
                    price=float(row["price"]) if row["price"] is not None else None,
                    status=row["status"].value if getattr(row["status"], "value", None) else str(row["status"]),
                    create_time=row["create_time"],
                    template_data=row.get("template_data") or {},
                    hit_tips=current_tips,
                    view_count=int(row.get("view_count") or 0),
                    favorite_count=int(row.get("favorite_count") or 0),
                    comment_count=int(row.get("comment_count") or 0),
                    publisher=publisher_map.get(int(row["publisher_id"])),
                )
            )

        return items, total

    @staticmethod
    async def _hydrate_search_metrics(db: AsyncSession, redis_client, rows: list[dict[str, Any]]) -> None:
        """按实体类型批量灌入实时计数器。"""
        post_items = []
        post_ids = []
        goods_items = []
        goods_ids = []
        for row in rows:
            if row["item_type"] in {"BUY_POST", "SELL_POST"}:
                post_items.append(row)
                post_ids.append(int(row["id"]))
            elif row["item_type"] == "GOODS":
                row["target_id"] = int(row["id"])
                goods_items.append(row)
                goods_ids.append(int(row["id"]))

        if post_items:
            await MetricsService.hydrate_posts_with_metrics(db, redis_client, post_items, post_ids, id_key="id")
        if goods_items:
            await MetricsService.hydrate_goods_with_metrics(db, redis_client, goods_items, goods_ids)

    @staticmethod
    async def _build_publisher_map(db: AsyncSession, rows: list[dict[str, Any]]) -> dict[int, UserRead]:
        """批量构造发布者脱敏简影。"""
        publisher_ids = sorted({int(row["publisher_id"]) for row in rows if row.get("publisher_id") is not None})
        if not publisher_ids:
            return {}

        result = await db.execute(
            select(User).where(
                User.user_id.in_(publisher_ids),
                User.is_deleted == False,
                User.is_active == True,
            )
        )
        users = list(result.scalars().all())
        avatar_ids = [user.avatar_id for user in users if getattr(user, "avatar_id", None)]
        avatar_url_map = {}
        if avatar_ids:
            avatar_result = await db.execute(
                select(Attachment.attachment_id, Attachment.url).where(
                    Attachment.attachment_id.in_(avatar_ids),
                    Attachment.is_deleted == False,
                )
            )
            avatar_url_map = {attachment_id: url for attachment_id, url in avatar_result.all()}

        publisher_map: dict[int, UserRead] = {}
        for user in users:
            avatar_url = None
            if getattr(user, "avatar_id", None):
                avatar_url = avatar_url_map.get(user.avatar_id)
            data = UserRead.model_validate(user).model_dump()
            data["avatar"] = avatar_url
            publisher_map[int(user.user_id)] = UserRead.model_validate(data)
        return publisher_map
