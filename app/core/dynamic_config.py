"""动态业务配置管理器。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SysConfig

logger = logging.getLogger(__name__)

SYS_CONFIG_REFRESH_CHANNEL = "sys_config_refresh_channel"


@dataclass(frozen=True)
class DynamicConfigItem:
    """单个动态配置的内存表示。"""

    key: str
    raw_value: str
    value: Any
    value_type: str
    description: str


DEFAULT_DYNAMIC_CONFIGS: dict[str, dict[str, str]] = {
    "USER_INITIAL_CREDIT_SCORE": {
        "config_value": "60",
        "config_type": "int",
        "description": "用户初始信用分",
    },
    "ORDER_COMPLETE_CREDIT": {
        "config_value": "10",
        "config_type": "int",
        "description": "订单完成后卖家获得的积分奖励",
    },
    "ORDER_AUTO_CONFIRM_HOURS": {
        "config_value": "12",
        "config_type": "int",
        "description": "CONFIRMED 状态超时自动完结时限（小时）",
    },
    "ORDER_ACCEPT_COOLDOWN_SECONDS": {
        "config_value": "300",
        "config_type": "int",
        "description": "申请取消后冷静期（秒）",
    },
    "ORDER_ACCEPT_CANCEL_DAILY_LIMIT": {
        "config_value": "3",
        "config_type": "int",
        "description": "同一用户同一帖子每天允许取消次数",
    },
    "REVIEW_DOUBLE_BLIND_DAYS": {
        "config_value": "1",
        "config_type": "int",
        "description": "评价双盲期（天）",
    },
    "HISTORY_TTL_SECONDS": {
        "config_value": "2592000",
        "config_type": "int",
        "description": "历史记录过期时间（秒）",
    },
    "HISTORY_MAX_SIZE": {
        "config_value": "100",
        "config_type": "int",
        "description": "历史记录最大条数",
    },
    "MAX_OPEN_BUY_POSTS_PER_USER": {
        "config_value": "10",
        "config_type": "int",
        "description": "用户同时开启的委托帖子上限",
    },
    "MAX_OPEN_SELL_POSTS_PER_USER": {
        "config_value": "10",
        "config_type": "int",
        "description": "用户同时开启的服务帖子上限",
    },
    "MAX_OPEN_GOODS_PER_USER": {
        "config_value": "10",
        "config_type": "int",
        "description": "用户同时开启的二手商品上限",
    },
    "LIGHTNING_CANCEL_LIMIT_SECONDS": {
        "config_value": "600",
        "config_type": "int",
        "description": "闪电退单分水岭阈值（秒）",
    },
    "LIGHTNING_CANCEL_DAILY_LIMIT": {
        "config_value": "10",
        "config_type": "int",
        "description": "闪电退单每人每日上限次数",
    },
}


class DynamicConfigManager:
    """运行时动态配置单例。"""

    _instance: "DynamicConfigManager | None" = None

    def __new__(cls) -> "DynamicConfigManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._cache = {}
            cls._instance._items = {}
        return cls._instance

    @staticmethod
    def _convert_value(config_type: str, config_value: str) -> Any:
        normalized_type = (config_type or "str").strip().lower()
        if normalized_type == "int":
            return int(config_value)
        if normalized_type == "float":
            return float(config_value)
        if normalized_type == "bool":
            lowered = str(config_value).strip().lower()
            return lowered in {"1", "true", "yes", "on"}
        return config_value

    async def seed_defaults_if_empty(self, db_session: AsyncSession) -> None:
        """启动时增量补齐缺失的默认配置。"""
        result = await db_session.execute(select(SysConfig.config_key))
        existing_keys = set(result.scalars().all())

        created = False
        for config_key, meta in DEFAULT_DYNAMIC_CONFIGS.items():
            if config_key in existing_keys:
                continue
            db_session.add(
                SysConfig(
                    config_key=config_key,
                    config_value=meta["config_value"],
                    config_type=meta["config_type"],
                    description=meta["description"],
                )
            )
            created = True

        if created:
            await db_session.commit()

    async def load_all(self, db_session: AsyncSession) -> None:
        """从数据库全量重建内存缓存。"""
        result = await db_session.execute(select(SysConfig).order_by(SysConfig.config_key.asc()))
        rows = list(result.scalars().all())

        new_cache: dict[str, Any] = {}
        new_items: dict[str, DynamicConfigItem] = {}
        for row in rows:
            value = self._convert_value(row.config_type, row.config_value)
            new_cache[row.config_key] = value
            new_items[row.config_key] = DynamicConfigItem(
                key=row.config_key,
                raw_value=row.config_value,
                value=value,
                value_type=row.config_type,
                description=row.description or "",
            )

        self._cache = new_cache
        self._items = new_items
        logger.info("动态配置已装载，共 %d 项", len(new_cache))

    def get(self, key: str, default: Any = None) -> Any:
        """只读内存缓存，禁止触发数据库查询。"""
        return self._cache.get(key, default)

    def has(self, key: str) -> bool:
        """判断配置是否存在于内存。"""
        return key in self._cache

    def keys(self) -> list[str]:
        """返回当前缓存的配置键集合。"""
        return list(self._cache.keys())

    def get_item(self, key: str) -> DynamicConfigItem | None:
        """返回包含原始值与说明的配置项。"""
        return self._items.get(key)


async def watch_dynamic_config_refresh(redis_client, session_factory) -> None:
    """监听 Redis 配置刷新广播，并重载当前进程内存配置。"""
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(SYS_CONFIG_REFRESH_CHANNEL)
    try:
        async for message in pubsub.listen():
            if not isinstance(message, dict):
                continue
            if message.get("type") != "message":
                continue
            if str(message.get("data")) != "refresh":
                continue
            async with session_factory() as db:
                try:
                    await DynamicConfigManager().load_all(db)
                except Exception as exc:
                    logger.error("动态配置刷新失败: %s", exc, exc_info=True)
    except asyncio.CancelledError:
        raise
    finally:
        try:
            await pubsub.unsubscribe(SYS_CONFIG_REFRESH_CHANNEL)
        finally:
            close_method = getattr(pubsub, "aclose", None)
            if callable(close_method):
                await close_method()
