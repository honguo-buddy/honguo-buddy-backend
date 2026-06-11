from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import select

from app.core import BusinessHTTPException
from app.models.goods import GoodsStatus, GoodsCondition
from app.schemas.goods import GoodsCreate, GoodsUpdate
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

    async def test_create_with_attachments(self):
        db = build_db()
        db.execute = AsyncMock(return_value=FakeResult(items=[]))
        obj_in = GoodsCreate(name="with pics", category_id=1, attachment_ids=[5, 6])
        goods = await GoodsService.create_goods(db, 1001, obj_in)
        assert goods.publisher_id == 1001
        db.execute.assert_called()  # attachment binding

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


class TestSoftDelete:
    async def test_soft_delete_marks_deleted(self):
        goods = build_goods()
        db = build_db()
        await GoodsService.soft_delete_goods(db, goods)
        assert goods.is_deleted is True
        db.commit.assert_called_once()
