from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exception_handler import BusinessHTTPException, ResourceHTTPException
from app.models import AttachmentTargetType
from app.services.attachment_service import AttachmentService
from tests.unit.fake_sqlalchemy import FakeResult


pytestmark = pytest.mark.asyncio


def build_db(*, execute_side_effect=None):
    db = SimpleNamespace()
    db.execute = AsyncMock(side_effect=execute_side_effect or [])
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    return db


class DummyFile:
    def __init__(self, filename: str, content: bytes):
        self.filename = filename
        self._content = content

    async def read(self):
        return self._content


class DummyAioFile:
    def __init__(self):
        self.written = b""

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def write(self, data: bytes):
        self.written += data


async def test_upload_rejects_extension_and_large_file():
    db = build_db()
    current_user = SimpleNamespace(user_id=1001)

    with pytest.raises(BusinessHTTPException):
        await AttachmentService.upload(DummyFile("bad.txt", b"1"), "USER", None, current_user, db)

    with pytest.raises(BusinessHTTPException):
        await AttachmentService.upload(DummyFile("a.jpg", b"x" * (5 * 1024 * 1024 + 1)), "USER", None, current_user, db)


async def test_upload_success_and_target_fallback(monkeypatch):
    db = build_db(execute_side_effect=[FakeResult()])
    current_user = SimpleNamespace(user_id=1001)
    fake_file = DummyAioFile()

    monkeypatch.setattr("app.services.attachment_service.time.time_ns", lambda: 123456)
    monkeypatch.setattr("app.services.attachment_service.aiofiles.open", lambda *args, **kwargs: fake_file)

    attachment = await AttachmentService.upload(
        DummyFile("a.jpg", b"abc"),
        target_type="INVALID",
        target_id=None,
        current_user=current_user,
        db=db,
    )

    assert attachment.target_type == AttachmentTargetType.USER
    assert attachment.target_id == current_user.user_id
    assert attachment.url.startswith("/static/avatar/")
    assert db.commit.await_count == 1


async def test_bind_attachments_and_get_urls(monkeypatch):
    db = build_db(execute_side_effect=[FakeResult(rows=[(1,), (2,)]), FakeResult()])
    await AttachmentService.bind_attachments_to_target(db, [1, 2], "POST", 88, 1001)
    assert db.execute.await_count == 2

    db = build_db(execute_side_effect=[FakeResult(rows=[(1,)])])
    with pytest.raises(ResourceHTTPException):
        await AttachmentService.bind_attachments_to_target(db, [1, 2], "POST", 88, 1001)

    db = build_db()
    with pytest.raises(BusinessHTTPException):
        await AttachmentService.bind_attachments_to_target(db, [1], "BAD", 1, 1)

    db = build_db(execute_side_effect=[FakeResult(rows=[(88, "a.png"), (88, "b.png"), (None, "x.png")])])
    monkeypatch.setattr(AttachmentService, "to_public_url", lambda url: f"/{url.strip('/')}" if url else url)
    mapping = await AttachmentService.get_urls_by_target(db, "POST", [88, 99])
    assert mapping[88] == ["/a.png", "/b.png"]
    assert mapping[99] == []

    with pytest.raises(BusinessHTTPException):
        await AttachmentService.get_urls_by_target(build_db(), "BAD", [1])


async def test_attachment_url_helpers():
    assert AttachmentService.to_public_url(None) is None
    assert AttachmentService.to_public_url("abc") == "/abc"

    db = build_db(execute_side_effect=[SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: None))])
    assert await AttachmentService.get_attachment_url_by_id(1, db) is None

    db = build_db(
        execute_side_effect=[SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: {"url": "/x.png"}))]
    )
    assert await AttachmentService.get_attachment_url_by_id(1, db) == "/x.png"
    assert await AttachmentService.get_attachment_url_by_id(None, db) is None
