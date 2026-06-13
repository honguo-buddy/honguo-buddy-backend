from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.core import BusinessHTTPException
from app.models.goods import GoodsStatus, GoodsCondition
from app.schemas.goods import GoodsCreate, GoodsUpdate
from app.services.attachment_service import AttachmentService
from app.services.goods_service import GoodsService
from tests.unit.fake_sqlalchemy import FakeResult


def build_db(*, execute_side_effect=None, scalar_one_or_none=None):
    db = SimpleNamespace()
    db.execute = AsyncMock(side_effect=execute_side_effect or [])
    db.get = AsyncMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.refresh = AsyncMock()
    db.add = Mock()
    return db


def build_goods(goods_id=1, publisher_id=1001, is_deleted=False, **overrides):
    payload = {
        "goods_id": goods_id,
        "publisher_id": publisher_id,
        "category_id": 1,
        "name": "test goods",
        "description": "desc",
        "price": 10.0,
        "condition": GoodsCondition.BRAND_NEW,
        "status": GoodsStatus.ON_SALE,
        "template_data": {},
        "is_deleted": is_deleted,
        "user": SimpleNamespace(avatar_id=None),
        "attachments": [],
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


class TestCreateGoods:
    async def test_create_minimal_goods(self):
        db = build_db()
        obj_in = GoodsCreate(name="test", category_id=1)
        goods = await GoodsService.create_goods(db, 1001, obj_in)
        assert goods.publisher_id == 1001
        assert goods.name == "test"
        db.add.assert_called_once()
        db.flush.assert_called_once()
        db.commit.assert_called_once()

    async def test_create_with_attachments(self, monkeypatch):
        db = build_db()
        bind_calls = []

        async def fake_bind_attachments_to_target(db, attachment_ids, target_type, target_id, creator_id):
            bind_calls.append(
                {
                    "attachment_ids": attachment_ids,
                    "target_type": target_type,
                    "target_id": target_id,
                    "creator_id": creator_id,
                }
            )

        monkeypatch.setattr(
            "app.services.goods_service.AttachmentService.bind_attachments_to_target",
            fake_bind_attachments_to_target,
            raising=False,
        )
        obj_in = GoodsCreate(name="with pics", category_id=1, attachment_ids=[5, 6])
        goods = await GoodsService.create_goods(db, 1001, obj_in)
        assert goods.publisher_id == 1001
        assert bind_calls == [
            {
                "attachment_ids": [5, 6],
                "target_type": "GOODS",
                "target_id": goods.goods_id,
                "creator_id": 1001,
            }
        ]

    async def test_create_rejects_invalid_expire_time(self):
        db = build_db()
        obj_in = GoodsCreate(name="bad expire", category_id=1, expire_time="not-a-real-time")

        with pytest.raises(BusinessHTTPException) as exc_info:
            await GoodsService.create_goods(db, 1001, obj_in)

        assert "截止时间格式不正确" in exc_info.value.detail["msg"]
        db.add.assert_not_called()
        db.commit.assert_not_awaited()


class TestGetGoodsById:
    async def test_get_existing_goods(self):
        goods = build_goods(goods_id=10)
        db = build_db()
        db.execute = AsyncMock(return_value=FakeResult(items=[goods]))
        result = await GoodsService.get_goods_by_id(db, 10)
        assert result is not None
        assert result.goods_id == 10
        assert result.is_deleted == False

    async def test_get_deleted_returns_none(self):
        goods = build_goods(goods_id=10, is_deleted=True)
        db = build_db()
        db.execute = AsyncMock(return_value=FakeResult(items=[goods]))
        result = await GoodsService.get_goods_by_id(db, 10)
        assert result == goods  # query filters deleted in SQL

    async def test_get_nonexistent_returns_none(self):
        db = build_db()
        db.execute = AsyncMock(return_value=FakeResult(items=[]))
        result = await GoodsService.get_goods_by_id(db, 999)
        assert result is None

    async def test_get_goods_by_id_retries_when_attachment_sort_order_column_missing(self, monkeypatch):
        goods = build_goods(goods_id=10)
        db = build_db()

        class FakeOrigExc(Exception):
            args = (1054, "Unknown column 'sort_order' in 'field list'")

        execute_count = {"value": 0}

        async def execute_side_effect(stmt):
            execute_count["value"] += 1
            if execute_count["value"] == 1:
                raise OperationalError("SELECT attachment.sort_order ...", {}, FakeOrigExc())
            return FakeResult(items=[goods])

        db.execute = AsyncMock(side_effect=execute_side_effect)
        ensure_mock = AsyncMock()
        hydrate_mock = AsyncMock()
        monkeypatch.setattr(AttachmentService, "ensure_sort_order_column", ensure_mock, raising=False)
        monkeypatch.setattr(GoodsService, "_hydrate_goods_avatar", hydrate_mock, raising=False)

        result = await GoodsService.get_goods_by_id(db, 10)

        assert result is not None
        assert result.goods_id == 10
        ensure_mock.assert_awaited_once_with(db)
        db.rollback.assert_awaited_once()
        db.commit.assert_awaited_once()


class TestListGoods:
    async def test_list_all_returns_items_and_total(self):
        g1 = build_goods(goods_id=1)
        g2 = build_goods(goods_id=2)
        # First execute = count query, second = data query
        db = build_db()
        db.execute = AsyncMock(side_effect=[
            FakeResult(scalar_value=5),
            FakeResult(items=[g1, g2]),
        ])
        items, total = await GoodsService.list_all_goods(db, page=1, page_size=20)
        assert total == 5
        assert len(items) == 2

    async def test_list_by_user_returns_user_items(self):
        g1 = build_goods(goods_id=1, publisher_id=2001)
        db = build_db()
        db.execute = AsyncMock(side_effect=[
            FakeResult(scalar_value=1),
            FakeResult(items=[g1]),
        ])
        items, total = await GoodsService.list_goods_by_user(db, 2001)
        assert total == 1
        assert items[0].publisher_id == 2001


class TestUpdateGoods:
    async def test_update_name_and_price(self):
        goods = build_goods()
        db = build_db()
        obj = GoodsUpdate(name="updated", price=20.0)
        result = await GoodsService.update_goods(db, goods, obj)
        assert result.name == "updated"
        assert result.price == 20.0
        db.commit.assert_called_once()

    async def test_update_status(self):
        goods = build_goods(status=GoodsStatus.ON_SALE)
        db = build_db()
        obj = GoodsUpdate(status="已下架")
        result = await GoodsService.update_goods(db, goods, obj)
        assert result.status == GoodsStatus.OFF_SHELF

    async def test_update_rebinds_attachments_with_owner_check(self, monkeypatch):
        goods = build_goods(goods_id=9)
        db = build_db()
        bind_calls = []
        unbind_calls = []

        async def fake_bind_attachments_to_target(db, attachment_ids, target_type, target_id, creator_id):
            bind_calls.append(
                {
                    "attachment_ids": attachment_ids,
                    "target_type": target_type,
                    "target_id": target_id,
                    "creator_id": creator_id,
                }
            )

        async def fake_unbind_attachments_from_target(db, target_type, target_id):
            unbind_calls.append(
                {
                    "target_type": target_type,
                    "target_id": target_id,
                }
            )

        monkeypatch.setattr(
            "app.services.goods_service.AttachmentService.bind_attachments_to_target",
            fake_bind_attachments_to_target,
            raising=False,
        )
        monkeypatch.setattr(
            "app.services.goods_service.AttachmentService.unbind_attachments_from_target",
            fake_unbind_attachments_from_target,
            raising=False,
        )

        obj = GoodsUpdate(attachment_ids=[8, 9])
        result = await GoodsService.update_goods(db, goods, obj)

        assert result.goods_id == 9
        assert unbind_calls == [
            {
                "target_type": "GOODS",
                "target_id": 9,
            }
        ]
        assert bind_calls == [
            {
                "attachment_ids": [8, 9],
                "target_type": "GOODS",
                "target_id": 9,
                "creator_id": goods.publisher_id,
            }
        ]

    async def test_update_allows_clearing_attachments(self, monkeypatch):
        goods = build_goods(goods_id=10)
        db = build_db()
        bind_mock = AsyncMock()
        unbind_calls = []

        async def fake_unbind_attachments_from_target(db, target_type, target_id):
            unbind_calls.append(
                {
                    "target_type": target_type,
                    "target_id": target_id,
                }
            )

        monkeypatch.setattr(
            "app.services.goods_service.AttachmentService.bind_attachments_to_target",
            bind_mock,
            raising=False,
        )
        monkeypatch.setattr(
            "app.services.goods_service.AttachmentService.unbind_attachments_from_target",
            fake_unbind_attachments_from_target,
            raising=False,
        )

        obj = GoodsUpdate(attachment_ids=[])
        result = await GoodsService.update_goods(db, goods, obj)

        assert result.goods_id == 10
        assert unbind_calls == [
            {
                "target_type": "GOODS",
                "target_id": 10,
            }
        ]
        bind_mock.assert_not_awaited()

    async def test_update_goods_keeps_attachment_order(self):
        goods = build_goods(
            goods_id=11,
            attachments=[
                SimpleNamespace(attachment_id=20, url="/static/goods/20.webp", is_deleted=False),
                SimpleNamespace(attachment_id=21, url="/static/goods/21.webp", is_deleted=False),
            ],
        )
        db = build_db()
        obj = GoodsUpdate(name="ordered")
        result = await GoodsService.update_goods(db, goods, obj)
        assert [att.attachment_id for att in result.attachments] == [20, 21]


class TestSoftDelete:
    async def test_soft_delete_marks_deleted(self):
        goods = build_goods()
        db = build_db()
        await GoodsService.soft_delete_goods(db, goods)
        assert goods.is_deleted is True
        db.commit.assert_called_once()
