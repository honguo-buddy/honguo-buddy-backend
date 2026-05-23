"""Post API 集成测试套件（使用真实 MySQL 通过 Testcontainers）。"""

import pytest
from httpx import AsyncClient

from app.models import Post, Category, Direction, UrgencyLevel, PostStatus, User, SexEnum, UserType
from app.core import settings
from tests.helpers import assert_api_error


@pytest.mark.asyncio
async def test_create_post_returns_full_post_info(
	client: AsyncClient,
	db_session,
	test_user,
	test_user_token,
	fake_redis,
):
	"""测试创建帖子接口返回完整帖子信息。"""
	await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))

	category = Category(category_id=100, name="创建分类", config_json={})
	db_session.add(category)
	await db_session.flush()

	resp = await client.post(
		"/posts/",
		headers={"Authorization": f"Bearer {test_user_token}"},
		json={
			"title": "创建后应返回完整信息",
			"description": "创建返回不应再只给摘要字段",
			"price": 12.5,
			"direction": "SELL",
			"urgency": "NORMAL",
			"max_accepters": 2,
			"category_id": category.category_id,
		},
	)
	assert resp.status_code == 200
	body = resp.json()
	assert body["code"] == settings.SUCCESS_CODE
	message = body["message"]
	assert message["category_id"] == category.category_id
	assert message["title"] == "创建后应返回完整信息"
	assert message["description"] == "创建返回不应再只给摘要字段"
	assert message["price"] == 12.5
	assert message["direction"] == "SELL"
	assert message["urgency"] == "NORMAL"
	assert message["status"] == "OPEN"
	assert message["max_accepters"] == 2
	assert message["current_accepters"] == 0
	assert message["attachment_urls"] == []


@pytest.mark.asyncio
async def test_create_post_rejects_negative_price(
	client: AsyncClient,
	db_session,
	test_user,
	test_user_token,
	fake_redis,
):
	await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
	await fake_redis.set(f"user_token:{test_user.user_id}", test_user_token)

	category = Category(category_id=111, name="价格校验分类", config_json={"fields": []})
	db_session.add(category)
	await db_session.flush()

	resp = await client.post(
		"/posts/",
		headers={"Authorization": f"Bearer {test_user_token}"},
		json={
			"title": "非法价格帖子",
			"description": "用于触发参数校验",
			"price": -1,
			"direction": "SELL",
			"urgency": "NORMAL",
			"max_accepters": 1,
			"category_id": category.category_id,
		},
	)

	assert resp.status_code == 200
	message = assert_api_error(resp.json(), code=settings.REQ_ERROR_CODE)
	assert "error" in message
	assert "msg" in message


@pytest.mark.asyncio
async def test_list_posts_has_direction_and_urgency(
	client: AsyncClient,
	db_session,
	test_user,
	fake_redis,
):
	"""测试列表端点返回 direction 和 urgency 字段（修复的字段）"""

	# 1. 准备分类数据
	category = Category(category_id=101, name="平面设计", config_json={})
	db_session.add(category)
	await db_session.flush()

	# 2. 创建帖子
	post = Post(
		post_id=2001,
		publisher_id=test_user.user_id,
		category_id=category.category_id,
		title="需要设计海报",
		description="帮我设计一张商业海报，要求高质量",
		price=500.0,
		template_data={"max_accepters": 3},
		direction=Direction.SELL,
		urgency=UrgencyLevel.NORMAL,
		status=PostStatus.OPEN,
	)
	db_session.add(post)
	await db_session.flush()

	# 3. 测试列表查询端点（GET /posts/）
	resp = await client.get(
		"/posts/",
		params={"keyword": "设计", "page": 1, "page_size": 20},
	)
	assert resp.status_code == 200
	data = resp.json()
	assert data["code"] == settings.SUCCESS_CODE, f"API 返回错误: {data}"
	plist = data["message"]["list"]
	assert len(plist) >= 1, "列表中应至少有一个帖子"
	first = plist[0]
	
	# ✅ 验证修复的字段存在
	assert "category_id" in first, "列表中缺少 category_id 字段"
	assert first["category_id"] == category.category_id
	assert "direction" in first, "列表中缺少 direction 字段"
	assert "urgency" in first, "列表中缺少 urgency 字段"
	assert first["direction"] == "SELL", "direction 字段值应为 SELL"
	assert first["urgency"] == "NORMAL", "urgency 字段值应为 NORMAL"


@pytest.mark.asyncio
async def test_get_post_detail_has_direction_and_urgency(
	client: AsyncClient,
	db_session,
	test_user,
	fake_redis,
):
	"""测试详情端点返回 direction 和 urgency 字段（修复的字段）"""

	# 1. 准备分类数据
	category = Category(category_id=102, name="视频编辑", config_json={})
	db_session.add(category)
	await db_session.flush()

	# 2. 创建帖子
	post = Post(
		post_id=2002,
		publisher_id=test_user.user_id,
		category_id=category.category_id,
		title="求助视频剪辑",
		description="需要有经验的视频编辑",
		price=1000.0,
		template_data={},
		direction=Direction.BUY,
		urgency=UrgencyLevel.URGENT,
		status=PostStatus.OPEN,
	)
	db_session.add(post)
	await db_session.flush()

	# 3. 测试详情获取端点（GET /posts/{id}）
	post_id = post.post_id
	resp = await client.get(f"/posts/{post_id}")
	assert resp.status_code == 200
	data = resp.json()
	assert data["code"] == settings.SUCCESS_CODE, f"API 返回错误: {data}"
	detail = data["message"]
	
	# ✅ 验证修复的字段存在
	assert detail["post_id"] == post_id
	assert detail["category_id"] == category.category_id
	assert "direction" in detail, "详情中缺少 direction 字段"
	assert "urgency" in detail, "详情中缺少 urgency 字段"
	assert detail["direction"] == "BUY", "direction 字段值应为 BUY"
	assert detail["urgency"] == "URGENT", "urgency 字段值应为 URGENT"


@pytest.mark.asyncio
async def test_list_posts_can_filter_by_category_id(
	client: AsyncClient,
	db_session,
	test_user,
	fake_redis,
):
	"""测试列表接口可按 category_id 过滤。"""
	category_a = Category(category_id=103, name="分类A", config_json={})
	category_b = Category(category_id=104, name="分类B", config_json={})
	db_session.add_all([category_a, category_b])
	await db_session.flush()

	post_a = Post(
		post_id=2003,
		publisher_id=test_user.user_id,
		category_id=category_a.category_id,
		title="分类A帖子",
		description="A 类帖子",
		price=20.0,
		template_data={},
		direction=Direction.SELL,
		urgency=UrgencyLevel.NORMAL,
		status=PostStatus.OPEN,
	)
	post_b = Post(
		post_id=2004,
		publisher_id=test_user.user_id,
		category_id=category_b.category_id,
		title="分类B帖子",
		description="B 类帖子",
		price=30.0,
		template_data={},
		direction=Direction.SELL,
		urgency=UrgencyLevel.NORMAL,
		status=PostStatus.OPEN,
	)
	db_session.add_all([post_a, post_b])
	await db_session.flush()

	resp = await client.get("/posts/", params={"category_id": category_b.category_id, "page": 1, "page_size": 20})
	assert resp.status_code == 200
	body = resp.json()
	assert body["code"] == settings.SUCCESS_CODE
	items = body["message"]["list"]
	assert len(items) == 1
	assert items[0]["post_id"] == post_b.post_id
	assert items[0]["category_id"] == category_b.category_id


@pytest.mark.asyncio
async def test_my_posts_and_public_user_posts_include_category_id(
	client: AsyncClient,
	db_session,
	test_user,
	test_user_token,
	fake_redis,
):
	"""测试 /posts/me 和 /posts/user/{user_id} 都返回 category_id。"""
	await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))

	category = Category(category_id=106, name="我的帖子分类", config_json={})
	db_session.add(category)
	await db_session.flush()

	post = Post(
		post_id=2006,
		publisher_id=test_user.user_id,
		category_id=category.category_id,
		title="我的发布",
		description="用于测试我的发布和主页帖子",
		price=19.0,
		template_data={"max_accepters": 1},
		direction=Direction.SELL,
		urgency=UrgencyLevel.NORMAL,
		status=PostStatus.OPEN,
	)
	db_session.add(post)
	await db_session.flush()

	my_resp = await client.get(
		"/posts/me",
		headers={"Authorization": f"Bearer {test_user_token}"},
		params={"category_id": category.category_id, "status": "OPEN", "page": 1, "size": 20},
	)
	assert my_resp.status_code == 200
	my_body = my_resp.json()
	assert my_body["code"] == settings.SUCCESS_CODE
	my_item = my_body["message"]["list"][0]
	assert my_item["post_id"] == post.post_id
	assert my_item["category_id"] == category.category_id

	public_resp = await client.get(
		f"/posts/user/{test_user.user_id}",
		params={"category_id": category.category_id, "status": "OPEN", "page": 1, "size": 20},
	)
	assert public_resp.status_code == 200
	public_body = public_resp.json()
	assert public_body["code"] == settings.SUCCESS_CODE
	public_item = public_body["message"]["list"][0]
	assert public_item["post_id"] == post.post_id
	assert public_item["category_id"] == category.category_id


@pytest.mark.asyncio
async def test_update_post_returns_full_post_info(
	client: AsyncClient,
	db_session,
	test_user,
	test_user_token,
	fake_redis,
):
	"""测试更新帖子接口返回完整帖子信息。"""
	await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))

	category = Category(category_id=105, name="更新分类", config_json={})
	db_session.add(category)
	await db_session.flush()

	post = Post(
		post_id=2005,
		publisher_id=test_user.user_id,
		category_id=category.category_id,
		title="更新前标题",
		description="更新前描述",
		price=18.0,
		template_data={"max_accepters": 1},
		direction=Direction.SELL,
		urgency=UrgencyLevel.NORMAL,
		status=PostStatus.OPEN,
	)
	db_session.add(post)
	await db_session.flush()

	resp = await client.patch(
		f"/posts/{post.post_id}",
		headers={"Authorization": f"Bearer {test_user_token}"},
		json={
			"title": "更新后标题",
			"description": "更新后描述",
			"price": 22.5,
			"max_accepters": 2,
		},
	)
	assert resp.status_code == 200
	body = resp.json()
	assert body["code"] == settings.SUCCESS_CODE
	message = body["message"]
	assert message["post_id"] == post.post_id
	assert message["category_id"] == category.category_id
	assert message["title"] == "更新后标题"
	assert message["description"] == "更新后描述"
	assert message["price"] == 22.5
	assert message["direction"] == "SELL"
	assert message["urgency"] == "NORMAL"
	assert message["status"] == "OPEN"
	assert message["max_accepters"] == 2
	assert message["current_accepters"] == 0
	assert message["attachment_urls"] == []


@pytest.mark.asyncio
async def test_update_post_rejects_non_owner(
	client: AsyncClient,
	db_session,
	test_user,
	fake_redis,
):
	outsider = User(
		user_id=2008,
		user_uuid=b"2222222222222222",
		user_name="outsider",
		email="outsider@example.com",
		phonenumber="13800008888",
		sex=SexEnum.UNKNOWN,
		user_type=UserType.USER,
		is_verified=True,
		is_active=True,
		is_admin=False,
		is_deleted=False,
		credit_score=100,
		wechat_openid="openid-outsider",
	)
	db_session.add(outsider)
	await db_session.flush()

	from app.core import create_access_token

	outsider_token = create_access_token({"sub": str(outsider.user_id), "user_name": outsider.user_name, "user_type": outsider.user_type.value})
	await fake_redis.set(f"token:{outsider_token}", str(outsider.user_id))
	await fake_redis.set(f"user_token:{outsider.user_id}", outsider_token)

	category = Category(category_id=107, name="他人帖子分类", config_json={"fields": []})
	db_session.add(category)
	await db_session.flush()

	post = Post(
		post_id=2007,
		publisher_id=test_user.user_id,
		category_id=category.category_id,
		title="他人帖子",
		description="非拥有者修改",
		price=18.0,
		template_data={"max_accepters": 1},
		direction=Direction.SELL,
		urgency=UrgencyLevel.NORMAL,
		status=PostStatus.OPEN,
	)
	db_session.add(post)
	await db_session.flush()

	resp = await client.patch(
		f"/posts/{post.post_id}",
		    headers={"Authorization": f"Bearer {outsider_token}"},
		json={"title": "篡改标题"},
	)

	assert resp.status_code == 200
	message = assert_api_error(resp.json(), code=settings.INSUFFICIENT_AUTHORITY_CODE)
	assert "只有帖子拥有者或管理员可以修改" in message["msg"]

