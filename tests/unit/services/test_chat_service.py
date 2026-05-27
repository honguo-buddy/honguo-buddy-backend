from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exception_handler import BusinessHTTPException, ResourceHTTPException
from app.models import AttachmentTargetType
from app.services.chat_service import ChatService
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


def build_session(**overrides):
    payload = {
        "session_id": 1,
        "user_one_id": 1001,
        "user_two_id": 1002,
        "context_type": "POST",
        "context_id": 88,
        "last_message_content": None,
        "last_message_time": None,
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def build_message(**overrides):
    payload = {
        "message_id": 11,
        "session_id": 1,
        "sender_id": 1001,
        "content": "hello",
        "context_type": "POST",
        "context_id": 88,
        "is_read": False,
        "is_recalled": False,
        "is_deleted_by_sender": False,
        "is_deleted_by_receiver": False,
        "quote_message_id": None,
        "create_time": datetime(2026, 5, 27, 10, 0),
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


async def test_init_session_branches(monkeypatch):
    db = build_db()
    with pytest.raises(BusinessHTTPException):
        await ChatService.init_session(db, current_user_id=1, peer_id=1)

    db = build_db(execute_side_effect=[FakeResult(scalar_value=None)])
    with pytest.raises(ResourceHTTPException):
        await ChatService.init_session(db, current_user_id=1, peer_id=2)

    existing = build_session(session_id=99)
    db = build_db(execute_side_effect=[FakeResult(scalar_value=2)])
    monkeypatch.setattr(ChatService, "_get_session_or_none", AsyncMock(return_value=existing))
    assert await ChatService.init_session(db, current_user_id=1, peer_id=2) == existing

    created = build_session(session_id=100)
    db = build_db(execute_side_effect=[FakeResult(scalar_value=2)])

    async def refresh_side_effect(obj):
        obj.session_id = 100

    db.refresh = AsyncMock(side_effect=refresh_side_effect)
    monkeypatch.setattr(ChatService, "_get_session_or_none", AsyncMock(return_value=None))
    result = await ChatService.init_session(db, current_user_id=1, peer_id=2, context_type="POST", context_id=9)
    assert result.session_id == 100


async def test_list_sessions_and_validate_membership():
    session = build_session(session_id=1, user_one_id=1001, user_two_id=1002, last_message_time=datetime(2026, 5, 27, 9, 0))
    db = build_db(execute_side_effect=[FakeResult(rows=[(session, 3)])])
    rows = await ChatService.list_sessions(db, current_user_id=1001)
    assert len(rows) == 1
    assert rows[0].unread_count == 3
    assert rows[0].peer_id == 1002

    db = build_db(execute_side_effect=[FakeResult(items=[])])
    with pytest.raises(ResourceHTTPException):
        await ChatService._validate_session_membership(db, 1, 1001)

    session = build_session(user_one_id=10, user_two_id=20)
    db = build_db(execute_side_effect=[FakeResult(items=[session])])
    with pytest.raises(BusinessHTTPException):
        await ChatService._validate_session_membership(db, 1, 1001)


async def test_send_message_with_quote_and_attachments(monkeypatch):
    session = build_session(context_type="POST", context_id=8)
    quote = build_message(message_id=20, session_id=1)
    db = build_db(execute_side_effect=[FakeResult(items=[quote]), FakeResult(items=[session])])
    monkeypatch.setattr(ChatService, "_validate_session_membership", AsyncMock(return_value=session))
    bind_mock = AsyncMock(return_value=None)
    monkeypatch.setattr("app.services.chat_service.AttachmentService.bind_attachments_to_target", bind_mock)

    async def refresh_side_effect(msg):
        msg.message_id = 66

    db.refresh = AsyncMock(side_effect=refresh_side_effect)
    message = await ChatService.send_message(
        db,
        current_user_id=1001,
        session_id=1,
        content="msg",
        attachment_ids=[1],
        quote_message_id=20,
    )
    assert message.message_id == 66
    assert bind_mock.await_count == 1

    db = build_db(execute_side_effect=[FakeResult(items=[None])])
    monkeypatch.setattr(ChatService, "_validate_session_membership", AsyncMock(return_value=session))
    with pytest.raises(BusinessHTTPException):
        await ChatService.send_message(db, 1001, 1, "x", quote_message_id=999)


async def test_get_messages_and_mark_read(monkeypatch):
    session = build_session(user_one_id=1001, user_two_id=1002)
    monkeypatch.setattr(ChatService, "_validate_session_membership", AsyncMock(return_value=session))

    messages = [build_message(message_id=5), build_message(message_id=4), build_message(message_id=3)]
    db = build_db(execute_side_effect=[FakeResult(items=messages)])
    items, next_cursor = await ChatService.get_messages(db, current_user_id=1001, session_id=1, size=2)
    assert len(items) == 2
    assert next_cursor == 4

    db = build_db(execute_side_effect=[FakeResult(scalar_value=0)])
    unread = await ChatService.mark_session_read(db, current_user_id=1001, session_id=1)
    assert unread == 0

    db = build_db(execute_side_effect=[FakeResult(scalar_value=2), FakeResult()])
    unread = await ChatService.mark_session_read(db, current_user_id=1001, session_id=1)
    assert unread == 2


async def test_recall_and_delete_local_message(monkeypatch):
    message = build_message(sender_id=1001, create_time=datetime(2026, 5, 27, 10, 0))
    session = build_session(last_message_time=message.create_time, last_message_content="old")
    db = build_db(execute_side_effect=[FakeResult(items=[message]), FakeResult(items=[session])])
    monkeypatch.setattr("app.services.chat_service.get_now_naive", lambda: datetime(2026, 5, 27, 10, 1))

    result = await ChatService.recall_message(db, current_user_id=1001, message_id=11)
    assert result.is_recalled is True
    assert session.last_message_content == ChatService.RECALL_TEXT

    db = build_db(execute_side_effect=[FakeResult(items=[build_message(sender_id=2000)])])
    with pytest.raises(BusinessHTTPException):
        await ChatService.recall_message(db, current_user_id=1001, message_id=11)

    old_msg = build_message(sender_id=1001, create_time=datetime(2026, 5, 27, 7, 0))
    db = build_db(execute_side_effect=[FakeResult(items=[old_msg])])
    monkeypatch.setattr("app.services.chat_service.get_now_naive", lambda: datetime(2026, 5, 27, 10, 0))
    with pytest.raises(BusinessHTTPException):
        await ChatService.recall_message(db, current_user_id=1001, message_id=11)

    sender_msg = build_message(sender_id=1001)
    db = build_db(execute_side_effect=[FakeResult(items=[sender_msg])])
    deleted = await ChatService.delete_local_message(db, current_user_id=1001, message_id=11)
    assert deleted.is_deleted_by_sender is True

    recv_msg = build_message(sender_id=1002)
    db = build_db(execute_side_effect=[FakeResult(items=[recv_msg])])
    monkeypatch.setattr(ChatService, "_validate_session_membership", AsyncMock(return_value=build_session()))
    deleted = await ChatService.delete_local_message(db, current_user_id=1001, message_id=11)
    assert deleted.is_deleted_by_receiver is True


async def test_message_attachment_urls_map(monkeypatch):
    assert await ChatService.get_message_attachment_urls_map(build_db(), []) == {}

    db = build_db(execute_side_effect=[FakeResult(rows=[(11, "/a.png"), (11, "/b.png"), (None, "/x.png")])])
    monkeypatch.setattr("app.services.chat_service.AttachmentService.to_public_url", lambda url: f"https://cdn{url}")
    mapping = await ChatService.get_message_attachment_urls_map(db, [11, 12])
    assert mapping[11] == ["https://cdn/a.png", "https://cdn/b.png"]
    assert mapping[12] == []
