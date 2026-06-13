"""Goods API integration tests."""

import io

import pytest
from httpx import AsyncClient
from PIL import Image

from app.core import settings
from app.models import Attachment, AttachmentTargetType, Category, Goods, GoodsCondition, GoodsStatus, User
from tests.helpers import assert_api_success, assert_api_error


async def _bind_user_token(fake_redis, user: User) -> str:
    from app.core import create_access_token
    token = create_access_token(data={"sub": str(user.user_id), "user_name": user.user_name, "user_type": "user"})
    await fake_redis.set(f"token:{token}", str(user.user_id))
    return token


def build_upload_image_bytes(size=(320, 240), fmt="PNG") -> bytes:
    image = Image.new("RGBA", size, (40, 80, 160, 255))
    buffer = io.BytesIO()
    image.save(buffer, format=fmt)
    image.close()
    return buffer.getvalue()


class TestCreateGoods:
    async def test_create_goods_success(self, client: AsyncClient, db_session, test_user, test_user_token, fake_redis):
        await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))

        category = Category(category_id=501, name="goods-cat", config_json={})
        db_session.add(category)
        await db_session.flush()

        resp = await client.post(
            "/goods/",
            headers={"Authorization": f"Bearer {test_user_token}"},
            json={"name": "test goods", "category_id": 501, "price": 99.0},
        )
        assert resp.status_code == 200
        msg = assert_api_success(resp.json())
        assert msg["name"] == "test goods"
        assert msg["price"] == 99.0

    async def test_create_goods_requires_auth(self, client: AsyncClient):
        resp = await client.post("/goods/", json={"name": "no auth", "category_id": 1})
        assert resp.status_code == 200
        assert resp.json()["code"] == settings.TOKEN_INVALID_CODE

    async def test_create_goods_rejects_when_goods_quota_exhausted(self, client: AsyncClient, db_session, test_user, test_user_token, fake_redis):
        from app.core import DynamicConfigManager, settings

        await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
        await fake_redis.set(f"user_token:{test_user.user_id}", test_user_token)

        DynamicConfigManager()._cache["MAX_OPEN_GOODS_PER_USER"] = 1

        category = Category(category_id=517, name="goods-quota-cat", config_json={})
        db_session.add(category)
        await db_session.flush()

        goods = Goods(
            goods_id=5017,
            publisher_id=test_user.user_id,
            category_id=category.category_id,
            name="existing on sale goods",
            price=77.0,
            condition=GoodsCondition.BRAND_NEW,
            status=GoodsStatus.ON_SALE,
        )
        db_session.add(goods)
        await db_session.flush()

        resp = await client.post(
            "/goods/",
            headers={"Authorization": f"Bearer {test_user_token}"},
            json={"name": "new goods", "category_id": 517, "price": 99.0},
        )
        assert resp.status_code == 200
        message = assert_api_error(resp.json(), code=settings.DATA_GET_FAILED_CODE)
        assert "当前发布的活跃商品已达上限" in message["msg"]
        DynamicConfigManager()._cache.pop("MAX_OPEN_GOODS_PER_USER", None)


class TestListGoods:
    async def test_list_goods_empty(self, client: AsyncClient, db_session, fake_redis):
        resp = await client.get("/goods/")
        assert resp.status_code == 200
        msg = assert_api_success(resp.json())
        assert msg["total"] == 0
        assert msg["list"] == []

    async def test_list_goods_returns_items(self, client: AsyncClient, db_session, test_user, fake_redis):
        category = Category(category_id=502, name="list-cat", config_json={})
        db_session.add(category)
        await db_session.flush()

        goods = Goods(
            goods_id=5001,
            publisher_id=test_user.user_id,
            category_id=category.category_id,
            name="listed goods",
            price=50.0,
            condition=GoodsCondition.BRAND_NEW,
            status=GoodsStatus.ON_SALE,
        )
        db_session.add(goods)
        await db_session.flush()

        resp = await client.get("/goods/")
        assert resp.status_code == 200
        msg = assert_api_success(resp.json())
        assert msg["total"] >= 1

    async def test_list_goods_returns_template_data(self, client: AsyncClient, db_session, test_user, fake_redis):
        category = Category(category_id=512, name="template-cat", config_json={})
        db_session.add(category)
        await db_session.flush()

        goods = Goods(
            goods_id=5012,
            publisher_id=test_user.user_id,
            category_id=category.category_id,
            name="templated goods",
            price=88.0,
            condition=GoodsCondition.BRAND_NEW,
            status=GoodsStatus.ON_SALE,
            template_data={"brand": "Apple", "model": "M3 Pro"},
        )
        db_session.add(goods)
        await db_session.flush()

        resp = await client.get("/goods/")
        assert resp.status_code == 200
        msg = assert_api_success(resp.json())
        cards = [c for c in msg["list"] if c["goods_id"] == 5012]
        assert len(cards) == 1
        assert cards[0]["template_data"] == {"brand": "Apple", "model": "M3 Pro"}

    async def test_list_goods_hydrated_counters(self, client: AsyncClient, db_session, test_user, fake_redis):
        category = Category(category_id=503, name="hydrate-cat", config_json={})
        db_session.add(category)
        await db_session.flush()

        goods = Goods(
            goods_id=5002,
            publisher_id=test_user.user_id,
            category_id=category.category_id,
            name="hydrated goods",
            price=30.0,
            condition=GoodsCondition.BRAND_NEW,
            status=GoodsStatus.ON_SALE,
        )
        db_session.add(goods)
        await db_session.flush()

        fake_redis._data["_hash:metrics:goods:5002"] = {"view": "10", "favorite": "3", "comment": "1"}

        resp = await client.get("/goods/")
        assert resp.status_code == 200
        msg = assert_api_success(resp.json())
        cards = [c for c in msg["list"] if c["goods_id"] == 5002]
        assert len(cards) == 1
        card = cards[0]
        assert card["view_count"] == 10
        assert card["favorite_count"] == 3
        assert card["comment_count"] == 1


class TestMyGoods:
    async def test_list_my_goods(self, client: AsyncClient, db_session, test_user, test_user_token, fake_redis):
        await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))

        category = Category(category_id=504, name="my-cat", config_json={})
        db_session.add(category)
        await db_session.flush()

        goods = Goods(
            goods_id=5003,
            publisher_id=test_user.user_id,
            category_id=category.category_id,
            name="my goods",
            price=20.0,
            condition=GoodsCondition.USED_WELL,
            status=GoodsStatus.ON_SALE,
        )
        db_session.add(goods)
        await db_session.flush()

        resp = await client.get("/goods/me", headers={"Authorization": f"Bearer {test_user_token}"})
        assert resp.status_code == 200
        msg = assert_api_success(resp.json())
        assert msg["total"] >= 1
        assert msg["list"][0]["name"] == "my goods"

    async def test_list_my_goods_hydrated_counters(self, client: AsyncClient, db_session, test_user, test_user_token, fake_redis):
        await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))

        category = Category(category_id=505, name="my-hydrate-cat", config_json={})
        db_session.add(category)
        await db_session.flush()

        goods = Goods(
            goods_id=5004,
            publisher_id=test_user.user_id,
            category_id=category.category_id,
            name="my hydrated goods",
            price=40.0,
            condition=GoodsCondition.BRAND_NEW,
            status=GoodsStatus.ON_SALE,
        )
        db_session.add(goods)
        await db_session.flush()

        fake_redis._data["_hash:metrics:goods:5004"] = {"view": "25", "favorite": "6", "comment": "2"}

        resp = await client.get("/goods/me", headers={"Authorization": f"Bearer {test_user_token}"})
        assert resp.status_code == 200
        msg = assert_api_success(resp.json())
        cards = [c for c in msg["list"] if c["goods_id"] == 5004]
        assert len(cards) == 1
        card = cards[0]
        assert card["view_count"] == 25
        assert card["favorite_count"] == 6
        assert card["comment_count"] == 2


class TestGoodsDetail:
    async def test_get_goods_detail(self, client: AsyncClient, db_session, test_user, fake_redis):
        category = Category(category_id=506, name="detail-cat", config_json={})
        db_session.add(category)
        await db_session.flush()

        goods = Goods(
            goods_id=5005,
            publisher_id=test_user.user_id,
            category_id=category.category_id,
            name="detail goods",
            description="a detailed description",
            price=60.0,
            condition=GoodsCondition.NEAR_NEW,
            status=GoodsStatus.ON_SALE,
        )
        db_session.add(goods)
        await db_session.flush()

        fake_redis._data["_hash:metrics:goods:5005"] = {"view": "5", "favorite": "2", "comment": "0"}

        resp = await client.get("/goods/5005")
        assert resp.status_code == 200
        msg = assert_api_success(resp.json())
        assert msg["name"] == "detail goods"
        assert msg["view_count"] == 6  # 5 in Redis + 1 from incr_goods_view
        assert msg["favorite_count"] == 2
        assert msg["comment_count"] == 0

    async def test_get_goods_detail_returns_attachment_briefs(self, client: AsyncClient, db_session, test_user, fake_redis):
        category = Category(category_id=515, name="detail-attachment-cat", config_json={})
        db_session.add(category)
        await db_session.flush()

        goods = Goods(
            goods_id=5015,
            publisher_id=test_user.user_id,
            category_id=category.category_id,
            name="detail goods attachment",
            description="with attachment id",
            price=66.0,
            condition=GoodsCondition.BRAND_NEW,
            status=GoodsStatus.ON_SALE,
        )
        db_session.add(goods)
        await db_session.flush()

        attachment = Attachment(
            attachment_id=7015,
            target_type=AttachmentTargetType.GOODS,
            target_id=goods.goods_id,
            url="/static/goods/detail.webp",
            creator_id=test_user.user_id,
        )
        db_session.add(attachment)
        await db_session.flush()

        resp = await client.get(f"/goods/{goods.goods_id}")
        assert resp.status_code == 200
        msg = assert_api_success(resp.json())
        assert msg["attachment_urls"] == ["/static/goods/detail.webp"]
        assert msg["attachments"] == [{"id": 7015, "url": "/static/goods/detail.webp"}]

    async def test_get_goods_detail_not_found(self, client: AsyncClient):
        resp = await client.get("/goods/99999")
        assert resp.status_code == 200
        assert resp.json()["code"] == settings.USER_GET_FAILED_CODE


class TestUpdateGoods:
    async def test_update_own_goods(self, client: AsyncClient, db_session, test_user, test_user_token, fake_redis):
        await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))

        category = Category(category_id=507, name="update-cat", config_json={})
        db_session.add(category)
        await db_session.flush()

        goods = Goods(
            goods_id=5006,
            publisher_id=test_user.user_id,
            category_id=category.category_id,
            name="old name",
            price=10.0,
            condition=GoodsCondition.BRAND_NEW,
            status=GoodsStatus.ON_SALE,
        )
        db_session.add(goods)
        await db_session.flush()

        resp = await client.patch(
            "/goods/5006",
            headers={"Authorization": f"Bearer {test_user_token}"},
            json={"name": "new name", "price": 25.0},
        )
        assert resp.status_code == 200
        msg = assert_api_success(resp.json())
        assert msg["name"] == "new name"
        assert msg["price"] == 25.0

    async def test_update_goods_replaces_attachments_and_returns_urls(
        self,
        client: AsyncClient,
        db_session,
        test_user,
        test_user_token,
        fake_redis,
    ):
        await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
        await fake_redis.set(f"user_token:{test_user.user_id}", test_user_token)

        category = Category(category_id=513, name="update-attachment-cat", config_json={})
        db_session.add(category)
        await db_session.flush()

        goods = Goods(
            goods_id=5013,
            publisher_id=test_user.user_id,
            category_id=category.category_id,
            name="old goods",
            price=12.0,
            condition=GoodsCondition.BRAND_NEW,
            status=GoodsStatus.ON_SALE,
        )
        db_session.add(goods)
        await db_session.flush()

        old_attachment = Attachment(
            attachment_id=7011,
            target_type=AttachmentTargetType.GOODS,
            target_id=goods.goods_id,
            url="/static/goods/old.webp",
            creator_id=test_user.user_id,
        )
        db_session.add(old_attachment)
        await db_session.flush()

        upload_resp = await client.post(
            "/attachments/upload",
            headers={"Authorization": f"Bearer {test_user_token}"},
            data={"target_type": "GOODS"},
            files={"file": ("goods.png", build_upload_image_bytes((640, 360)), "image/png")},
        )
        assert upload_resp.status_code == 200
        upload_msg = assert_api_success(upload_resp.json())
        new_attachment_id = upload_msg["id"]

        resp = await client.patch(
            f"/goods/{goods.goods_id}",
            headers={"Authorization": f"Bearer {test_user_token}"},
            json={"attachment_ids": [new_attachment_id]},
        )
        assert resp.status_code == 200
        msg = assert_api_success(resp.json())
        assert len(msg["attachment_urls"]) == 1
        assert msg["attachment_urls"][0].endswith(".webp")
        assert msg["attachments"] == [{"id": new_attachment_id, "url": msg["attachment_urls"][0]}]

        await db_session.refresh(old_attachment)
        assert old_attachment.target_id is None

        new_attachment = await db_session.get(Attachment, new_attachment_id)
        assert new_attachment is not None
        assert new_attachment.target_type == AttachmentTargetType.GOODS
        assert new_attachment.target_id == goods.goods_id
        assert new_attachment.sort_order == 0

    async def test_update_goods_allows_clearing_attachments(
        self,
        client: AsyncClient,
        db_session,
        test_user,
        test_user_token,
        fake_redis,
    ):
        await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
        await fake_redis.set(f"user_token:{test_user.user_id}", test_user_token)

        category = Category(category_id=514, name="clear-attachment-cat", config_json={})
        db_session.add(category)
        await db_session.flush()

        goods = Goods(
            goods_id=5014,
            publisher_id=test_user.user_id,
            category_id=category.category_id,
            name="clear goods",
            price=18.0,
            condition=GoodsCondition.BRAND_NEW,
            status=GoodsStatus.ON_SALE,
        )
        db_session.add(goods)
        await db_session.flush()

        old_attachment = Attachment(
            attachment_id=7012,
            target_type=AttachmentTargetType.GOODS,
            target_id=goods.goods_id,
            url="/static/goods/existing.webp",
            creator_id=test_user.user_id,
        )
        db_session.add(old_attachment)
        await db_session.flush()

        resp = await client.patch(
            f"/goods/{goods.goods_id}",
            headers={"Authorization": f"Bearer {test_user_token}"},
            json={"attachment_ids": []},
        )
        assert resp.status_code == 200
        msg = assert_api_success(resp.json())
        assert msg["attachment_urls"] == []
        assert msg["attachments"] == []

        await db_session.refresh(old_attachment)
        assert old_attachment.target_id is None

    async def test_update_goods_returns_attachments_in_submitted_order(
        self,
        client: AsyncClient,
        db_session,
        test_user,
        test_user_token,
        fake_redis,
    ):
        await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
        await fake_redis.set(f"user_token:{test_user.user_id}", test_user_token)

        category = Category(category_id=516, name="goods-order-cat", config_json={})
        db_session.add(category)
        await db_session.flush()

        goods = Goods(
            goods_id=5016,
            publisher_id=test_user.user_id,
            category_id=category.category_id,
            name="ordered goods",
            price=21.0,
            condition=GoodsCondition.BRAND_NEW,
            status=GoodsStatus.ON_SALE,
        )
        db_session.add(goods)
        await db_session.flush()

        first = Attachment(
            attachment_id=7016,
            target_type=AttachmentTargetType.USER,
            target_id=test_user.user_id,
            url="/static/goods/first.webp",
            creator_id=test_user.user_id,
        )
        second = Attachment(
            attachment_id=7017,
            target_type=AttachmentTargetType.USER,
            target_id=test_user.user_id,
            url="/static/goods/second.webp",
            creator_id=test_user.user_id,
        )
        db_session.add_all([first, second])
        await db_session.flush()

        resp = await client.patch(
            f"/goods/{goods.goods_id}",
            headers={"Authorization": f"Bearer {test_user_token}"},
            json={"attachment_ids": [7017, 7016]},
        )
        assert resp.status_code == 200
        msg = assert_api_success(resp.json())
        assert msg["attachments"] == [
            {"id": 7017, "url": "/static/goods/second.webp"},
            {"id": 7016, "url": "/static/goods/first.webp"},
        ]

        await db_session.refresh(first)
        await db_session.refresh(second)
        assert first.sort_order == 1
        assert second.sort_order == 0


class TestDeleteGoods:
    async def test_delete_own_goods(self, client: AsyncClient, db_session, test_user, test_user_token, fake_redis):
        await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))

        category = Category(category_id=508, name="delete-cat", config_json={})
        db_session.add(category)
        await db_session.flush()

        goods = Goods(
            goods_id=5007,
            publisher_id=test_user.user_id,
            category_id=category.category_id,
            name="to delete",
            price=5.0,
            condition=GoodsCondition.USED_HEAVILY,
            status=GoodsStatus.ON_SALE,
        )
        db_session.add(goods)
        await db_session.flush()

        resp = await client.delete(
            "/goods/5007",
            headers={"Authorization": f"Bearer {test_user_token}"},
        )
        assert resp.status_code == 200
        msg = assert_api_success(resp.json())
        assert msg["goods_id"] == 5007
        assert msg["deleted"] is True



class TestGoodsDetailHistory:
    """GET /goods/{goods_id}: authenticated users register history footprint."""

    async def test_goods_detail_registers_history_footprint(
        self,
        client: AsyncClient,
        db_session,
        test_user,
        test_user_token,
        fake_redis,
    ):
        """Visiting goods detail with auth records ZSET entry for history wall."""
        await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))

        category = Category(category_id=510, name="history-footprint-cat", config_json={})
        db_session.add(category)
        await db_session.flush()

        goods = Goods(
            goods_id=5001,
            publisher_id=test_user.user_id,
            category_id=category.category_id,
            name="history goods",
            price=30.0,
            condition=GoodsCondition.BRAND_NEW,
            status=GoodsStatus.ON_SALE,
        )
        db_session.add(goods)
        await db_session.flush()

        resp = await client.get(
            f"/goods/{5001}",
            headers={"Authorization": f"Bearer {test_user_token}"},
        )
        assert resp.status_code == 200

        # Verify history ZSET entry was created
        history_key = f"user:history:{test_user.user_id}"
        zset = fake_redis._zsets.get(history_key, {})
        assert "GOODS:5001" in zset, f"Expected GOODS:5001 in {history_key}, got {list(zset.keys())}"





@pytest.mark.asyncio
async def test_goods_apply_creates_pending_order_and_keeps_goods_on_sale(
    client: AsyncClient,
    db_session,
    test_user,
    test_user_token,
    fake_redis,
    monkeypatch,
):
    """商品申请接口应创建 PENDING 订单，不锁定商品。"""
    from app.models import ItemType, Order, OrderStatus
    from app.services import WeChatNotificationService
    from sqlalchemy import select

    async def _noop_notify(*args, **kwargs):
        return None

    monkeypatch.setattr(WeChatNotificationService, "notify_goods_purchased", _noop_notify)

    seller = User(user_id=8101, user_uuid=b"8101000000000001", user_name="goods_apply_seller", wechat_openid="wx_goods_apply_seller",
                  email="seller_apply@test.com", user_type="user", credit_score=60)
    db_session.add(seller)
    await db_session.flush()

    category = Category(category_id=611, name="apply-test-cat", config_json={})
    db_session.add(category)
    await db_session.flush()

    goods = Goods(
        goods_id=6101, publisher_id=seller.user_id, category_id=category.category_id,
        name="apply test goods", price=59.0, condition=GoodsCondition.BRAND_NEW, status=GoodsStatus.ON_SALE,
    )
    db_session.add(goods)
    await db_session.flush()

    await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
    resp = await client.post(
        f"/goods/{goods.goods_id}/accept",
        headers={"Authorization": f"Bearer {test_user_token}"},
    )
    assert resp.status_code == 200
    msg = assert_api_success(resp.json())
    assert msg["goods_id"] == goods.goods_id
    assert msg["status"] == OrderStatus.PENDING.value

    order_row = await db_session.execute(
        select(Order).where(Order.item_type == ItemType.GOODS, Order.item_id == goods.goods_id, Order.is_deleted == False)
    )
    order = order_row.scalars().first()
    assert order is not None
    assert order.status == OrderStatus.PENDING

    await db_session.refresh(goods)
    assert goods.status == GoodsStatus.ON_SALE


@pytest.mark.asyncio
async def test_goods_apply_allows_multiple_pending_orders(
    client: AsyncClient,
    db_session,
    test_user,
    test_user_token,
    fake_redis,
    monkeypatch,
):
    """商品申请可重复发起，不应因为 PENDING 被当作锁单拦截。"""
    from app.models import ItemType, Order, OrderStatus
    from app.services import WeChatNotificationService
    from sqlalchemy import select

    async def _noop_notify(*args, **kwargs):
        return None

    monkeypatch.setattr(WeChatNotificationService, "notify_goods_purchased", _noop_notify)

    seller = User(user_id=8102, user_uuid=b"8101000000000002", user_name="goods_apply_multi_seller", wechat_openid="wx_goods_apply_multi_seller",
                  email="seller_apply_multi@test.com", user_type="user", credit_score=60)
    db_session.add(seller)
    await db_session.flush()

    category = Category(category_id=612, name="apply-multi-cat", config_json={})
    db_session.add(category)
    await db_session.flush()

    goods = Goods(
        goods_id=6102, publisher_id=seller.user_id, category_id=category.category_id,
        name="apply multi goods", price=66.0, condition=GoodsCondition.BRAND_NEW, status=GoodsStatus.ON_SALE,
    )
    db_session.add(goods)
    await db_session.flush()

    await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
    first = await client.post(f"/goods/{goods.goods_id}/accept", headers={"Authorization": f"Bearer {test_user_token}"})
    assert first.status_code == 200
    assert_api_success(first.json())

    second_user = User(user_id=8103, user_uuid=b"8101000000000003", user_name="goods_apply_second", wechat_openid="wx_goods_apply_second",
                       email="second_apply@test.com", user_type="user", credit_score=60)
    db_session.add(second_user)
    await db_session.flush()

    second_token = await _bind_user_token(fake_redis, second_user)
    second = await client.post(f"/goods/{goods.goods_id}/accept", headers={"Authorization": f"Bearer {second_token}"})
    assert second.status_code == 200
    second_msg = assert_api_success(second.json())
    assert second_msg["status"] == OrderStatus.PENDING.value

    order_rows = await db_session.execute(
        select(Order.order_id, Order.status).where(Order.item_type == ItemType.GOODS, Order.item_id == goods.goods_id, Order.is_deleted == False)
    )
    statuses = [status for _, status in order_rows.all()]
    assert statuses.count(OrderStatus.PENDING) >= 2


@pytest.mark.asyncio
async def test_goods_contact_allows_any_related_order(
    client: AsyncClient,
    db_session,
    test_user,
    test_user_token,
    fake_redis,
):
    """只要存在关联订单，不论状态，都可查看商品联系方式。"""
    from app.models import ItemType, Order, OrderStatus

    seller = User(user_id=8104, user_uuid=b"8101000000000004", user_name="goods_contact_seller", wechat_openid="wx_goods_contact_seller",
                  email="seller_contact@test.com", user_type="user", credit_score=60)
    db_session.add(seller)
    await db_session.flush()

    category = Category(category_id=613, name="contact-cat", config_json={})
    db_session.add(category)
    await db_session.flush()

    goods = Goods(
        goods_id=6103, publisher_id=seller.user_id, category_id=category.category_id,
        name="contact goods", price=77.0, condition=GoodsCondition.BRAND_NEW, status=GoodsStatus.ON_SALE,
        contact={"phone": "13800000000"},
    )
    db_session.add(goods)
    await db_session.flush()

    related_order = Order(
        buyer_id=test_user.user_id,
        seller_id=seller.user_id,
        initiator_id=test_user.user_id,
        item_type=ItemType.GOODS,
        item_id=goods.goods_id,
        status=OrderStatus.PENDING,
    )
    db_session.add(related_order)
    await db_session.flush()

    await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
    resp = await client.get(
        f"/goods/{goods.goods_id}/contact",
        headers={"Authorization": f"Bearer {test_user_token}"},
    )
    assert resp.status_code == 200
    msg = assert_api_success(resp.json())
    assert msg["phone"] == "13800000000"


@pytest.mark.asyncio
async def test_goods_applications_returns_owner_view_with_completed_count(
    client: AsyncClient,
    db_session,
    test_user,
    test_user_token,
    fake_redis,
    monkeypatch,
):
    """商品发布者可查看申请列表，并返回申请人历史完成单数。"""
    from app.models import ItemType, Order, OrderStatus, OrderTriggerType
    from app.services import WeChatNotificationService

    async def _noop_notify(*args, **kwargs):
        return None

    monkeypatch.setattr(WeChatNotificationService, "notify_goods_purchased", _noop_notify)

    await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))

    applicant = User(
        user_id=8105,
        user_uuid=b"8105000000000005",
        user_name="goods_apply_list_user",
        wechat_openid="wx_goods_apply_list_user",
        email="goods_apply_list_user@test.com",
        user_type="user",
        credit_score=60,
    )
    db_session.add(applicant)
    await db_session.flush()
    applicant_token = await _bind_user_token(fake_redis, applicant)

    category = Category(category_id=614, name="goods-application-list-cat", config_json={})
    db_session.add(category)
    await db_session.flush()

    goods = Goods(
        goods_id=6104,
        publisher_id=test_user.user_id,
        category_id=category.category_id,
        name="application list goods",
        price=88.0,
        condition=GoodsCondition.BRAND_NEW,
        status=GoodsStatus.ON_SALE,
    )
    db_session.add(goods)
    await db_session.flush()

    completed_order_1 = Order(
        buyer_id=applicant.user_id,
        seller_id=test_user.user_id,
        initiator_id=applicant.user_id,
        item_type=ItemType.POST,
        item_id=9101,
        status=OrderStatus.COMPLETED,
        trigger_type=OrderTriggerType.APPLICATION,
    )
    completed_order_2 = Order(
        buyer_id=test_user.user_id,
        seller_id=applicant.user_id,
        initiator_id=applicant.user_id,
        item_type=ItemType.GOODS,
        item_id=9102,
        status=OrderStatus.COMPLETED,
        trigger_type=OrderTriggerType.DIRECT,
    )
    db_session.add_all([completed_order_1, completed_order_2])
    await db_session.flush()

    accept_resp = await client.post(
        f"/goods/{goods.goods_id}/accept",
        headers={"Authorization": f"Bearer {applicant_token}"},
    )
    assert accept_resp.status_code == 200
    accept_msg = assert_api_success(accept_resp.json())
    application_id = accept_msg["order_id"]

    resp = await client.get(
        f"/goods/{goods.goods_id}/applications",
        headers={"Authorization": f"Bearer {test_user_token}"},
    )
    assert resp.status_code == 200
    msg = assert_api_success(resp.json())
    applications = msg["applications"]
    assert len(applications) == 1
    application = applications[0]
    assert application["application_id"] == application_id
    assert application["goods_id"] == goods.goods_id
    assert application["status"] == OrderStatus.PENDING.value
    assert application["applicant"]["user_id"] == applicant.user_id
    assert application["applicant"]["completed_order_count"] == 2
    assert application["note"] is None
    assert application["created_at"]

    order_after = await db_session.get(Order, application_id)
    assert order_after is not None
    assert order_after.is_seen_by_seller is True


@pytest.mark.asyncio
async def test_goods_applications_rejects_non_owner(
    client: AsyncClient,
    db_session,
    test_user,
    fake_redis,
):
    """商品申请列表仅商品拥有者可查看。"""
    seller = User(
        user_id=8106,
        user_uuid=b"8106000000000006",
        user_name="goods_application_owner",
        wechat_openid="wx_goods_application_owner",
        email="goods_application_owner@test.com",
        user_type="user",
        credit_score=60,
    )
    db_session.add(seller)
    await db_session.flush()

    category = Category(category_id=615, name="goods-application-forbidden-cat", config_json={})
    db_session.add(category)
    await db_session.flush()

    goods = Goods(
        goods_id=6105,
        publisher_id=seller.user_id,
        category_id=category.category_id,
        name="forbidden application goods",
        price=96.0,
        condition=GoodsCondition.BRAND_NEW,
        status=GoodsStatus.ON_SALE,
    )
    db_session.add(goods)
    await db_session.flush()

    applicant_token = await _bind_user_token(fake_redis, test_user)
    resp = await client.get(
        f"/goods/{goods.goods_id}/applications",
        headers={"Authorization": f"Bearer {applicant_token}"},
    )
    assert resp.status_code == 200
    message = assert_api_error(resp.json(), code=settings.INSUFFICIENT_AUTHORITY_CODE)
    assert "仅商品拥有者可查看申请列表" in message["msg"]


@pytest.mark.asyncio
async def test_buy_goods_creates_ongoing_order_and_locks(
    client: AsyncClient,
    db_session,
    test_user,
    test_user_token,
    fake_redis,
    monkeypatch,
):
    """买家在已有申请单前提下发起 buy，应转为 ONGOING 并锁定商品。"""
    from app.models import ItemType, Order, OrderStatus
    from app.services import WeChatNotificationService
    from sqlalchemy import select

    async def _noop_notify(*args, **kwargs):
        return None

    monkeypatch.setattr(WeChatNotificationService, "notify_goods_purchased", _noop_notify)

    # 创建卖家
    seller = User(user_id=8001, user_uuid=b"8001000000000001", user_name="goods_seller", wechat_openid="wx_goods_seller",
                  email="seller@test.com", user_type="user", credit_score=60)
    db_session.add(seller)
    await db_session.flush()

    category = Category(category_id=601, name="buy-test-cat", config_json={})
    db_session.add(category)
    await db_session.flush()

    goods = Goods(
        goods_id=6001, publisher_id=seller.user_id, category_id=category.category_id,
        name="test buy goods", price=50.0, condition=GoodsCondition.BRAND_NEW, status=GoodsStatus.ON_SALE,
    )
    db_session.add(goods)
    await db_session.flush()

    await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
    accept_resp = await client.post(
        f"/goods/{goods.goods_id}/accept",
        headers={"Authorization": f"Bearer {test_user_token}"},
    )
    assert accept_resp.status_code == 200
    accept_msg = assert_api_success(accept_resp.json())
    assert accept_msg["status"] == OrderStatus.PENDING.value

    resp = await client.post(
        f"/goods/{goods.goods_id}/buy",
        headers={"Authorization": f"Bearer {test_user_token}"},
    )
    assert resp.status_code == 200
    msg = assert_api_success(resp.json())
    assert msg["goods_id"] == goods.goods_id
    assert msg["status"] == OrderStatus.ONGOING.value

    order_row = await db_session.execute(
        select(Order).where(Order.item_type == ItemType.GOODS, Order.item_id == goods.goods_id, Order.initiator_id == test_user.user_id)
    )
    order = order_row.scalars().first()
    assert order is not None
    assert order.status == OrderStatus.ONGOING

    await db_session.refresh(goods)
    assert goods.status == GoodsStatus.OFF_SHELF


@pytest.mark.asyncio
async def test_buy_goods_rejects_other_pending_orders(
    client: AsyncClient,
    db_session,
    test_user,
    test_user_token,
    fake_redis,
    monkeypatch,
):
    """buy 成功后，其他待处理商品申请应被批量拒绝。"""
    from app.models import ItemType, Order, OrderStatus
    from app.services import WeChatNotificationService
    from sqlalchemy import select

    async def _noop_notify(*args, **kwargs):
        return None

    monkeypatch.setattr(WeChatNotificationService, "notify_goods_purchased", _noop_notify)

    seller = User(user_id=8007, user_uuid=b"8007000000000007", user_name="buy_wash_seller", wechat_openid="wx_buy_wash",
                  email="buywash@test.com", user_type="user", credit_score=60)
    other_user = User(user_id=8008, user_uuid=b"8008000000000008", user_name="buy_wash_other", wechat_openid="wx_buy_wash_other",
                      email="buywashother@test.com", user_type="user", credit_score=60)
    db_session.add_all([seller, other_user])
    await db_session.flush()

    category = Category(category_id=607, name="buy-wash-cat", config_json={})
    db_session.add(category)
    await db_session.flush()

    goods = Goods(
        goods_id=6007, publisher_id=seller.user_id, category_id=category.category_id,
        name="wash pending goods", price=42.0, condition=GoodsCondition.BRAND_NEW, status=GoodsStatus.ON_SALE,
    )
    db_session.add(goods)
    await db_session.flush()

    my_pending = Order(
        buyer_id=test_user.user_id,
        seller_id=seller.user_id,
        initiator_id=test_user.user_id,
        item_type=ItemType.GOODS,
        item_id=goods.goods_id,
        status=OrderStatus.PENDING,
    )
    other_pending = Order(
        buyer_id=other_user.user_id,
        seller_id=seller.user_id,
        initiator_id=other_user.user_id,
        item_type=ItemType.GOODS,
        item_id=goods.goods_id,
        status=OrderStatus.PENDING,
    )
    db_session.add_all([my_pending, other_pending])
    await db_session.flush()

    await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
    resp = await client.post(
        f"/goods/{goods.goods_id}/buy",
        headers={"Authorization": f"Bearer {test_user_token}"},
    )
    assert resp.status_code == 200
    msg = assert_api_success(resp.json())
    assert msg["status"] == OrderStatus.ONGOING.value

    rows = await db_session.execute(
        select(Order.initiator_id, Order.status).where(Order.item_type == ItemType.GOODS, Order.item_id == goods.goods_id)
    )
    status_map = {initiator_id: status for initiator_id, status in rows.all()}
    assert status_map[test_user.user_id] == OrderStatus.ONGOING
    assert status_map[other_user.user_id] == OrderStatus.REJECTED


@pytest.mark.asyncio
async def test_buy_goods_rejects_self_purchase(
    client: AsyncClient,
    db_session,
    test_user,
    test_user_token,
    fake_redis,
):
    """不能购买自己发布的商品。"""
    category = Category(category_id=602, name="self-buy-cat", config_json={})
    db_session.add(category)
    await db_session.flush()

    goods = Goods(
        goods_id=6002, publisher_id=test_user.user_id, category_id=category.category_id,
        name="my own goods", price=30.0, condition=GoodsCondition.BRAND_NEW, status=GoodsStatus.ON_SALE,
    )
    db_session.add(goods)
    await db_session.flush()

    await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
    resp = await client.post(
        f"/goods/{goods.goods_id}/buy",
        headers={"Authorization": f"Bearer {test_user_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["code"] == settings.REQ_ERROR_CODE


@pytest.mark.asyncio
async def test_buy_goods_rejects_off_shelf(
    client: AsyncClient,
    db_session,
    test_user,
    test_user_token,
    fake_redis,
):
    """已下架商品不可购买。"""
    seller = User(user_id=8003, user_uuid=b"8003000000000003", user_name="off_shelf_seller", wechat_openid="wx_off",
                  email="off@test.com", user_type="user", credit_score=60)
    db_session.add(seller)
    await db_session.flush()

    category = Category(category_id=603, name="off-shelf-cat", config_json={})
    db_session.add(category)
    await db_session.flush()

    goods = Goods(
        goods_id=6003, publisher_id=seller.user_id, category_id=category.category_id,
        name="off shelf goods", price=40.0, condition=GoodsCondition.BRAND_NEW, status=GoodsStatus.OFF_SHELF,
    )
    db_session.add(goods)
    await db_session.flush()

    await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
    resp = await client.post(
        f"/goods/{goods.goods_id}/buy",
        headers={"Authorization": f"Bearer {test_user_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["code"] == settings.REQ_ERROR_CODE


@pytest.mark.asyncio
async def test_buy_goods_requires_existing_pending_application(
    client: AsyncClient,
    db_session,
    test_user,
    test_user_token,
    fake_redis,
):
    """buy 前必须已存在当前用户的商品申请单。"""
    seller = User(user_id=8006, user_uuid=b"8006000000000006", user_name="buy_guard_seller", wechat_openid="wx_buy_guard",
                  email="buyguard@test.com", user_type="user", credit_score=60)
    db_session.add(seller)
    await db_session.flush()

    category = Category(category_id=606, name="buy-guard-cat", config_json={})
    db_session.add(category)
    await db_session.flush()

    goods = Goods(
        goods_id=6006, publisher_id=seller.user_id, category_id=category.category_id,
        name="need apply first", price=41.0, condition=GoodsCondition.BRAND_NEW, status=GoodsStatus.ON_SALE,
    )
    db_session.add(goods)
    await db_session.flush()

    await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
    resp = await client.post(
        f"/goods/{goods.goods_id}/buy",
        headers={"Authorization": f"Bearer {test_user_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == settings.REQ_ERROR_CODE
    assert "请先申请该商品" in body["message"]["msg"]


@pytest.mark.asyncio
async def test_delist_and_relist_goods(
    client: AsyncClient,
    db_session,
    test_user,
    test_user_token,
    fake_redis,
):
    """卖家下架再重新上架商品。"""
    category = Category(category_id=604, name="delist-cat", config_json={})
    db_session.add(category)
    await db_session.flush()

    goods = Goods(
        goods_id=6004, publisher_id=test_user.user_id, category_id=category.category_id,
        name="toggle goods", price=25.0, condition=GoodsCondition.BRAND_NEW, status=GoodsStatus.ON_SALE,
    )
    db_session.add(goods)
    await db_session.flush()

    await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
    # 下架
    resp1 = await client.post(
        f"/goods/{goods.goods_id}/delist",
        headers={"Authorization": f"Bearer {test_user_token}"},
    )
    assert resp1.status_code == 200
    msg1 = assert_api_success(resp1.json())
    assert msg1["status"] == GoodsStatus.OFF_SHELF.value

    await db_session.refresh(goods)
    assert goods.status == GoodsStatus.OFF_SHELF

    # 上架
    resp2 = await client.post(
        f"/goods/{goods.goods_id}/relist",
        headers={"Authorization": f"Bearer {test_user_token}"},
    )
    assert resp2.status_code == 200
    msg2 = assert_api_success(resp2.json())
    assert msg2["status"] == GoodsStatus.ON_SALE.value

    await db_session.refresh(goods)
    assert goods.status == GoodsStatus.ON_SALE


@pytest.mark.asyncio
async def test_delist_rejects_non_publisher(
    client: AsyncClient,
    db_session,
    test_user,
    test_user_token,
    fake_redis,
):
    """非卖家不可操作下架。"""
    seller = User(user_id=8005, user_uuid=b"8005000000000005", user_name="other_seller", wechat_openid="wx_other",
                  email="other@test.com", user_type="user", credit_score=60)
    db_session.add(seller)
    await db_session.flush()

    category = Category(category_id=605, name="auth-delist-cat", config_json={})
    db_session.add(category)
    await db_session.flush()

    goods = Goods(
        goods_id=6005, publisher_id=seller.user_id, category_id=category.category_id,
        name="not my goods", price=10.0, condition=GoodsCondition.BRAND_NEW, status=GoodsStatus.ON_SALE,
    )
    db_session.add(goods)
    await db_session.flush()

    await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
    resp = await client.post(
        f"/goods/{goods.goods_id}/delist",
        headers={"Authorization": f"Bearer {test_user_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["code"] == settings.INSUFFICIENT_AUTHORITY_CODE

