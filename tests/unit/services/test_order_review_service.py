from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.order_review_service import OrderReviewService
from app.core.exception_handler import BusinessHTTPException, ResourceHTTPException
from app.models import OrderStatus, ReviewType
from tests.unit.fake_sqlalchemy import FakeResult


def test_normalize_review_type():
    assert OrderReviewService._normalize_review_type("initial") == ReviewType.INITIAL
    with pytest.raises(BusinessHTTPException):
        OrderReviewService._normalize_review_type("badtype")


@pytest.mark.asyncio
async def test_release_reviews_if_ready_by_count():
    order = SimpleNamespace(order_id=1, status=OrderStatus.COMPLETED, update_time=None, create_time=None)

    # execute called twice: first for count, second for fetching reviews
    class FakeDB:
        def __init__(self):
            self.calls = 0

        async def execute(self, stmt):
            self.calls += 1
            if self.calls == 1:
                return FakeResult(scalar_value=2)
            return FakeResult(items=[SimpleNamespace(is_visible=False), SimpleNamespace(is_visible=False)])

    db = FakeDB()
    released = await OrderReviewService._release_reviews_if_ready(db, order)
    assert released is True


@pytest.mark.asyncio
async def test_release_reviews_if_ready_by_time(monkeypatch):
    from datetime import timedelta
    from app import core

    now = core.get_now_naive()
    old_time = now - timedelta(days=10)
    order = SimpleNamespace(order_id=2, status=OrderStatus.COMPLETED, update_time=old_time, create_time=old_time)

    class FakeDB2:
        def __init__(self):
            self.calls = 0

        async def execute(self, stmt):
            self.calls += 1
            if self.calls == 1:
                return FakeResult(scalar_value=0)
            return FakeResult(items=[SimpleNamespace(is_visible=False)])

    # force get_now_naive to return now far after update_time
    monkeypatch.setattr("app.services.order_review_service.get_now_naive", lambda: now)
    db2 = FakeDB2()
    released = await OrderReviewService._release_reviews_if_ready(db2, order)
    assert released is True


@pytest.mark.asyncio
async def test_create_review_order_not_completed_raises(monkeypatch):
    class FakeDB:
        async def execute(self, stmt):
            return FakeResult(items=[SimpleNamespace(status=OrderStatus.PENDING, buyer_id=1, seller_id=2)])

    with pytest.raises(BusinessHTTPException):
        await OrderReviewService.create_review(FakeDB(), current_user_id=1, order_id=1, reviewee_id=2, review_type="INITIAL", rating=5)


@pytest.mark.asyncio
async def test_auto_release_expired_double_blind_reviews_calls_release(monkeypatch):
    # prepare one completed order
    o = SimpleNamespace(order_id=11, status=OrderStatus.COMPLETED, update_time=None, create_time=None)

    class FakeDB3:
        async def execute(self, stmt):
            return FakeResult(items=[o])
        async def commit(self):
            pass

    called = {"count": 0}

    async def fake_release(db, order):
        called["count"] += 1
        return True

    monkeypatch.setattr(OrderReviewService, "_release_reviews_if_ready", fake_release)
    released = await OrderReviewService.auto_release_expired_double_blind_reviews(FakeDB3())
    assert released == 1


@pytest.mark.asyncio
async def test_create_review_success(monkeypatch):
    # simulate order is completed and review insert succeeds
    from datetime import datetime
    order = SimpleNamespace(order_id=10, status=OrderStatus.COMPLETED, buyer_id=1, seller_id=2, create_time=datetime.now(), update_time=None)

    class FakeDB:
        def __init__(self):
            self.calls = 0

        async def execute(self, stmt):
            self.calls += 1
            s = str(stmt).lower()
            if self.calls == 1:
                return FakeResult(items=[order])
            if "count(" in s or "count" in s:
                return FakeResult(scalar_value=0)
            return FakeResult(items=[])

        def add(self, obj):
            # mark added
            obj._added = True

        async def flush(self):
            return None

        async def refresh(self, obj):
            return None

        async def commit(self):
            return None

    # monkeypatch normalize to return enum INITIAL
    monkeypatch.setattr(OrderReviewService, "_normalize_review_type", lambda v: ReviewType.INITIAL)

    db = FakeDB()
    # should not raise
    async def _noexist(db_, order_id_, user_id_):
        return None
    monkeypatch.setattr(OrderReviewService, "_get_existing_initial_review", _noexist)
    await OrderReviewService.create_review(db, current_user_id=1, order_id=10, reviewee_id=2, review_type="INITIAL", rating=5)


@pytest.mark.asyncio
async def test_lookup_and_release_negative_paths(monkeypatch):
    class FakeDB:
        async def execute(self, stmt):
            return FakeResult(scalar_value=0)

    with pytest.raises(ResourceHTTPException):
        await OrderReviewService._get_order(FakeDB(), order_id=99)

    existing_db = FakeDB()
    existing_db.execute = AsyncMock(return_value=FakeResult(items=[SimpleNamespace(review_id=1)]))
    assert await OrderReviewService._get_existing_initial_review(existing_db, 99, 1) is not None

    empty_db = FakeDB()
    empty_db.execute = AsyncMock(return_value=FakeResult(items=[]))
    assert await OrderReviewService._get_existing_initial_review(empty_db, 99, 1) is None

    order = SimpleNamespace(order_id=99, status=OrderStatus.PENDING, update_time=None, create_time=None)
    released = await OrderReviewService._release_reviews_if_ready(FakeDB(), order)
    assert released is False


@pytest.mark.asyncio
async def test_create_review_validation_matrix(monkeypatch):
    order = SimpleNamespace(order_id=10, status=OrderStatus.COMPLETED, buyer_id=1, seller_id=2)

    async def fake_get_order(db, order_id):
        return order

    monkeypatch.setattr(OrderReviewService, "_get_order", fake_get_order)
    monkeypatch.setattr(OrderReviewService, "_get_existing_initial_review", AsyncMock(return_value=None))

    class FakeDB:
        def __init__(self, parent_result=None, count_result=0):
            self.calls = 0
            self.parent_result = parent_result
            self.count_result = count_result

        async def execute(self, stmt):
            self.calls += 1
            if self.parent_result is not None:
                if self.calls == 1:
                    return self.parent_result
                if self.calls == 2:
                    return FakeResult(scalar_value=self.count_result)
            else:
                if self.calls == 1:
                    return FakeResult(scalar_value=self.count_result)
            return FakeResult(items=[])

        def add(self, obj):
            pass

        async def flush(self):
            pass

        async def refresh(self, obj):
            pass

        async def commit(self):
            pass

    with pytest.raises(BusinessHTTPException) as auth_exc:
        await OrderReviewService.create_review(FakeDB(), current_user_id=3, order_id=10, reviewee_id=2, review_type="INITIAL", rating=5)
    assert "仅订单相关方可评价" in auth_exc.value.detail["msg"]

    with pytest.raises(BusinessHTTPException) as reviewee_exc:
        await OrderReviewService.create_review(FakeDB(), current_user_id=1, order_id=10, reviewee_id=999, review_type="INITIAL", rating=5)
    assert "被评价人必须是订单另一方" in reviewee_exc.value.detail["msg"]

    with pytest.raises(BusinessHTTPException) as rating_exc:
        await OrderReviewService.create_review(FakeDB(), current_user_id=1, order_id=10, reviewee_id=2, review_type="INITIAL")
    assert "首评必须填写评分" in rating_exc.value.detail["msg"]

    with pytest.raises(BusinessHTTPException) as parent_exc:
        await OrderReviewService.create_review(FakeDB(), current_user_id=1, order_id=10, reviewee_id=2, review_type="INITIAL", rating=5, parent_id=1)
    assert "首评不允许关联父评价" in parent_exc.value.detail["msg"]

    with pytest.raises(BusinessHTTPException) as additional_rating_exc:
        await OrderReviewService.create_review(FakeDB(), current_user_id=1, order_id=10, reviewee_id=2, review_type="ADDITIONAL", rating=5, parent_id=1)
    assert "追评/回评不允许填写评分" in additional_rating_exc.value.detail["msg"]

    with pytest.raises(BusinessHTTPException) as additional_parent_exc:
        await OrderReviewService.create_review(FakeDB(), current_user_id=1, order_id=10, reviewee_id=2, review_type="ADDITIONAL")
    assert "追评/回评必须关联父评价" in additional_parent_exc.value.detail["msg"]


@pytest.mark.asyncio
async def test_create_review_duplicate_parent_and_enqueue_paths(monkeypatch):
    from datetime import datetime

    order = SimpleNamespace(order_id=20, status=OrderStatus.COMPLETED, buyer_id=1, seller_id=2, update_time=None, create_time=datetime.now())
    parent_review = SimpleNamespace(review_id=200, order_id=20)
    initial_review = SimpleNamespace(review_id=201, order_id=20, is_visible=False)

    async def fake_get_order(db, order_id):
        return order

    monkeypatch.setattr(OrderReviewService, "_get_order", fake_get_order)

    class FakeDB:
        def __init__(self, parent_result=None, count_result=1, release_count_result=0):
            self.calls = 0
            self.parent_result = parent_result
            self.count_result = count_result
            self.release_count_result = release_count_result

        async def execute(self, stmt):
            self.calls += 1
            if self.parent_result is not None:
                if self.calls == 1:
                    return self.parent_result
                if self.calls == 2:
                    return FakeResult(scalar_value=self.count_result)
                if self.calls == 3:
                    return FakeResult(scalar_value=self.release_count_result)
            else:
                if self.calls == 1:
                    return FakeResult(scalar_value=self.count_result)
                if self.calls == 2:
                    return FakeResult(scalar_value=self.release_count_result)
            return FakeResult(items=[])

        def add(self, obj):
            self.added = obj

        async def flush(self):
            pass

        async def refresh(self, obj):
            pass

        async def commit(self):
            pass

    async def fake_existing(db, order_id, reviewer_id):
        return initial_review

    monkeypatch.setattr(OrderReviewService, "_get_existing_initial_review", fake_existing)
    with pytest.raises(BusinessHTTPException) as dup_exc:
        await OrderReviewService.create_review(FakeDB(), current_user_id=1, order_id=20, reviewee_id=2, review_type="INITIAL", rating=5)
    assert "只能发布一次首评" in dup_exc.value.detail["msg"]

    monkeypatch.setattr(OrderReviewService, "_get_existing_initial_review", AsyncMock(return_value=None))
    parent_db = FakeDB(parent_result=FakeResult(items=[]), count_result=0)
    with pytest.raises(ResourceHTTPException) as parent_missing_exc:
        await OrderReviewService.create_review(parent_db, current_user_id=1, order_id=20, reviewee_id=2, review_type="ADDITIONAL", parent_id=200)
    assert "父评价不存在" in parent_missing_exc.value.detail["msg"]

    parent_db = FakeDB(parent_result=FakeResult(items=[parent_review]), count_result=0)
    review = await OrderReviewService.create_review(parent_db, current_user_id=1, order_id=20, reviewee_id=2, review_type="ADDITIONAL", parent_id=200)
    assert review.review_type == ReviewType.ADDITIONAL

    enqueue_calls = {"count": 0}

    async def fake_enqueue(redis_client, queue_key, order_id, delayed_score):
        enqueue_calls["count"] += 1

    monkeypatch.setattr("app.services.order_review_service.enqueue_delayed_task", fake_enqueue)
    redis_db = FakeDB(count_result=1, release_count_result=0)
    await OrderReviewService.create_review(redis_db, current_user_id=1, order_id=20, reviewee_id=2, review_type="INITIAL", rating=5, redis_client=object())
    assert enqueue_calls["count"] == 1

    async def failing_enqueue(redis_client, queue_key, order_id, delayed_score):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.services.order_review_service.enqueue_delayed_task", failing_enqueue)
    await OrderReviewService.create_review(FakeDB(count_result=1, release_count_result=0), current_user_id=1, order_id=20, reviewee_id=2, review_type="INITIAL", rating=5, redis_client=object())


@pytest.mark.asyncio
async def test_release_and_listing_paths(monkeypatch):
    order = SimpleNamespace(order_id=30, status=OrderStatus.COMPLETED, buyer_id=1, seller_id=2, update_time=None, create_time=None)
    visible_review = SimpleNamespace(review_id=1, is_visible=True)
    hidden_review = SimpleNamespace(review_id=2, is_visible=False)

    class FakeDB:
        def __init__(self):
            self.calls = 0

        async def execute(self, stmt):
            self.calls += 1
            if self.calls == 1:
                return FakeResult(scalar_value=2)
            return FakeResult(items=[visible_review, hidden_review])

        async def commit(self):
            pass

    async def fake_get_order(db, order_id):
        return order

    monkeypatch.setattr(OrderReviewService, "_get_order", fake_get_order)
    released = await OrderReviewService.release_double_blind_reviews_for_order(FakeDB(), 30)
    assert released is True

    async def fake_release(db, ord_):
        return False

    monkeypatch.setattr(OrderReviewService, "_release_reviews_if_ready", fake_release)
    assert await OrderReviewService.release_double_blind_reviews_for_order(FakeDB(), 30) is False

    class ListDB:
        async def execute(self, stmt):
            return FakeResult(items=[visible_review, hidden_review])

    monkeypatch.setattr(OrderReviewService, "_get_order", fake_get_order)
    with pytest.raises(BusinessHTTPException) as list_exc:
        await OrderReviewService.list_reviews_for_order(ListDB(), order_id=30, current_user_id=999)
    assert "仅订单相关方可查看评价" in list_exc.value.detail["msg"]

    assert await OrderReviewService.list_reviews_for_order(ListDB(), order_id=30, current_user_id=1) == [visible_review, hidden_review]
    assert await OrderReviewService.list_reviews_for_order(ListDB(), order_id=30, is_admin=True) == [visible_review, hidden_review]

    class AutoDB:
        def __init__(self, orders):
            self.orders = orders

        async def execute(self, stmt):
            return FakeResult(items=self.orders)

        async def commit(self):
            self.committed = True

    assert await OrderReviewService.auto_release_expired_double_blind_reviews(AutoDB([])) == 0


@pytest.mark.asyncio
async def test_auto_release_expired_double_blind_reviews_no_release_branch(monkeypatch):
    order = SimpleNamespace(order_id=40, status=OrderStatus.COMPLETED, update_time=None, create_time=None)

    class FakeDB:
        async def execute(self, stmt):
            return FakeResult(items=[order])

        async def commit(self):
            self.committed = True

    monkeypatch.setattr(OrderReviewService, "_release_reviews_if_ready", AsyncMock(return_value=False))
    assert await OrderReviewService.auto_release_expired_double_blind_reviews(FakeDB()) == 0
