"""Post / Order 全链路集成测试（真实 MySQL Testcontainers）。"""

import asyncio
import time
from types import SimpleNamespace
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select

from app.api import get_current_user
from app.core.cleantask import process_delayed_queues_once
from app.core.delay_queue import ORDER_AUTO_CONFIRM_QUEUE_KEY, REVIEW_DOUBLE_BLIND_QUEUE_KEY
from app.core import settings
from app.db import AsyncSessionLocal
from app.models import Attachment, AttachmentTargetType, Category, Direction, ItemType, Order, OrderReview, OrderStatus, Post, PostStatus, SexEnum, UrgencyLevel, User, UserType
from app.models import CreditLog
from app.services import OrderReviewService, OrderService
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
async def test_order_review_can_attach_images(client, app, db_session):
    order, publisher, helper = await _prepare_completed_order(client, app, db_session)

    attachment = Attachment(
        target_type=AttachmentTargetType.USER,
        target_id=helper.user_id,
        url="/static/order_review_attachment.png",
        creator_id=helper.user_id,
    )
    db_session.add(attachment)
    await db_session.flush()

    await _set_current_user(app, helper)
    resp = await client.post(
        "/orders/reviews",
        json={
            "order_id": order.order_id,
            "reviewee_id": publisher.user_id,
            "review_type": "INITIAL",
            "rating": 5,
            "content": "评价时带附件",
            "is_anonymous": False,
            "attachment_ids": [attachment.attachment_id],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == settings.SUCCESS_CODE
    assert body["message"]["attachment_urls"] == ["/static/order_review_attachment.png"]

    await db_session.refresh(attachment)
    assert attachment.target_type == AttachmentTargetType.ORDERREVIEW
    assert attachment.target_id == body["message"]["review_id"]

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


@pytest.mark.asyncio
async def test_delayed_queue_worker_enqueues_and_consumes_order_and_review(client, app, db_session, fake_redis):
    category = Category(
        category_id=9901,
        name="延迟任务分类",
        item_type="POST",
        config_json={"fields": []},
    )
    publisher = User(
        user_id=9901,
        user_uuid=b"queuepub00000001",
        user_name="queue-publisher",
        email="queue-publisher@example.com",
        phonenumber="13800990001",
        sex=SexEnum.UNKNOWN,
        user_type=UserType.USER,
        is_verified=True,
        is_active=True,
        is_admin=False,
        is_deleted=False,
        credit_score=100,
        wechat_openid="queue-openid-a",
    )
    helper = User(
        user_id=9902,
        user_uuid=b"queuehelp0000002",
        user_name="queue-helper",
        email="queue-helper@example.com",
        phonenumber="13800990002",
        sex=SexEnum.UNKNOWN,
        user_type=UserType.USER,
        is_verified=True,
        is_active=True,
        is_admin=False,
        is_deleted=False,
        credit_score=100,
        wechat_openid="queue-openid-b",
    )
    db_session.add_all([category, publisher, helper])
    await db_session.flush()

    post = Post(
        post_id=9903,
        publisher_id=publisher.user_id,
        category_id=category.category_id,
        title="延迟队列任务",
        description="用于验证 Redis ZSET 延迟队列",
        price=18.0,
        template_data={"max_accepters": 1},
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
    approve_resp = await client.post(f"/orders/{order_id}/approve")
    assert approve_resp.status_code == 200

    submit_resp = await client.post(f"/orders/{order_id}/submit-delivery")
    assert submit_resp.status_code == 200
    assert submit_resp.json()["message"]["status"] == "CONFIRMED"

    auto_score = await fake_redis.zscore(ORDER_AUTO_CONFIRM_QUEUE_KEY, str(order_id))
    assert auto_score is not None
    assert auto_score > time.time()

    await fake_redis.zadd(ORDER_AUTO_CONFIRM_QUEUE_KEY, {str(order_id): time.time() - 1})
    @asynccontextmanager
    async def _session_factory():
        yield db_session

    processed = await process_delayed_queues_once(session_factory=_session_factory)
    assert processed is True

    order_after_worker = await db_session.get(Order, order_id)
    await db_session.refresh(order_after_worker)
    assert order_after_worker.status == OrderStatus.COMPLETED

    order_after_worker.update_time = order_after_worker.update_time.replace(year=order_after_worker.update_time.year - 1)
    await db_session.flush()

    await _set_current_user(app, helper)
    review_resp = await client.post(
        "/orders/reviews",
        json={
            "order_id": order_id,
            "reviewee_id": publisher.user_id,
            "review_type": "INITIAL",
            "rating": 5,
            "content": "延迟队列首评",
            "is_anonymous": False,
        },
    )
    assert review_resp.status_code == 200
    assert review_resp.json()["code"] == settings.SUCCESS_CODE

    review_score = await fake_redis.zscore(REVIEW_DOUBLE_BLIND_QUEUE_KEY, str(order_id))
    assert review_score is not None
    assert review_score > time.time()

    await fake_redis.zadd(REVIEW_DOUBLE_BLIND_QUEUE_KEY, {str(order_id): time.time() - 1})
    processed = await process_delayed_queues_once(session_factory=_session_factory)
    assert processed is True

    review_rows = await db_session.execute(select(OrderReview).where(OrderReview.order_id == order_id))
    assert len(review_rows.scalars().all()) == 1
    assert (await db_session.get(Order, order_id)).status == OrderStatus.COMPLETED

    await _clear_current_user(app)


@pytest.mark.asyncio
async def test_order_complete_route_allows_admin_force_complete(client, app, db_session):
    category = Category(
        category_id=30021,
        name="管理员完结分类",
        item_type="POST",
        config_json={"fields": []},
    )
    publisher = User(
        user_id=30021,
        user_uuid=b"adminforcepub001",
        user_name="admin-force-publisher",
        email="admin-force-publisher@example.com",
        phonenumber="13800030021",
        sex=SexEnum.UNKNOWN,
        user_type=UserType.USER,
        is_verified=True,
        is_active=True,
        is_admin=False,
        is_deleted=False,
        credit_score=100,
        wechat_openid="admin-force-openid-a",
    )
    buyer = User(
        user_id=30022,
        user_uuid=b"adminforcebuy002",
        user_name="admin-force-buyer",
        email="admin-force-buyer@example.com",
        phonenumber="13800030022",
        sex=SexEnum.UNKNOWN,
        user_type=UserType.USER,
        is_verified=True,
        is_active=True,
        is_admin=False,
        is_deleted=False,
        credit_score=100,
        wechat_openid="admin-force-openid-b",
    )
    db_session.add_all([category, publisher, buyer])
    await db_session.flush()

    post = Post(
        post_id=30023,
        publisher_id=publisher.user_id,
        category_id=category.category_id,
        title="管理员强制完结帖子",
        description="用于验证管理员完结",
        price=15.0,
        template_data={"max_accepters": 1},
        direction=Direction.SELL,
        urgency=UrgencyLevel.NORMAL,
        status=PostStatus.OPEN,
    )
    db_session.add(post)
    await db_session.flush()

    await _set_current_user(app, buyer)
    accept_resp = await client.post(f"/posts/{post.post_id}/accept")
    assert accept_resp.status_code == 200
    order_id = accept_resp.json()["message"]["order_id"]

    await _set_current_user(app, publisher)
    approve_resp = await client.post(f"/orders/{order_id}/approve")
    assert approve_resp.status_code == 200

    await _set_current_user(app, buyer)
    reject_accept_resp = await client.post(f"/orders/{order_id}/accept-delivery")
    assert reject_accept_resp.status_code == 200
    reject_accept_body = reject_accept_resp.json()
    assert reject_accept_body["code"] == settings.REQ_ERROR_CODE
    assert "只有待验收订单可以确认完成" in reject_accept_body["message"]["msg"]

    test_admin_user = User(
        user_id=30024,
        user_uuid=b"adminforceadm003",
        user_name="admin-force-admin",
        email="admin-force-admin@example.com",
        phonenumber="13800030024",
        sex=SexEnum.UNKNOWN,
        user_type=UserType.ADMIN,
        is_verified=True,
        is_active=True,
        is_admin=True,
        is_deleted=False,
        credit_score=100,
        wechat_openid="admin-force-openid-c",
    )
    db_session.add(test_admin_user)
    await db_session.flush()
    await _set_current_user(app, test_admin_user)

    complete_resp = await client.post(f"/orders/{order_id}/complete")
    assert complete_resp.status_code == 200
    complete_body = complete_resp.json()
    assert complete_body["code"] == settings.SUCCESS_CODE
    assert complete_body["message"]["status"] == "COMPLETED"

    final_order = await db_session.get(Order, order_id)
    assert final_order.status == OrderStatus.COMPLETED
    final_post = await db_session.get(Post, post.post_id)
    assert final_post.status == PostStatus.CLOSED

    await _clear_current_user(app)


@pytest.mark.asyncio
async def test_buy_direction_applications_require_post_publisher(client, app, db_session):
    category = Category(
        category_id=30031,
        name="BUY 审批分类",
        item_type="POST",
        config_json={"fields": []},
    )
    publisher = User(
        user_id=30031,
        user_uuid=b"buyflowpub0001",
        user_name="buy-flow-publisher",
        email="buy-flow-publisher@example.com",
        phonenumber="13800030031",
        sex=SexEnum.UNKNOWN,
        user_type=UserType.USER,
        is_verified=True,
        is_active=True,
        is_admin=False,
        is_deleted=False,
        credit_score=100,
        wechat_openid="buy-flow-openid-a",
    )
    applicant = User(
        user_id=30032,
        user_uuid=b"buyflowapp0002",
        user_name="buy-flow-applicant",
        email="buy-flow-applicant@example.com",
        phonenumber="13800030032",
        sex=SexEnum.UNKNOWN,
        user_type=UserType.USER,
        is_verified=True,
        is_active=True,
        is_admin=False,
        is_deleted=False,
        credit_score=100,
        wechat_openid="buy-flow-openid-b",
    )
    db_session.add_all([category, publisher, applicant])
    await db_session.flush()

    post = Post(
        post_id=30033,
        publisher_id=publisher.user_id,
        category_id=category.category_id,
        title="BUY 方向帖子",
        description="用于验证审批人",
        price=18.0,
        template_data={"max_accepters": 1},
        direction=Direction.BUY,
        urgency=UrgencyLevel.NORMAL,
        status=PostStatus.OPEN,
    )
    db_session.add(post)
    await db_session.flush()

    await _set_current_user(app, applicant)
    accept_resp = await client.post(f"/posts/{post.post_id}/accept")
    assert accept_resp.status_code == 200
    order_id = accept_resp.json()["message"]["order_id"]

    applicant_approve_resp = await client.post(f"/orders/{order_id}/approve")
    assert applicant_approve_resp.status_code == 200
    applicant_approve_body = applicant_approve_resp.json()
    assert applicant_approve_body["code"] == settings.INSUFFICIENT_AUTHORITY_CODE
    assert "只有发帖人可以同意接单" in applicant_approve_body["message"]["msg"]

    await _set_current_user(app, publisher)
    publisher_approve_resp = await client.post(f"/orders/{order_id}/approve")
    assert publisher_approve_resp.status_code == 200
    publisher_approve_body = publisher_approve_resp.json()
    assert publisher_approve_body["code"] == settings.SUCCESS_CODE
    assert publisher_approve_body["message"]["status"] == "ONGOING"

    final_order = await db_session.get(Order, order_id)
    assert final_order.seller_id == applicant.user_id
    assert final_order.buyer_id == publisher.user_id
    assert final_order.status == OrderStatus.ONGOING

    await _clear_current_user(app)


@pytest.mark.asyncio
async def test_auto_confirm_and_accept_delivery_do_not_double_credit(monkeypatch):
    class DummySession:
        async def flush(self):
            return None

        async def refresh(self, obj):
            return None

        async def commit(self):
            return None

        async def execute(self, stmt):
            class _DummyScalarResult:
                def first(self_inner):
                    return post

            class _DummyResult:
                def scalars(self_inner):
                    return _DummyScalarResult()

            return _DummyResult()

    order = SimpleNamespace(
        order_id=30043,
        status=OrderStatus.CONFIRMED,
        buyer_id=30042,
        seller_id=30041,
        item_type=ItemType.POST,
        item_id=40043,
        meta_data={},
        update_time=None,
    )
    post = SimpleNamespace(status=PostStatus.OPEN)
    credit_calls: list[tuple[int, int, str]] = []

    async def _fake_get_order_for_update(db, order_id):
        return order

    async def _fake_get_post_for_update(db, post_id):
        return post

    async def _fake_add_credit(db, user_id, amount, reason):
        credit_calls.append((user_id, amount, reason))

    monkeypatch.setattr(OrderService, "_get_order_for_update", staticmethod(_fake_get_order_for_update))
    monkeypatch.setattr(OrderService, "_get_post_for_update", staticmethod(_fake_get_post_for_update))
    monkeypatch.setattr(OrderService, "_load_goods_for_update", staticmethod(lambda db, goods_id: None))
    monkeypatch.setattr(OrderService, "_add_credit", staticmethod(_fake_add_credit))

    auto_result = await OrderService.auto_confirm_overdue_order_by_id(DummySession(), order.order_id)
    assert auto_result is True

    with pytest.raises(Exception):
        await OrderService.accept_delivery(DummySession(), order.order_id, order.buyer_id)
    assert len(credit_calls) == 1
    assert order.status == OrderStatus.COMPLETED
    assert post.status == PostStatus.CLOSED


async def test_register_scheduler_jobs_adds_double_blind_fallback():
    from app.main import register_scheduler_jobs

    captured = {}

    class FakeScheduler:
        def add_job(self, func, trigger, **kwargs):
            captured["func"] = func
            captured["trigger"] = trigger
            captured["kwargs"] = kwargs

    register_scheduler_jobs(FakeScheduler())
    assert captured["trigger"] == "interval"
    assert captured["kwargs"]["id"] == "auto_release_expired_double_blind_reviews"
    assert captured["kwargs"]["replace_existing"] is True