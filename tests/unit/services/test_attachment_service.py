from __future__ import annotations

import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image

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


def build_image_bytes(size: tuple[int, int], fmt: str = "PNG", color=(20, 120, 220, 255)) -> bytes:
    mode = "RGB" if fmt.upper() in {"JPEG", "JPG", "BMP"} else "RGBA"
    image_color = color[:3] if mode == "RGB" else color
    image = Image.new(mode, size, image_color)
    buffer = io.BytesIO()
    image.save(buffer, format=fmt)
    image.close()
    return buffer.getvalue()


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


def read_image_size(data: bytes) -> tuple[str, tuple[int, int]]:
    with Image.open(io.BytesIO(data)) as image:
        return image.format, image.size


async def test_upload_rejects_large_file_and_non_image():
    db = build_db()
    current_user = SimpleNamespace(user_id=1001)

    with pytest.raises(BusinessHTTPException) as non_image_exc:
        await AttachmentService.upload(DummyFile("bad.txt", b"not-an-image"), "USER", None, current_user, db)
    assert non_image_exc.value.detail["msg"] == "不支持的文件类型"

    with pytest.raises(BusinessHTTPException) as too_large_exc:
        await AttachmentService.upload(
            DummyFile("a.png", b"x" * (5 * 1024 * 1024 + 1)),
            "USER",
            None,
            current_user,
            db,
        )
    assert too_large_exc.value.detail["msg"] == "文件大小不能超过 5MB"


async def test_upload_success_avatar_crop_and_target_fallback(monkeypatch):
    db = build_db(execute_side_effect=[FakeResult()])
    current_user = SimpleNamespace(user_id=1001)
    fake_file = DummyAioFile()

    monkeypatch.setattr("app.services.attachment_service.time.time_ns", lambda: 123456)
    monkeypatch.setattr("app.services.attachment_service.aiofiles.open", lambda *args, **kwargs: fake_file)

    attachment = await AttachmentService.upload(
        DummyFile("avatar.png", build_image_bytes((420, 300))),
        target_type="INVALID",
        target_id=None,
        current_user=current_user,
        db=db,
    )

    image_format, image_size = read_image_size(fake_file.written)
    assert image_format == "WEBP"
    assert image_size == (200, 200)
    assert attachment.target_type == AttachmentTargetType.USER
    assert attachment.target_id == current_user.user_id
    assert attachment.url == "/static/avatar/user_1001_123456.webp"
    assert db.commit.await_count == 1


async def test_upload_resizes_post_and_default_targets(monkeypatch):
    db = build_db()
    current_user = SimpleNamespace(user_id=2002)
    post_file = DummyAioFile()
    chat_file = DummyAioFile()
    open_calls = []

    def fake_open(*args, **kwargs):
        open_calls.append(args[0])
        return post_file if len(open_calls) == 1 else chat_file

    monkeypatch.setattr("app.services.attachment_service.time.time_ns", lambda: 999)
    monkeypatch.setattr("app.services.attachment_service.aiofiles.open", fake_open)

    post_attachment = await AttachmentService.upload(
        DummyFile("post.jpg", build_image_bytes((2400, 1600), fmt="JPEG", color=(120, 20, 30, 255))),
        target_type="POST",
        target_id=88,
        current_user=current_user,
        db=db,
    )
    post_format, post_size = read_image_size(post_file.written)
    assert post_format == "WEBP"
    assert post_size == (1080, 720)
    assert post_attachment.url == "/static/post/post_2002_999.webp"

    chat_attachment = await AttachmentService.upload(
        DummyFile("chat.png", build_image_bytes((1600, 1200))),
        target_type="CHAT",
        target_id=99,
        current_user=current_user,
        db=db,
    )
    chat_format, chat_size = read_image_size(chat_file.written)
    assert chat_format == "WEBP"
    assert chat_size == (800, 600)
    assert chat_attachment.url == "/static/chat/chat_2002_999.webp"


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

    db = build_db(execute_side_effect=[FakeResult(rows=[(88, "a.webp"), (88, "b.webp"), (None, "x.webp")])])
    monkeypatch.setattr(AttachmentService, "to_public_url", lambda url: f"/{url.strip('/')}" if url else url)
    mapping = await AttachmentService.get_urls_by_target(db, "POST", [88, 99])
    assert mapping[88] == ["/a.webp", "/b.webp"]
    assert mapping[99] == []

    with pytest.raises(BusinessHTTPException):
        await AttachmentService.get_urls_by_target(build_db(), "BAD", [1])


async def test_attachment_url_helpers():
    assert AttachmentService.to_public_url(None) is None
    assert AttachmentService.to_public_url("abc") == "/abc"

    db = build_db(execute_side_effect=[SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: None))])
    assert await AttachmentService.get_attachment_url_by_id(1, db) is None

    db = build_db(
        execute_side_effect=[SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: {"url": "/x.webp"}))]
    )
    assert await AttachmentService.get_attachment_url_by_id(1, db) == "/x.webp"
    assert await AttachmentService.get_attachment_url_by_id(None, db) is None
