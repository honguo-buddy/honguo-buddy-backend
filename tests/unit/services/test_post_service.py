from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.post_service import PostService
from app.core.exception_handler import ResourceHTTPException
from app.models import PostStatus
from app.schemas.post import PostCreate
from tests.unit.fake_sqlalchemy import FakeResult


@pytest.mark.asyncio
async def test_resolve_default_category_id_success():
    class FakeDB:
        async def execute(self, stmt):
            return FakeResult(scalar_value=5)

    db = FakeDB()
    val = await PostService._resolve_default_category_id(db)
    assert val == 5


@pytest.mark.asyncio
async def test_resolve_default_category_id_none_raises():
    class FakeDB:
        async def execute(self, stmt):
            return FakeResult(scalar_value=None)

    with pytest.raises(ResourceHTTPException):
        await PostService._resolve_default_category_id(FakeDB())


@pytest.mark.asyncio
async def test_get_post_for_update_found():
    class FakeDB:
        async def execute(self, stmt):
            return FakeResult(items=[SimpleNamespace(post_id=1)])

    post = await PostService._get_post_for_update(FakeDB(), 1)
    assert post.post_id == 1


@pytest.mark.asyncio
async def test_get_post_for_update_not_found_raises():
    class FakeDB:
        async def execute(self, stmt):
            return FakeResult(items=[])

    with pytest.raises(ResourceHTTPException):
        await PostService._get_post_for_update(FakeDB(), 999)


@pytest.mark.asyncio
async def test_create_post_with_attachments_and_bind(monkeypatch):
    # prepare fake db which resolves default category and handles add/flush/refresh/commit
    class FakeDB2:
        def __init__(self):
            self.added = None

        def add(self, obj):
            self.added = obj

        async def flush(self):
            # assign id as DB would
            if self.added is not None:
                setattr(self.added, "post_id", 123)

        async def refresh(self, obj, attribute_names=None):
            return None

        async def commit(self):
            return None

        async def execute(self, stmt):
            # called first to resolve default category id
            return FakeResult(scalar_value=9)

    called = {"count": 0}

    async def fake_bind(db, attachment_ids, target_type, target_id, creator_id):
        assert attachment_ids == [1, 2]
        assert target_type == "POST"
        assert target_id == 123
        assert creator_id == 88
        called["count"] += 1

    monkeypatch.setattr("app.services.attachment_service.AttachmentService.bind_attachments_to_target", fake_bind, raising=False)

    pc = PostCreate(title="t", description="d", price=1.0)
    db = FakeDB2()
    post = await PostService.create_post(db, publisher_id=88, post_create=pc, attachment_ids=[1, 2])
    assert post.post_id == 123
    assert post.status == PostStatus.OPEN
    assert called["count"] == 1


@pytest.mark.asyncio
async def test_create_post_bind_raises_logs(monkeypatch):
    class FakeDB3:
        def __init__(self):
            self.added = None

        def add(self, obj):
            self.added = obj

        async def flush(self):
            if self.added is not None:
                setattr(self.added, "post_id", 222)

        async def refresh(self, obj, attribute_names=None):
            return None

        async def commit(self):
            return None

        async def execute(self, stmt):
            return FakeResult(scalar_value=11)

    async def fake_bind_raising(db, attachment_ids, target_type, target_id, creator_id):
        if 1 in attachment_ids:
            raise Exception("boom")

    monkeypatch.setattr("app.services.attachment_service.AttachmentService.bind_attachments_to_target", fake_bind_raising, raising=False)

    pc = PostCreate(title="x", description=None, price=3.0)
    db3 = FakeDB3()
    post = await PostService.create_post(db3, publisher_id=7, post_create=pc, attachment_ids=[1, 2])
    assert post.post_id == 222
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exception_handler import BusinessHTTPException, ResourceHTTPException
from app.models import Direction, PostStatus, UrgencyLevel
from app.schemas.post import PostCreate, PostUpdate
from app.services.attachment_service import AttachmentService
from app.services.post_service import PostService
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


def build_post_obj(**overrides):
    payload = {
        "post_id": 9001,
        "publisher_id": 1001,
        "title": "原始标题",
        "description": "原始描述",
        "price": Decimal("10.0"),
        "direction": Direction.SELL,
        "urgency": UrgencyLevel.NORMAL,
        "category_id": 1,
        "status": PostStatus.OPEN,
        "template_data": {"max_accepters": 1},
        "is_deleted": False,
        "create_time": datetime(2026, 5, 27, 10, 0),
        "user": None,
        "attachments": [],
        "orders": [],
        "comments": [],
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


async def test_resolve_default_category_id_success_and_failure():
    db = build_db(execute_side_effect=[FakeResult(scalar_value=11)])
    assert await PostService._resolve_default_category_id(db) == 11

    db = build_db(execute_side_effect=[FakeResult(scalar_value=None)])
    with pytest.raises(ResourceHTTPException) as exc_info:
        await PostService._resolve_default_category_id(db)
    assert "暂无可用分类" in exc_info.value.detail["msg"]


async def test_create_post_with_fallback_enums_and_attachment_binding(monkeypatch):
    db = build_db(execute_side_effect=[FakeResult(scalar_value=3)])

    async def refresh_side_effect(post, attribute_names=None):
        if getattr(post, "post_id", None) is None:
            post.post_id = 777

    db.refresh = AsyncMock(side_effect=refresh_side_effect)
    bind_mock = AsyncMock(side_effect=[None, RuntimeError("bind failed")])
    monkeypatch.setattr(AttachmentService, "bind_attachments_to_target", bind_mock, raising=False)

    payload = PostCreate.model_validate(
        {
            "title": "测试创建",
            "description": "desc",
            "price": 12.5,
            "direction": "INVALID",
            "urgency": "INVALID",
            "max_accepters": 2,
            "template_filters": {"k": "v"},
        }
    )

    post = await PostService.create_post(db, publisher_id=1001, post_create=payload, attachment_ids=[1, 2])

    assert post.direction == Direction.SELL
    assert post.urgency == UrgencyLevel.NORMAL
    assert post.category_id == 3
    assert post.template_data["max_accepters"] == 2
    assert bind_mock.await_count == 1
    assert db.commit.await_count == 1


async def test_update_post_permission_status_pending_and_field_updates(monkeypatch):
    post = build_post_obj(template_data={"a": 1, "max_accepters": 1})
    monkeypatch.setattr(PostService, "_get_post_for_update", AsyncMock(return_value=post))

    db = build_db(execute_side_effect=[FakeResult(scalar_value=0)])

    with pytest.raises(BusinessHTTPException):
        await PostService.update_post(db, post_id=1, payload=PostUpdate.model_validate({"title": "x"}), operator_id=9999)

    post.status = PostStatus.IN_PROGRESS
    with pytest.raises(BusinessHTTPException):
        await PostService.update_post(db, post_id=1, payload=PostUpdate.model_validate({"title": "x"}), operator_id=1001)

    post.status = PostStatus.OPEN
    db = build_db(execute_side_effect=[FakeResult(scalar_value=2)])
    with pytest.raises(BusinessHTTPException) as pending_err:
        await PostService.update_post(db, post_id=1, payload=PostUpdate.model_validate({"title": "x"}), operator_id=1001)
    assert "禁止修改委托信息" in pending_err.value.detail["msg"]

    post.status = PostStatus.OPEN
    db = build_db(execute_side_effect=[FakeResult(scalar_value=0)])
    payload = PostUpdate.model_validate(
        {
            "title": "新标题",
            "description": "新描述",
            "price": 22.5,
            "direction": "BUY",
            "urgency": "URGENT",
            "category_id": 2,
            "max_accepters": 3,
            "template_filters": {"b": 2},
        }
    )
    updated = await PostService.update_post(db, post_id=1, payload=payload, operator_id=1001)
    assert updated.title == "新标题"
    assert updated.direction == Direction.BUY
    assert updated.urgency == UrgencyLevel.URGENT
    assert updated.template_data["b"] == 2
    assert updated.template_data["max_accepters"] == 3


async def test_update_post_binds_attachments(monkeypatch):
    post = build_post_obj()
    monkeypatch.setattr(PostService, "_get_post_for_update", AsyncMock(return_value=post))
    db = build_db(execute_side_effect=[FakeResult(scalar_value=0)])
    bind_mock = AsyncMock()
    monkeypatch.setattr(AttachmentService, "bind_attachments_to_target", bind_mock, raising=False)

    payload = PostUpdate.model_validate({"attachment_ids": [99, 100]})
    await PostService.update_post(db, post_id=1, payload=payload, operator_id=1001)

    bind_mock.assert_awaited_once_with(
        db=db,
        attachment_ids=[99, 100],
        target_type="POST",
        target_id=post.post_id,
        creator_id=1001,
    )


async def test_update_post_rejects_invalid_direction_and_urgency(monkeypatch):
    post = build_post_obj()
    monkeypatch.setattr(PostService, "_get_post_for_update", AsyncMock(return_value=post))
    db = build_db(execute_side_effect=[FakeResult(scalar_value=0)])

    with pytest.raises(BusinessHTTPException):
        await PostService.update_post(
            db,
            post_id=1,
            payload=PostUpdate.model_validate({"direction": "UNKNOWN"}),
            operator_id=post.publisher_id,
        )

    db = build_db(execute_side_effect=[FakeResult(scalar_value=0)])
    with pytest.raises(BusinessHTTPException):
        await PostService.update_post(
            db,
            post_id=1,
            payload=PostUpdate.model_validate({"urgency": "UNKNOWN"}),
            operator_id=post.publisher_id,
        )


async def test_soft_delete_post_permission(monkeypatch):
    post = build_post_obj()
    db = build_db()
    monkeypatch.setattr(PostService, "_get_post_for_update", AsyncMock(return_value=post))

    with pytest.raises(BusinessHTTPException):
        await PostService.soft_delete_post(db, post_id=1, operator_id=9999)

    deleted = await PostService.soft_delete_post(db, post_id=1, operator_id=1001)
    assert deleted.is_deleted is True


async def test_list_posts_and_user_scopes(monkeypatch):
    post = build_post_obj()
    db = build_db(execute_side_effect=[FakeResult(scalar_value=1), FakeResult(items=[post])])
    monkeypatch.setattr("app.services.post_service.parse_datetime_to_beijing_naive", lambda value: datetime(2026, 5, 27, 9, 0))

    posts, total = await PostService.list_posts(
        db,
        keyword="跑腿",
        category_id=1,
        urgency="NORMAL,URGENT",
        direction="SELL",
        price_min=1.0,
        price_max=99.0,
        create_time_start="2026-05-27T00:00:00",
        create_time_end="2026-05-28T00:00:00",
        status="INVALID",
            template_filters=None,
    )
    assert total == 1
    assert len(posts) == 1

    db_user = build_db(execute_side_effect=[FakeResult(scalar_value=1), FakeResult(items=[post])])
    scoped_posts, scoped_total = await PostService.list_posts_by_user(db_user, user_id=1001, status="OPEN,INVALID", public_only=False)
    assert scoped_total == 1
    assert len(scoped_posts) == 1

    db_public = build_db(execute_side_effect=[FakeResult(scalar_value=1), FakeResult(items=[post])])
    public_posts, public_total = await PostService.list_public_posts_by_user(db_public, user_id=1001, status="CLOSED,INVALID")
    assert public_total == 1
    assert len(public_posts) == 1


async def test_get_post_detail_success_and_not_found():
    post = build_post_obj()
    db = build_db(execute_side_effect=[FakeResult(items=[post])])
    result = await PostService.get_post_detail(db, post_id=1)
    assert result.post_id == post.post_id

    db = build_db(execute_side_effect=[FakeResult(items=[])])
    with pytest.raises(ResourceHTTPException) as exc_info:
        await PostService.get_post_detail(db, post_id=1)
    assert "帖子不存在或已删除" in exc_info.value.detail["msg"]
