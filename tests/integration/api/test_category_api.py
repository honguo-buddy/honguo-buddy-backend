"""Category 模板分类 API 集成测试。"""

import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient

from app.core import settings
from app.models import Category, ItemType

pytestmark = pytest.mark.asyncio


class TestCategoryPublicGet:
    async def test_list_categories_by_post_type(self, client: AsyncClient, db_session, test_user, test_user_token, fake_redis):
        """GET /categories?type=POST 只返回 POST 类型分类。"""
        post_category = Category(
            category_id=7001,
            name="任务分类",
            icon="/static/category/task.png",
            config_json={"fields": [{"key": "deadline", "label": "截止时间"}]},
            item_type=ItemType.POST,
            is_deleted=False,
        )
        goods_category = Category(
            category_id=7002,
            name="商品分类",
            icon="/static/category/goods.png",
            config_json={"fields": [{"key": "brand", "label": "品牌"}]},
            item_type=ItemType.GOODS,
            is_deleted=False,
        )
        db_session.add_all([post_category, goods_category])
        await db_session.flush()

        resp = await client.get("/categories/", params={"type": "POST"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == settings.SUCCESS_CODE
        items = body["message"]
        assert len(items) == 1
        assert items[0]["name"] == "任务分类"
        assert items[0]["item_type"] == "POST"

    async def test_list_categories_by_goods_type(self, client: AsyncClient, db_session):
        """GET /categories?type=GOODS 只返回 GOODS 类型分类。"""
        goods_category = Category(
            category_id=7003,
            name="二手商品",
            icon=None,
            config_json={"fields": [{"key": "condition", "label": "成色"}]},
            item_type=ItemType.GOODS,
            is_deleted=False,
        )
        db_session.add(goods_category)
        await db_session.flush()

        resp = await client.get("/categories/", params={"type": "GOODS"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == settings.SUCCESS_CODE
        assert len(body["message"]) == 1
        assert body["message"][0]["item_type"] == "GOODS"

    async def test_get_category_detail(self, client: AsyncClient, db_session):
        """GET /categories/{id} 返回单个模板分类详情。"""
        category = Category(
            category_id=7004,
            name="详情分类",
            icon="/static/category/detail.png",
            config_json={"fields": [{"key": "foo", "label": "Foo"}]},
            item_type=ItemType.POST,
            is_deleted=False,
        )
        db_session.add(category)
        await db_session.flush()

        resp = await client.get("/categories/7004")

        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == settings.SUCCESS_CODE
        assert body["message"]["category_id"] == 7004
        assert body["message"]["item_type"] == "POST"


class TestCategoryAdminCrud:
    async def test_create_category_template(self, client: AsyncClient, test_admin_user, test_admin_token, fake_redis):
        """管理员可创建模板分类。"""
        await fake_redis.set(f"token:{test_admin_token}", str(test_admin_user.user_id))
        await fake_redis.set(f"user_token:{test_admin_user.user_id}", test_admin_token)

        fake_category = type(
            "FakeCategory",
            (),
            {
                "category_id": 8001,
                "name": "管理员创建分类",
                "icon": None,
                "item_type": ItemType.POST.value,
                "config_json": {"fields": [{"key": "deadline", "label": "截止时间"}]},
                "create_time": __import__("datetime").datetime.utcnow(),
                "update_time": __import__("datetime").datetime.utcnow(),
            },
        )()

        with patch("app.api.category.CategoryService.create_category", new=AsyncMock(return_value=fake_category)):
            resp = await client.post(
                "/categories/",
                headers={"Authorization": f"Bearer {test_admin_token}"},
                json={
                    "name": "管理员创建分类",
                    "item_type": "POST",
                    "config_json": {"fields": [{"key": "deadline", "label": "截止时间"}]},
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == settings.SUCCESS_CODE
        assert body["message"]["name"] == "管理员创建分类"
        assert body["message"]["item_type"] == "POST"

    async def test_update_category_template(self, client: AsyncClient, db_session, test_admin_user, test_admin_token, fake_redis):
        """管理员可更新模板分类。"""
        category = Category(
            category_id=7005,
            name="待更新分类",
            icon=None,
            config_json={"fields": [{"key": "old", "label": "旧字段"}]},
            item_type=ItemType.POST,
            is_deleted=False,
        )
        db_session.add(category)
        await db_session.flush()

        await fake_redis.set(f"token:{test_admin_token}", str(test_admin_user.user_id))
        await fake_redis.set(f"user_token:{test_admin_user.user_id}", test_admin_token)

        resp = await client.put(
            "/categories/7005",
            headers={"Authorization": f"Bearer {test_admin_token}"},
            json={
                "name": "已更新分类",
                "item_type": "GOODS",
                "config_json": {"fields": [{"key": "new", "label": "新字段"}]},
            },
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == settings.SUCCESS_CODE
        assert body["message"]["name"] == "已更新分类"
        assert body["message"]["item_type"] == "GOODS"

    async def test_delete_category_template(self, client: AsyncClient, db_session, test_admin_user, test_admin_token, fake_redis):
        """管理员可软删除模板分类。"""
        category = Category(
            category_id=7006,
            name="待删除分类",
            icon=None,
            config_json={"fields": [{"key": "foo", "label": "Foo"}]},
            item_type=ItemType.POST,
            is_deleted=False,
        )
        db_session.add(category)
        await db_session.flush()

        await fake_redis.set(f"token:{test_admin_token}", str(test_admin_user.user_id))
        await fake_redis.set(f"user_token:{test_admin_user.user_id}", test_admin_token)

        resp = await client.delete(
            "/categories/7006",
            headers={"Authorization": f"Bearer {test_admin_token}"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == settings.SUCCESS_CODE
        assert body["message"]["deleted"] is True

    async def test_admin_only_rejects_non_admin(self, client: AsyncClient, test_user, test_user_token, fake_redis):
        """非管理员不能执行分类创建。"""
        await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
        await fake_redis.set(f"user_token:{test_user.user_id}", test_user_token)

        resp = await client.post(
            "/categories/",
            headers={"Authorization": f"Bearer {test_user_token}"},
            json={
                "name": "无权限创建",
                "item_type": "POST",
                "config_json": {"fields": [{"key": "a", "label": "A"}]},
            },
        )

        assert resp.status_code == 200
        assert resp.json()["code"] == settings.INSUFFICIENT_AUTHORITY_CODE
