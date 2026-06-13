"""动态配置服务层。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import BusinessHTTPException, settings
from app.core.dynamic_config import DEFAULT_DYNAMIC_CONFIGS, DynamicConfigManager
from app.models import SysConfig


class ConfigService:
    """系统动态配置读写服务。"""

    @staticmethod
    def _build_default_config(config_key: str) -> SysConfig:
        """根据默认配置构造配置实体。"""
        meta = DEFAULT_DYNAMIC_CONFIGS[config_key]
        return SysConfig(
            config_key=config_key,
            config_value=meta["config_value"],
            config_type=meta["config_type"],
            description=meta["description"],
        )

    @staticmethod
    async def _get_or_create_persisted_config(
        db: AsyncSession,
        *,
        config_key: str,
    ) -> SysConfig:
        """获取或创建可持久化的动态配置实体。"""
        stmt = select(SysConfig).where(SysConfig.config_key == config_key)
        result = await db.execute(stmt)
        config_row = result.scalar_one_or_none()
        if config_row is not None:
            return config_row

        config_row = ConfigService._build_default_config(config_key)
        db.add(config_row)
        await db.flush()
        return config_row

    @staticmethod
    async def list_configs(db: AsyncSession) -> list[SysConfig]:
        """获取全部可热更新业务配置。"""
        stmt = select(SysConfig).order_by(SysConfig.config_key.asc())
        result = await db.execute(stmt)
        rows = list(result.scalars().all())
        config_map = {row.config_key: row for row in rows if row.config_key in DEFAULT_DYNAMIC_CONFIGS}
        for config_key in DEFAULT_DYNAMIC_CONFIGS:
            if config_key not in config_map:
                config_map[config_key] = ConfigService._build_default_config(config_key)
        return [config_map[key] for key in sorted(config_map.keys())]

    @staticmethod
    async def get_config_by_key(
        db: AsyncSession,
        *,
        config_key: str,
    ) -> SysConfig:
        """按配置键获取单个业务配置。"""
        if config_key not in DEFAULT_DYNAMIC_CONFIGS:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="配置项不存在或不允许热更新",
            )

        stmt = select(SysConfig).where(SysConfig.config_key == config_key)
        result = await db.execute(stmt)
        config_row = result.scalar_one_or_none()
        if config_row is None:
            return ConfigService._build_default_config(config_key)
        return config_row

    @staticmethod
    async def update_config_value(
        db: AsyncSession,
        *,
        config_key: str,
        config_value: str,
    ) -> SysConfig:
        """更新指定业务配置。"""
        if config_key not in DEFAULT_DYNAMIC_CONFIGS:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg="配置项不存在或不允许热更新",
            )

        config_row = await ConfigService._get_or_create_persisted_config(
            db,
            config_key=config_key,
        )

        try:
            DynamicConfigManager._convert_value(config_row.config_type, config_value)
        except Exception as exc:
            raise BusinessHTTPException(
                code=settings.REQ_ERROR_CODE,
                msg=f"配置值类型不合法，应为 {config_row.config_type}",
            ) from exc

        config_row.config_value = str(config_value)
        await db.commit()
        await db.refresh(config_row)
        return config_row
