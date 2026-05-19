"""Post API 集成测试套件（使用真实 MySQL 通过 Testcontainers）。"""

import pytest
from httpx import AsyncClient

from app.models import Post, Category, Direction, UrgencyLevel, PostStatus
from app.core import settings


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
	assert "direction" in detail, "详情中缺少 direction 字段"
	assert "urgency" in detail, "详情中缺少 urgency 字段"
	assert detail["direction"] == "BUY", "direction 字段值应为 BUY"
	assert detail["urgency"] == "URGENT", "urgency 字段值应为 URGENT"

