"""管理员动态配置接口集成测试。"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core import DynamicConfigManager, settings
from app.models import SysConfig
from tests.helpers import assert_api_error


pytestmark = pytest.mark.asyncio


async def test_admin_list_sys_configs_success(client: AsyncClient, db_session, test_admin_user, test_admin_token, fake_redis):
    await fake_redis.set(f"token:{test_admin_token}", str(test_admin_user.user_id))
    await fake_redis.set(f"user_token:{test_admin_user.user_id}", test_admin_token)
    db_session.add(
        SysConfig(
            config_key="MAX_OPEN_BUY_POSTS_PER_USER",
            config_value="10",
            config_type="int",
            description="用户同时开启的委托帖子上限",
        )
    )
    await db_session.flush()

    resp = await client.get(
        "/admin/configs",
        headers={"Authorization": f"Bearer {test_admin_token}"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == settings.SUCCESS_CODE
    assert isinstance(body["message"], list)
    assert any(item["config_key"] == "MAX_OPEN_BUY_POSTS_PER_USER" for item in body["message"])
    assert any(item["config_key"] == "MAX_OPEN_SELL_POSTS_PER_USER" for item in body["message"])
    assert any(item["config_key"] == "MAX_OPEN_GOODS_PER_USER" for item in body["message"])
    assert any(item["config_key"] == "HISTORY_MAX_SIZE" for item in body["message"])


async def test_admin_list_sys_configs_forbidden_for_non_admin(client: AsyncClient, test_user, test_user_token, fake_redis):
    await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
    await fake_redis.set(f"user_token:{test_user.user_id}", test_user_token)

    resp = await client.get(
        "/admin/configs",
        headers={"Authorization": f"Bearer {test_user_token}"},
    )

    assert resp.status_code == 200
    assert_api_error(resp.json(), code=settings.INSUFFICIENT_AUTHORITY_CODE)


async def test_admin_get_sys_config_detail_success(client: AsyncClient, db_session, test_admin_user, test_admin_token, fake_redis):
    await fake_redis.set(f"token:{test_admin_token}", str(test_admin_user.user_id))
    await fake_redis.set(f"user_token:{test_admin_user.user_id}", test_admin_token)
    db_session.add(
        SysConfig(
            config_key="MAX_OPEN_BUY_POSTS_PER_USER",
            config_value="10",
            config_type="int",
            description="用户同时开启的委托帖子上限",
        )
    )
    await db_session.flush()

    resp = await client.get(
        "/admin/configs/MAX_OPEN_BUY_POSTS_PER_USER",
        headers={"Authorization": f"Bearer {test_admin_token}"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == settings.SUCCESS_CODE
    assert body["message"]["config_key"] == "MAX_OPEN_BUY_POSTS_PER_USER"
    assert body["message"]["config_value"] == "10"


async def test_admin_get_sys_config_detail_forbidden_for_non_admin(client: AsyncClient, test_user, test_user_token, fake_redis):
    await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
    await fake_redis.set(f"user_token:{test_user.user_id}", test_user_token)

    resp = await client.get(
        "/admin/configs/MAX_OPEN_BUY_POSTS_PER_USER",
        headers={"Authorization": f"Bearer {test_user_token}"},
    )

    assert resp.status_code == 200
    assert_api_error(resp.json(), code=settings.INSUFFICIENT_AUTHORITY_CODE)


async def test_admin_get_sys_config_detail_rejects_invalid_key(client: AsyncClient, test_admin_user, test_admin_token, fake_redis):
    await fake_redis.set(f"token:{test_admin_token}", str(test_admin_user.user_id))
    await fake_redis.set(f"user_token:{test_admin_user.user_id}", test_admin_token)

    resp = await client.get(
        "/admin/configs/INVALID_KEY",
        headers={"Authorization": f"Bearer {test_admin_token}"},
    )

    assert resp.status_code == 200
    assert_api_error(resp.json(), code=settings.REQ_ERROR_CODE)


async def test_admin_update_sys_config_success(client: AsyncClient, db_session, test_admin_user, test_admin_token, fake_redis):
    await fake_redis.set(f"token:{test_admin_token}", str(test_admin_user.user_id))
    await fake_redis.set(f"user_token:{test_admin_user.user_id}", test_admin_token)
    row = SysConfig(
        config_key="MAX_OPEN_BUY_POSTS_PER_USER",
        config_value="10",
        config_type="int",
        description="用户同时开启的委托帖子上限",
    )
    db_session.add(row)
    await db_session.flush()

    resp = await client.patch(
        "/admin/configs/MAX_OPEN_BUY_POSTS_PER_USER",
        headers={"Authorization": f"Bearer {test_admin_token}"},
        json={"config_value": "18"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == settings.SUCCESS_CODE
    assert body["message"]["config_key"] == "MAX_OPEN_BUY_POSTS_PER_USER"
    assert body["message"]["config_value"] == "18"
    assert fake_redis._data["_pub:sys_config_refresh_channel"] == "refresh"

    result = await db_session.execute(select(SysConfig).where(SysConfig.config_key == "MAX_OPEN_BUY_POSTS_PER_USER"))
    updated = result.scalar_one()
    assert updated.config_value == "18"
    assert DynamicConfigManager().get("MAX_OPEN_BUY_POSTS_PER_USER") == 18


async def test_admin_update_sys_config_forbidden_for_non_admin(client: AsyncClient, test_user, test_user_token, fake_redis):
    await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
    await fake_redis.set(f"user_token:{test_user.user_id}", test_user_token)

    resp = await client.patch(
        "/admin/configs/MAX_OPEN_BUY_POSTS_PER_USER",
        headers={"Authorization": f"Bearer {test_user_token}"},
        json={"config_value": "18"},
    )

    assert resp.status_code == 200
    assert_api_error(resp.json(), code=settings.INSUFFICIENT_AUTHORITY_CODE)


async def test_admin_update_sys_config_rejects_invalid_key_and_type(client: AsyncClient, db_session, test_admin_user, test_admin_token, fake_redis):
    await fake_redis.set(f"token:{test_admin_token}", str(test_admin_user.user_id))
    await fake_redis.set(f"user_token:{test_admin_user.user_id}", test_admin_token)
    row = SysConfig(
        config_key="MAX_OPEN_BUY_POSTS_PER_USER",
        config_value="10",
        config_type="int",
        description="用户同时开启的委托帖子上限",
    )
    db_session.add(row)
    await db_session.flush()

    invalid_key_resp = await client.patch(
        "/admin/configs/INVALID_KEY",
        headers={"Authorization": f"Bearer {test_admin_token}"},
        json={"config_value": "18"},
    )
    assert_api_error(invalid_key_resp.json(), code=settings.REQ_ERROR_CODE)

    invalid_type_resp = await client.patch(
        "/admin/configs/MAX_OPEN_BUY_POSTS_PER_USER",
        headers={"Authorization": f"Bearer {test_admin_token}"},
        json={"config_value": "abc"},
    )
    assert_api_error(invalid_type_resp.json(), code=settings.REQ_ERROR_CODE)
