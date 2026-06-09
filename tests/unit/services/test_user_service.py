from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exception_handler import AuthHTTPException, ResourceHTTPException
from app.models import AttachmentTargetType
from app.services.user_service import UserService
from tests.unit.fake_sqlalchemy import FakeResult


pytestmark = pytest.mark.asyncio


def build_db(*, execute_side_effect=None):
    db = SimpleNamespace()
    db.execute = AsyncMock(side_effect=execute_side_effect or [])
    db.get = AsyncMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


def build_user(**overrides):
    payload = {
        "user_id": 1001,
        "user_uuid": b"1234567890123456",
        "user_name": "u1",
        "avatar_attachment": None,
        "sex": "未知",
        "email": "u1@example.com",
        "phonenumber": "13800138000",
        "user_type": "user",
        "credit_score": 100,
        "is_verified": False,
        "is_active": True,
        "is_admin": False,
        "last_login_ip": None,
        "last_login_time": None,
        "wechat_unionid": None,
        "bio": None,
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def build_attachment(**overrides):
    payload = {
        "attachment_id": 1,
        "target_type": AttachmentTargetType.USER,
        "creator_id": 1001,
        "is_deleted": False,
        "url": "/static/avatar/a.png",
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


async def test_user_query_and_payload_building():
    user = build_user(avatar_attachment=build_attachment())
    db = build_db(execute_side_effect=[FakeResult(items=[user]), FakeResult(items=[user]), FakeResult(items=[user])])

    assert await UserService.get_user_by_id(1001, db) == user
    assert (await UserService.get_user_with_avatar_url(1001, db))["avatar"] == "/static/avatar/a.png"
    assert "email" not in (await UserService.get_user_public_with_avatar_url(1001, db))


async def test_update_profile_and_admin_update(monkeypatch):
    user = build_user()
    db = build_db(execute_side_effect=[FakeResult(items=[build_attachment()]), FakeResult(items=[user]), FakeResult(items=[user])])
    db.get = AsyncMock(return_value=user)

    updated = await UserService.update_user_profile(1001, user_name="new", avatar_id=1, sex="男", db=db)
    assert updated.user_name == "new"

    admin_user = build_user(user_id=2001)
    db_admin = build_db(execute_side_effect=[FakeResult(items=[build_attachment()]), FakeResult(items=[admin_user]), FakeResult(items=[admin_user])])
    db_admin.get = AsyncMock(return_value=admin_user)
    updated_admin = await UserService.update_user_by_admin(2001, user_name="admin-new", avatar_id=1, is_admin=True, db=db_admin)
    assert updated_admin.user_name == "admin-new"


async def test_avatar_validation_and_setter_branches():
    db = build_db(execute_side_effect=[FakeResult(items=[]), FakeResult(items=[build_attachment(target_type="POST")]), FakeResult(items=[build_attachment(creator_id=9999)]), FakeResult(items=[build_attachment()])])

    with pytest.raises(TypeError):
        await UserService._validate_avatar_attachment_owned_by_user(1001, 1, db)
    with pytest.raises(AuthHTTPException):
        await UserService._validate_avatar_attachment_owned_by_user(1001, 1, db)
    with pytest.raises(AuthHTTPException):
        await UserService._validate_avatar_attachment_owned_by_user(1001, 1, db)
    await UserService._validate_avatar_attachment_owned_by_user(1001, 1, db)

    db_set = build_db(execute_side_effect=[FakeResult(items=[]), FakeResult(items=[build_attachment(target_type="POST")]), FakeResult(items=[build_attachment(creator_id=9999)]), FakeResult(items=[build_attachment()]), FakeResult()])

    with pytest.raises(TypeError):
        await UserService.set_user_avatar_by_attachment(1001, 1, db_set)
    with pytest.raises(AuthHTTPException):
        await UserService.set_user_avatar_by_attachment(1001, 1, db_set)
    with pytest.raises(AuthHTTPException):
        await UserService.set_user_avatar_by_attachment(1001, 1, db_set)
    await UserService.set_user_avatar_by_attachment(1001, 1, db_set, allow_force=True)


async def test_delete_and_admin_views():
    user = build_user()
    db = build_db(execute_side_effect=[FakeResult(items=[user]), FakeResult(items=[user]), FakeResult(items=[user]), FakeResult(items=[user])])
    await UserService.delete_user(1001, db)
    await UserService.admin_delete_user(1001, db)

    assert await UserService.get_user_by_user_id_admin(1001, db) == user
    payload = await UserService.get_user_with_avatar_url_admin(1001, db)
    assert payload["user_id"] == 1001
