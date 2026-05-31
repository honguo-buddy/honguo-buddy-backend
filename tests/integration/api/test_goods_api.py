"""Goods API integration tests."""

import pytest
from httpx import AsyncClient

from app.core import settings
from app.models import Category, Goods, GoodsCondition, GoodsStatus, User
from tests.helpers import assert_api_success, assert_api_error


async def _bind_user_token(fake_redis, user: User) -> str:
    from app.core import create_access_token
    token = create_access_token(data={"sub": str(user.user_id), "user_name": user.user_name, "user_type": "user"})
    await fake_redis.set(f"token:{token}", str(user.user_id))
    return token


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

