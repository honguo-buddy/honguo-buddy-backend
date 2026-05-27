"""Post API 集成测试套件（使用真实 MySQL 通过 Testcontainers）。"""

from uuid import uuid4

import pytest
from httpx import AsyncClient

from app.core import create_access_token, settings
from app.models import Attachment, AttachmentTargetType, Category, Direction, ItemType, Order, OrderStatus, OrderTriggerType, Post, PostStatus, SexEnum, UrgencyLevel, User, UserType
from tests.helpers import assert_api_error, assert_api_success


async def _create_user_with_avatar(
	db_session,
	*,
	user_id: int,
	user_name: str,
	openid: str,
	avatar_url: str,
	is_verified: bool = True,
):
	user = User(
		user_id=user_id,
		user_uuid=uuid4().bytes,
		user_name=user_name,
		email=f"{user_name}@example.com",
		phonenumber=f"138{user_id:08d}"[:11],
		sex=SexEnum.UNKNOWN,
		user_type=UserType.USER,
		credit_score=100,
		is_verified=is_verified,
		is_active=True,
		is_admin=False,
		is_deleted=False,
		wechat_openid=openid,
	)
	db_session.add(user)
	await db_session.flush()

	attachment = Attachment(
		target_type=AttachmentTargetType.USER,
		target_id=user.user_id,
		url=avatar_url,
		creator_id=user.user_id,
	)
	db_session.add(attachment)
	await db_session.flush()

	user.avatar_id = attachment.attachment_id
	await db_session.flush()
	return user


async def _bind_user_token(fake_redis, user: User) -> str:
	token = create_access_token(
		{
			"sub": str(user.user_id),
			"user_name": user.user_name,
			"user_type": user.user_type.value,
		}
	)
	await fake_redis.set(f"token:{token}", str(user.user_id))
	await fake_redis.set(f"user_token:{user.user_id}", token)
	return token


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
async def test_create_post_accepts_attachment_ids(
	client: AsyncClient,
	db_session,
	test_user,
	test_user_token,
	fake_redis,
):
	await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))

	category = Category(category_id=101, name="附件分类", config_json={})
	db_session.add(category)
	await db_session.flush()

	attachment = Attachment(
		target_type=AttachmentTargetType.USER,
		target_id=test_user.user_id,
		url="/static/user_attachment.png",
		creator_id=test_user.user_id,
	)
	db_session.add(attachment)
	await db_session.flush()

	resp = await client.post(
		"/posts/",
		headers={"Authorization": f"Bearer {test_user_token}"},
		json={
			"title": "带附件发布帖子",
			"description": "帖子包含附件绑定",
			"price": 8.8,
			"direction": "SELL",
			"urgency": "NORMAL",
			"max_accepters": 1,
			"category_id": category.category_id,
			"attachment_ids": [attachment.attachment_id],
		},
	)
	assert resp.status_code == 200
	body = resp.json()
	assert body["code"] == settings.SUCCESS_CODE
	message = body["message"]
	assert message["attachment_urls"] == ["/static/user_attachment.png"]

	await db_session.refresh(attachment)
	assert attachment.target_type == AttachmentTargetType.POST
	assert attachment.target_id == message["post_id"]


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


@pytest.mark.asyncio
async def test_batch_accept_posts_returns_partial_success_and_errors(
	client: AsyncClient,
	db_session,
	test_user,
	test_user_token,
	fake_redis,
):
	"""测试批量接单接口支持部分成功、部分失败。"""
	await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
	await fake_redis.set(f"user_token:{test_user.user_id}", test_user_token)

	category = Category(category_id=108, name="批量接单分类", config_json={})
	db_session.add(category)
	await db_session.flush()

	publisher = await _create_user_with_avatar(
		db_session,
		user_id=3008,
		user_name="publisher_batch",
		openid="openid-publisher-batch",
		avatar_url="/static/avatar/publisher_batch.png",
	)

	post_buy_1 = Post(
		post_id=3001,
		publisher_id=publisher.user_id,
		category_id=category.category_id,
		title="顺路任务1",
		description="BUY 方向批量接单1",
		price=10.0,
		template_data={"max_accepters": 2},
		direction=Direction.BUY,
		urgency=UrgencyLevel.NORMAL,
		status=PostStatus.OPEN,
	)
	post_buy_2 = Post(
		post_id=3002,
		publisher_id=publisher.user_id,
		category_id=category.category_id,
		title="顺路任务2",
		description="BUY 方向批量接单2",
		price=12.0,
		template_data={"max_accepters": 2},
		direction=Direction.BUY,
		urgency=UrgencyLevel.NORMAL,
		status=PostStatus.OPEN,
	)
	post_sell = Post(
		post_id=3003,
		publisher_id=publisher.user_id,
		category_id=category.category_id,
		title="SELL 方向任务",
		description="用于熔断方向校验",
		price=13.0,
		template_data={"max_accepters": 2},
		direction=Direction.SELL,
		urgency=UrgencyLevel.NORMAL,
		status=PostStatus.OPEN,
	)
	post_owned = Post(
		post_id=3004,
		publisher_id=test_user.user_id,
		category_id=category.category_id,
		title="自己的 BUY 帖子",
		description="用于校验 OWN_POST",
		price=14.0,
		template_data={"max_accepters": 2},
		direction=Direction.BUY,
		urgency=UrgencyLevel.NORMAL,
		status=PostStatus.OPEN,
	)
	db_session.add_all([post_buy_1, post_buy_2, post_sell, post_owned])
	await db_session.flush()

	resp = await client.post(
		"/posts/batch-accept",
		headers={"Authorization": f"Bearer {test_user_token}"},
		json={"post_ids": [post_buy_1.post_id, post_buy_1.post_id, post_sell.post_id, post_owned.post_id]},
	)

	assert resp.status_code == 200
	body = resp.json()
	assert body["code"] == settings.SUCCESS_CODE
	message = body["message"]
	assert len(message["results"]) == 1
	assert message["results"][0]["post_id"] == post_buy_1.post_id
	assert message["results"][0]["status"] == "PENDING"
	assert {item["error"] for item in message["errors"]} == {"ALREADY_ACCEPTED", "INVALID_DIRECTION", "OWN_POST"}


@pytest.mark.asyncio
async def test_batch_accept_posts_rejects_more_than_five(
	client: AsyncClient,
	db_session,
	test_user,
	test_user_token,
	fake_redis,
):
	"""测试批量接单的后端 5 单上限熔断。"""
	await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
	await fake_redis.set(f"user_token:{test_user.user_id}", test_user_token)

	category = Category(category_id=109, name="批量接单上限分类", config_json={})
	db_session.add(category)
	await db_session.flush()

	publisher = await _create_user_with_avatar(
		db_session,
		user_id=3009,
		user_name="publisher_limit",
		openid="openid-publisher-limit",
		avatar_url="/static/avatar/publisher_limit.png",
	)
	posts = []
	for index in range(6):
		post = Post(
			post_id=3100 + index,
			publisher_id=publisher.user_id,
			category_id=category.category_id,
			title=f"顺路任务{index}",
			description="超出 5 单限制",
			price=10.0 + index,
			template_data={"max_accepters": 2},
			direction=Direction.BUY,
			urgency=UrgencyLevel.NORMAL,
			status=PostStatus.OPEN,
		)
		posts.append(post)
	db_session.add_all(posts)
	await db_session.flush()

	resp = await client.post(
		"/posts/batch-accept",
		headers={"Authorization": f"Bearer {test_user_token}"},
		json={"post_ids": [post.post_id for post in posts]},
	)

	assert resp.status_code == 200
	message = assert_api_error(resp.json(), code=settings.REQ_ERROR_CODE)
	assert "最多一次只能接 5 单" in message["msg"]


@pytest.mark.asyncio
async def test_post_applications_returns_owner_view_with_completed_count(
	client: AsyncClient,
	db_session,
	test_user,
	test_user_token,
	fake_redis,
):
	"""测试帖子申请列表返回申请人、历史完成数与申请时间。"""
	await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
	await fake_redis.set(f"user_token:{test_user.user_id}", test_user_token)

	category = Category(category_id=110, name="申请列表分类", config_json={})
	db_session.add(category)
	await db_session.flush()

	applicant = await _create_user_with_avatar(
		db_session,
		user_id=3010,
		user_name="申请人",
		openid="openid-applicant-apply",
		avatar_url="/static/avatar/applicant.png",
	)
	applicant_token = await _bind_user_token(fake_redis, applicant)

	post = Post(
		post_id=3200,
		publisher_id=test_user.user_id,
		category_id=category.category_id,
		title="待查看申请的 BUY 帖子",
		description="用于申请列表测试",
		price=15.0,
		template_data={"max_accepters": 2},
		direction=Direction.BUY,
		urgency=UrgencyLevel.NORMAL,
		status=PostStatus.OPEN,
	)
	db_session.add(post)
	await db_session.flush()

	completed_order_1 = Order(
		buyer_id=applicant.user_id,
		seller_id=test_user.user_id,
		initiator_id=applicant.user_id,
		item_type=ItemType.POST,
		item_id=8001,
		status=OrderStatus.COMPLETED,
		trigger_type=OrderTriggerType.APPLICATION,
	)
	completed_order_2 = Order(
		buyer_id=test_user.user_id,
		seller_id=applicant.user_id,
		initiator_id=applicant.user_id,
		item_type=ItemType.POST,
		item_id=8002,
		status=OrderStatus.COMPLETED,
		trigger_type=OrderTriggerType.COLLECTIVE,
	)
	db_session.add_all([completed_order_1, completed_order_2])
	await db_session.flush()

	apply_resp = await client.post(
		f"/posts/{post.post_id}/accept",
		headers={"Authorization": f"Bearer {applicant_token}"},
	)
	assert apply_resp.status_code == 200
	apply_body = apply_resp.json()
	assert apply_body["code"] == settings.SUCCESS_CODE

	resp = await client.get(
		f"/posts/{post.post_id}/applications",
		headers={"Authorization": f"Bearer {test_user_token}"},
	)

	assert resp.status_code == 200
	body = resp.json()
	assert body["code"] == settings.SUCCESS_CODE
	applications = body["message"]["applications"]
	assert len(applications) == 1
	application = applications[0]
	assert application["post_id"] == post.post_id
	assert application["status"] == "PENDING"
	assert application["applicant"]["user_id"] == applicant.user_id
	assert application["applicant"]["avatar"] == "/static/avatar/applicant.png"
	assert application["applicant"]["completed_order_count"] == 2
	assert application["note"] is None
	assert application["created_at"]


@pytest.mark.asyncio
async def test_post_applications_rejects_non_owner(
	client: AsyncClient,
	db_session,
	test_user,
	fake_redis,
):
	"""测试申请列表仅帖子拥有者可查看。"""
	category = Category(category_id=111, name="申请列表越权分类", config_json={})
	db_session.add(category)
	await db_session.flush()

	applicant = await _create_user_with_avatar(
		db_session,
		user_id=3011,
		user_name="越权申请人",
		openid="openid-applicant-forbidden",
		avatar_url="/static/avatar/applicant_forbidden.png",
	)
	applicant_token = await _bind_user_token(fake_redis, applicant)

	post = Post(
		post_id=3300,
		publisher_id=test_user.user_id,
		category_id=category.category_id,
		title="越权查看申请列表的 BUY 帖子",
		description="用于越权测试",
		price=18.0,
		template_data={"max_accepters": 2},
		direction=Direction.BUY,
		urgency=UrgencyLevel.NORMAL,
		status=PostStatus.OPEN,
	)
	db_session.add(post)
	await db_session.flush()

	resp = await client.get(
		f"/posts/{post.post_id}/applications",
		headers={"Authorization": f"Bearer {applicant_token}"},
	)

	assert resp.status_code == 200
	message = assert_api_error(resp.json(), code=settings.INSUFFICIENT_AUTHORITY_CODE)
	assert "仅帖子拥有者可查看申请列表" in message["msg"]

