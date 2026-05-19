"""Post / Order 全链路集成测试（真实 MySQL Testcontainers）。"""

from types import SimpleNamespace

import pytest

from app.api import get_current_user
from app.core import settings
from app.models import Category, Direction, Order, OrderStatus, Post, PostStatus, SexEnum, UrgencyLevel, User, UserType


pytestmark = pytest.mark.asyncio


def _build_current_user(user: User) -> SimpleNamespace:
    return SimpleNamespace(
        user_id=user.user_id,
        user_uuid=user.user_uuid,
        user_name=user.user_name,
        is_admin=user.is_admin,
        is_verified=user.is_verified,
        user_type=user.user_type.value if getattr(user.user_type, "value", None) else str(user.user_type),
    )


async def _set_current_user(app, user: User) -> None:
    async def _override():
        return _build_current_user(user)

    app.dependency_overrides[get_current_user] = _override


async def _clear_current_user(app) -> None:
    app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_post_order_full_flow(client, app, db_session):
    """覆盖发布、接单申请、主页可见性、防线、审批、拒绝与完成。"""

    category = Category(
        category_id=2001,
        name="跑腿服务",
        item_type="POST",
        config_json={"fields": []},
    )
    user_a = User(
        user_id=3001,
        user_uuid=b"aaaaaaaaaaaaaaaa",
        user_name="publisher",
        email="a@example.com",
        phonenumber="13800000001",
        sex=SexEnum.UNKNOWN,
        user_type=UserType.USER,
        is_verified=True,
        is_active=True,
        is_admin=False,
        is_deleted=False,
        credit_score=100,
        wechat_openid="openid-a",
    )
    user_b = User(
        user_id=3002,
        user_uuid=b"bbbbbbbbbbbbbbbb",
        user_name="runner-b",
        email="b@example.com",
        phonenumber="13800000002",
        sex=SexEnum.UNKNOWN,
        user_type=UserType.USER,
        is_verified=True,
        is_active=True,
        is_admin=False,
        is_deleted=False,
        credit_score=100,
        wechat_openid="openid-b",
    )
    user_c = User(
        user_id=3003,
        user_uuid=b"cccccccccccccccc",
        user_name="runner-c",
        email="c@example.com",
        phonenumber="13800000003",
        sex=SexEnum.UNKNOWN,
        user_type=UserType.USER,
        is_verified=True,
        is_active=True,
        is_admin=False,
        is_deleted=False,
        credit_score=100,
        wechat_openid="openid-c",
    )
    db_session.add_all([category, user_a, user_b, user_c])
    await db_session.flush()

    post = Post(
        post_id=4001,
        publisher_id=user_a.user_id,
        category_id=category.category_id,
        title="校园跑腿",
        description="帮忙取快递并送到宿舍",
        price=12.5,
        template_data={"max_accepters": 3},
        direction=Direction.SELL,
        urgency=UrgencyLevel.NORMAL,
        status=PostStatus.OPEN,
    )
    db_session.add(post)
    await db_session.flush()

    await _set_current_user(app, user_b)
    accept_b_resp = await client.post(f"/posts/{post.post_id}/accept")
    assert accept_b_resp.status_code == 200
    accept_b_data = accept_b_resp.json()
    assert accept_b_data["code"] == settings.SUCCESS_CODE
    order_b_id = accept_b_data["message"]["order_id"]
    assert accept_b_data["message"]["status"] == "PENDING"
    post_after_first_accept = await db_session.get(Post, post.post_id)
    assert post_after_first_accept.status == PostStatus.OPEN

    await _set_current_user(app, user_c)
    accept_c_resp = await client.post(f"/posts/{post.post_id}/accept")
    assert accept_c_resp.status_code == 200
    accept_c_data = accept_c_resp.json()
    assert accept_c_data["code"] == settings.SUCCESS_CODE
    order_c_id = accept_c_data["message"]["order_id"]
    post_after_second_accept = await db_session.get(Post, post.post_id)
    assert post_after_second_accept.status == PostStatus.OPEN

    await _clear_current_user(app)
    public_resp = await client.get(f"/posts/user/{user_a.user_id}")
    assert public_resp.status_code == 200
    public_data = public_resp.json()
    assert public_data["code"] == settings.SUCCESS_CODE
    assert any(item["post_id"] == post.post_id for item in public_data["message"]["list"])

    await _set_current_user(app, user_a)
    patch_resp = await client.patch(
        f"/posts/{post.post_id}",
        json={"title": "不能改"},
    )
    assert patch_resp.status_code == 200
    patch_data = patch_resp.json()
    assert patch_data["code"] == settings.REQ_ERROR_CODE
    assert "禁止修改委托信息" in patch_data["message"]["msg"]

    orders_by_item_resp = await client.get(
        "/orders/by-item",
        params={"item_id": post.post_id, "item_type": "POST"},
    )
    assert orders_by_item_resp.status_code == 200
    orders_by_item_data = orders_by_item_resp.json()
    assert orders_by_item_data["code"] == settings.SUCCESS_CODE
    assert len(orders_by_item_data["message"]["list"]) == 2
    assert all(item["status"] == "PENDING" for item in orders_by_item_data["message"]["list"])

    approve_resp = await client.post(f"/orders/{order_b_id}/approve")
    assert approve_resp.status_code == 200
    approve_data = approve_resp.json()
    assert approve_data["code"] == settings.SUCCESS_CODE
    assert approve_data["message"]["status"] == "ONGOING"

    order_b = await db_session.get(Order, order_b_id)
    order_c = await db_session.get(Order, order_c_id)
    refreshed_post = await db_session.get(Post, post.post_id)
    assert order_b.status == OrderStatus.ONGOING
    assert order_c.status == OrderStatus.REJECTED
    assert refreshed_post.status == PostStatus.IN_PROGRESS

    me_orders_resp = await client.get(
        "/orders/me",
        params={"role": "seller", "status": "all"},
    )
    assert me_orders_resp.status_code == 200
    me_orders_data = me_orders_resp.json()
    assert me_orders_data["code"] == settings.SUCCESS_CODE
    assert len(me_orders_data["message"]["list"]) == 2

    complete_resp = await client.post(f"/orders/{order_b_id}/complete")
    assert complete_resp.status_code == 200
    complete_data = complete_resp.json()
    assert complete_data["code"] == settings.SUCCESS_CODE
    assert complete_data["message"]["status"] == "COMPLETED"

    final_order_b = await db_session.get(Order, order_b_id)
    final_post = await db_session.get(Post, post.post_id)
    assert final_order_b.status == OrderStatus.COMPLETED
    assert final_post.status == PostStatus.CLOSED

    await _clear_current_user(app)