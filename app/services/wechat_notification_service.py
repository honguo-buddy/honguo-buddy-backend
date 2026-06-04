"""微信订阅消息通知服务。

职责：
- 管理微信 access_token 的 Redis 缓存与自愈刷新
- 对下发参数执行强制字数清洗（phrase <=5字，thing <=20字）
- 封装统一的异步发送管道供路由层通过 BackgroundTasks 调用
- 全局复用 httpx 长连接池，杜绝短连接 TCP TIME_WAIT 耗尽
"""
import logging
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import get_now_naive, settings
from app.models import Order, User

logger = logging.getLogger(__name__)

# 模块级 httpx 长连接池，全局复用，避免高并发下短连接 TCP 端口耗尽
_httpx_client: Optional[httpx.AsyncClient] = None


def _get_httpx_client() -> httpx.AsyncClient:
    """获取模块级全局复用的 httpx 长连接池，懒初始化。"""
    global _httpx_client
    if _httpx_client is None:
        _httpx_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
    return _httpx_client


class WeChatNotificationService:
    """微信订阅消息通知服务，完全解耦于核心业务逻辑。"""

    # ======================== Token 缓存自愈 ========================

    @staticmethod
    async def _get_access_token(redis_client) -> str:
        """从 Redis 缓存获取 access_token，miss 时向微信网关现拉并回填。"""
        cached = await redis_client.get(settings.WX_ACCESS_TOKEN_CACHE_KEY)
        if cached:
            return cached

        params: Dict[str, str] = {
            "grant_type": "client_credential",
            "appid": settings.WX_APP_ID,
            "secret": settings.WX_APP_SECRET,
        }
        try:
            client = _get_httpx_client()
            resp = await client.get(settings.WX_ACCESS_TOKEN_URL, params=params)
            data = resp.json()
            if "access_token" not in data:
                logger.error("微信 access_token 获取失败: %s", data, exc_info=True)
                raise RuntimeError(f"微信 access_token 获取失败: {data}")
            token: str = data["access_token"]
            await redis_client.set(settings.WX_ACCESS_TOKEN_CACHE_KEY, token, ex=settings.WX_ACCESS_TOKEN_CACHE_TTL)
            return token
        except Exception:
            logger.error("获取微信 access_token 异常", exc_info=True)
            raise

    # ======================== 参数清洗引擎 ========================

    @staticmethod
    def _sanitize_phrase(value: str) -> str:
        """短语类型参数清洗：强制压缩至5个字符以内。

        phrase 参数微信硬限制为5个字符，截断时使用 [:3] + ".." 确保总长度=5。
        """
        if not value:
            return ""
        if len(value) <= 5:
            return value
        return value[:3] + ".."

    @staticmethod
    def _sanitize_thing(value: str) -> str:
        """文本类型参数清洗：强制截断至20字符以内，超出加省略号。

        thing 参数微信硬限制为20个字符，截断时使用 [:17] + "..." 确保总长度=20。
        """
        if not value:
            return ""
        if len(value) <= 20:
            return value
        return value[:17] + "..."

    # ======================== 批量 openid 预加载 ========================

    @staticmethod
    async def _batch_load_openids(db: AsyncSession, user_ids: List[int]) -> Dict[int, str]:
        """批量查询用户 openid，一次 SQL 消除 N+1 问题。

        返回 {user_id: openid} 字典，无 openid 的用户不出现在结果中。
        """
        if not user_ids:
            return {}
        stmt = select(User.user_id, User.wechat_openid).where(
            User.user_id.in_(user_ids),
            User.is_deleted == False,
        )
        res = await db.execute(stmt)
        rows = res.all()
        return {int(row[0]): row[1] for row in rows if row[1]}

    # ======================== 统一发送管道 ========================

    @staticmethod
    async def _send_to_openid(
        redis_client,
        openid: str,
        template_id: str,
        data: Dict[str, Any],
        page: str = "pages/index/index",
    ) -> bool:
        """向指定 openid 发送微信订阅消息（底层管道，不查 DB）。"""
        try:
            access_token = await WeChatNotificationService._get_access_token(redis_client)
            url = f"{settings.WX_SUBSCRIBE_SEND_URL}?access_token={access_token}"
            payload: Dict[str, Any] = {
                "touser": openid,
                "template_id": template_id,
                "page": page,
                "data": data,
                "miniprogram_state": "formal",
                "lang": "zh_CN",
            }
            client = _get_httpx_client()
            resp = await client.post(url, json=payload)
            result = resp.json()
            if result.get("errcode") != 0:
                logger.error("微信订阅消息发送失败: %s", result, exc_info=True)
                return False
            logger.info("微信订阅消息发送成功: openid=%s template=%s", openid[:8] + "***", template_id)
            return True
        except Exception:
            logger.error("微信订阅消息发送异常", exc_info=True)
            return False

    @staticmethod
    async def send_subscribe_message(
        redis_client,
        touser_id: int,
        template_id: str,
        data: Dict[str, Any],
        page: str = "pages/index/index",
        db: Optional[AsyncSession] = None,
    ) -> bool:
        """向指定用户发送微信订阅消息（单用户版本，内部查 DB 获取 openid）。

        参数：
            redis_client: Redis 客户端，用于 token 缓存
            touser_id: 接收方用户 ID
            template_id: 微信模板 ID
            data: 微信模板数据字典（已清洗）
            page: 点击跳转的小程序页面路径
            db: 数据库会话
        返回：
            bool: 发送成功返回 True，失败返回 False
        """
        if db is None:
            logger.warning("send_subscribe_message 缺少 db 参数")
            return False

        openid_map = await WeChatNotificationService._batch_load_openids(db, [touser_id])
        openid = openid_map.get(touser_id)
        if not openid:
            logger.warning("用户 %s 无 wechat_openid，跳过通知", touser_id)
            return False

        return await WeChatNotificationService._send_to_openid(redis_client, openid, template_id, data, page)

    # ======================== 业务钩子 ========================

    @staticmethod
    async def notify_new_application(
        db: AsyncSession, redis_client, order: Order, post_title: str, applicant_name: str
    ) -> None:
        """钩子 A：收到新接单申请 -> 通知发帖人（模板二）。"""
        now = get_now_naive()
        data: Dict[str, Any] = {
            "thing4": {"value": "收到一笔新的加入申请"},
            "thing5": {"value": WeChatNotificationService._sanitize_thing(post_title)},
            "thing2": {"value": WeChatNotificationService._sanitize_thing(applicant_name)},
            "time1": {"value": now.strftime("%Y-%m-%d %H:%M:%S")},
        }
        await WeChatNotificationService.send_subscribe_message(
            redis_client, order.seller_id, settings.WX_TEMPLATE_NEW_APPLICATION, data, db=db
        )

    @staticmethod
    async def notify_approved(
        db: AsyncSession, redis_client, order: Order, post_title: str
    ) -> None:
        """钩子 B：申请被录用 -> 通知买家（模板一）。"""
        now = get_now_naive()
        data: Dict[str, Any] = {
            "thing4": {"value": "学长已录用你，请火速前往履约"},
            "character_string5": {"value": str(order.order_id)},
            "thing7": {"value": WeChatNotificationService._sanitize_thing(post_title)},
            "phrase2": {"value": WeChatNotificationService._sanitize_phrase("已录用")},
            "time3": {"value": now.strftime("%Y-%m-%d %H:%M:%S")},
        }
        await WeChatNotificationService.send_subscribe_message(
            redis_client, order.buyer_id, settings.WX_TEMPLATE_ORDER_STATUS, data, db=db
        )

    @staticmethod
    async def notify_rejected(
        db: AsyncSession, redis_client, order: Order, post_title: str
    ) -> None:
        """钩子 C：申请被拒绝 -> 通知买家（模板一）。"""
        now = get_now_naive()
        data: Dict[str, Any] = {
            "thing4": {"value": "手慢啦，发帖人已录用其他同学"},
            "character_string5": {"value": str(order.order_id)},
            "thing7": {"value": WeChatNotificationService._sanitize_thing(post_title)},
            "phrase2": {"value": WeChatNotificationService._sanitize_phrase("未通过")},
            "time3": {"value": now.strftime("%Y-%m-%d %H:%M:%S")},
        }
        await WeChatNotificationService.send_subscribe_message(
            redis_client, order.buyer_id, settings.WX_TEMPLATE_ORDER_STATUS, data, db=db
        )

    @staticmethod
    async def notify_delivery(
        db: AsyncSession, redis_client, order: Order, post_title: str
    ) -> None:
        """钩子 D：服务已送达 -> 通知买家（模板一）。"""
        now = get_now_naive()
        data: Dict[str, Any] = {
            "thing4": {"value": "学长已完成服务，请火速前往确认验收"},
            "character_string5": {"value": str(order.order_id)},
            "thing7": {"value": WeChatNotificationService._sanitize_thing(post_title)},
            "phrase2": {"value": WeChatNotificationService._sanitize_phrase("已送达")},
            "time3": {"value": now.strftime("%Y-%m-%d %H:%M:%S")},
        }
        await WeChatNotificationService.send_subscribe_message(
            redis_client, order.buyer_id, settings.WX_TEMPLATE_ORDER_STATUS, data, db=db
        )

    @staticmethod
    async def notify_cancelled(
        db: AsyncSession, redis_client, order: Order, post_title: str, target_user_id: int
    ) -> None:
        """钩子 E：订单被取消 -> 通知被动取消的对端（模板一）。"""
        now = get_now_naive()
        data: Dict[str, Any] = {
            "thing4": {"value": "抱歉，进行中的订单已被对方取消"},
            "character_string5": {"value": str(order.order_id)},
            "thing7": {"value": WeChatNotificationService._sanitize_thing(post_title)},
            "phrase2": {"value": WeChatNotificationService._sanitize_phrase("已取消")},
            "time3": {"value": now.strftime("%Y-%m-%d %H:%M:%S")},
        }
        await WeChatNotificationService.send_subscribe_message(
            redis_client, target_user_id, settings.WX_TEMPLATE_ORDER_STATUS, data, db=db
        )

    @staticmethod
    async def notify_batch_start_collective(
        db: AsyncSession,
        redis_client,
        buyer_ids: List[int],
        post_title: str,
    ) -> None:
        """钩子 F 批量版：SELL批量开工 -> 一次性批量加载 openid 后扇出通知所有 ONGOING 买家。

        与逐人调用 notify_batch_start 不同，此方法先通过 _batch_load_openids
        一次性查出所有买家的 openid，然后在内存中循环分发，消除 N+1 查询。
        """
        if not buyer_ids:
            return

        openid_map = await WeChatNotificationService._batch_load_openids(db, buyer_ids)
        now = get_now_naive()
        data_template: Dict[str, Any] = {
            "thing4": {"value": "学长已经正式出发，请保持沟通关注"},
            "thing7": {"value": WeChatNotificationService._sanitize_thing(post_title)},
            "phrase2": {"value": WeChatNotificationService._sanitize_phrase("进行中")},
            "time3": {"value": now.strftime("%Y-%m-%d %H:%M:%S")},
        }

        for buyer_id in buyer_ids:
            openid = openid_map.get(buyer_id)
            if not openid:
                continue
            payload = dict(data_template)
            payload["character_string5"] = {"value": str(buyer_id)}
            await WeChatNotificationService._send_to_openid(
                redis_client, openid, settings.WX_TEMPLATE_ORDER_STATUS, payload
            )

    # ======================== 商品通知钩子 ========================

    @staticmethod
    async def notify_goods_purchased(
        db: AsyncSession, redis_client, order: Order, goods_title: str, buyer_name: str
    ) -> None:
        """钩子 G：商品被购买 -> 通知卖家（模板二）。"""
        now = get_now_naive()
        data: Dict[str, Any] = {
            "thing4": {"value": "你的商品已被拍下，请及时发货"},
            "thing5": {"value": WeChatNotificationService._sanitize_thing(goods_title)},
            "thing2": {"value": WeChatNotificationService._sanitize_thing(buyer_name)},
            "time1": {"value": now.strftime("%Y-%m-%d %H:%M:%S")},
        }
        await WeChatNotificationService.send_subscribe_message(
            redis_client, order.seller_id, settings.WX_TEMPLATE_NEW_APPLICATION, data, db=db
        )

    @staticmethod
    async def notify_goods_delivered(
        db: AsyncSession, redis_client, order: Order, goods_title: str
    ) -> None:
        """钩子 H：商品已发货 -> 通知买家（模板一）。"""
        now = get_now_naive()
        data: Dict[str, Any] = {
            "thing4": {"value": "卖家已发货，请注意查收"},
            "character_string5": {"value": str(order.order_id)},
            "thing7": {"value": WeChatNotificationService._sanitize_thing(goods_title)},
            "phrase2": {"value": WeChatNotificationService._sanitize_phrase("已发货")},
            "time3": {"value": now.strftime("%Y-%m-%d %H:%M:%S")},
        }
        await WeChatNotificationService.send_subscribe_message(
            redis_client, order.buyer_id, settings.WX_TEMPLATE_ORDER_STATUS, data, db=db
        )

    @staticmethod
    async def notify_goods_cancelled(
        db: AsyncSession, redis_client, order: Order, goods_title: str, target_user_id: int
    ) -> None:
        """钩子 I：商品订单被取消 -> 通知被动取消的对端（模板一）。"""
        now = get_now_naive()
        data: Dict[str, Any] = {
            "thing4": {"value": "抱歉，商品订单已被对方取消"},
            "character_string5": {"value": str(order.order_id)},
            "thing7": {"value": WeChatNotificationService._sanitize_thing(goods_title)},
            "phrase2": {"value": WeChatNotificationService._sanitize_phrase("已取消")},
            "time3": {"value": now.strftime("%Y-%m-%d %H:%M:%S")},
        }
        await WeChatNotificationService.send_subscribe_message(
            redis_client, target_user_id, settings.WX_TEMPLATE_ORDER_STATUS, data, db=db
        )
