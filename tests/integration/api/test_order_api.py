"""Post / Order 全链路集成测试（真实 MySQL Testcontainers）。"""

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.api import get_current_user
from app.core import settings
from app.models import Category, Direction, Order, OrderReview, OrderStatus, Post, PostStatus, SexEnum, UrgencyLevel, User, UserType
from app.services import OrderReviewService
from tests.helpers import assert_api_error


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
    order_detail_resp = await client.get(f"/orders/{order_b_id}")
    assert order_detail_resp.status_code == 200
    order_detail_data = order_detail_resp.json()
    assert order_detail_data["code"] == settings.SUCCESS_CODE
    assert order_detail_data["message"]["buyer_id"] == user_b.user_id
    assert order_detail_data["message"]["seller_id"] == user_a.user_id
    assert order_detail_data["message"]["trigger_type"] == "COLLECTIVE"
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
    assert order_c.status == OrderStatus.PENDING
    assert refreshed_post.status == PostStatus.OPEN

    await _set_current_user(app, user_a)
    submit_resp = await client.post(f"/orders/{order_b_id}/submit-delivery")
    assert submit_resp.status_code == 200
    submit_data = submit_resp.json()
    assert submit_data["code"] == settings.SUCCESS_CODE
    assert submit_data["message"]["status"] == "CONFIRMED"

    await _set_current_user(app, user_b)
    accept_delivery_resp = await client.post(f"/orders/{order_b_id}/accept-delivery")
    assert accept_delivery_resp.status_code == 200
    accept_delivery_data = accept_delivery_resp.json()
    assert accept_delivery_data["code"] == settings.SUCCESS_CODE
    assert accept_delivery_data["message"]["status"] == "COMPLETED"

    await _set_current_user(app, user_a)
    me_orders_resp = await client.get(
        "/orders/me",
        params={"role": "seller", "status": "all"},
    )
    assert me_orders_resp.status_code == 200
    me_orders_data = me_orders_resp.json()
    assert me_orders_data["code"] == settings.SUCCESS_CODE
    assert len(me_orders_data["message"]["list"]) == 2

    final_order_b = await db_session.get(Order, order_b_id)
    final_post = await db_session.get(Post, post.post_id)
    assert final_order_b.status == OrderStatus.COMPLETED
    assert final_post.status == PostStatus.CLOSED

    await _clear_current_user(app)


@pytest.mark.asyncio
async def test_orders_by_item_rejects_invalid_item_type(client, app, db_session):
    user = User(
        user_id=4101,
        user_uuid=b"dddddddddddddddd",
        user_name="item-user",
        email="item@example.com",
        phonenumber="13800004444",
        sex=SexEnum.UNKNOWN,
        user_type=UserType.USER,
        is_verified=True,
        is_active=True,
        is_admin=False,
        is_deleted=False,
        credit_score=100,
        wechat_openid="openid-item",
    )
    db_session.add(user)
    await db_session.flush()
    await _set_current_user(app, user)

    resp = await client.get("/orders/by-item", params={"item_id": 1, "item_type": "INVALID"})
    assert resp.status_code == 200
    message = assert_api_error(resp.json(), code=settings.REQ_ERROR_CODE)
    assert "不支持的 item_type" in message["msg"]
    await _clear_current_user(app)


@pytest.mark.asyncio
async def test_orders_by_item_rejects_non_owner(client, app, db_session):
    owner = User(
        user_id=4102,
        user_uuid=b"eeeeeeeeeeeeeeee",
        user_name="owner-user",
        email="owner@example.com",
        phonenumber="13800005555",
        sex=SexEnum.UNKNOWN,
        user_type=UserType.USER,
        is_verified=True,
        is_active=True,
        is_admin=False,
        is_deleted=False,
        credit_score=100,
        wechat_openid="openid-owner",
    )
    viewer = User(
        user_id=4103,
        user_uuid=b"ffffffffffffffff",
        user_name="viewer-user",
        email="viewer@example.com",
        phonenumber="13800006666",
        sex=SexEnum.UNKNOWN,
        user_type=UserType.USER,
        is_verified=True,
        is_active=True,
        is_admin=False,
        is_deleted=False,
        credit_score=100,
        wechat_openid="openid-viewer",
    )
    category = Category(category_id=4104, name="订单分类", item_type="POST", config_json={"fields": []})
    post = Post(
        post_id=4105,
        publisher_id=owner.user_id,
        category_id=category.category_id,
        title="订单帖子",
        description="订单帖子",
        price=11.0,
        template_data={"max_accepters": 1},
        direction=Direction.SELL,
        urgency=UrgencyLevel.NORMAL,
        status=PostStatus.OPEN,
    )
    db_session.add_all([owner, viewer, category, post])
    await db_session.flush()

    await _set_current_user(app, viewer)
    resp = await client.get("/orders/by-item", params={"item_id": post.post_id, "item_type": "POST"})
    assert resp.status_code == 200
    message = assert_api_error(resp.json(), code=settings.INSUFFICIENT_AUTHORITY_CODE)
    assert "仅项目拥有者可查看关联订单" in message["msg"]
    await _clear_current_user(app)


@pytest.mark.asyncio
async def test_my_orders_rejects_invalid_role(client, app, db_session):
    user = User(
        user_id=4106,
        user_uuid=b"1111111111111111",
        user_name="role-user",
        email="role@example.com",
        phonenumber="13800007777",
        sex=SexEnum.UNKNOWN,
        user_type=UserType.USER,
        is_verified=True,
        is_active=True,
        is_admin=False,
        is_deleted=False,
        credit_score=100,
        wechat_openid="openid-role",
    )
    db_session.add(user)
    await db_session.flush()
    await _set_current_user(app, user)

    resp = await client.get("/orders/me", params={"role": "invalid"})
    assert resp.status_code == 200
    message = assert_api_error(resp.json(), code=settings.REQ_ERROR_CODE)
    assert "role 仅支持 buyer/seller/all" in message["msg"]
    await _clear_current_user(app)


async def _prepare_completed_order(client, app, db_session):
    category = Category(category_id=9001, name="评价分类", item_type="POST", config_json={"fields": []})
    publisher = User(
        user_id=9001,
        user_uuid=b"reviewpub0000001",
        user_name="review-publisher",
        email="publisher@bjtu.edu.cn",
        phonenumber="13800009001",
        sex=SexEnum.UNKNOWN,
        user_type=UserType.USER,
        is_verified=True,
        is_active=True,
        is_admin=False,
        is_deleted=False,
        credit_score=100,
        wechat_openid="review-openid-a",
    )
    helper = User(
        user_id=9002,
        user_uuid=b"reviewhelp000002",
        user_name="review-helper",
        email="helper@bjtu.edu.cn",
        phonenumber="13800009002",
        sex=SexEnum.UNKNOWN,
        user_type=UserType.USER,
        is_verified=True,
        is_active=True,
        is_admin=False,
        is_deleted=False,
        credit_score=100,
        wechat_openid="review-openid-b",
    )
    db_session.add_all([category, publisher, helper])
    await db_session.flush()

    post = Post(
        post_id=9101,
        publisher_id=publisher.user_id,
        category_id=category.category_id,
        title="评价测试任务",
        description="用于订单评价测试",
        price=9.9,
        template_data={"max_accepters": 2},
        direction=Direction.SELL,
        urgency=UrgencyLevel.NORMAL,
        status=PostStatus.OPEN,
    )
    db_session.add(post)
    await db_session.flush()

    await _set_current_user(app, helper)
    accept_resp = await client.post(f"/posts/{post.post_id}/accept")
    assert accept_resp.status_code == 200
    order_id = accept_resp.json()["message"]["order_id"]

    await _set_current_user(app, publisher)
    await client.post(f"/orders/{order_id}/approve")
    await client.post(f"/orders/{order_id}/submit-delivery")

    await _set_current_user(app, helper)
    await client.post(f"/orders/{order_id}/accept-delivery")

    order = await db_session.get(Order, order_id)
    assert order.status == OrderStatus.COMPLETED
    return order, publisher, helper


@pytest.mark.asyncio
async def test_order_review_double_blind_flow(client, app, db_session):
    order, publisher, helper = await _prepare_completed_order(client, app, db_session)

    await _set_current_user(app, helper)
    first_resp = await client.post(
        "/orders/reviews",
        json={
            "order_id": order.order_id,
            "reviewee_id": publisher.user_id,
            "review_type": "INITIAL",
            "rating": 5,
            "content": "服务及时",
            "is_anonymous": True,
        },
    )
    assert first_resp.status_code == 200
    assert first_resp.json()["code"] == settings.SUCCESS_CODE
    assert first_resp.json()["message"]["is_visible"] is False

    list_before_resp = await client.get(f"/orders/{order.order_id}/reviews")
    assert list_before_resp.status_code == 200
    assert list_before_resp.json()["code"] == settings.SUCCESS_CODE
    assert len(list_before_resp.json()["message"]["items"]) == 0

    await _set_current_user(app, publisher)
    second_resp = await client.post(
        "/orders/reviews",
        json={
            "order_id": order.order_id,
            "reviewee_id": helper.user_id,
            "review_type": "INITIAL",
            "rating": 4,
            "content": "配合顺畅",
            "is_anonymous": False,
        },
    )
    assert second_resp.status_code == 200
    assert second_resp.json()["code"] == settings.SUCCESS_CODE
    assert second_resp.json()["message"]["is_visible"] is True

    await _set_current_user(app, helper)
    list_after_resp = await client.get(f"/orders/{order.order_id}/reviews")
    assert list_after_resp.status_code == 200
    list_after_body = list_after_resp.json()
    assert list_after_body["code"] == settings.SUCCESS_CODE
    assert len(list_after_body["message"]["items"]) == 2
    assert all(item["is_visible"] is True for item in list_after_body["message"]["items"])

    review_rows = await db_session.execute(select(OrderReview).where(OrderReview.order_id == order.order_id))
    assert len(review_rows.scalars().all()) == 2

    await _clear_current_user(app)


@pytest.mark.asyncio
async def test_order_review_rejects_foreign_user(client, app, db_session):
    order, publisher, helper = await _prepare_completed_order(client, app, db_session)

    outsider = User(
        user_id=9003,
        user_uuid=b"reviewout0000003",
        user_name="review-outsider",
        email="outsider@bjtu.edu.cn",
        phonenumber="13800009003",
        sex=SexEnum.UNKNOWN,
        user_type=UserType.USER,
        is_verified=True,
        is_active=True,
        is_admin=False,
        is_deleted=False,
        credit_score=100,
        wechat_openid="review-openid-c",
    )
    db_session.add(outsider)
    await db_session.flush()

    await _set_current_user(app, outsider)
    resp = await client.post(
        "/orders/reviews",
        json={
            "order_id": order.order_id,
            "reviewee_id": publisher.user_id,
            "review_type": "INITIAL",
            "rating": 5,
            "content": "越权评价",
            "is_anonymous": False,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["code"] == settings.INSUFFICIENT_AUTHORITY_CODE

    await _clear_current_user(app)


@pytest.mark.asyncio
async def test_order_review_auto_release_stub(client, app, db_session):
    order, publisher, helper = await _prepare_completed_order(client, app, db_session)

    await _set_current_user(app, helper)
    resp = await client.post(
        "/orders/reviews",
        json={
            "order_id": order.order_id,
            "reviewee_id": publisher.user_id,
            "review_type": "INITIAL",
            "rating": 5,
            "content": "自动解封前",
            "is_anonymous": False,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["code"] == settings.SUCCESS_CODE

    order.update_time = order.update_time.replace(year=order.update_time.year - 1)
    await db_session.flush()

    released = await OrderReviewService.auto_release_expired_double_blind_reviews(db_session)
    assert released >= 1

    list_resp = await client.get(f"/orders/{order.order_id}/reviews")
    assert list_resp.status_code == 200
    body = list_resp.json()
    assert body["code"] == settings.SUCCESS_CODE
    assert len(body["message"]["items"]) == 1
    assert body["message"]["items"][0]["is_visible"] is True

    await _clear_current_user(app)