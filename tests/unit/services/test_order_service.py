from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core import settings
from app.core.exception_handler import BusinessHTTPException, ResourceHTTPException
from app.models import Direction, Goods, ItemType, Order, OrderStatus, OrderTriggerType, Post, PostStatus, User
from app.services.order_service import OrderService
from tests.unit.fake_sqlalchemy import AsyncContextManager, FakeResult


pytestmark = pytest.mark.asyncio


def build_db(*, execute_side_effect=None):
    db = SimpleNamespace()
    db.execute = AsyncMock(side_effect=execute_side_effect or [])
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    db.commit = AsyncMock()
    db.begin_nested = MagicMock(return_value=AsyncContextManager())
    return db


def build_post(**overrides):
    payload = {
        "post_id": 2001,
        "publisher_id": 3001,
        "status": PostStatus.OPEN,
        "direction": Direction.SELL,
        "max_accepters": 2,
        "template_data": {"max_accepters": 2},
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def build_goods(**overrides):
    payload = {
        "goods_id": 4001,
        "publisher_id": 5001,
        "is_sold": False,
        "template_data": {},
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def build_order(**overrides):
    payload = {
        "order_id": 6001,
        "buyer_id": 7001,
        "seller_id": 8001,
        "initiator_id": 7001,
        "item_type": ItemType.POST,
        "item_id": 2001,
        "trigger_type": OrderTriggerType.DIRECT,
        "status": OrderStatus.PENDING,
        "meta_data": {"note": "hello"},
        "accepted_time": None,
        "create_time": datetime(2026, 5, 27, 8, 0),
        "update_time": datetime(2026, 5, 27, 9, 0),
        "buyer": SimpleNamespace(user_id=7001),
        "seller": SimpleNamespace(user_id=8001),
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


async def test_helper_functions_cover_multiple_branches():
    assert OrderService._normalize_item_type("posts") == ItemType.POST
    assert OrderService._normalize_status_filter(None) is None
    assert OrderService._normalize_status_filter("accepted") == OrderStatus.ONGOING
    assert OrderService._normalize_status_filter("rejected") == OrderStatus.REJECTED
    assert OrderService._normalize_status_filter("all") is None
    assert OrderService._display_status(OrderStatus.COMPLETED) == "COMPLETED"
    assert OrderService._resolve_order_participants("BUY", 1, 2) == (1, 2)
    assert OrderService._resolve_order_participants("SELL", 1, 2) == (2, 1)
    assert OrderService._resolve_trigger_type_for_post("BUY") == OrderTriggerType.APPLICATION
    assert OrderService._resolve_trigger_type_for_post("SELL") == OrderTriggerType.COLLECTIVE

    post = build_post(template_data={"locked": False})
    goods = build_goods(template_data={"locked": True})
    order = build_order()
    order_dict = OrderService._serialize_order(order)
    OrderService._apply_completion_side_effects(order, post, goods)

    assert order_dict["item_type"] == "POST"
    assert order_dict["status"] == "PENDING"
    assert order_dict["accepted_time"] is None
    assert post.status == PostStatus.CLOSED
    assert goods.is_sold is True
    assert goods.template_data == {}

    with pytest.raises(BusinessHTTPException):
        OrderService._normalize_item_type("invalid")
    with pytest.raises(BusinessHTTPException):
        OrderService._normalize_status_filter("invalid")


async def test_get_current_accepters_count_and_map():
    db = build_db(execute_side_effect=[FakeResult(scalar_value=3), FakeResult(rows=[(2001, 2), (2002, 1)])])

    count = await OrderService.get_current_accepters_count(db, "posts", 2001)
    empty_map = await OrderService.get_current_accepters_count_map(db, "POST", [])
    count_map = await OrderService.get_current_accepters_count_map(db, "POST", [2001, 2001, 2002])

    assert count == 3
    assert empty_map == {}
    assert count_map == {2001: 2, 2002: 1}


async def test_create_order_post_error_branches():
    db = build_db(execute_side_effect=[FakeResult(items=[None]), FakeResult(scalar_value=0)])
    self_post = build_post(publisher_id=1001)
    with pytest.raises(BusinessHTTPException) as own_post:
        await OrderService.create_order(db, "POST", 2001, 1001, post=self_post)
    assert "不能接自己的帖子" in own_post.value.detail["msg"]

    db = build_db(execute_side_effect=[FakeResult(items=[None]), FakeResult(scalar_value=0)])
    closed_post = build_post(status=PostStatus.CLOSED)
    with pytest.raises(BusinessHTTPException) as closed_err:
        await OrderService.create_order(db, "POST", 2001, 1002, post=closed_post)
    assert "当前帖子状态不允许接单" in closed_err.value.detail["msg"]

    db = build_db(execute_side_effect=[FakeResult(items=[1]), FakeResult(scalar_value=0)])
    with pytest.raises(BusinessHTTPException) as duplicate_err:
        await OrderService.create_order(db, "POST", 2001, 1002, post=build_post())
    assert "该帖子已申请过" in duplicate_err.value.detail["msg"]

    db = build_db(execute_side_effect=[FakeResult(items=[None]), FakeResult(scalar_value=2)])
    with pytest.raises(BusinessHTTPException) as full_err:
        await OrderService.create_order(db, "POST", 2001, 1002, post=build_post(max_accepters=2))
    assert "接单已满" in full_err.value.detail["msg"]


async def test_create_order_post_success(monkeypatch):
    db = build_db(execute_side_effect=[FakeResult(items=[None]), FakeResult(scalar_value=0)])
    post = build_post(direction=Direction.BUY, publisher_id=3001, max_accepters=3)

    order = await OrderService.create_order(
        db,
        "POST",
        2001,
        4001,
        trigger_type="unknown",
        post=post,
    )

    assert order.status == OrderStatus.PENDING
    assert order.trigger_type == OrderTriggerType.APPLICATION
    assert order.buyer_id == 3001
    assert order.seller_id == 4001
    assert db.commit.await_count == 1


async def test_create_order_goods_branches():
    own_goods = build_goods(publisher_id=1001)
    db = build_db(execute_side_effect=[FakeResult(items=[own_goods])])
    with pytest.raises(BusinessHTTPException) as own_err:
        await OrderService.create_order(db, "GOODS", 4001, 1001)
    assert "不能购买自己的商品" in own_err.value.detail["msg"]

    db = build_db(execute_side_effect=[FakeResult(items=[build_goods(is_sold=True)])])
    with pytest.raises(BusinessHTTPException) as sold_err:
        await OrderService.create_order(db, "GOODS", 4001, 1002)
    assert "商品已售出" in sold_err.value.detail["msg"]

    db = build_db(execute_side_effect=[FakeResult(items=[build_goods(template_data={"locked": True})])])
    with pytest.raises(BusinessHTTPException) as locked_err:
        await OrderService.create_order(db, "GOODS", 4001, 1002)
    assert "商品已被锁定，无法购买" in locked_err.value.detail["msg"]

    goods = build_goods(template_data={})
    db = build_db(execute_side_effect=[FakeResult(items=[goods])])
    order = await OrderService.create_order(db, "GOODS", 4001, 1002)

    assert order.status == OrderStatus.ONGOING
    assert order.trigger_type == OrderTriggerType.DIRECT
    assert goods.template_data["locked"] is True


async def test_batch_accept_posts_and_error_mapping(monkeypatch):
    db = build_db()
    with pytest.raises(BusinessHTTPException) as too_many:
        await OrderService.batch_accept_posts(db, 1001, [1, 2, 3, 4, 5, 6])
    assert "最多一次只能接 5 单" in too_many.value.detail["msg"]

    buy_post = build_post(direction=Direction.BUY, post_id=1)
    sell_post = build_post(direction=Direction.SELL, post_id=2)
    order = build_order(order_id=9001, status=OrderStatus.PENDING)
    monkeypatch.setattr(OrderService, "_get_post_for_update", AsyncMock(side_effect=[buy_post, sell_post]))
    monkeypatch.setattr(OrderService, "create_order", AsyncMock(return_value=order))

    result = await OrderService.batch_accept_posts(build_db(), 1001, [1, 2])

    assert result["results"][0]["post_id"] == 1
    assert result["errors"][0]["error"] == "INVALID_DIRECTION"
    assert OrderService._batch_accept_error_code(BusinessHTTPException(code=1, msg="不能接自己的帖子"))[0] == "OWN_POST"
    assert OrderService._batch_accept_error_code(BusinessHTTPException(code=1, msg="该帖子已申请过"))[0] == "ALREADY_ACCEPTED"
    assert OrderService._batch_accept_error_code(BusinessHTTPException(code=1, msg="接单已满"))[0] == "FULL"


async def test_submit_delivery_and_auto_confirm_branches(monkeypatch):
    order = build_order(status=OrderStatus.PENDING, seller_id=8001, meta_data={})
    db = build_db()
    monkeypatch.setattr(OrderService, "_get_order_for_update", AsyncMock(return_value=order))
    monkeypatch.setattr("app.services.order_service.enqueue_delayed_task", AsyncMock(side_effect=RuntimeError("boom")))

    with pytest.raises(BusinessHTTPException) as status_err:
        await OrderService.submit_delivery(build_db(), 1, 8001)
    assert "只有进行中订单可以提交交付" in status_err.value.detail["msg"]

    order.status = OrderStatus.ONGOING
    with pytest.raises(BusinessHTTPException) as auth_err:
        await OrderService.submit_delivery(db, 1, 9999)
    assert "只有卖家可以提交交付" in auth_err.value.detail["msg"]

    order.status = OrderStatus.ONGOING
    result = await OrderService.submit_delivery(db, 1, 8001)
    assert result.status == OrderStatus.CONFIRMED

    order.status = OrderStatus.PENDING
    assert await OrderService.auto_confirm_overdue_order_by_id(db, 1) is False


async def test_accept_delivery_force_complete_and_cancel_status_machine(monkeypatch):
    order = build_order(status=OrderStatus.PENDING, buyer_id=7001, seller_id=8001, meta_data={}, item_type=ItemType.POST)
    post = build_post()
    db = build_db()
    monkeypatch.setattr(OrderService, "_get_order_for_update", AsyncMock(return_value=order))
    monkeypatch.setattr(OrderService, "_get_post_for_update", AsyncMock(return_value=post))
    monkeypatch.setattr(OrderService, "_load_goods_for_update", AsyncMock(return_value=None))
    monkeypatch.setattr(OrderService, "_add_credit", AsyncMock(return_value=None))

    with pytest.raises(BusinessHTTPException) as status_err:
        await OrderService.accept_delivery(build_db(), 1, 7001)
    assert "只有待验收订单可以确认完成" in status_err.value.detail["msg"]

    order.status = OrderStatus.CONFIRMED
    with pytest.raises(BusinessHTTPException) as auth_err:
        await OrderService.accept_delivery(db, 1, 9999)
    assert "只有买家可以确认验收" in auth_err.value.detail["msg"]

    result = await OrderService.accept_delivery(db, 1, 7001)
    assert result.status == OrderStatus.COMPLETED
    assert post.status == PostStatus.CLOSED

    completed = build_order(status=OrderStatus.COMPLETED)
    monkeypatch.setattr(OrderService, "_get_order_for_update", AsyncMock(return_value=completed))
    assert await OrderService.force_complete_order_by_admin(db, 1, 1) == completed


async def test_list_orders_and_related_lookups(monkeypatch):
    db = build_db(execute_side_effect=[FakeResult(scalar_value=1), FakeResult(items=[build_order()])])
    monkeypatch.setattr("app.services.order_service.parse_datetime_to_beijing_naive", lambda value: datetime(2026, 5, 27, 10, 0))

    with pytest.raises(BusinessHTTPException) as role_err:
        await OrderService.list_orders(db, 1001, role="invalid")
    assert "role 仅支持 buyer/seller/all" in role_err.value.detail["msg"]

    orders, total = await OrderService.list_orders(db, 1001, role="all", status="accepted", start_time="2026-05-27T00:00:00", end_time="2026-05-28T00:00:00")
    assert total == 1
    assert len(orders) == 1

    items = await OrderService.list_orders_by_item(build_db(execute_side_effect=[FakeResult(items=[build_order()])]), "posts", 2001)
    assert len(items) == 1

    rows = await OrderService.list_post_applications(
        build_db(execute_side_effect=[FakeResult(rows=[(build_order(status=OrderStatus.PENDING), build_user := SimpleNamespace(user_id=9001, avatar_attachment=SimpleNamespace(url="/a.png")), 2)])]),
        2001,
    )
    assert rows[0]["completed_order_count"] == 2
    assert rows[0]["note"] == "hello"


async def test_get_order_detail_and_status_transitions(monkeypatch):
    order = build_order(status=OrderStatus.PENDING, buyer_id=7001, seller_id=8001)
    db = build_db()
    monkeypatch.setattr(OrderService, "_get_order_readonly", AsyncMock(return_value=order))
    monkeypatch.setattr(OrderService, "_get_order_for_update", AsyncMock(return_value=order))
    monkeypatch.setattr(OrderService, "_get_post_for_update", AsyncMock(return_value=build_post()))
    monkeypatch.setattr(OrderService, "_load_goods_for_update", AsyncMock(return_value=build_goods()))
    monkeypatch.setattr(OrderService, "_add_credit", AsyncMock(return_value=None))

    with pytest.raises(BusinessHTTPException) as auth_err:
        await OrderService.get_order_detail(db, 1, 1)
    assert "仅订单相关方可查看订单详情" in auth_err.value.detail["msg"]

    assert await OrderService.get_order_detail(db, 1, 7001) == order

    with pytest.raises(ResourceHTTPException):
        await OrderService.update_status(build_db(execute_side_effect=[FakeResult(items=[None])]), 1, "ONGOING", 1)

    db_illegal = build_db(execute_side_effect=[FakeResult(items=[order])])
    with pytest.raises(BusinessHTTPException) as illegal_err:
        await OrderService.update_status(db_illegal, 1, "INVALID", 1)
    assert "非法目标状态" in illegal_err.value.detail["msg"]

    db_transition = build_db(execute_side_effect=[FakeResult(items=[order])])
    with pytest.raises(BusinessHTTPException) as transition_err:
        await OrderService.update_status(db_transition, 1, "COMPLETED", 1)
    assert "非法的状态迁移" in transition_err.value.detail["msg"]


@pytest.mark.asyncio
async def test_create_order_goods_not_found_raises():
    class FakeDB:
        async def execute(self, stmt):
            # simulate no goods found
            return FakeResult(items=None)

        def add(self, obj):
            pass

    fake_db = FakeDB()
    with pytest.raises(ResourceHTTPException):
        await OrderService.create_order(db=fake_db, item_type="GOODS", item_id=9999, initiator_id=1, commit=False)


@pytest.mark.asyncio
async def test_update_status_pending_to_ongoing_success():
    from app.services.order_service import OrderStatus, OrderTriggerType

    class DummyOrder3:
        def __init__(self):
            self.status = OrderStatus.PENDING
            self.order_id = 333
            self.seller_id = 11
            self.buyer_id = 22
            self.initiator_id = 22
            self.item_type = None
            self.trigger_type = OrderTriggerType.APPLICATION
            self.meta_data = {}

    class FakeDB7:
        async def execute(self, stmt):
            return FakeResult(items=[DummyOrder3()])

        async def flush(self):
            pass

        async def refresh(self, o):
            pass

        async def commit(self):
            pass

    db7 = FakeDB7()
    order = await OrderService.update_status(db=db7, order_id=333, new_status="ONGOING", operator_id=11)
    assert order.status.name == "ONGOING"
    assert isinstance(order.meta_data.get("history"), list)


async def test_add_credit_noop_and_user_missing(monkeypatch):
    # amount == 0 should return early
    db = build_db()
    await OrderService._add_credit(db, user_id=1, amount=0, reason="noop")

    # user missing should raise ResourceHTTPException
    class FakeDBNoUser:
        async def execute(self, stmt):
            return FakeResult(items=[None])

    with pytest.raises(ResourceHTTPException):
        await OrderService._add_credit(FakeDBNoUser(), user_id=9999, amount=10, reason="x")


@pytest.mark.asyncio
async def test_cancel_order_goods_unlocks_template(monkeypatch):
    from app.services.order_service import OrderStatus, ItemType

    class OrderObj:
        def __init__(self):
            self.status = OrderStatus.ONGOING
            self.order_id = 555
            self.buyer_id = 1
            self.seller_id = 2
            self.item_type = ItemType.GOODS
            self.item_id = 4001
            self.meta_data = {}

    class GoodsObj:
        def __init__(self):
            self.goods_id = 4001
            self.template_data = {"locked": True}

    async def fake_get_order(db, oid):
        return OrderObj()

    async def fake_execute(stmt):
        return FakeResult(items=[GoodsObj()])

    monkeypatch.setattr(OrderService, "_get_order_for_update", fake_get_order)
    class FakeDB:
        async def execute(self, stmt):
            return FakeResult(items=[GoodsObj()])

        async def flush(self):
            pass

        async def refresh(self, o):
            pass

        async def commit(self):
            pass

    db = FakeDB()
    res = await OrderService.cancel_order(db, order_id=555, operator_id=1)
    assert res.status == OrderStatus.CANCELED


@pytest.mark.asyncio
async def test_approve_and_reject_order_branches(monkeypatch):
    # approve_order: collective branch where accepted_cnt >= max -> pending orders set to ONGOING
    order = build_order(status=OrderStatus.PENDING, item_type=ItemType.POST, item_id=2001)
    post = build_post()
    pending_other = build_order(order_id=9002, status=OrderStatus.PENDING)

    async def fake_get_order(db, oid):
        return order

    async def fake_get_post(db, pid):
        return post

    async def fake_execute_pending(stmt):
        return FakeResult(items=None, rows=[pending_other])

    class FakeDBA:
        async def execute(self, stmt):
            return FakeResult(rows=[pending_other])

        async def flush(self):
            pass

        async def refresh(self, o):
            pass

        async def commit(self):
            pass

    monkeypatch.setattr(OrderService, "_get_order_for_update", fake_get_order)
    monkeypatch.setattr(OrderService, "_get_post_for_update", fake_get_post)
    monkeypatch.setattr(OrderService, "get_current_accepters_count", AsyncMock(return_value=2))

    db_a = FakeDBA()
    res = await OrderService.approve_order(db_a, order_id=6001, operator_id=post.publisher_id)
    assert res.status == OrderStatus.ONGOING
    assert post.status in (PostStatus.IN_PROGRESS, PostStatus.OPEN)

    # reject_order: set to REJECTED and post status depends on remaining statuses
    order2 = build_order(status=OrderStatus.PENDING, item_type=ItemType.POST, item_id=2001)
    post2 = build_post()

    async def fake_exec_remaining_open(stmt):
        return FakeResult(items=[])

    async def fake_exec_remaining_inprog(stmt):
        return FakeResult(items=[OrderStatus.ONGOING])

    monkeypatch.setattr(OrderService, "_get_order_for_update", AsyncMock(return_value=order2))
    monkeypatch.setattr(OrderService, "_get_post_for_update", AsyncMock(return_value=post2))

    class FakeDBB:
        def __init__(self, res):
            self._res = res

        async def execute(self, stmt):
            return self._res

        async def flush(self):
            pass

        async def refresh(self, o):
            pass

        async def commit(self):
            pass

    db_b1 = FakeDBB(FakeResult(items=[]))
    r1 = await OrderService.reject_order(db_b1, order_id=6001, operator_id=post2.publisher_id)
    assert r1.status == OrderStatus.REJECTED
    assert post2.status == PostStatus.OPEN

    post3 = build_post()
    order3 = build_order(status=OrderStatus.PENDING)
    monkeypatch.setattr(OrderService, "_get_order_for_update", AsyncMock(return_value=order3))
    monkeypatch.setattr(OrderService, "_get_post_for_update", AsyncMock(return_value=post3))
    db_b2 = FakeDBB(FakeResult(items=[OrderStatus.ONGOING]))
    r2 = await OrderService.reject_order(db_b2, order_id=6001, operator_id=post3.publisher_id)
    assert r2.status == OrderStatus.REJECTED


@pytest.mark.asyncio
async def test_approve_order_non_collective_rejects_others(monkeypatch):
    order = build_order(status=OrderStatus.PENDING, item_type=ItemType.POST, item_id=2001, trigger_type=OrderTriggerType.APPLICATION)
    post = build_post()
    pending_other = build_order(order_id=9002, status=OrderStatus.PENDING)

    async def fake_get_order(db, oid):
        return order

    async def fake_get_post(db, pid):
        return post

    class FakeDB:
        async def execute(self, stmt):
            # simulate one pending other
            return FakeResult(items=[pending_other])

        async def flush(self):
            pass

        async def refresh(self, o):
            pass

        async def commit(self):
            pass

    monkeypatch.setattr(OrderService, "_get_order_for_update", fake_get_order)
    monkeypatch.setattr(OrderService, "_get_post_for_update", fake_get_post)

    res = await OrderService.approve_order(FakeDB(), order_id=6001, operator_id=post.publisher_id)
    assert res.status == OrderStatus.ONGOING
    assert post.status == PostStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_approve_order_collective_not_full(monkeypatch):
    order = build_order(status=OrderStatus.PENDING, item_type=ItemType.POST, item_id=2001, trigger_type=OrderTriggerType.COLLECTIVE)
    post = build_post(max_accepters=3)

    async def fake_get_order(db, oid):
        return order

    async def fake_get_post(db, pid):
        return post

    monkeypatch.setattr(OrderService, "_get_order_for_update", fake_get_order)
    monkeypatch.setattr(OrderService, "_get_post_for_update", fake_get_post)
    monkeypatch.setattr(OrderService, "get_current_accepters_count", AsyncMock(return_value=1))

    class FakeDB2:
        async def execute(self, stmt):
            return FakeResult(items=[])

        async def flush(self):
            pass

        async def refresh(self, o):
            pass

        async def commit(self):
            pass

    res = await OrderService.approve_order(FakeDB2(), order_id=6001, operator_id=post.publisher_id)
    assert res.status == OrderStatus.ONGOING
    assert post.status == PostStatus.OPEN


@pytest.mark.asyncio
async def test_approve_order_collective_full_promotes_all_pending(monkeypatch):
    order = build_order(status=OrderStatus.PENDING, item_type=ItemType.POST, item_id=2001, trigger_type=OrderTriggerType.COLLECTIVE)
    post = build_post(max_accepters=1)
    pending_one = build_order(order_id=9001, status=OrderStatus.PENDING)
    pending_two = build_order(order_id=9002, status=OrderStatus.PENDING)

    async def fake_get_order(db, oid):
        return order

    async def fake_get_post(db, pid):
        return post

    class FakeDB:
        async def execute(self, stmt):
            return FakeResult(items=[pending_one, pending_two])

        async def flush(self):
            pass

        async def refresh(self, o):
            pass

        async def commit(self):
            pass

    monkeypatch.setattr(OrderService, "_get_order_for_update", fake_get_order)
    monkeypatch.setattr(OrderService, "_get_post_for_update", fake_get_post)
    monkeypatch.setattr(OrderService, "get_current_accepters_count", AsyncMock(return_value=1))

    res = await OrderService.approve_order(FakeDB(), order_id=6001, operator_id=post.publisher_id)
    assert res.status == OrderStatus.ONGOING
    assert post.status == PostStatus.IN_PROGRESS
    assert pending_one.status == OrderStatus.ONGOING
    assert pending_two.status == OrderStatus.ONGOING


@pytest.mark.asyncio
async def test_update_status_matrix_covers_disputed_and_cancel_paths():
    class FakeDB:
        def __init__(self, order, extra_results=None):
            self.order = order
            self.extra_results = extra_results or []

        async def execute(self, stmt):
            if self.order is not None:
                order = self.order
                self.order = None
                return FakeResult(items=[order])
            if self.extra_results:
                return self.extra_results.pop(0)
            return FakeResult(items=[])

        async def flush(self):
            pass

        async def refresh(self, o):
            pass

        async def commit(self):
            pass

    pending = build_order(status=OrderStatus.PENDING, buyer_id=7001, seller_id=8001, initiator_id=7001)
    res_pending = await OrderService.update_status(FakeDB(pending), pending.order_id, "CANCELED", operator_id=7001)
    assert res_pending.status == OrderStatus.CANCELED
    assert isinstance(res_pending.meta_data.get("history"), list)

    pending_unauth = build_order(status=OrderStatus.PENDING, buyer_id=7001, seller_id=8001, initiator_id=7001)
    with pytest.raises(BusinessHTTPException) as pending_exc:
        await OrderService.update_status(FakeDB(pending_unauth), pending_unauth.order_id, "CANCELED", operator_id=9999)
    assert "只有发起人或发布者可以取消申请" in pending_exc.value.detail["msg"]

    ongoing = build_order(status=OrderStatus.ONGOING, buyer_id=7001, seller_id=8001)
    res_disputed = await OrderService.update_status(FakeDB(ongoing), ongoing.order_id, "DISPUTED", operator_id=7001)
    assert res_disputed.status == OrderStatus.DISPUTED

    ongoing_cancel = build_order(status=OrderStatus.ONGOING, buyer_id=7001, seller_id=8001)
    with pytest.raises(BusinessHTTPException) as ongoing_exc:
        await OrderService.update_status(FakeDB(ongoing_cancel), ongoing_cancel.order_id, "CANCELED", operator_id=9999)
    assert "只有买家或卖家可以取消进行中的订单" in ongoing_exc.value.detail["msg"]

    disputed = build_order(status=OrderStatus.DISPUTED, buyer_id=7001, seller_id=8001)
    res_confirmed = await OrderService.update_status(FakeDB(disputed), disputed.order_id, "CONFIRMED", operator_id=8001)
    assert res_confirmed.status == OrderStatus.CONFIRMED

    disputed_cancel = build_order(status=OrderStatus.DISPUTED, buyer_id=7001, seller_id=8001)
    with pytest.raises(BusinessHTTPException) as disputed_exc:
        await OrderService.update_status(FakeDB(disputed_cancel), disputed_cancel.order_id, "CANCELED", operator_id=7001)
    assert "该阶段不允许取消" in disputed_exc.value.detail["msg"]


@pytest.mark.asyncio
async def test_cancel_order_and_completion_goods_paths(monkeypatch):
    post_order = build_order(status=OrderStatus.PENDING, buyer_id=7001, seller_id=8001, initiator_id=7001, item_type=ItemType.POST, item_id=2001)
    post = build_post()
    goods_order = build_order(status=OrderStatus.ONGOING, buyer_id=7001, seller_id=8001, item_type=ItemType.GOODS, item_id=4001)
    goods = build_goods(template_data={"locked": True})

    class FakeDBPost:
        def __init__(self):
            self.calls = 0

        async def execute(self, stmt):
            self.calls += 1
            if self.calls == 1:
                return FakeResult(items=[post_order])
            return FakeResult(items=[])

        async def flush(self):
            pass

        async def refresh(self, o):
            pass

        async def commit(self):
            pass

    async def fake_get_post(db, pid):
        return post

    monkeypatch.setattr(OrderService, "_get_post_for_update", fake_get_post)
    canceled_post = await OrderService.cancel_order(FakeDBPost(), order_id=post_order.order_id, operator_id=7001)
    assert canceled_post.status == OrderStatus.CANCELED
    assert post.status == PostStatus.OPEN

    class FakeDBGoods:
        def __init__(self):
            self.calls = 0

        async def execute(self, stmt):
            self.calls += 1
            if self.calls == 1:
                return FakeResult(items=[goods_order])
            return FakeResult(items=[goods])

        async def flush(self):
            pass

        async def refresh(self, o):
            pass

        async def commit(self):
            pass

    canceled_goods = await OrderService.cancel_order(FakeDBGoods(), order_id=goods_order.order_id, operator_id=7001)
    assert canceled_goods.status == OrderStatus.CANCELED
    assert goods.template_data == {}

    completed_order = build_order(status=OrderStatus.COMPLETED, buyer_id=7001, seller_id=8001)
    with pytest.raises(BusinessHTTPException) as completed_exc:
        await OrderService.cancel_order(FakeDBGoods(), order_id=completed_order.order_id, operator_id=7001)
    assert "该状态不允许取消订单" in completed_exc.value.detail["msg"]


@pytest.mark.asyncio
async def test_force_complete_and_accept_delivery_goods_branch(monkeypatch):
    goods_order = build_order(status=OrderStatus.CONFIRMED, buyer_id=7001, seller_id=8001, item_type=ItemType.GOODS, item_id=4001, meta_data={})
    goods = build_goods(template_data={"locked": True})

    class FakeDB:
        def __init__(self, order):
            self.order = order
            self.calls = 0

        async def execute(self, stmt):
            self.calls += 1
            if self.calls == 1:
                return FakeResult(items=[self.order])
            return FakeResult(items=[goods])

        async def flush(self):
            pass

        async def refresh(self, o):
            pass

        async def commit(self):
            pass

    monkeypatch.setattr(OrderService, "_load_goods_for_update", AsyncMock(return_value=goods))
    monkeypatch.setattr(OrderService, "_add_credit", AsyncMock(return_value=None))

    completed = await OrderService.accept_delivery(FakeDB(goods_order), goods_order.order_id, goods_order.buyer_id)
    assert completed.status == OrderStatus.COMPLETED
    assert goods.is_sold is True
    assert goods.template_data == {}

    pending_order = build_order(status=OrderStatus.PENDING)
    with pytest.raises(BusinessHTTPException) as force_exc:
        await OrderService.force_complete_order_by_admin(FakeDB(pending_order), pending_order.order_id, 1)
    assert "只有进行中或待验收订单可以手动完结" in force_exc.value.detail["msg"]


@pytest.mark.asyncio
async def test_list_orders_and_post_application_edge_cases(monkeypatch):
    buyer_order = build_order(order_id=6101, buyer_id=7001, seller_id=8001)
    seller_order = build_order(order_id=6102, buyer_id=9001, seller_id=7001)

    class FakeDBBuyer:
        async def execute(self, stmt):
            return FakeResult(scalar_value=1, items=[buyer_order])

    class FakeDBSeller:
        async def execute(self, stmt):
            return FakeResult(scalar_value=1, items=[seller_order])

    buyer_orders, buyer_total = await OrderService.list_orders(FakeDBBuyer(), 7001, role="buyer")
    seller_orders, seller_total = await OrderService.list_orders(FakeDBSeller(), 7001, role="seller")
    assert buyer_total == 1 and seller_total == 1
    assert buyer_orders[0].order_id == 6101
    assert seller_orders[0].order_id == 6102

    class FakeDBApplications:
        async def execute(self, stmt):
            order = build_order(status=OrderStatus.PENDING, meta_data="not-a-dict")
            applicant = SimpleNamespace(user_id=9001, avatar_attachment=SimpleNamespace(url="/a.png"))
            return FakeResult(rows=[(order, applicant, 0)])

    rows = await OrderService.list_post_applications(FakeDBApplications(), 2001)
    assert rows[0]["completed_order_count"] == 0
    assert rows[0]["note"] is None


@pytest.mark.asyncio
async def test_lookup_helpers_and_create_order_edge_paths(monkeypatch):
    with pytest.raises(ResourceHTTPException):
        await OrderService._get_post_for_update(build_db(execute_side_effect=[FakeResult(items=[])]), 1)

    with pytest.raises(ResourceHTTPException):
        await OrderService._get_order_for_update(build_db(execute_side_effect=[FakeResult(items=[])]), 1)

    with pytest.raises(ResourceHTTPException):
        await OrderService._get_user_for_update(build_db(execute_side_effect=[FakeResult(items=[])]), 1)

    with pytest.raises(BusinessHTTPException) as invalid_type_exc:
        await OrderService.create_order(build_db(), "UNKNOWN", 1, 1)
    assert "不支持的 item_type" in invalid_type_exc.value.detail["msg"]

    class BrokenDict(dict):
        def get(self, key, default=None):
            raise RuntimeError("boom")

    goods = build_goods(template_data=BrokenDict({"locked": False}))
    db = build_db(execute_side_effect=[FakeResult(items=[goods])])
    order = await OrderService.create_order(db, "GOODS", 4001, 6001, commit=False)
    assert order.status == OrderStatus.ONGOING
    assert db.commit.await_count == 0

    post = build_post(direction=Direction.SELL)
    db_post = build_db(execute_side_effect=[FakeResult(items=[None]), FakeResult(scalar_value=0)])
    order_post = await OrderService.create_order(db_post, "POST", 2001, 4001, trigger_type="APPLICATION", post=post, commit=False)
    assert order_post.trigger_type == OrderTriggerType.APPLICATION
    assert db_post.commit.await_count == 0


@pytest.mark.asyncio
async def test_submit_delivery_and_auto_confirm_single_order_paths(monkeypatch):
    order = build_order(status=OrderStatus.ONGOING, seller_id=8001, meta_data={})
    monkeypatch.setattr(OrderService, "_get_order_for_update", AsyncMock(return_value=order))

    submitted = await OrderService.submit_delivery(build_db(), 1, 8001)
    assert submitted.status == OrderStatus.CONFIRMED
    assert submitted.meta_data["delivery_submitted_time"]

    post_order = build_order(status=OrderStatus.CONFIRMED, item_type=ItemType.POST, item_id=2001, seller_id=8001, meta_data={})
    post = build_post()
    post_db = build_db(execute_side_effect=[FakeResult(items=[post])])
    monkeypatch.setattr(OrderService, "_get_order_for_update", AsyncMock(return_value=post_order))
    monkeypatch.setattr(OrderService, "_add_credit", AsyncMock(return_value=None))
    assert await OrderService.auto_confirm_overdue_order_by_id(post_db, post_order.order_id) is True
    assert post_order.status == OrderStatus.COMPLETED
    assert post.status == PostStatus.CLOSED

    goods_order = build_order(status=OrderStatus.CONFIRMED, item_type=ItemType.GOODS, item_id=4001, seller_id=8001, meta_data={})
    goods = build_goods(template_data={"locked": True})
    goods_db = build_db(execute_side_effect=[FakeResult(items=[goods])])
    monkeypatch.setattr(OrderService, "_get_order_for_update", AsyncMock(return_value=goods_order))
    assert await OrderService.auto_confirm_overdue_order_by_id(goods_db, goods_order.order_id) is True
    assert goods_order.status == OrderStatus.COMPLETED
    assert goods.is_sold is True
    assert goods.template_data == {}


@pytest.mark.asyncio
async def test_update_status_history_and_completion_hooks(monkeypatch):
    class BrokenHistory(list):
        def append(self, item):
            raise RuntimeError("broken history")

    class FakeDB:
        def __init__(self, order, extra_results=None):
            self.order = order
            self.extra_results = extra_results or []

        async def execute(self, stmt):
            if self.order is not None:
                order = self.order
                self.order = None
                return FakeResult(items=[order])
            if self.extra_results:
                return self.extra_results.pop(0)
            return FakeResult(items=[])

        async def flush(self):
            pass

        async def refresh(self, o):
            pass

        async def commit(self):
            pass

    order = build_order(status=OrderStatus.ONGOING, buyer_id=7001, seller_id=8001, item_type=ItemType.GOODS, item_id=4001, meta_data={"history": BrokenHistory()})
    goods = build_goods(template_data={"locked": True})
    monkeypatch.setattr(OrderService, "_load_goods_for_update", AsyncMock(return_value=goods))
    monkeypatch.setattr(OrderService, "_add_credit", AsyncMock(side_effect=RuntimeError("credit boom")))

    updated = await OrderService.update_status(FakeDB(order, extra_results=[FakeResult(items=[goods])]), order.order_id, "CANCELED", operator_id=7001)
    assert updated.status == OrderStatus.CANCELED
    assert goods.template_data == {}

    completed_order = build_order(status=OrderStatus.CONFIRMED, buyer_id=7001, seller_id=8001, item_type=ItemType.GOODS, item_id=4001, meta_data={"history": []})
    goods_two = build_goods(template_data={"locked": True})
    monkeypatch.setattr(OrderService, "_load_goods_for_update", AsyncMock(return_value=goods_two))
    monkeypatch.setattr(OrderService, "_add_credit", AsyncMock(return_value=None))
    completed = await OrderService.update_status(FakeDB(completed_order, extra_results=[FakeResult(items=[goods_two])]), completed_order.order_id, "COMPLETED", operator_id=7001)
    assert completed.status == OrderStatus.COMPLETED
    assert goods_two.is_sold is True
    assert completed.meta_data["history"]


@pytest.mark.asyncio
async def test_credit_and_wrapper_paths(monkeypatch):
    user = SimpleNamespace(user_id=1, credit_score=10)

    class FakeDB:
        def __init__(self):
            self.added = []

        async def execute(self, stmt):
            return FakeResult(items=[user])

        def add(self, obj):
            self.added.append(obj)

        async def flush(self):
            pass

    await OrderService._add_credit(FakeDB(), 1, 5, "bonus")
    assert user.credit_score == 15

    wrapper_called = {"called": False}

    async def fake_accept_delivery(db, order_id, operator_id):
        wrapper_called["called"] = True
        return build_order(status=OrderStatus.COMPLETED)

    monkeypatch.setattr(OrderService, "accept_delivery", fake_accept_delivery)
    result = await OrderService.complete_order(build_db(), 1, 2)
    assert wrapper_called["called"] is True
    assert result.status == OrderStatus.COMPLETED


@pytest.mark.asyncio
async def test_approve_order_invalid_item_type_raises(monkeypatch):
    o = build_order(status=OrderStatus.PENDING, item_type=ItemType.GOODS)

    async def fake_get_order(db, oid):
        return o

    monkeypatch.setattr(OrderService, "_get_order_for_update", fake_get_order)

    with pytest.raises(BusinessHTTPException):
        await OrderService.approve_order(build_db(), order_id=6001, operator_id=1)


@pytest.mark.asyncio
async def test_approve_order_unauthorized_raises(monkeypatch):
    o = build_order(status=OrderStatus.PENDING)
    p = build_post(publisher_id=9999)

    async def fake_get_order(db, oid):
        return o

    async def fake_get_post(db, pid):
        return p

    monkeypatch.setattr(OrderService, "_get_order_for_update", fake_get_order)
    monkeypatch.setattr(OrderService, "_get_post_for_update", fake_get_post)

    with pytest.raises(BusinessHTTPException):
        await OrderService.approve_order(build_db(), order_id=6001, operator_id=1)


@pytest.mark.asyncio
async def test_approve_order_collective_full_promotes_pending(monkeypatch):
    order = build_order(status=OrderStatus.PENDING, trigger_type=OrderTriggerType.COLLECTIVE)
    post = build_post(max_accepters=1)

    async def fake_get_order(db, oid):
        return order

    async def fake_get_post(db, pid):
        return post

    pending1 = build_order(order_id=9001, status=OrderStatus.PENDING)
    pending2 = build_order(order_id=9002, status=OrderStatus.PENDING)

    class FakeDBX:
        async def execute(self, stmt):
            # first call to get_current_accepters_count will be monkeypatched
            return FakeResult(rows=[pending1, pending2])

        async def flush(self):
            pass

        async def refresh(self, o):
            pass

        async def commit(self):
            pass

    monkeypatch.setattr(OrderService, "_get_order_for_update", fake_get_order)
    monkeypatch.setattr(OrderService, "_get_post_for_update", fake_get_post)
    monkeypatch.setattr(OrderService, "get_current_accepters_count", AsyncMock(return_value=1))

    res = await OrderService.approve_order(FakeDBX(), order_id=6001, operator_id=post.publisher_id)
    assert res.status == OrderStatus.ONGOING
    assert post.status == PostStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_approve_order_status_not_pending_raises(monkeypatch):
    o = build_order(status=OrderStatus.ONGOING)

    async def fake_get_order(db, oid):
        return o

    monkeypatch.setattr(OrderService, "_get_order_for_update", fake_get_order)
    with pytest.raises(BusinessHTTPException):
        await OrderService.approve_order(build_db(), order_id=6001, operator_id=1)


@pytest.mark.asyncio
async def test_reject_order_unauthorized_raises(monkeypatch):
    o = build_order(status=OrderStatus.PENDING)
    p = build_post(publisher_id=1234)

    async def fake_get_order(db, oid):
        return o

    async def fake_get_post(db, pid):
        return p

    monkeypatch.setattr(OrderService, "_get_order_for_update", fake_get_order)
    monkeypatch.setattr(OrderService, "_get_post_for_update", fake_get_post)

    with pytest.raises(BusinessHTTPException):
        await OrderService.reject_order(build_db(), order_id=6001, operator_id=999)


@pytest.mark.asyncio
async def test_auto_confirm_overdue_orders_promotes_and_calls_add_credit(monkeypatch):
    # prepare two orders: one POST one GOODS
    o_post = build_order(status=OrderStatus.CONFIRMED, item_type=ItemType.POST, item_id=2001, seller_id=8001)
    o_goods = build_order(status=OrderStatus.CONFIRMED, item_type=ItemType.GOODS, item_id=4001, seller_id=8002)

    class FakeDBC:
        async def execute(self, stmt):
            return FakeResult(items=[o_post, o_goods])

        async def flush(self):
            pass

        async def commit(self):
            pass

    async def fake_get_post(db, pid):
        return build_post()

    async def fake_load_goods(db, gid):
        return build_goods(template_data={})

    calls = {"count": 0}

    async def fake_add_credit(db, uid, amount, reason):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("boom")

    monkeypatch.setattr(OrderService, "_get_post_for_update", fake_get_post)
    monkeypatch.setattr(OrderService, "_load_goods_for_update", fake_load_goods)
    monkeypatch.setattr(OrderService, "_add_credit", fake_add_credit)

    db_c = FakeDBC()
    promoted = await OrderService.auto_confirm_overdue_orders(db_c)
    assert promoted == 2


@pytest.mark.asyncio
async def test_update_status_pending_to_ongoing_unauthorized():
    # PENDING -> ONGOING requires operator == seller_id
    class O:
        def __init__(self):
            self.status = OrderStatus.PENDING
            self.order_id = 444
            self.seller_id = 11
            self.buyer_id = 22
            self.initiator_id = 22
            self.item_type = ItemType.POST
            self.meta_data = {}

    class FakeDB:
        async def execute(self, stmt):
            return FakeResult(items=[O()])

    with pytest.raises(BusinessHTTPException) as exc:
        await OrderService.update_status(FakeDB(), 444, "ONGOING", operator_id=99)
    assert "只有发布者可以确认申请" in exc.value.detail["msg"]


@pytest.mark.asyncio
async def test_update_status_cancel_from_ongoing_checks_roles():
    class O2:
        def __init__(self):
            self.status = OrderStatus.ONGOING
            self.order_id = 555
            self.seller_id = 10
            self.buyer_id = 20
            self.initiator_id = 20
            self.item_type = ItemType.POST
            self.meta_data = {}

    class FakeDB2:
        async def execute(self, stmt):
            return FakeResult(items=[O2()])

        async def flush(self):
            pass

        async def refresh(self, o):
            pass

        async def commit(self):
            pass

    # operator not in (buyer,seller)
    with pytest.raises(BusinessHTTPException):
        await OrderService.update_status(FakeDB2(), 555, "CANCELED", operator_id=999)

    # buyer can cancel
    res = await OrderService.update_status(FakeDB2(), 555, "CANCELED", operator_id=20)
    assert res.status == OrderStatus.CANCELED


@pytest.mark.asyncio
async def test_update_status_confirmed_to_completed_calls_hooks(monkeypatch):
    # CONFIRMED -> COMPLETED must be by buyer and triggers _add_credit and side effects
    class O3:
        def __init__(self):
            self.status = OrderStatus.CONFIRMED
            self.order_id = 777
            self.seller_id = 10
            self.buyer_id = 99
            self.initiator_id = 99
            self.item_type = ItemType.POST
            self.item_id = 2001
            self.meta_data = {}

    async def fake_add_credit(db, uid, amount, reason):
        called["credit"] = True

    async def fake_get_post(db, pid):
        called["post"] = True
        return build_post()

    called = {"credit": False, "post": False}

    class FakeDB3:
        async def execute(self, stmt):
            return FakeResult(items=[O3()])

        async def flush(self):
            pass

        async def refresh(self, o):
            pass

        async def commit(self):
            pass

    monkeypatch.setattr(OrderService, "_add_credit", fake_add_credit)
    monkeypatch.setattr(OrderService, "_get_post_for_update", fake_get_post)

    res = await OrderService.update_status(FakeDB3(), 777, "COMPLETED", operator_id=99)
    assert res.status == OrderStatus.COMPLETED
    assert called["credit"] is True
    assert called["post"] is True


@pytest.mark.asyncio
async def test_submit_delivery_redis_enqueue_failure_is_logged(monkeypatch):
    # ensure submit_delivery swallows enqueue errors but still returns
    order = build_order(status=OrderStatus.ONGOING, seller_id=8001, meta_data={})
    monkeypatch.setattr(OrderService, "_get_order_for_update", AsyncMock(return_value=order))
    async def fake_enqueue(redis, key, oid, score):
        raise RuntimeError("redis boom")

    monkeypatch.setattr("app.services.order_service.enqueue_delayed_task", AsyncMock(side_effect=fake_enqueue))

    class FakeRedis:
        pass

    res = await OrderService.submit_delivery(build_db(), 1, 8001, redis_client=FakeRedis())
    assert res.status == OrderStatus.CONFIRMED


@pytest.mark.asyncio
async def test_update_status_ongoing_to_confirmed_unauthorized():
    class O:
        def __init__(self):
            self.status = OrderStatus.ONGOING
            self.order_id = 4444
            self.seller_id = 11
            self.buyer_id = 22
            self.initiator_id = 22
            self.item_type = ItemType.POST
            self.meta_data = {}

    class FakeDB:
        async def execute(self, stmt):
            return FakeResult(items=[O()])

    with pytest.raises(BusinessHTTPException) as exc:
        await OrderService.update_status(FakeDB(), 4444, "CONFIRMED", operator_id=99)
    assert "只有卖家可以提交交付" in exc.value.detail["msg"]


@pytest.mark.asyncio
async def test_update_status_confirmed_to_completed_unauthorized():
    class O:
        def __init__(self):
            self.status = OrderStatus.CONFIRMED
            self.order_id = 5555
            self.seller_id = 11
            self.buyer_id = 22
            self.initiator_id = 22
            self.item_type = ItemType.POST
            self.meta_data = {}

    class FakeDB:
        async def execute(self, stmt):
            return FakeResult(items=[O()])

    with pytest.raises(BusinessHTTPException) as exc:
        await OrderService.update_status(FakeDB(), 5555, "COMPLETED", operator_id=99)
    assert "只有买家可以确认验收" in exc.value.detail["msg"]


@pytest.mark.asyncio
async def test_update_status_cancel_from_confirmed_raises():
    class O:
        def __init__(self):
            self.status = OrderStatus.CONFIRMED
            self.order_id = 6666
            self.seller_id = 11
            self.buyer_id = 22
            self.initiator_id = 22
            self.item_type = ItemType.POST
            self.meta_data = {}

    class FakeDB:
        async def execute(self, stmt):
            return FakeResult(items=[O()])

    with pytest.raises(BusinessHTTPException) as exc:
        await OrderService.update_status(FakeDB(), 6666, "CANCELED", operator_id=11)
    assert "非法的状态迁移" in exc.value.detail["msg"]


@pytest.mark.asyncio
async def test_update_status_ongoing_to_confirmed_success_appends_history(monkeypatch):
    class O:
        def __init__(self):
            self.status = OrderStatus.ONGOING
            self.order_id = 7777
            self.seller_id = 11
            self.buyer_id = 22
            self.initiator_id = 22
            self.item_type = ItemType.POST
            self.meta_data = {}

    class FakeDB:
        async def execute(self, stmt):
            return FakeResult(items=[O()])

        async def flush(self):
            pass

        async def refresh(self, o):
            pass

        async def commit(self):
            pass

    db = FakeDB()
    res = await OrderService.update_status(db, 7777, "CONFIRMED", operator_id=11)
    assert res.status == OrderStatus.CONFIRMED
    assert isinstance(res.meta_data.get("history"), list)


@pytest.mark.asyncio
async def test_reject_order_invalid_item_type_raises(monkeypatch):
    o = build_order(status=OrderStatus.PENDING, item_type=ItemType.GOODS)

    async def fake_get_order(db, oid):
        return o

    monkeypatch.setattr(OrderService, "_get_order_for_update", fake_get_order)

    with pytest.raises(BusinessHTTPException):
        await OrderService.reject_order(build_db(), order_id=6001, operator_id=1)


@pytest.mark.asyncio
async def test_cancel_order_unauthorized_raises(monkeypatch):
    o = build_order(status=OrderStatus.ONGOING, buyer_id=10, seller_id=20)

    async def fake_get_order(db, oid):
        return o

    monkeypatch.setattr(OrderService, "_get_order_for_update", fake_get_order)

    with pytest.raises(BusinessHTTPException):
        await OrderService.cancel_order(build_db(), order_id=6001, operator_id=999)
