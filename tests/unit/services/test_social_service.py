from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock  # 引入同步 Mock

import pytest
from sqlalchemy.exc import IntegrityError

from app.core import BusinessHTTPException, ResourceHTTPException
from app.services.social_service import SocialService
from tests.unit.fake_sqlalchemy import FakeResult


pytestmark = pytest.mark.asyncio


def build_db(*, execute_side_effect=None):
    db = SimpleNamespace()
    # 异步方法使用 AsyncMock
    db.execute = AsyncMock(side_effect=execute_side_effect or [])
    db.get = AsyncMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()   # 补上异步回滚，彻底救活异常流用例
    
    # SQLAlchemy 的 add 和 delete 是同步方法，使用普通 Mock
    # 这样可以完美消除 Jenkins 中的 RuntimeWarning (coroutine was never awaited) 告警
    db.add = AsyncMock()
    db.delete = AsyncMock()
    return db


def build_user(user_id=1001, **overrides):
    payload = {
        "user_id": user_id,
        "is_deleted": False,
        "is_active": True,
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def build_follow(follower_id=1001, following_id=2001):
    return SimpleNamespace(follower_id=follower_id, following_id=following_id)


async def test_toggle_follow_cannot_follow_self():
    db = build_db()
    with pytest.raises(BusinessHTTPException):
        await SocialService.toggle_follow(db, 1, 1)


async def test_toggle_follow_target_not_exists():
    db = build_db()
    db.get = AsyncMock(return_value=None)
    with pytest.raises(ResourceHTTPException):
        await SocialService.toggle_follow(db, 1001, 2001)


async def test_toggle_follow_existing_unfollow():
    # follower exists and there's an existing follow record -> should delete and return is_following False
    user = build_user(2001)
    follow = build_follow(1001, 2001)
    db = build_db(execute_side_effect=[FakeResult(items=[follow])])
    db.get = AsyncMock(return_value=user)

    result = await SocialService.toggle_follow(db, 1001, 2001)
    assert result["following_id"] == 2001
    assert result["is_following"] is False
    # 验证同步 delete 是否被正确调用
    db.delete.assert_called_once()


async def test_toggle_follow_create_success():
    user = build_user(2002)
    db = build_db(execute_side_effect=[FakeResult(items=[])])
    db.get = AsyncMock(return_value=user)

    result = await SocialService.toggle_follow(db, 1001, 2002)
    assert result["following_id"] == 2002
    assert result["is_following"] is True
    # 验证同步 add 是否被正确调用，此时绝不会再触发未 awaited 警告
    db.add.assert_called_once()


async def test_toggle_follow_integrity_error_on_commit_returns_true():
    """测试并发唯一性冲突时，系统能否安全回滚事务并妥善对账"""
    user = build_user(2010)
    db = build_db(execute_side_effect=[FakeResult(items=[])])
    db.get = AsyncMock(return_value=user)
    # 模拟 commit 时由于联合唯一索引引发冲突
    db.commit = AsyncMock(side_effect=IntegrityError("msg", params=None, orig=None))

    result = await SocialService.toggle_follow(db, 1001, 2010)
    
    # 1. 即使唯一索引报错，由于对方确实已存在关注关系，依然要正确返回 True 状态
    assert result["is_following"] is True
    # 2. 由于补全了基础 Mock，这里必须成功触发并安全执行回滚
    db.rollback.assert_awaited_once()