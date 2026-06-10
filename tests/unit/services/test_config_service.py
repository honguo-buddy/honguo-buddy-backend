from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core import DynamicConfigManager, settings
from app.models import SysConfig
from app.services import ConfigService
from tests.unit.fake_sqlalchemy import FakeResult


def build_db(*, execute_side_effect=None):
    db = SimpleNamespace()
    db.execute = AsyncMock(side_effect=execute_side_effect or [])
    db.add = lambda obj: None
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_dynamic_config_manager_seed_load_and_get():
    manager = DynamicConfigManager()

    class EmptyDB:
        def __init__(self):
            self.added = []
            self.commit = AsyncMock()

        async def execute(self, stmt):
            return FakeResult(items=[])

        def add(self, obj):
            self.added.append(obj)

    seed_db = EmptyDB()
    await manager.seed_defaults_if_empty(seed_db)
    assert any(isinstance(item, SysConfig) and item.config_key == "MAX_OPEN_POSTS_PER_USER" for item in seed_db.added)
    seed_db.commit.assert_awaited_once()

    load_db = build_db(
        execute_side_effect=[
            FakeResult(
                items=[
                    SysConfig(config_key="MAX_OPEN_POSTS_PER_USER", config_value="15", config_type="int", description="x"),
                    SysConfig(config_key="HISTORY_TTL_SECONDS", config_value="123", config_type="int", description="y"),
                    SysConfig(config_key="FEATURE_FLAG", config_value="true", config_type="bool", description="z"),
                ]
            )
        ]
    )
    await manager.load_all(load_db)
    assert manager.get("MAX_OPEN_POSTS_PER_USER") == 15
    assert manager.get("HISTORY_TTL_SECONDS") == 123
    assert manager.get("FEATURE_FLAG") is True
    assert manager.get("NOT_EXISTS", 9) == 9


@pytest.mark.asyncio
async def test_config_service_update_success_and_validation():
    config_row = SysConfig(
        config_key="MAX_OPEN_POSTS_PER_USER",
        config_value="10",
        config_type="int",
        description="用户同时开启的帖子/商品上限",
    )
    db = build_db(execute_side_effect=[FakeResult(items=[config_row])])

    updated = await ConfigService.update_config_value(
        db,
        config_key="MAX_OPEN_POSTS_PER_USER",
        config_value="12",
    )
    assert updated.config_value == "12"
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once()

    with pytest.raises(Exception):
        await ConfigService.update_config_value(
            db,
            config_key="UNKNOWN_CONFIG",
            config_value="1",
        )

    invalid_db = build_db(execute_side_effect=[FakeResult(items=[config_row])])
    with pytest.raises(Exception):
        await ConfigService.update_config_value(
            invalid_db,
            config_key="MAX_OPEN_POSTS_PER_USER",
            config_value="abc",
        )


@pytest.mark.asyncio
async def test_config_service_list_and_get_detail():
    config_row = SysConfig(
        config_key="MAX_OPEN_POSTS_PER_USER",
        config_value="10",
        config_type="int",
        description="用户同时开启的帖子/商品上限",
    )
    db = build_db(execute_side_effect=[FakeResult(items=[config_row]), FakeResult(items=[config_row]), FakeResult(items=[])])

    config_list = await ConfigService.list_configs(db)
    assert any(item.config_key == "MAX_OPEN_POSTS_PER_USER" for item in config_list)
    assert any(item.config_key == "HISTORY_MAX_SIZE" for item in config_list)

    detail = await ConfigService.get_config_by_key(db, config_key="MAX_OPEN_POSTS_PER_USER")
    assert detail.config_key == "MAX_OPEN_POSTS_PER_USER"
    assert detail.config_value == "10"

    default_detail = await ConfigService.get_config_by_key(db, config_key="HISTORY_MAX_SIZE")
    assert default_detail.config_key == "HISTORY_MAX_SIZE"
    assert default_detail.config_value == "100"


def test_settings_dynamic_proxy_override():
    original = settings.MAX_OPEN_POSTS_PER_USER
    settings.MAX_OPEN_POSTS_PER_USER = 33
    assert settings.MAX_OPEN_POSTS_PER_USER == 33
    settings._dynamic_overrides.pop("MAX_OPEN_POSTS_PER_USER", None)
    assert settings.MAX_OPEN_POSTS_PER_USER == original
