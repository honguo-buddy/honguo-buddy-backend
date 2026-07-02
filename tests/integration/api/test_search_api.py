"""全局搜索接口集成测试。"""

import pytest
from httpx import AsyncClient

from app.core import create_access_token, settings
from app.models import (
    Attachment,
    AttachmentTargetType,
    Category,
    Direction,
    Goods,
    GoodsCondition,
    GoodsMetrics,
    GoodsStatus,
    Post,
    PostMetrics,
    PostStatus,
    SexEnum,
    UrgencyLevel,
    User,
    UserBlacklist,
    UserType,
)
from tests.helpers import assert_api_success


pytestmark = pytest.mark.asyncio


async def _create_search_user(db_session, *, user_id: int, user_name: str) -> User:
    user = User(
        user_id=user_id,
        user_uuid=f"{user_id:016d}".encode(),
        user_name=user_name,
        email=f"{user_name}@example.com",
        phonenumber=f"139{user_id:08d}"[:11],
        sex=SexEnum.UNKNOWN,
        user_type=UserType.USER,
        credit_score=100,
        is_verified=True,
        is_active=True,
        is_admin=False,
        is_deleted=False,
        wechat_openid=f"openid-{user_id}",
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _bind_token(fake_redis, user: User) -> str:
    token = create_access_token(
        {
            "sub": str(user.user_id),
            "user_name": user.user_name,
            "user_type": user.user_type.value if getattr(user.user_type, "value", None) else str(user.user_type),
        }
    )
    await fake_redis.set(f"token:{token}", str(user.user_id))
    await fake_redis.set(f"user_token:{user.user_id}", token)
    return token


async def test_global_search_all_merges_posts_and_goods_with_json_value_match(
    client: AsyncClient,
    db_session,
    test_user,
    fake_redis,
):
    category = Category(category_id=9101, name="全局搜索分类", config_json={}, direction="SELL")
    db_session.add(category)
    await db_session.flush()

    buy_post = Post(
        post_id=91001,
        publisher_id=test_user.user_id,
        category_id=category.category_id,
        title="普通标题",
        description="普通描述",
        price=12.0,
        template_data={"pickup_address": "北门近邻宝"},
        direction=Direction.BUY,
        urgency=UrgencyLevel.NORMAL,
        status=PostStatus.OPEN,
    )
    goods = Goods(
        goods_id=91002,
        publisher_id=test_user.user_id,
        category_id=category.category_id,
        name="普通商品",
        description="普通商品描述",
        price=30.0,
        condition=GoodsCondition.BRAND_NEW,
        template_data={"brand": "北门近邻宝"},
        status=GoodsStatus.ON_SALE,
    )
    key_only_post = Post(
        post_id=91003,
        publisher_id=test_user.user_id,
        category_id=category.category_id,
        title="不应命中",
        description="不应命中",
        price=8.0,
        template_data={"北门近邻宝": "only-key"},
        direction=Direction.SELL,
        urgency=UrgencyLevel.NORMAL,
        status=PostStatus.OPEN,
    )
    db_session.add_all([buy_post, goods, key_only_post])
    await db_session.flush()

    resp = await client.get(
        "/search/global",
        params={"keyword": "北门近邻宝", "tab": "ALL", "page": 1, "page_size": 20},
    )

    assert resp.status_code == 200
    msg = assert_api_success(resp.json())
    result_pairs = {(item["item_type"], item["id"]) for item in msg["list"]}
    assert ("BUY_POST", buy_post.post_id) in result_pairs
    assert ("GOODS", goods.goods_id) in result_pairs
    assert ("SELL_POST", key_only_post.post_id) not in result_pairs
    assert msg["total"] == 2
    item_map = {(item["item_type"], item["id"]): item for item in msg["list"]}
    assert item_map[("BUY_POST", buy_post.post_id)]["template_data"] == {"pickup_address": "北门近邻宝"}
    assert item_map[("GOODS", goods.goods_id)]["template_data"] == {"brand": "北门近邻宝"}
    assert item_map[("BUY_POST", buy_post.post_id)]["hit_tips"] == "在【取件地址】中匹配到: 北门近邻宝"
    assert item_map[("GOODS", goods.goods_id)]["hit_tips"] == "在【品牌成色】中匹配到: 北门近邻宝"


async def test_global_search_returns_attachment_urls_and_attachments(
    client: AsyncClient,
    db_session,
    test_user,
):
    category = Category(category_id=91011, name="附件搜索分类", config_json={}, direction="SELL")
    db_session.add(category)
    await db_session.flush()

    post = Post(
        post_id=910111,
        publisher_id=test_user.user_id,
        category_id=category.category_id,
        title="附件搜索委托",
        description="附件搜索描述",
        price=15.0,
        direction=Direction.BUY,
        urgency=UrgencyLevel.NORMAL,
        status=PostStatus.OPEN,
    )
    goods = Goods(
        goods_id=910112,
        publisher_id=test_user.user_id,
        category_id=category.category_id,
        name="附件搜索商品",
        description="附件搜索商品描述",
        price=35.0,
        condition=GoodsCondition.BRAND_NEW,
        status=GoodsStatus.ON_SALE,
    )
    db_session.add_all([post, goods])
    await db_session.flush()

    post_attachment_1 = Attachment(
        target_type=AttachmentTargetType.POST,
        target_id=post.post_id,
        url="/static/post/search_post_cover.webp",
        creator_id=test_user.user_id,
        sort_order=0,
    )
    post_attachment_2 = Attachment(
        target_type=AttachmentTargetType.POST,
        target_id=post.post_id,
        url="/static/post/search_post_second.webp",
        creator_id=test_user.user_id,
        sort_order=1,
    )
    goods_attachment = Attachment(
        target_type=AttachmentTargetType.GOODS,
        target_id=goods.goods_id,
        url="/static/goods/search_goods_cover.webp",
        creator_id=test_user.user_id,
        sort_order=0,
    )
    db_session.add_all([post_attachment_1, post_attachment_2, goods_attachment])
    await db_session.flush()

    resp = await client.get(
        "/search/global",
        params={"keyword": "附件搜索", "tab": "ALL", "page": 1, "page_size": 20},
    )

    assert resp.status_code == 200
    msg = assert_api_success(resp.json())
    item_map = {(item["item_type"], item["id"]): item for item in msg["list"]}

    assert item_map[("BUY_POST", post.post_id)]["attachment_urls"] == [
        "/static/post/search_post_cover.webp",
        "/static/post/search_post_second.webp",
    ]
    assert item_map[("BUY_POST", post.post_id)]["attachments"] == [
        {"id": post_attachment_1.attachment_id, "url": "/static/post/search_post_cover.webp"},
        {"id": post_attachment_2.attachment_id, "url": "/static/post/search_post_second.webp"},
    ]
    assert item_map[("GOODS", goods.goods_id)]["attachment_urls"] == [
        "/static/goods/search_goods_cover.webp",
    ]
    assert item_map[("GOODS", goods.goods_id)]["attachments"] == [
        {"id": goods_attachment.attachment_id, "url": "/static/goods/search_goods_cover.webp"},
    ]


async def test_global_search_tab_and_sort_by_metrics(
    client: AsyncClient,
    db_session,
    test_user,
):
    category = Category(category_id=9102, name="排序搜索分类", config_json={}, direction="SELL")
    db_session.add(category)
    await db_session.flush()

    low_favorite = Post(
        post_id=91101,
        publisher_id=test_user.user_id,
        category_id=category.category_id,
        title="打印服务低收藏",
        description="提供打印",
        price=5.0,
        direction=Direction.SELL,
        urgency=UrgencyLevel.NORMAL,
        status=PostStatus.OPEN,
    )
    high_favorite = Post(
        post_id=91102,
        publisher_id=test_user.user_id,
        category_id=category.category_id,
        title="打印服务高收藏",
        description="提供打印",
        price=6.0,
        direction=Direction.SELL,
        urgency=UrgencyLevel.NORMAL,
        status=PostStatus.OPEN,
    )
    buy_post = Post(
        post_id=91103,
        publisher_id=test_user.user_id,
        category_id=category.category_id,
        title="打印委托",
        description="需要打印",
        price=7.0,
        direction=Direction.BUY,
        urgency=UrgencyLevel.NORMAL,
        status=PostStatus.OPEN,
    )
    db_session.add_all([low_favorite, high_favorite, buy_post])
    await db_session.flush()
    db_session.add_all(
        [
            PostMetrics(post_id=low_favorite.post_id, view_count=10, favorite_count=2, comment_count=1),
            PostMetrics(post_id=high_favorite.post_id, view_count=3, favorite_count=20, comment_count=0),
            PostMetrics(post_id=buy_post.post_id, view_count=30, favorite_count=30, comment_count=0),
        ]
    )
    await db_session.flush()

    resp = await client.get(
        "/search/global",
        params={"keyword": "打印", "tab": "SELL_POST", "sort_by": "FAVORITE", "page": 1, "page_size": 20},
    )

    assert resp.status_code == 200
    msg = assert_api_success(resp.json())
    assert [item["id"] for item in msg["list"]] == [high_favorite.post_id, low_favorite.post_id]
    assert {item["item_type"] for item in msg["list"]} == {"SELL_POST"}
    assert msg["list"][0]["template_data"] == {}
    assert msg["list"][1]["template_data"] == {}
    assert msg["list"][0]["hit_tips"] is None
    assert msg["list"][1]["hit_tips"] is None


async def test_global_search_filters_blacklist_when_authenticated(
    client: AsyncClient,
    db_session,
    test_user,
    fake_redis,
):
    viewer = await _create_search_user(db_session, user_id=9201, user_name="search_viewer")
    blocked_publisher = await _create_search_user(db_session, user_id=9202, user_name="blocked_publisher")
    visible_publisher = await _create_search_user(db_session, user_id=9203, user_name="visible_publisher")
    token = await _bind_token(fake_redis, viewer)

    category = Category(category_id=9103, name="黑名单搜索分类", config_json={}, direction="SELL")
    db_session.add(category)
    await db_session.flush()

    db_session.add(UserBlacklist(user_id=viewer.user_id, target_id=blocked_publisher.user_id))
    hidden_goods = Goods(
        goods_id=91201,
        publisher_id=blocked_publisher.user_id,
        category_id=category.category_id,
        name="滑板车",
        description="黑名单用户商品",
        price=100.0,
        condition=GoodsCondition.BRAND_NEW,
        status=GoodsStatus.ON_SALE,
    )
    visible_goods = Goods(
        goods_id=91202,
        publisher_id=visible_publisher.user_id,
        category_id=category.category_id,
        name="滑板车",
        description="正常用户商品",
        price=120.0,
        condition=GoodsCondition.BRAND_NEW,
        status=GoodsStatus.ON_SALE,
    )
    db_session.add_all([hidden_goods, visible_goods])
    await db_session.flush()

    resp = await client.get(
        "/search/global",
        headers={"Authorization": f"Bearer {token}"},
        params={"keyword": "滑板车", "tab": "GOODS", "page": 1, "page_size": 20},
    )

    assert resp.status_code == 200
    msg = assert_api_success(resp.json())
    ids = {item["id"] for item in msg["list"]}
    assert visible_goods.goods_id in ids
    assert hidden_goods.goods_id not in ids


async def test_global_search_time_range_filters_old_items(
    client: AsyncClient,
    db_session,
    test_user,
):
    from app.core import get_now_naive

    category = Category(category_id=9104, name="时间搜索分类", config_json={}, direction="SELL")
    db_session.add(category)
    await db_session.flush()

    recent_goods = Goods(
        goods_id=91301,
        publisher_id=test_user.user_id,
        category_id=category.category_id,
        name="时间过滤水杯",
        description="最近商品",
        price=18.0,
        condition=GoodsCondition.BRAND_NEW,
        status=GoodsStatus.ON_SALE,
        create_time=get_now_naive(),
    )
    old_goods = Goods(
        goods_id=91302,
        publisher_id=test_user.user_id,
        category_id=category.category_id,
        name="时间过滤水杯",
        description="旧商品",
        price=16.0,
        condition=GoodsCondition.BRAND_NEW,
        status=GoodsStatus.ON_SALE,
        create_time=get_now_naive().replace(year=2020),
    )
    db_session.add_all([recent_goods, old_goods])
    await db_session.flush()

    resp = await client.get(
        "/search/global",
        params={"keyword": "时间过滤水杯", "tab": "GOODS", "time_range": "7D", "page": 1, "page_size": 20},
    )

    assert resp.status_code == 200
    msg = assert_api_success(resp.json())
    ids = {item["id"] for item in msg["list"]}
    assert recent_goods.goods_id in ids
    assert old_goods.goods_id not in ids


async def test_global_search_empty_keyword_uses_lobby_fallback(
    client: AsyncClient,
    db_session,
    test_user,
):
    category = Category(category_id=9105, name="空搜索分类", config_json={}, direction="SELL")
    db_session.add(category)
    await db_session.flush()

    sell_post = Post(
        post_id=91401,
        publisher_id=test_user.user_id,
        category_id=category.category_id,
        title="空搜索服务",
        description="大厅兜底",
        price=9.0,
        direction=Direction.SELL,
        urgency=UrgencyLevel.NORMAL,
        status=PostStatus.OPEN,
    )
    buy_post = Post(
        post_id=91402,
        publisher_id=test_user.user_id,
        category_id=category.category_id,
        title="空搜索委托",
        description="大厅兜底",
        price=10.0,
        direction=Direction.BUY,
        urgency=UrgencyLevel.NORMAL,
        status=PostStatus.OPEN,
    )
    db_session.add_all([sell_post, buy_post])
    await db_session.flush()

    resp = await client.get(
        "/search/global",
        params={"keyword": "", "tab": "SELL_POST", "page": 1, "page_size": 20},
    )

    assert resp.status_code == 200
    msg = assert_api_success(resp.json())
    ids = {item["id"] for item in msg["list"]}
    assert sell_post.post_id in ids
    assert buy_post.post_id not in ids


async def test_global_search_blank_keyword_uses_lobby_fallback(
    client: AsyncClient,
    db_session,
    test_user,
):
    category = Category(category_id=9106, name="空格搜索分类", config_json={}, direction="SELL")
    db_session.add(category)
    await db_session.flush()

    goods = Goods(
        goods_id=91411,
        publisher_id=test_user.user_id,
        category_id=category.category_id,
        name="空格兜底商品",
        description="大厅兜底",
        price=20.0,
        condition=GoodsCondition.BRAND_NEW,
        status=GoodsStatus.ON_SALE,
    )
    db_session.add(goods)
    await db_session.flush()

    resp = await client.get(
        "/search/global",
        params={"keyword": "   ", "tab": "GOODS", "page": 1, "page_size": 20},
    )

    assert resp.status_code == 200
    msg = assert_api_success(resp.json())
    ids = {item["id"] for item in msg["list"]}
    assert goods.goods_id in ids


async def test_global_search_missing_keyword_uses_lobby_fallback(
    client: AsyncClient,
    db_session,
    test_user,
):
    category = Category(category_id=9109, name="未传搜索分类", config_json={}, direction="SELL")
    db_session.add(category)
    await db_session.flush()

    goods = Goods(
        goods_id=91412,
        publisher_id=test_user.user_id,
        category_id=category.category_id,
        name="未传关键词商品",
        description="大厅兜底",
        price=22.0,
        condition=GoodsCondition.BRAND_NEW,
        status=GoodsStatus.ON_SALE,
    )
    db_session.add(goods)
    await db_session.flush()

    resp = await client.get(
        "/search/global",
        params={"tab": "GOODS", "page": 1, "page_size": 20},
    )

    assert resp.status_code == 200
    msg = assert_api_success(resp.json())
    ids = {item["id"] for item in msg["list"]}
    assert goods.goods_id in ids


async def test_global_search_multi_tokens_require_all_tokens_across_text_scopes(
    client: AsyncClient,
    db_session,
    test_user,
):
    category = Category(category_id=9107, name="多词搜索分类", config_json={}, direction="SELL")
    db_session.add(category)
    await db_session.flush()

    matched_post = Post(
        post_id=91421,
        publisher_id=test_user.user_id,
        category_id=category.category_id,
        title="南门代取",
        description="需要轻拿轻放",
        price=8.0,
        template_data={"pickup_address": "近邻宝柜台"},
        direction=Direction.BUY,
        urgency=UrgencyLevel.NORMAL,
        status=PostStatus.OPEN,
    )
    missing_json_token = Post(
        post_id=91422,
        publisher_id=test_user.user_id,
        category_id=category.category_id,
        title="南门代取",
        description="只命中一个词根",
        price=8.0,
        template_data={"pickup_address": "快递站"},
        direction=Direction.BUY,
        urgency=UrgencyLevel.NORMAL,
        status=PostStatus.OPEN,
    )
    db_session.add_all([matched_post, missing_json_token])
    await db_session.flush()

    resp = await client.get(
        "/search/global",
        params={"keyword": "南门 近邻宝", "tab": "BUY_POST", "page": 1, "page_size": 20},
    )

    assert resp.status_code == 200
    msg = assert_api_success(resp.json())
    ids = {item["id"] for item in msg["list"]}
    assert matched_post.post_id in ids
    assert missing_json_token.post_id not in ids
    matched_item = next(item for item in msg["list"] if item["id"] == matched_post.post_id)
    assert matched_item["hit_tips"] is None


async def test_global_search_does_not_match_json_key_or_numeric_columns_only(
    client: AsyncClient,
    db_session,
    test_user,
):
    category = Category(category_id=9108, name="边界搜索分类", config_json={}, direction="SELL")
    db_session.add(category)
    await db_session.flush()

    key_only_post = Post(
        post_id=91431,
        publisher_id=test_user.user_id,
        category_id=category.category_id,
        title="边界标题",
        description="边界描述",
        price=1.0,
        template_data={"唯一键名边界词": "plain-value"},
        direction=Direction.SELL,
        urgency=UrgencyLevel.NORMAL,
        status=PostStatus.OPEN,
    )
    numeric_only_goods = Goods(
        goods_id=91499,
        publisher_id=test_user.user_id,
        category_id=category.category_id,
        name="数值边界商品",
        description="没有目标数字",
        price=91499.0,
        condition=GoodsCondition.BRAND_NEW,
        template_data={"brand": "普通品牌"},
        status=GoodsStatus.ON_SALE,
    )
    db_session.add_all([key_only_post, numeric_only_goods])
    await db_session.flush()

    key_resp = await client.get(
        "/search/global",
        params={"keyword": "唯一键名边界词", "tab": "SELL_POST", "page": 1, "page_size": 20},
    )
    num_resp = await client.get(
        "/search/global",
        params={"keyword": "91499", "tab": "GOODS", "page": 1, "page_size": 20},
    )

    assert key_resp.status_code == 200
    key_msg = assert_api_success(key_resp.json())
    assert key_only_post.post_id not in {item["id"] for item in key_msg["list"]}

    assert num_resp.status_code == 200
    num_msg = assert_api_success(num_resp.json())
    assert numeric_only_goods.goods_id not in {item["id"] for item in num_msg["list"]}
