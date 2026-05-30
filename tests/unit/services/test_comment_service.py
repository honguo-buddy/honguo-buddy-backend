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
