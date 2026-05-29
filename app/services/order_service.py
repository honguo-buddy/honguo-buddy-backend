"""订单服务：支持多态关联（Post / Goods）和状态机管理。"""

import time
from datetime import timedelta
from typing import Optional

import logging

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from app.core import BusinessHTTPException, ResourceHTTPException, get_now, get_now_naive, parse_datetime_to_beijing_naive, settings
from app.core.delay_queue import ORDER_AUTO_CONFIRM_QUEUE_KEY, enqueue_delayed_task
from app.models import CreditLog, Direction, Goods, ItemType, Order, OrderStatus, OrderTriggerType, Post, PostStatus, User

logger = logging.getLogger(__name__)


class OrderService:

    @staticmethod
    def _normalize_item_type(item_type: str) -> ItemType:
        normalized = str(item_type).upper().strip()
        if normalized == "POSTS":
            normalized = "POST"
        try:
            return ItemType[normalized]
        except KeyError as exc:
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg=f"不支持的 item_type: {item_type}") from exc

    @staticmethod
    def _normalize_status_filter(status: Optional[str]) -> Optional[OrderStatus]:
        if not status:
            return None
        normalized = str(status).upper()
        if normalized == "ACCEPTED":
            normalized = OrderStatus.ONGOING.name
        if normalized == "REJECTED":
            normalized = OrderStatus.REJECTED.name
        if normalized == "ALL":
            return None
        try:
            return OrderStatus[normalized]
        except KeyError as exc:
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg=f"不支持的订单状态: {status}") from exc

    @staticmethod
    def _display_status(status: OrderStatus) -> str:
        return status.value

    @staticmethod
    def _serialize_order(order: Order) -> dict:
        return {
            "order_id": order.order_id,
            "item_type": order.item_type.value if getattr(order.item_type, "value", None) else str(order.item_type),
            "item_id": order.item_id,
            "status": OrderService._display_status(order.status),
            "buyer_id": order.buyer_id,
            "seller_id": order.seller_id,
            "initiator_id": order.initiator_id,
            "trigger_type": order.trigger_type.value if getattr(order.trigger_type, "value", None) else str(order.trigger_type),
            "accepted_time": order.accepted_time.isoformat() if order.accepted_time else None,
            "create_time": order.create_time.isoformat() if order.create_time else None,
            "update_time": order.update_time.isoformat() if order.update_time else None,
            "meta_data": order.meta_data,
            "buyer": order.buyer,
            "seller": order.seller,
            "curr_accepters": getattr(order, "curr_accepters", None),
        }

    @staticmethod
    async def _get_post_for_update(db: AsyncSession, post_id: int) -> Post:
        stmt = select(Post).where(Post.post_id == post_id, Post.is_deleted == False).with_for_update()
        res = await db.execute(stmt)
        post = res.scalars().first()
        if not post:
            raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="帖子不存在")
        return post

    @staticmethod
    async def _get_order_for_update(db: AsyncSession, order_id: int) -> Order:
        stmt = (
            select(Order)
            .options(selectinload(Order.buyer), selectinload(Order.seller))
            .where(Order.order_id == order_id, Order.is_deleted == False)
            .with_for_update()
        )
        res = await db.execute(stmt)
        order = res.scalars().first()
        if not order:
            raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="订单不存在")
        return order

    @staticmethod
    async def _get_order_readonly(db: AsyncSession, order_id: int) -> Order:
        stmt = (
            select(Order)
            .options(selectinload(Order.buyer), selectinload(Order.seller))
            .where(Order.order_id == order_id, Order.is_deleted == False)
        )
        res = await db.execute(stmt)
        order = res.scalars().first()
        if not order:
            raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="订单不存在")
        return order

    @staticmethod
    async def _get_user_for_update(db: AsyncSession, user_id: int) -> User:
        stmt = select(User).where(User.user_id == user_id, User.is_deleted == False).with_for_update()
        res = await db.execute(stmt)
        user = res.scalars().first()
        if not user:
            raise ResourceHTTPException(code=settings.USER_GET_FAILED_CODE, msg="用户不存在")
        return user

    @staticmethod
    def _resolve_order_participants(item_direction: str, publisher_id: int, initiator_id: int) -> tuple[int, int]:
        direction = str(item_direction or "SELL").upper().strip()
        if direction == "BUY":
            return int(publisher_id), int(initiator_id)
        return int(initiator_id), int(publisher_id)

    @staticmethod
    def _resolve_trigger_type_for_post(item_direction: str) -> OrderTriggerType:
        direction = str(item_direction or "SELL").upper().strip()
        if direction == "BUY":
            return OrderTriggerType.APPLICATION
        return OrderTriggerType.COLLECTIVE

    @staticmethod
    def _apply_completion_side_effects(order: Order, post: Post | None, goods: Goods | None) -> None:
        if post is not None:
            post.status = PostStatus.CLOSED
        if goods is not None:
            goods.is_sold = True
            td = goods.template_data or {}
            if isinstance(td, dict) and td.get("locked"):
                td = dict(td)
                td.pop("locked", None)
                goods.template_data = td

    @staticmethod
    def _post_accept_cooldown_key(user_id: int, post_id: int) -> str:
        return f"lock:cooldown:user:{user_id}:post:{post_id}"

    @staticmethod
    def _post_cancel_count_key(user_id: int, post_id: int, today: str | None = None) -> str:
        if today is None:
            today = get_now_naive().date().isoformat()
        return f"lock:cancel_count:user:{user_id}:post:{post_id}:{today}"

    @staticmethod
    async def _raise_post_accept_rate_limit(redis_client, user_id: int, post_id: int) -> None:
        cooldown_key = OrderService._post_accept_cooldown_key(user_id, post_id)
        if await redis_client.get(cooldown_key):
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg=f"你刚刚取消了该帖子的申请，请冷静 {settings.ORDER_ACCEPT_COOLDOWN_SECONDS // 60} 分钟后再试",
            )
        count_key = OrderService._post_cancel_count_key(user_id, post_id)
        count = await redis_client.get(count_key)
        if count is not None and int(count) >= settings.ORDER_ACCEPT_CANCEL_DAILY_LIMIT:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="今日取消次数已达上限，无法继续接该帖子",
            )

    @staticmethod
    async def _record_post_cancel(redis_client, user_id: int, post_id: int) -> None:
        cooldown_key = OrderService._post_accept_cooldown_key(user_id, post_id)
        count_key = OrderService._post_cancel_count_key(user_id, post_id)

        await redis_client.set(cooldown_key, "1", ex=settings.ORDER_ACCEPT_COOLDOWN_SECONDS)
        cancel_count = await redis_client.incr(count_key)
        if cancel_count == 1:
            await redis_client.expire(count_key, 86400)

    @staticmethod
    async def _load_goods_for_update(db: AsyncSession, goods_id: int) -> Goods | None:
        stmt = select(Goods).where(Goods.goods_id == goods_id, Goods.is_deleted == False).with_for_update()
        res = await db.execute(stmt)
        return res.scalars().first()

    @staticmethod
    def _post_accept_valid_statuses(direction: Direction) -> list[OrderStatus]:
        if direction == Direction.BUY:
            return [OrderStatus.ONGOING, OrderStatus.CONFIRMED, OrderStatus.COMPLETED]
        return [OrderStatus.PENDING, OrderStatus.ONGOING, OrderStatus.CONFIRMED, OrderStatus.COMPLETED]

    @staticmethod
    async def get_current_accepters_count(db: AsyncSession, item_type: str, item_id: int) -> int:
        """获取指定项目（Post/Goods）当前有效接单/参与人数。
        
        统一的接单数计算逻辑，避免 DRY 违反。
        
        Args:
            db: 数据库会话
            item_type: 项目类型（"POST" 或 "GOODS"，大小写不敏感）
            item_id: 项目 ID
            
        Returns:
            有效接单人数（不包括已取消的订单）
        """
        item_type_enum = OrderService._normalize_item_type(item_type)
        if item_type_enum == ItemType.POST:
            post_stmt = select(Post.direction).where(Post.post_id == item_id, Post.is_deleted == False)
            post_res = await db.execute(post_stmt)
            direction = post_res.scalar_one_or_none()
            if direction is None:
                return 0
            valid_statuses = OrderService._post_accept_valid_statuses(direction)
        else:
            valid_statuses = [
                OrderStatus.PENDING,
                OrderStatus.ONGOING,
                OrderStatus.CONFIRMED,
                OrderStatus.COMPLETED,
            ]

        cnt_stmt = select(func.count()).select_from(Order).where(
            Order.item_type == item_type_enum,
            Order.item_id == item_id,
            Order.status.in_(valid_statuses),
            Order.is_deleted == False,
        )
        cnt_res = await db.execute(cnt_stmt)
        return int(cnt_res.scalar_one() or 0)

    @staticmethod
    async def get_current_accepters_count_map(
        db: AsyncSession,
        item_type: str,
        item_ids: list[int],
    ) -> dict[int, int]:
        """批量获取多个项目的接单数，避免在列表页逐条查询。"""
        unique_item_ids = [int(item_id) for item_id in dict.fromkeys(item_ids) if item_id is not None]
        if not unique_item_ids:
            return {}

        item_type_enum = OrderService._normalize_item_type(item_type)
        if item_type_enum == ItemType.POST:
            post_stmt = select(Post.post_id, Post.direction).where(Post.post_id.in_(unique_item_ids), Post.is_deleted == False)
            post_res = await db.execute(post_stmt)
            direction_map = {int(post_id): direction for post_id, direction in post_res.all()}

            results: dict[int, int] = {}
            buy_ids = [post_id for post_id, direction in direction_map.items() if direction == Direction.BUY]
            sell_ids = [post_id for post_id, direction in direction_map.items() if direction != Direction.BUY]

            if sell_ids:
                sell_stmt = (
                    select(Order.item_id, func.count())
                    .where(
                        Order.item_type == item_type_enum,
                        Order.item_id.in_(sell_ids),
                        Order.status.in_(OrderService._post_accept_valid_statuses(Direction.SELL)),
                        Order.is_deleted == False,
                    )
                    .group_by(Order.item_id)
                )
                sell_res = await db.execute(sell_stmt)
                results.update({int(item_id): int(count or 0) for item_id, count in sell_res.all()})

            if buy_ids:
                buy_stmt = (
                    select(Order.item_id, func.count())
                    .where(
                        Order.item_type == item_type_enum,
                        Order.item_id.in_(buy_ids),
                        Order.status.in_(OrderService._post_accept_valid_statuses(Direction.BUY)),
                        Order.is_deleted == False,
                    )
                    .group_by(Order.item_id)
                )
                buy_res = await db.execute(buy_stmt)
                results.update({int(item_id): int(count or 0) for item_id, count in buy_res.all()})

            return results

        valid_statuses = [
            OrderStatus.PENDING,
            OrderStatus.ONGOING,
            OrderStatus.CONFIRMED,
            OrderStatus.COMPLETED,
        ]
        stmt = (
            select(Order.item_id, func.count())
            .where(
                Order.item_type == item_type_enum,
                Order.item_id.in_(unique_item_ids),
                Order.status.in_(valid_statuses),
                Order.is_deleted == False,
            )
            .group_by(Order.item_id)
        )
        res = await db.execute(stmt)
        return {int(item_id): int(count or 0) for item_id, count in res.all()}

    @staticmethod
    async def create_order(
        db: AsyncSession,
        item_type: str,
        item_id: int,
        initiator_id: int,
        trigger_type: Optional[str] = None,
        post: Optional[Post] = None,
        redis_client=None,
        commit: bool = True,
    ) -> Order:
        """统一下单入口。

        - POST SELL: 发帖人是卖家，接单人是买家，初始 PENDING，后续走双向确认/双盲评价
        - POST BUY: 发帖人是买家，接单人是卖家，初始 PENDING，后续走申请制审批
        - GOODS: 直接创建 ONGOING，进入买卖交付流程
        """
        t = str(item_type).upper()
        if t == ItemType.POST.name:
            if post is None:
                post = await OrderService._get_post_for_update(db, item_id)

            if post.publisher_id == initiator_id:
                raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="不能接自己的帖子")

            if post.status != PostStatus.OPEN:
                raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="当前帖子状态不允许接单")

            if redis_client is not None:
                await OrderService._raise_post_accept_rate_limit(redis_client, initiator_id, item_id)

            duplicate_stmt = select(Order.order_id).where(
                Order.item_type == ItemType.POST,
                Order.item_id == item_id,
                Order.is_deleted == False,
                Order.status.in_(
                    [
                        OrderStatus.PENDING,
                        OrderStatus.ONGOING,
                        OrderStatus.CONFIRMED,
                        OrderStatus.COMPLETED,
                    ]
                ),
                or_(
                    Order.initiator_id == initiator_id,
                    Order.buyer_id == initiator_id,
                    Order.seller_id == initiator_id,
                ),
            )
            duplicate_res = await db.execute(duplicate_stmt)
            if duplicate_res.scalar_one_or_none() is not None:
                raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="该帖子已申请过")

            # 从 Post.model 封装属性读取最大接单数
            max_accepters = getattr(post, "max_accepters", 1)

            # 使用统一的接单数计算方法（DRY 原则）
            curr = await OrderService.get_current_accepters_count(db, ItemType.POST.name, item_id)
            if curr >= max_accepters:
                raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="接单已满")

            buyer_id, seller_id = OrderService._resolve_order_participants(post.direction.value, post.publisher_id, initiator_id)
            trigger_enum = OrderService._resolve_trigger_type_for_post(post.direction.value)
            if trigger_type is not None:
                try:
                    trigger_enum = OrderTriggerType[str(trigger_type).upper().strip()]
                except KeyError:
                    pass

            # 创建意向单（PENDING）
            order = Order(
                buyer_id=buyer_id,
                seller_id=seller_id,
                initiator_id=initiator_id,
                item_type=ItemType.POST,
                item_id=item_id,
                status=OrderStatus.PENDING,
                trigger_type=trigger_enum,
            )
            db.add(order)
            await db.flush()
            await db.refresh(order)
            if commit:
                await db.commit()
            return order

        elif t == ItemType.GOODS.name:
            stmt = select(Goods).where(Goods.goods_id == item_id, Goods.is_deleted == False).with_for_update()
            res = await db.execute(stmt)
            goods = res.scalars().first()
            if not goods:
                raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="商品不存在")

            if goods.publisher_id == initiator_id:
                raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="不能购买自己的商品")

            # 商品必须可用（未售出且未被显式锁定）
            if goods.is_sold:
                raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="商品已售出")

            locked = False
            try:
                if goods.template_data and isinstance(goods.template_data, dict):
                    locked = bool(goods.template_data.get("locked", False))
            except Exception:
                locked = False
            if locked:
                raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="商品已被锁定，无法购买")

            # 创建订单并立即进入 ONGOING
            order = Order(
                buyer_id=initiator_id,
                seller_id=goods.publisher_id,
                initiator_id=initiator_id,
                item_type=ItemType.GOODS,
                item_id=item_id,
                status=OrderStatus.ONGOING,
                trigger_type=OrderTriggerType.DIRECT,
            )
            # 标记商品为锁定，避免重复购买（临时置于 template_data.locked）
            td = goods.template_data or {}
            td = dict(td)
            td["locked"] = True
            goods.template_data = td

            db.add(order)
            await db.flush()
            await db.refresh(order)
            if commit:
                await db.commit()
            return order

        else:
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="不支持的 item_type")

    @staticmethod
    def _batch_accept_error_code(exc: Exception) -> tuple[str, str]:
        msg = str(getattr(exc, "detail", getattr(exc, "msg", exc)))
        if "不能接自己的帖子" in msg:
            return "OWN_POST", msg
        if "该帖子已申请过" in msg:
            return "ALREADY_ACCEPTED", msg
        if "接单已满" in msg:
            return "FULL", msg
        if "冷静" in msg:
            return "COOLDOWN", msg
        if "今日取消次数已达上限" in msg:
            return "DAILY_LIMIT", msg
        if "仅支持 BUY 方向" in msg:
            return "INVALID_DIRECTION", msg
        if "当前帖子状态不允许接单" in msg:
            return "INVALID_STATUS", msg
        if "帖子不存在" in msg:
            return "NOT_FOUND", msg
        return "FAILED", msg

    @staticmethod
    async def batch_accept_posts(
        db: AsyncSession,
        initiator_id: int,
        post_ids: list[int],
        redis_client=None,
    ) -> dict:
        """批量申请多个 BUY 方向帖子，允许部分成功、部分失败。"""

        normalized_post_ids = [int(post_id) for post_id in post_ids if post_id is not None]
        if not normalized_post_ids:
            return {"results": [], "errors": []}
            
        # 严格入参去重，防止单兵重复冲锋
        unique_post_ids = list(dict.fromkeys(normalized_post_ids))

        if len(unique_post_ids) > 5:
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="最多一次只能接 5 单")

        results: list[dict] = []
        errors: list[dict] = []

        for post_id in unique_post_ids:
            try:
                # 预检锁单
                post = await OrderService._get_post_for_update(db, post_id)
                if post.direction != Direction.BUY:
                    raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="仅支持 BUY 方向的帖子接单")
                
                order = await OrderService.create_order(
                    db=db,
                    item_type=ItemType.POST.name,
                    item_id=post_id,
                    initiator_id=initiator_id,
                    post=post,
                    redis_client=redis_client,
                    commit=True,  # 锁死：单条当场独立提交
                )
                
                # 只有安全落库后，才会宣告成功，数据绝对真实
                results.append(
                    {
                        "post_id": int(post_id),
                        "order_id": int(order.order_id),
                        "status": order.status.value,
                    }
                )
                    
            except Exception as exc:
                # 如果这一单在路上卡死了（比如触发了那 5 秒的雪崩或者别的问题）
                # 没关系！立刻执行会话级回滚，把当前这一单在内存里折腾出来的脏痕迹擦干净
                # 确保 Session 恢复绝对纯净，绝不横向污染下一单的执行！
                await db.rollback()
                
                error_code, message = OrderService._batch_accept_error_code(exc)
                errors.append(
                    {
                        "post_id": int(post_id),
                        "error": error_code,
                        "message": message,
                    }
                )

        # 移除末尾全局的 db.commit()，因为成功的单在循环内部已经各自安全存盘了
        return {"results": results, "errors": errors}

    @staticmethod
    async def submit_delivery(db: AsyncSession, order_id: int, operator_id: int, redis_client=None) -> Order:
        """卖家提交交付，订单从 ONGOING 进入 CONFIRMED。"""

        order = await OrderService._get_order_for_update(db, order_id)
        if order.status != OrderStatus.ONGOING:
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="只有进行中订单可以提交交付")
        if operator_id != order.seller_id:
            raise BusinessHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="只有卖家可以提交交付")

        order.status = OrderStatus.CONFIRMED
        md = dict(order.meta_data or {})
        md["delivery_submitted_time"] = get_now().isoformat()
        order.meta_data = md
        await db.flush()
        await db.refresh(order)
        await db.commit()

        if redis_client is not None:
            try:
                delayed_score = time.time() + settings.ORDER_AUTO_CONFIRM_HOURS * 3600
                await enqueue_delayed_task(redis_client, ORDER_AUTO_CONFIRM_QUEUE_KEY, order.order_id, delayed_score)
            except Exception as exc:
                logger.error(f"Failed to enqueue order auto confirm task for order {order.order_id}: {exc}")
        return order

    @staticmethod
    async def auto_confirm_overdue_order_by_id(db: AsyncSession, order_id: int) -> bool:
        """针对单个订单执行自动完结。"""

        order = await OrderService._get_order_for_update(db, order_id)
        if order.status != OrderStatus.CONFIRMED:
            return False

        post = None
        goods = None
        if order.item_type == ItemType.POST:
            post_stmt = select(Post).where(Post.post_id == order.item_id, Post.is_deleted == False)
            post_res = await db.execute(post_stmt)
            post = post_res.scalars().first()
        elif order.item_type == ItemType.GOODS:
            goods_stmt = select(Goods).where(Goods.goods_id == order.item_id, Goods.is_deleted == False)
            goods_res = await db.execute(goods_stmt)
            goods = goods_res.scalars().first()

        order.status = OrderStatus.COMPLETED
        OrderService._apply_completion_side_effects(order, post, goods)
        try:
            await OrderService._add_credit(db, order.seller_id, settings.ORDER_COMPLETE_CREDIT, f"订单自动完成，order_id={order.order_id}")
        except Exception as exc:
            logger.error(f"Auto credit sync failed for order {order.order_id} seller {order.seller_id}: {exc}")

        md = dict(order.meta_data or {})
        md["auto_completed_time"] = get_now().isoformat()
        order.meta_data = md
        await db.flush()
        await db.commit()
        return True

    @staticmethod
    async def accept_delivery(db: AsyncSession, order_id: int, operator_id: int) -> Order:
        """买家确认验收，订单从 CONFIRMED 进入 COMPLETED。"""

        order = await OrderService._get_order_for_update(db, order_id)
        if order.status != OrderStatus.CONFIRMED:
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="只有待验收订单可以确认完成")
        if operator_id != order.buyer_id:
            raise BusinessHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="只有买家可以确认验收")

        order.status = OrderStatus.COMPLETED

        post = None
        goods = None
        if order.item_type == ItemType.POST:
            post = await OrderService._get_post_for_update(db, order.item_id)
        elif order.item_type == ItemType.GOODS:
            goods = await OrderService._load_goods_for_update(db, order.item_id)

        OrderService._apply_completion_side_effects(order, post, goods)

        try:
            await OrderService._add_credit(db, order.seller_id, settings.ORDER_COMPLETE_CREDIT, f"订单完成，order_id={order.order_id}")
        except Exception as exc:
            logger.error(f"Credit sync failed for order {order.order_id} seller {order.seller_id}: {exc}")

        md = dict(order.meta_data or {})
        md["delivery_accepted_time"] = get_now().isoformat()
        order.meta_data = md

        await db.flush()
        await db.refresh(order)
        await db.commit()
        return order

    @staticmethod
    async def force_complete_order_by_admin(db: AsyncSession, order_id: int, operator_id: int) -> Order:
        """管理员手动完结订单，允许在异常情况下跳过买家验收流程。"""

        order = await OrderService._get_order_for_update(db, order_id)
        if order.status == OrderStatus.COMPLETED:
            return order
        if order.status not in {OrderStatus.ONGOING, OrderStatus.CONFIRMED}:
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="只有进行中或待验收订单可以手动完结")

        order.status = OrderStatus.COMPLETED

        post = None
        goods = None
        if order.item_type == ItemType.POST:
            post = await OrderService._get_post_for_update(db, order.item_id)
        elif order.item_type == ItemType.GOODS:
            goods = await OrderService._load_goods_for_update(db, order.item_id)

        OrderService._apply_completion_side_effects(order, post, goods)

        try:
            await OrderService._add_credit(db, order.seller_id, settings.ORDER_COMPLETE_CREDIT, f"管理员手动完成订单，order_id={order.order_id}")
        except Exception as exc:
            logger.error(f"Admin credit sync failed for order {order.order_id} seller {order.seller_id}: {exc}")

        md = dict(order.meta_data or {})
        md["manual_completed_time"] = get_now().isoformat()
        md["manual_completed_by"] = operator_id
        order.meta_data = md

        await db.flush()
        await db.refresh(order)
        await db.commit()
        return order

    @staticmethod
    async def auto_confirm_overdue_orders(db: AsyncSession) -> int:
        """自动将超时未验收的 CONFIRMED 订单推入 COMPLETED。"""

        cutoff = get_now_naive() - timedelta(hours=settings.ORDER_AUTO_CONFIRM_HOURS)
        stmt = select(Order).where(
            Order.status == OrderStatus.CONFIRMED,
            Order.update_time <= cutoff,
            Order.is_deleted == False,
        ).with_for_update()
        res = await db.execute(stmt)
        promoted = 0
        for order in res.scalars().all():
            post = None
            goods = None
            if order.item_type == ItemType.POST:
                post = await OrderService._get_post_for_update(db, order.item_id)
            elif order.item_type == ItemType.GOODS:
                goods = await OrderService._load_goods_for_update(db, order.item_id)
            order.status = OrderStatus.COMPLETED
            OrderService._apply_completion_side_effects(order, post, goods)
            try:
                await OrderService._add_credit(db, order.seller_id, settings.ORDER_COMPLETE_CREDIT, f"订单自动完成，order_id={order.order_id}")
            except Exception as exc:
                logger.error(f"Auto credit sync failed for order {order.order_id} seller {order.seller_id}: {exc}")
            promoted += 1
        if promoted:
            await db.commit()
        return promoted

    @staticmethod
    async def list_orders(
        db: AsyncSession,
        user_id: int,
        role: str = "all",
        status: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Order], int]:
        """按用户视角查询订单列表。"""

        conditions = [Order.is_deleted == False]
        role_normalized = str(role or "all").lower()
        if role_normalized == "buyer":
            conditions.append(Order.buyer_id == user_id)
        elif role_normalized == "seller":
            conditions.append(Order.seller_id == user_id)
        elif role_normalized == "all":
            conditions.append(or_(Order.buyer_id == user_id, Order.seller_id == user_id))
        else:
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="role 仅支持 buyer/seller/all")

        status_enum = OrderService._normalize_status_filter(status)
        if status_enum is not None:
            conditions.append(Order.status == status_enum)

        if start_time:
            conditions.append(Order.create_time >= parse_datetime_to_beijing_naive(start_time))
        if end_time:
            conditions.append(Order.create_time <= parse_datetime_to_beijing_naive(end_time))

        count_stmt = select(func.count()).select_from(Order).where(and_(*conditions))
        count_res = await db.execute(count_stmt)
        total = int(count_res.scalar_one() or 0)

        stmt = (
            select(Order)
            .options(selectinload(Order.buyer), selectinload(Order.seller))
            .where(and_(*conditions))
            .order_by(Order.create_time.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        res = await db.execute(stmt)
        orders = res.scalars().unique().all()
        return orders, total

    @staticmethod
    async def list_orders_by_item(db: AsyncSession, item_type: str, item_id: int) -> list[Order]:
        item_type_enum = OrderService._normalize_item_type(item_type)
        stmt = (
            select(Order)
            .options(selectinload(Order.buyer), selectinload(Order.seller))
            .where(
                Order.item_type == item_type_enum,
                Order.item_id == item_id,
                Order.is_deleted == False,
            )
            .order_by(Order.create_time.desc())
        )
        res = await db.execute(stmt)
        return res.scalars().unique().all()

    @staticmethod
    async def list_post_applications(db: AsyncSession, post_id: int) -> list[dict]:
        """高效查询指定帖子下的待处理申请记录，并一次性带出申请人统计信息。"""

        applicant_user = aliased(User)
        completed_count_subquery = (
            select(func.count())
            .select_from(Order)
            .where(
                Order.is_deleted == False,
                Order.status == OrderStatus.COMPLETED,
                or_(
                    Order.buyer_id == applicant_user.user_id,
                    Order.seller_id == applicant_user.user_id,
                ),
            )
            .correlate(applicant_user)
            .scalar_subquery()
        )
        applicant_id_expr = func.coalesce(Order.initiator_id, Order.seller_id, Order.buyer_id)
        stmt = (
            select(Order, applicant_user, completed_count_subquery.label("completed_order_count"))
            .join(applicant_user, applicant_user.user_id == applicant_id_expr)
            .options(selectinload(applicant_user.avatar_attachment))
            .where(
                Order.item_type == ItemType.POST,
                Order.item_id == post_id,
                Order.status == OrderStatus.PENDING,
                Order.is_deleted == False,
            )
            .order_by(Order.create_time.desc())
        )
        res = await db.execute(stmt)
        rows = []
        for order, applicant, completed_order_count in res.all():
            rows.append(
                {
                    "order": order,
                    "applicant": applicant,
                    "completed_order_count": int(completed_order_count or 0),
                    "note": (order.meta_data or {}).get("note") if isinstance(order.meta_data, dict) else None,
                }
            )
        return rows

    @staticmethod
    async def get_order_detail(db: AsyncSession, order_id: int, current_user_id: int) -> Order:
        order = await OrderService._get_order_readonly(db, order_id)
        if current_user_id not in {order.buyer_id, order.seller_id}:
            raise BusinessHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="仅订单相关方可查看订单详情")
        return order

    @staticmethod
    async def approve_order(db: AsyncSession, order_id: int, operator_id: int) -> Order:
        order = await OrderService._get_order_for_update(db, order_id)
        if order.status != OrderStatus.PENDING:
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="只有待处理订单可以同意")
        if order.item_type != ItemType.POST:
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="当前订单不支持审批")

        post = await OrderService._get_post_for_update(db, order.item_id)
        if operator_id != post.publisher_id:
            raise BusinessHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="只有发帖人可以同意接单")

        if order.trigger_type == OrderTriggerType.COLLECTIVE:
            accepted_cnt = await OrderService.get_current_accepters_count(db, ItemType.POST.name, order.item_id)
            if accepted_cnt >= getattr(post, "max_accepters", 1):
                pending_stmt = (
                    select(Order)
                    .where(
                        Order.item_type == ItemType.POST,
                        Order.item_id == order.item_id,
                        Order.is_deleted == False,
                        Order.status == OrderStatus.PENDING,
                    )
                    .with_for_update()
                )
                pending_res = await db.execute(pending_stmt)
                for pending_order in pending_res.scalars().all():
                    pending_order.status = OrderStatus.ONGOING
                post.status = PostStatus.IN_PROGRESS
            else:
                post.status = PostStatus.OPEN
        else:
            pending_stmt = (
                select(Order)
                .where(
                    Order.item_type == ItemType.POST,
                    Order.item_id == order.item_id,
                    Order.is_deleted == False,
                    Order.status == OrderStatus.PENDING,
                    Order.order_id != order.order_id,
                )
                .with_for_update()
            )
            pending_res = await db.execute(pending_stmt)
            for pending_order in pending_res.scalars().all():
                pending_order.status = OrderStatus.REJECTED
            post.status = PostStatus.IN_PROGRESS

        order.status = OrderStatus.ONGOING
        order.accepted_time = get_now_naive()
        await db.flush()
        await db.refresh(order)
        await db.commit()
        return order

    @staticmethod
    async def reject_order(db: AsyncSession, order_id: int, operator_id: int) -> Order:
        order = await OrderService._get_order_for_update(db, order_id)
        if order.status != OrderStatus.PENDING:
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="只有待处理订单可以拒绝")
        if order.item_type != ItemType.POST:
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="当前订单不支持审批")

        post = await OrderService._get_post_for_update(db, order.item_id)
        if operator_id != post.publisher_id:
            raise BusinessHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="只有发帖人可以拒绝接单")

        order.status = OrderStatus.REJECTED
        remaining_stmt = select(Order.status).where(
            Order.item_type == ItemType.POST,
            Order.item_id == order.item_id,
            Order.is_deleted == False,
            Order.status.in_([OrderStatus.PENDING, OrderStatus.ONGOING, OrderStatus.CONFIRMED]),
        )
        remaining_res = await db.execute(remaining_stmt)
        remaining_statuses = list(remaining_res.scalars().all())
        if any(status in {OrderStatus.ONGOING, OrderStatus.CONFIRMED} for status in remaining_statuses):
            post.status = PostStatus.IN_PROGRESS
        else:
            post.status = PostStatus.OPEN

        await db.flush()
        await db.refresh(order)
        await db.commit()
        return order

    @staticmethod
    async def complete_order(db: AsyncSession, order_id: int, operator_id: int) -> Order:
        """兼容旧入口：等同于买家确认验收。"""

        return await OrderService.accept_delivery(db, order_id, operator_id)

    @staticmethod
    async def cancel_order(db: AsyncSession, order_id: int, operator_id: int, redis_client=None) -> Order:
        # 检查全局每日10次取消限制
        if redis_client is not None:
            cancel_key = f"user:global_cancel:count:{operator_id}"
            cancel_count = await redis_client.get(cancel_key)
            if cancel_count is not None and int(cancel_count) >= settings.GLOBAL_CANCEL_DAILY_LIMIT:
                raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="您今日取消申请过于频繁，请明天再试")
        
        order = await OrderService._get_order_for_update(db, order_id)
        if order.status not in {OrderStatus.PENDING, OrderStatus.ONGOING, OrderStatus.CONFIRMED}:
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="该状态不允许取消订单")
        if operator_id not in {order.buyer_id, order.seller_id}:
            raise BusinessHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="只有订单相关方可以取消订单")

        order.status = OrderStatus.CANCELED
        if order.item_type == ItemType.POST:
            post = await OrderService._get_post_for_update(db, order.item_id)
            remaining_stmt = select(Order.status).where(
                Order.item_type == ItemType.POST,
                Order.item_id == order.item_id,
                Order.is_deleted == False,
                Order.status.in_([OrderStatus.PENDING, OrderStatus.ONGOING, OrderStatus.CONFIRMED]),
            )
            remaining_res = await db.execute(remaining_stmt)
            remaining_statuses = list(remaining_res.scalars().all())
            if any(status in {OrderStatus.ONGOING, OrderStatus.CONFIRMED} for status in remaining_statuses):
                post.status = PostStatus.IN_PROGRESS
            else:
                post.status = PostStatus.OPEN
        if order.item_type == ItemType.GOODS:
            g_stmt = select(Goods).where(Goods.goods_id == order.item_id).with_for_update()
            g_res = await db.execute(g_stmt)
            goods = g_res.scalars().first()
            if goods and goods.template_data and isinstance(goods.template_data, dict):
                locked = dict(goods.template_data)
                locked.pop("locked", None)
                goods.template_data = locked

        if order.item_type == ItemType.POST and operator_id == order.initiator_id and redis_client is not None:
            await OrderService._record_post_cancel(redis_client, operator_id, order.item_id)

        # 成功取消后增加全局计数
        if redis_client is not None:
            cancel_key = f"user:global_cancel:count:{operator_id}"
            await redis_client.incr(cancel_key)
            # 首次增加时设置过期时间为今天午夜
            ttl = await redis_client.ttl(cancel_key)
            if ttl == -1:  # 新建的 key，还没有设置过期时间
                from app.core.datetime_utils import get_now_naive
                now = get_now_naive()
                midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                seconds_until_midnight = int((midnight - now).total_seconds())
                await redis_client.expire(cancel_key, seconds_until_midnight)

        await db.flush()
        await db.refresh(order)
        await db.commit()
        return order

    @staticmethod
    async def update_status(db: AsyncSession, order_id: int, new_status: str, operator_id: int) -> Order:
        """状态机引擎：校验并执行状态迁移。

        支持的迁移规则（保守实现）：
        - PENDING -> ONGOING | CANCELED
        - ONGOING -> CONFIRMED | DISPUTED | CANCELED
        - CONFIRMED -> COMPLETED | DISPUTED
        - DISPUTED -> CANCELED | CONFIRMED
        """
        new_status = str(new_status).upper()
        # 读取订单并加行锁
        stmt = select(Order).where(Order.order_id == order_id, Order.is_deleted == False).with_for_update()
        res = await db.execute(stmt)
        order = res.scalars().first()
        if not order:
            raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="订单不存在")

        cur = order.status
        try:
            dest = OrderStatus[new_status]
        except KeyError:
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="非法目标状态")

        TRANSITIONS = {
            OrderStatus.PENDING: {OrderStatus.ONGOING, OrderStatus.CANCELED},
            OrderStatus.ONGOING: {OrderStatus.CONFIRMED, OrderStatus.DISPUTED, OrderStatus.CANCELED},
            OrderStatus.CONFIRMED: {OrderStatus.COMPLETED, OrderStatus.DISPUTED},
            OrderStatus.DISPUTED: {OrderStatus.CONFIRMED, OrderStatus.CANCELED},
        }

        allowed = TRANSITIONS.get(cur, set())
        if dest not in allowed:
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg=f"非法的状态迁移：{cur.name} -> {dest.name}")

        # 权限校验矩阵（最小化原则）
        # PENDING -> ONGOING: 必须由卖家（发布者）确认
        if cur == OrderStatus.PENDING and dest == OrderStatus.ONGOING:
            if operator_id != order.seller_id:
                raise BusinessHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="只有发布者可以确认申请")
        # ONGOING -> CONFIRMED: 必须由卖家提交交付
        if cur == OrderStatus.ONGOING and dest == OrderStatus.CONFIRMED:
            if operator_id != order.seller_id:
                raise BusinessHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="只有卖家可以提交交付")
        # CONFIRMED -> COMPLETED: 必须由买家确认验收
        if cur == OrderStatus.CONFIRMED and dest == OrderStatus.COMPLETED:
            if operator_id != order.buyer_id:
                raise BusinessHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="只有买家可以确认验收")

        # 取消权限规则
        if dest == OrderStatus.CANCELED:
            if cur == OrderStatus.PENDING:
                # 发起人可以取消申请，发布者可以拒绝
                if operator_id not in (order.initiator_id, order.seller_id):
                    raise BusinessHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="只有发起人或发布者可以取消申请")
            elif cur == OrderStatus.ONGOING:
                # 进行中，双方均可取消（简单策略）
                if operator_id not in (order.buyer_id, order.seller_id):
                    raise BusinessHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="只有买家或卖家可以取消进行中的订单")
            else:
                # 其它阶段默认不允许任意取消
                raise BusinessHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="该阶段不允许取消")

        # 执行状态更新
        prev_status = order.status
        order.status = dest

        # 追加审计轨迹到 order.meta_data.history（避免新增表）
        try:
            md = order.meta_data or {}
            history = md.get("history") if isinstance(md, dict) else None
            if history is None:
                history = []
            history.append({
                "operator_id": operator_id,
                "from": prev_status.name if prev_status is not None else None,
                "to": dest.name,
                "time": get_now().isoformat(),
            })
            md = dict(md) if isinstance(md, dict) else {}
            md["history"] = history
            order.meta_data = md
        except Exception:
            # 审计失败不阻塞主流程
            pass

        # 钩子逻辑
        if dest == OrderStatus.COMPLETED:
            # 1) 增加积分（卖家）
            try:
                await OrderService._add_credit(db, order.seller_id, settings.ORDER_COMPLETE_CREDIT, f"订单完成，order_id={order.order_id}")
            except Exception as e:
                # 积分失败不应该阻断主流程，记录日志供人工/异步补偿
                logger.error(f"Credit sync failed for order {order.order_id} seller {order.seller_id}: {e}")

            # 2) 统一收尾状态
            post = None
            goods = None
            if order.item_type == ItemType.POST:
                post = await OrderService._get_post_for_update(db, order.item_id)
            if order.item_type == ItemType.GOODS:
                goods = await OrderService._load_goods_for_update(db, order.item_id)
            OrderService._apply_completion_side_effects(order, post, goods)
            md = dict(order.meta_data or {})
            md["completed_time"] = get_now().isoformat()
            order.meta_data = md

        if dest == OrderStatus.CANCELED:
            # 若为 GOODS，解除锁定
            if order.item_type == ItemType.GOODS:
                g_stmt = select(Goods).where(Goods.goods_id == order.item_id).with_for_update()
                g_res = await db.execute(g_stmt)
                goods = g_res.scalars().first()
                if goods:
                    td = goods.template_data or {}
                    if isinstance(td, dict) and td.get("locked"):
                        td = dict(td)
                        td.pop("locked", None)
                        goods.template_data = td

        await db.flush()
        await db.refresh(order)
        await db.commit()
        return order

    @staticmethod
    async def _add_credit(db: AsyncSession, user_id: int, amount: int, reason: str) -> None:
        """内部信用变更：创建 CreditLog 并更新 user.credit_score。"""
        if amount == 0:
            return

        # 更新用户信用分并写流水
        user = await OrderService._get_user_for_update(db, user_id)
        if not user:
            raise ResourceHTTPException(code=settings.USER_GET_FAILED_CODE, msg="用户不存在，无法变更积分")

        user.credit_score = (user.credit_score or 0) + int(amount)
        cl = CreditLog(user_id=user_id, change_amount=int(amount), reason=reason)
        db.add(cl)
        await db.flush()
        return
