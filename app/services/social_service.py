import logging
import time

from fastapi import BackgroundTasks

from app.db import redis
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

logger = logging.getLogger(__name__)
from app.services.order_service import OrderService
from app.services.metrics_service import MetricsService
from app.core import BusinessHTTPException, ResourceHTTPException, settings
from app.models import (
    GoodsStatus,
    FavoriteTargetType,
    Goods,
    ItemType,
    Post,
    PostStatus,
    User,
    UserFavorite,
    UserFollow,
    Attachment,
)


class SocialService:
    """社交服务：关注、收藏、历史记录逻辑。"""

    @staticmethod
    async def toggle_follow(db: AsyncSession, follower_id: int, following_id: int) -> dict[str, Any]:
        if follower_id == following_id:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="不能关注自己",
            )

        following_user = await db.get(User, following_id)
        if not following_user or following_user.is_deleted or not following_user.is_active:
            raise ResourceHTTPException(
                code=settings.USER_GET_FAILED_CODE,
                msg="关注用户不存在或不可用",
            )

        existing_result = await db.execute(
            select(UserFollow).where(
                UserFollow.follower_id == follower_id,
                UserFollow.following_id == following_id,
            )
        )
        existing_follow = existing_result.scalar_one_or_none()

        if existing_follow:
            await db.delete(existing_follow)
            await db.commit()
            return {"following_id": following_id, "is_following": False}

        new_follow = UserFollow(follower_id=follower_id, following_id=following_id)
        db.add(new_follow)

        try:
            await db.flush()
            await db.commit()
        except IntegrityError:
            await db.rollback()
            return {"following_id": following_id, "is_following": True}

        return {"following_id": following_id, "is_following": True}

    @staticmethod
    async def list_followings(db: AsyncSession, user_id: int, offset: int, limit: int) -> dict[str, Any]:
        total_result = await db.execute(
            select(func.count()).select_from(UserFollow).where(UserFollow.follower_id == user_id)
        )
        total = int(total_result.scalar_one())

        follow_rows = await db.execute(
            select(UserFollow)
            .where(UserFollow.follower_id == user_id)
            .order_by(UserFollow.create_time.desc())
            .offset(offset)
            .limit(limit)
        )
        follows = follow_rows.scalars().all()
        following_ids = [item.following_id for item in follows]

        users = []
        if following_ids:
            users_result = await db.execute(
                select(User)
                .where(User.user_id.in_(following_ids), User.is_deleted == False)
                .order_by(User.user_id)
            )
            users = users_result.scalars().all()

        user_map = {user.user_id: user for user in users}
        mutual_result = await db.execute(
            select(UserFollow.follower_id)
            .where(
                UserFollow.following_id == user_id,
                UserFollow.follower_id.in_(following_ids),
            )
        )
        mutual_set = {row[0] for row in mutual_result.all()} if following_ids else set()

        items = []
        for follow in follows:
            target = user_map.get(follow.following_id)
            if not target:
                continue
            items.append(
                {
                    "user": target,
                    "is_mutual": follow.following_id in mutual_set,
                }
            )

        return {
            "total": total,
            "offset": offset,
            "limit": limit,
            "list": items,
        }
    @staticmethod
    async def delete_user_history(
        db: AsyncSession,
        user_id: int,
        payload,  # HistoryDeletePayload
        bg_tasks: BackgroundTasks,
        redis_client,
    ) -> dict[str, Any]:
        """多维聚合清理用户的历史足迹。

        支持 SINGLE（单条删除）、RANGE（时间段删除）、CLEAR_ALL（全量清空）三种模式。
        Redis 为主存储，直接执行原子操作。
        """
        key = f"user:history:{user_id}"
        action = payload.action_type
        deleted_count = 0

        if action == "SINGLE":
            fingerprint = f"{payload.target_type}:{payload.target_id}"
            deleted_count = int((await redis_client.zrem(key, fingerprint)) or 0)

        elif action == "RANGE":
            deleted_count = int((await redis_client.zremrangebyscore(key, payload.start_time, payload.end_time)) or 0)

        elif action == "CLEAR_ALL":
            # 主线程直接清理 Redis 全量数据
            deleted_count = int((await redis_client.zcard(key)) or 0)
            await redis_client.delete(key)

        return {
            "action_type": action,
            "message": "清理意图已成功接收并在后台异步蒸发",
            "deleted_count": deleted_count,
        }

    @staticmethod
    async def list_followers(db: AsyncSession, user_id: int, offset: int, limit: int) -> dict[str, Any]:
        total_result = await db.execute(
            select(func.count()).select_from(UserFollow).where(UserFollow.following_id == user_id)
        )
        total = int(total_result.scalar_one())

        follow_rows = await db.execute(
            select(UserFollow)
            .where(UserFollow.following_id == user_id)
            .order_by(UserFollow.create_time.desc())
            .offset(offset)
            .limit(limit)
        )
        follows = follow_rows.scalars().all()
        follower_ids = [item.follower_id for item in follows]

        users = []
        if follower_ids:
            users_result = await db.execute(
                select(User)
                .where(User.user_id.in_(follower_ids), User.is_deleted == False)
                .order_by(User.user_id)
            )
            users = users_result.scalars().all()

        user_map = {user.user_id: user for user in users}
        mutual_result = await db.execute(
            select(UserFollow.following_id)
            .where(
                UserFollow.follower_id == user_id,
                UserFollow.following_id.in_(follower_ids),
            )
        )
        mutual_set = {row[0] for row in mutual_result.all()} if follower_ids else set()

        items = []
        for follow in follows:
            target = user_map.get(follow.follower_id)
            if not target:
                continue
            items.append(
                {
                    "user": target,
                    "is_mutual": follow.follower_id in mutual_set,
                }
            )

        return {
            "total": total,
            "offset": offset,
            "limit": limit,
            "list": items,
        }

    @staticmethod
    async def toggle_favorite(db: AsyncSession, user_id: int, target_type: str, target_id: int) -> dict[str, Any]:
        try:
            normalized_target = FavoriteTargetType(target_type)
        except ValueError:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="收藏目标类型必须为 POST 或 GOODS",
            )

        if normalized_target == FavoriteTargetType.POST:
            target_obj = await db.get(Post, target_id)
            if not target_obj or target_obj.is_deleted:
                raise ResourceHTTPException(
                    msg="帖子不存在或已被删除",
                    code=settings.DATA_GET_FAILED_CODE,
                )
        else:
            target_obj = await db.get(Goods, target_id)
            if not target_obj or target_obj.is_deleted:
                raise ResourceHTTPException(
                    msg="商品不存在或已被删除",
                    code=settings.DATA_GET_FAILED_CODE,
                )

        existing_result = await db.execute(
            select(UserFavorite).where(
                UserFavorite.user_id == user_id,
                UserFavorite.target_type == normalized_target,
                UserFavorite.target_id == target_id,
            )
        )
        existing_favorite = existing_result.scalar_one_or_none()

        if existing_favorite:
            await db.delete(existing_favorite)
            await db.commit()
            try:
                if normalized_target == FavoriteTargetType.POST:
                    await MetricsService.incr_post_favorite(redis, target_id, delta=-1)
                else:
                    await MetricsService.incr_goods_favorite(redis, target_id, delta=-1)
            except Exception as e:
                logger.warning("Swallowed exception in social_service: %s", e, exc_info=True)
            return {
                "target_type": normalized_target.value,
                "target_id": target_id,
                "is_favorite": False,
            }

        new_favorite = UserFavorite(
            user_id=user_id,
            target_type=normalized_target,
            target_id=target_id,
        )
        db.add(new_favorite)

        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            return {
                "target_type": normalized_target.value,
                "target_id": target_id,
                "is_favorite": True,
            }

        try:
            if normalized_target == FavoriteTargetType.POST:
                await MetricsService.incr_post_favorite(redis, target_id, delta=1)
            else:
                await MetricsService.incr_goods_favorite(redis, target_id, delta=1)
        except Exception as e:
            logger.warning("Swallowed exception in social_service: %s", e, exc_info=True)

        return {
            "target_type": normalized_target.value,
            "target_id": target_id,
            "is_favorite": True,
        }

    @staticmethod
    async def list_favorites(db: AsyncSession, user_id: int, offset: int, limit: int) -> dict[str, Any]:
        total_result = await db.execute(
            select(func.count()).select_from(UserFavorite).where(UserFavorite.user_id == user_id)
        )
        total = int(total_result.scalar_one())

        favorite_rows = await db.execute(
            select(UserFavorite)
            .where(UserFavorite.user_id == user_id)
            .order_by(UserFavorite.create_time.desc())
            .offset(offset)
            .limit(limit)
        )
        favorites = favorite_rows.scalars().all()

        post_ids = [fav.target_id for fav in favorites if fav.target_type == FavoriteTargetType.POST]
        goods_ids = [fav.target_id for fav in favorites if fav.target_type == FavoriteTargetType.GOODS]

        posts_map = {}
        goods_map = {}
        if post_ids:
            posts_result = await db.execute(select(Post).options(selectinload(Post.user)).where(Post.post_id.in_(post_ids)))
            posts_map = {post.post_id: post for post in posts_result.scalars().all()}
        if goods_ids:
            goods_result = await db.execute(select(Goods).options(selectinload(Goods.user)).where(Goods.goods_id.in_(goods_ids)))
            goods_map = {goods.goods_id: goods for goods in goods_result.scalars().all()}

        # =====================================================================
        # 核心对齐：针对收藏夹建立的Anti-N+1 批量头像真实 URL 灌水中心
        # =====================================================================
        avatar_ids = []
        for post in posts_map.values():
            if post.user and getattr(post.user, "avatar_id", None):
                avatar_ids.append(post.user.avatar_id)
        for goods in goods_map.values():
            if hasattr(goods, "user") and goods.user and getattr(goods.user, "avatar_id", None):
                avatar_ids.append(goods.user.avatar_id)

        avatar_url_map = {}
        if avatar_ids:
            attachments_result = await db.execute(
                select(Attachment).where(Attachment.attachment_id.in_(avatar_ids))
            )
            avatar_url_map = {att.attachment_id: att.url for att in attachments_result.scalars().all()}
        # =====================================================================

        # 批量获取接单数
        post_accepters_map = {}
        if post_ids:
            post_accepters_map = await OrderService.get_current_accepters_count_map(db, "POST", post_ids)

        items = []
        for fav in favorites:
            if fav.target_type == FavoriteTargetType.POST:
                post_item = posts_map.get(fav.target_id)
                curr_accepters = post_accepters_map.get(fav.target_id, 0) if post_item else 0
                max_accepters = post_item.max_accepters if post_item else 1
                
                # 提取发帖人的真实头像路径
                user_item = post_item.user if post_item else None
                post_avatar_url = None
                if user_item:
                    p_avatar_id = getattr(user_item, "avatar_id", None)
                    post_avatar_url = avatar_url_map.get(p_avatar_id) if p_avatar_id else None

                items.append(
                    {
                        "target_type": fav.target_type.value,
                        "target_id": fav.target_id,
                        "title": post_item.title if post_item else None,
                        "description": post_item.description if post_item else None,
                        "price": float(post_item.price) if post_item and post_item.price is not None else None,
                        "target_status": post_item.status.value if post_item and post_item.status else None,
                        "is_effective": bool(post_item and not post_item.is_deleted and post_item.status != PostStatus.CLOSED),
                        "is_full": curr_accepters >= max_accepters if post_item else False,
                        "create_time": int(fav.create_time.timestamp() * 1000) if fav.create_time else 0,
                        # 干净对接：杜绝盲读 user.avatar 引起的报错，干净输出
                        "publisher": {
                            "user_name": user_item.user_name if hasattr(user_item, "user_name") else "神秘同学",
                            "avatar": post_avatar_url
                        } if user_item else None,
                    }
                )
            else:
                goods_item = goods_map.get(fav.target_id)
                goods_user = goods_item.user if goods_item and hasattr(goods_item, 'user') else None
                
                # 提取商品店主的真实头像路径
                goods_avatar_url = None
                if goods_user:
                    g_avatar_id = getattr(goods_user, "avatar_id", None)
                    goods_avatar_url = avatar_url_map.get(g_avatar_id) if g_avatar_id else None

                publisher_info = {
                    "user_name": goods_user.user_name if hasattr(goods_user, "user_name") else "神秘同学",
                    "avatar": goods_avatar_url
                } if goods_user else None

                items.append(
                    {
                        "target_type": fav.target_type.value,
                        "target_id": fav.target_id,
                        "title": goods_item.name if goods_item else None,
                        "description": goods_item.description if goods_item else None,
                        "price": float(goods_item.price) if goods_item and goods_item.price is not None else None,
                        "target_status": "SOLD" if goods_item and (goods_item.status == GoodsStatus.SOLD if goods_item and hasattr(goods_item, "status") else False) else "AVAILABLE",
                        "is_effective": bool(goods_item and not goods_item.is_deleted and not (goods_item.status == GoodsStatus.SOLD if goods_item and hasattr(goods_item, "status") else False)),
                        "is_full": (goods_item.status == GoodsStatus.SOLD if goods_item and hasattr(goods_item, "status") else False) if goods_item else False,
                        "create_time": int(fav.create_time.timestamp() * 1000) if fav.create_time else 0,
                        "publisher": publisher_info,
                    }
                )

        return {
            "total": total,
            "offset": offset,
            "limit": limit,
            "list": items,
        }

    @staticmethod
    async def record_history(redis_client: Any, user_id: int, target_type: str, target_id: int) -> None:
        normalized_target = str(target_type).upper()
        if normalized_target not in {FavoriteTargetType.POST.value, FavoriteTargetType.GOODS.value}:
            return

        key = f"user:history:{user_id}"
        fingerprint = f"{normalized_target}:{target_id}"
        score = int(time.time() * 1000)  # 13位毫秒级时间戳
        # 1. 正常射入最新足迹
        await redis_client.zadd(key, {fingerprint: float(score)})
        # 2. 先检查当前总数，防止负数索引越界被 Redis 误判归零
        current_card = await redis_client.zcard(key)
        if current_card > 100:
        # 只有大于100条时，才安全切除冷数据（从0数到倒数第101条）
            await redis_client.zremrangebyrank(key, 0, -101)
        # 3. 滚动刷新整张卡片的30天全局生死大限
        await redis_client.expire(key, settings.HISTORY_TTL_SECONDS)

    @staticmethod
    async def list_history(redis_client: Any, db: AsyncSession, user_id: int, offset: int, limit: int) -> dict[str, Any]:
        key = f"user:history:{user_id}"
        total = await redis_client.zcard(key)
        entries = await redis_client.zrevrange(key, offset, offset + limit - 1, withscores=True)

        parsed = []
        post_ids = []
        goods_ids = []
        for value, score in entries:
            if not isinstance(value, str) or ":" not in value:
                continue
            target_type, target_id_str = value.split(":", 1)
            try:
                target_id = int(target_id_str)
            except ValueError:
                continue
            target_type = target_type.upper()
            if target_type == FavoriteTargetType.POST.value:
                post_ids.append(target_id)
            elif target_type == FavoriteTargetType.GOODS.value:
                goods_ids.append(target_id)
            parsed.append({"target_type": target_type, "target_id": target_id, "view_time": int(score)})

        posts_map = {}
        goods_map = {}
        if post_ids:
            posts_result = await db.execute(select(Post).options(selectinload(Post.user)).where(Post.post_id.in_(post_ids)))
            posts_map = {post.post_id: post for post in posts_result.scalars().all()}
        if goods_ids:
            goods_result = await db.execute(select(Goods).options(selectinload(Goods.user)).where(Goods.goods_id.in_(goods_ids)))
            goods_map = {goods.goods_id: goods for goods in goods_result.scalars().all()}

        # =====================================================================
        # 严格死锁业务：针对 avatar_id 建立的不污染业务逻辑的批量头像灌水中心
        # =====================================================================
        avatar_ids = []
        for post in posts_map.values():
            if post.user and getattr(post.user, "avatar_id", None):
                avatar_ids.append(post.user.avatar_id)
        for goods in goods_map.values():
            if hasattr(goods, "user") and goods.user and getattr(goods.user, "avatar_id", None):
                avatar_ids.append(goods.user.avatar_id)

        avatar_url_map = {}
        if avatar_ids:
            attachments_result = await db.execute(
                select(Attachment).where(Attachment.attachment_id.in_(avatar_ids))
            )
            avatar_url_map = {att.attachment_id: att.url for att in attachments_result.scalars().all()}
        # =====================================================================

        # 批量获取接单数
        post_accepters_map = {}
        if post_ids:
            post_accepters_map = await OrderService.get_current_accepters_count_map(db, "POST", post_ids)

        items = []
        for entry in parsed:
            target_type = entry["target_type"]
            target_id = entry["target_id"]
            view_time = entry["view_time"]
            if target_type == FavoriteTargetType.POST.value:
                post_item = posts_map.get(target_id)
                curr_accepters = post_accepters_map.get(target_id, 0) if post_item else 0
                max_accepters = post_item.max_accepters if post_item else 1
                
                # 提取发帖人头像资产
                user_item = post_item.user if post_item else None
                post_avatar_url = None
                if user_item:
                    p_avatar_id = getattr(user_item, "avatar_id", None)
                    post_avatar_url = avatar_url_map.get(p_avatar_id) if p_avatar_id else None

                items.append(
                    {
                        "target_type": target_type,
                        "target_id": target_id,
                        "title": post_item.title if post_item else None,
                        "description": post_item.description if post_item else None,
                        "price": float(post_item.price) if post_item and post_item.price is not None else None,
                        "target_status": post_item.status.value if post_item and post_item.status else None,
                        "is_effective": bool(post_item and not post_item.is_deleted and post_item.status != PostStatus.CLOSED),
                        "is_full": curr_accepters >= max_accepters if post_item else False,
                        "view_time": view_time,
                        # 清洗转义：无斜杠、无污染对接批量映射
                        "publisher": {
                            "user_name": user_item.user_name if hasattr(user_item, "user_name") else "神秘同学",
                            "avatar": post_avatar_url
                        } if user_item else None,
                    }
                )
            else:
                goods_item = goods_map.get(target_id)
                goods_user = goods_item.user if goods_item and hasattr(goods_item, 'user') else None
                
                # 提取商品商家头像资产
                goods_avatar_url = None
                if goods_user:
                    g_avatar_id = getattr(goods_user, "avatar_id", None)
                    goods_avatar_url = avatar_url_map.get(g_avatar_id) if g_avatar_id else None

                publisher_info = {
                    "user_name": goods_user.user_name if hasattr(goods_user, "user_name") else "神秘同学",
                    "avatar": goods_avatar_url
                } if goods_user else None

                items.append(
                    {
                        "target_type": target_type,
                        "target_id": target_id,
                        "title": goods_item.name if goods_item else None,
                        "description": goods_item.description if goods_item else None,
                        "price": float(goods_item.price) if goods_item and goods_item.price is not None else None,
                        "target_status": "SOLD" if goods_item and (goods_item.status == GoodsStatus.SOLD if goods_item and hasattr(goods_item, "status") else False) else "AVAILABLE",
                        "is_effective": bool(goods_item and not goods_item.is_deleted and not (goods_item.status == GoodsStatus.SOLD if goods_item and hasattr(goods_item, "status") else False)),
                        "is_full": (goods_item.status == GoodsStatus.SOLD if goods_item and hasattr(goods_item, "status") else False) if goods_item else False,
                        "view_time": view_time,
                        "publisher": publisher_info,
                    }
                )

        return {
            "total": total,
            "offset": offset,
            "limit": limit,
            "list": items,
        }
