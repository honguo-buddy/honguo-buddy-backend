from datetime import datetime

import pytest
from pydantic import ValidationError

from app.models.goods import GoodsCondition, GoodsStatus
from app.schemas.goods import (
    GoodsAttachmentBriefRead,
    GoodsBase,
    GoodsCreate,
    GoodsUpdate,
    GoodsPublisherSchema,
    GoodsRead,
    GoodsDetailRead,
    GoodsListResponse,
)


class TestGoodsCreate:
    def test_valid_minimal(self):
        obj = GoodsCreate(name="test", category_id=1, price=10.0)
        assert obj.name == "test"
        assert obj.price == 10.0

    def test_price_negative_rejected(self):
        with pytest.raises(ValidationError) as exc:
            GoodsCreate(name="bad", category_id=1, price=-5.0)
        assert "price" in str(exc.value)

    def test_defaults_applied(self):
        obj = GoodsCreate(name="defaults", category_id=1)
        assert obj.condition == GoodsCondition.BRAND_NEW
        assert obj.attachment_ids == []
        assert obj.template_data == {}


class TestGoodsUpdate:
    def test_empty_update_allowed(self):
        obj = GoodsUpdate()
        assert obj.model_dump(exclude_unset=True) == {}

    def test_partial_update(self):
        obj = GoodsUpdate(name="new name", status="上架中")
        data = obj.model_dump(exclude_unset=True)
        assert data["name"] == "new name"
        assert data["status"] == GoodsStatus.ON_SALE


class TestGoodsRead:
    def test_counter_fields_default_to_zero(self):
        obj = GoodsRead(
            goods_id=1,
            category_id=2,
            name="test",
            condition=GoodsCondition.BRAND_NEW,
            status=GoodsStatus.ON_SALE,
            create_time=datetime(2026, 5, 30),
        )
        assert obj.view_count == 0
        assert obj.favorite_count == 0
        assert obj.comment_count == 0

    def test_counter_fields_can_be_set(self):
        obj = GoodsRead(
            goods_id=1,
            category_id=2,
            name="test",
            condition=GoodsCondition.BRAND_NEW,
            status=GoodsStatus.ON_SALE,
            create_time=datetime(2026, 5, 30),
            view_count=42,
            favorite_count=7,
            comment_count=3,
        )
        assert obj.view_count == 42
        assert obj.favorite_count == 7
        assert obj.comment_count == 3

    def test_template_data_field_exists(self):
        obj = GoodsRead(
            goods_id=1,
            category_id=2,
            name="test",
            condition=GoodsCondition.BRAND_NEW,
            status=GoodsStatus.ON_SALE,
            create_time=datetime(2026, 5, 30),
            template_data={"brand": "Apple"},
        )
        assert obj.model_dump()["template_data"] == {"brand": "Apple"}


class TestGoodsDetailRead:
    def test_inherited_fields_exist(self):
        obj = GoodsDetailRead(
            goods_id=1,
            category_id=2,
            name="test",
            condition=GoodsCondition.BRAND_NEW,
            status=GoodsStatus.ON_SALE,
            create_time=datetime(2026, 5, 30),
        )
        assert obj.goods_id == 1
        assert obj.description is None

    def test_attachment_briefs_exist(self):
        obj = GoodsDetailRead(
            goods_id=1,
            category_id=2,
            name="test",
            condition=GoodsCondition.BRAND_NEW,
            status=GoodsStatus.ON_SALE,
            create_time=datetime(2026, 5, 30),
            attachment_urls=["/static/goods/a.webp"],
            attachments=[GoodsAttachmentBriefRead(id=201, url="/static/goods/a.webp")],
        )
        assert obj.attachments[0].id == 201


class TestGoodsListResponse:
    def test_empty_list(self):
        obj = GoodsListResponse(total=0, page=1, page_size=20, list=[])
        assert obj.total == 0
        assert obj.list == []


class TestGoodsPublisherSchema:
    def test_from_attributes_config(self):
        assert GoodsPublisherSchema.model_config.get("from_attributes") is True
