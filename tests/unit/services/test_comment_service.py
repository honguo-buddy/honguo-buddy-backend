from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exception_handler import AuthHTTPException, BusinessHTTPException, ResourceHTTPException
from app.services.comment_service import CommentService
from tests.unit.fake_sqlalchemy import FakeResult


pytestmark = pytest.mark.asyncio


def build_db(*, execute_side_effect=None):
    db = SimpleNamespace()
    db.execute = AsyncMock(side_effect=execute_side_effect or [])
    _stub = type("_Stub", (), {"is_deleted": False})()
    db.get = AsyncMock(return_value=_stub)
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    db.commit = AsyncMock()
    return db


def build_comment(**overrides):
    payload = {
        "comment_id": 1,
        "user_id": 1001,
        "target_type": "POST",
        "target_id": 88,
        "parent_id": None,
        "content": "hello",
        "is_deleted": False,
        "create_time": datetime(2026, 5, 27, 10, 0),
        "user": None,
        "replies": [],
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


async def test_create_comment_branches(monkeypatch):
    db = build_db()
    with pytest.raises(BusinessHTTPException):
        await CommentService.create_comment(db, 1, "BAD", 1, "x")

    db = build_db(execute_side_effect=[FakeResult(items=[])])
    with pytest.raises(ResourceHTTPException):
        await CommentService.create_comment(db, 1, "POST", 1, "x", parent_id=9)

    db = build_db(execute_side_effect=[FakeResult(items=[build_comment()])])
    bind_mock = AsyncMock(return_value=None)
    monkeypatch.setattr("app.services.comment_service.AttachmentService.bind_attachments_to_target", bind_mock)
    created = await CommentService.create_comment(db, 1, "POST", 1, "x", parent_id=1, attachment_ids=[1])
    assert created.content == "x"
    assert bind_mock.await_count == 1


async def test_delete_comment_permission_and_children_cleaning():
    comment = build_comment(comment_id=1, user_id=1001)
    child = build_comment(comment_id=2, parent_id=1, content="child")
    db = build_db(execute_side_effect=[FakeResult(items=[]), FakeResult(items=[])])
    with pytest.raises(ResourceHTTPException):
        await CommentService.delete_comment(db, 1, 1001, False)

    db = build_db(execute_side_effect=[FakeResult(items=[comment]), FakeResult(items=[])])
    with pytest.raises(AuthHTTPException):
        await CommentService.delete_comment(db, 1, 9999, False)

    db = build_db(execute_side_effect=[FakeResult(items=[comment]), FakeResult(items=[child])])
    await CommentService.delete_comment(db, 1, 1001, False)
    assert comment.is_deleted is True
    assert comment.content == "该评论已由用户删除"
    assert child.content == "该评论已由用户删除"


async def test_comment_query_helpers(monkeypatch):
    root1 = build_comment(comment_id=10)
    root2 = build_comment(comment_id=9)
    db = build_db(execute_side_effect=[FakeResult(items=[root1, root2])])
    rows, next_cursor = await CommentService.get_root_comments(db, "POST", 88, cursor=None, size=1)
    assert len(rows) == 1
    assert next_cursor == 10

    with pytest.raises(BusinessHTTPException):
        await CommentService.get_root_comments(build_db(), "BAD", 88)

    parent = build_comment(comment_id=1)
    r1 = build_comment(comment_id=2, parent_id=1)
    r2 = build_comment(comment_id=3, parent_id=1)
    db = build_db(execute_side_effect=[FakeResult(items=[parent]), FakeResult(items=[r1, r2])])
    replies, cursor = await CommentService.get_replies(db, 1, size=1)
    assert len(replies) == 1
    assert cursor == 2

    with pytest.raises(ResourceHTTPException):
        await CommentService.get_replies(build_db(execute_side_effect=[FakeResult(items=[])]), 1)

    db = build_db(execute_side_effect=[SimpleNamespace(scalar=lambda: 3), FakeResult(rows=[(1, 2)])])
    assert await CommentService.get_reply_count(db, 1) == 3
    assert await CommentService.get_reply_count_map(db, [1, 2]) == {1: 2, 2: 0}
    assert await CommentService.get_reply_count_map(db, []) == {}

    db = build_db(execute_side_effect=[FakeResult(items=[r1, r2])])
    preview = await CommentService.get_preview_replies(db, 1, limit=2)
    assert preview[0].comment_id == r2.comment_id

    get_urls = AsyncMock(return_value={1: ["/a.png"]})
    monkeypatch.setattr("app.services.comment_service.AttachmentService.get_urls_by_target", get_urls)
    assert await CommentService.get_comment_attachment_urls_map(build_db(), [1]) == {1: ["/a.png"]}


async def test_create_comment_on_non_existent_target():
    """评论不存在的 POST/GOODS 目标应被拦截，返回 ResourceHTTPException。"""
    from app.services.comment_service import CommentService
    from app.core.exception_handler import ResourceHTTPException

    # Non-existent POST target
    db = build_db()
    _stub = type("_Stub", (), {"is_deleted": False})()
    db.get = AsyncMock(return_value=None)
    with pytest.raises(ResourceHTTPException) as exc_info:
        await CommentService.create_comment(
            db, user_id=1001, target_type="POST", target_id=999999,
            content="orphan comment",
        )
    assert "不存在" in exc_info.value.detail["msg"]

    # Non-existent GOODS target
    db2 = build_db()
    db2.get = AsyncMock(return_value=None)
    with pytest.raises(ResourceHTTPException) as exc_info2:
        await CommentService.create_comment(
            db2, user_id=1001, target_type="GOODS", target_id=888888,
            content="orphan goods comment",
        )
    assert "不存在" in exc_info2.value.detail["msg"]

    # Soft-deleted POST target
    fake_post = type("Post", (), {"is_deleted": True, "post_id": 1})()
    db3 = build_db()
    db3.get = AsyncMock(return_value=fake_post)
    with pytest.raises(ResourceHTTPException) as exc_info3:
        await CommentService.create_comment(
            db3, user_id=1001, target_type="POST", target_id=1,
            content="comment on deleted post",
        )
    assert "不存在" in exc_info3.value.detail["msg"]


async def test_create_comment_on_existing_target_succeeds(monkeypatch):
    """评论已存在的 POST 目标应该成功创建。"""
    from app.services.comment_service import CommentService
    from app.models import Post, PostStatus

    # Mock the metrics incr to avoid Redis dependency
    monkeypatch.setattr(
        "app.services.comment_service.MetricsService.incr_post_comment",
        AsyncMock()
    )

    fake_post = type("Post", (), {
        "is_deleted": False,
        "post_id": 100,
        "user_id": 2001,
    })()
    db = build_db()
    db.get = AsyncMock(return_value=fake_post)
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    comment = await CommentService.create_comment(
        db, user_id=1001, target_type="POST", target_id=100,
        content="valid comment",
    )
    assert comment is not None
    assert comment.content == "valid comment"
    assert comment.target_id == 100


async def test_create_comment_blocks_blacklist_relationship(monkeypatch):
    import app.services.comment_service as comment_module

    fake_post = type("Post", (), {
        "is_deleted": False,
        "post_id": 100,
        "publisher_id": 2001,
    })()
    db = build_db()
    db.get = AsyncMock(return_value=fake_post)

    is_blocked_mock = AsyncMock(side_effect=[True, False])
    monkeypatch.setattr(
        comment_module,
        "BlacklistService",
        SimpleNamespace(is_blocked=is_blocked_mock),
        raising=False,
    )

    with pytest.raises(BusinessHTTPException) as exc_info:
        await CommentService.create_comment(
            db,
            user_id=1001,
            target_type="POST",
            target_id=100,
            content="blocked comment",
        )

    assert "拉黑" in exc_info.value.detail["msg"]
    assert db.add.call_count == 0


async def test_create_comment_blocks_when_current_user_blocked_target_owner(monkeypatch):
    import app.services.comment_service as comment_module

    fake_goods = type("Goods", (), {
        "is_deleted": False,
        "goods_id": 88,
        "publisher_id": 3001,
    })()
    db = build_db()
    db.get = AsyncMock(return_value=fake_goods)

    is_blocked_mock = AsyncMock(side_effect=[False, True])
    monkeypatch.setattr(
        comment_module,
        "BlacklistService",
        SimpleNamespace(is_blocked=is_blocked_mock),
        raising=False,
    )

    with pytest.raises(BusinessHTTPException) as exc_info:
        await CommentService.create_comment(
            db,
            user_id=1001,
            target_type="GOODS",
            target_id=88,
            content="blocked goods comment",
        )

    assert "拉黑" in exc_info.value.detail["msg"]
    assert db.add.call_count == 0

