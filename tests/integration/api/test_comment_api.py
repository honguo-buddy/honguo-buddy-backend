"""Comment API 集成测试套件。"""

import pytest
from httpx import AsyncClient

from app.models import Post, Category, PostStatus, Direction, UrgencyLevel, Comment, TargetType
from app.core import settings


@pytest.mark.asyncio
async def test_create_comment(
    client: AsyncClient,
    db_session,
    test_user,
    test_user_token,
    fake_redis,
):
    """测试创建根评论。"""
    # 1. 设置token
    await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
    
    # 2. 准备数据
    category = Category(category_id=201, name="测试分类", config_json={})
    db_session.add(category)
    await db_session.flush()
    
    post = Post(
        post_id=3001,
        publisher_id=test_user.user_id,
        category_id=category.category_id,
        title="测试帖子",
        description="这是一个测试帖子",
        price=100.0,
        template_data={"max_accepters": 1},
        direction=Direction.SELL,
        urgency=UrgencyLevel.NORMAL,
        status=PostStatus.OPEN,
    )
    db_session.add(post)
    await db_session.flush()
    
    # 3. 创建根评论
    resp = await client.post(
        "/comments",
        json={
            "target_type": "POST",
            "target_id": post.post_id,
            "parent_id": None,
            "content": "这是一条根评论"
        },
        headers={"Authorization": f"Bearer {test_user_token}"}
    )
    
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == settings.SUCCESS_CODE
    assert data["message"]["content"] == "这是一条根评论"
    assert data["message"]["parent_id"] is None


@pytest.mark.asyncio
async def test_create_reply(
    client: AsyncClient,
    db_session,
    test_user,
    test_user_token,
    fake_redis,
):
    """测试创建回复（子评论）。"""
    # 1. 设置token
    await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
    
    # 2. 准备数据
    category = Category(category_id=202, name="测试分类", config_json={})
    db_session.add(category)
    await db_session.flush()
    
    post = Post(
        post_id=3002,
        publisher_id=test_user.user_id,
        category_id=category.category_id,
        title="测试帖子",
        description="这是一个测试帖子",
        price=100.0,
        template_data={"max_accepters": 1},
        direction=Direction.SELL,
        urgency=UrgencyLevel.NORMAL,
        status=PostStatus.OPEN,
    )
    db_session.add(post)
    await db_session.flush()
    
    # 3. 创建根评论
    root_comment = Comment(
        user_id=test_user.user_id,
        target_type=TargetType.POST,
        target_id=post.post_id,
        parent_id=None,
        content="根评论内容",
    )
    db_session.add(root_comment)
    await db_session.flush()
    
    # 4. 创建回复
    resp = await client.post(
        "/comments",
        json={
            "target_type": "POST",
            "target_id": post.post_id,
            "parent_id": root_comment.comment_id,
            "content": "这是对根评论的回复"
        },
        headers={"Authorization": f"Bearer {test_user_token}"}
    )
    
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == settings.SUCCESS_CODE
    assert data["message"]["parent_id"] == root_comment.comment_id
    assert data["message"]["content"] == "这是对根评论的回复"


@pytest.mark.asyncio
async def test_get_root_comments(
    client: AsyncClient,
    db_session,
    test_user,
):
    """测试获取根评论列表（游标分页）。"""
    # 1. 准备数据
    category = Category(category_id=203, name="测试分类", config_json={})
    db_session.add(category)
    await db_session.flush()
    
    post = Post(
        post_id=3003,
        publisher_id=test_user.user_id,
        category_id=category.category_id,
        title="测试帖子",
        description="这是一个测试帖子",
        price=100.0,
        template_data={"max_accepters": 1},
        direction=Direction.SELL,
        urgency=UrgencyLevel.NORMAL,
        status=PostStatus.OPEN,
    )
    db_session.add(post)
    await db_session.flush()
    
    # 2. 创建多个根评论
    for i in range(5):
        comment = Comment(
            user_id=test_user.user_id,
            target_type=TargetType.POST,
            target_id=post.post_id,
            parent_id=None,
            content=f"根评论 {i+1}",
        )
        db_session.add(comment)
    await db_session.flush()
    
    # 3. 获取根评论列表
    resp = await client.get(
        f"/comments/POST/{post.post_id}",
        params={"size": 20}
    )
    
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == settings.SUCCESS_CODE
    assert len(data["message"]["items"]) == 5
    assert all(item.get("reply_count") is not None for item in data["message"]["items"])


@pytest.mark.asyncio
async def test_get_replies(
    client: AsyncClient,
    db_session,
    test_user,
):
    """测试获取单条评论的回复列表。"""
    # 1. 准备数据
    category = Category(category_id=204, name="测试分类", config_json={})
    db_session.add(category)
    await db_session.flush()
    
    post = Post(
        post_id=3004,
        publisher_id=test_user.user_id,
        category_id=category.category_id,
        title="测试帖子",
        description="这是一个测试帖子",
        price=100.0,
        template_data={"max_accepters": 1},
        direction=Direction.SELL,
        urgency=UrgencyLevel.NORMAL,
        status=PostStatus.OPEN,
    )
    db_session.add(post)
    await db_session.flush()
    
    # 2. 创建根评论
    root_comment = Comment(
        user_id=test_user.user_id,
        target_type=TargetType.POST,
        target_id=post.post_id,
        parent_id=None,
        content="根评论",
    )
    db_session.add(root_comment)
    await db_session.flush()
    
    # 3. 创建多个回复
    for i in range(3):
        reply = Comment(
            user_id=test_user.user_id,
            target_type=TargetType.POST,
            target_id=post.post_id,
            parent_id=root_comment.comment_id,
            content=f"回复 {i+1}",
        )
        db_session.add(reply)
    await db_session.flush()
    
    # 4. 获取回复列表
    resp = await client.get(
        f"/comments/{root_comment.comment_id}/replies",
        params={"size": 20}
    )
    
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == settings.SUCCESS_CODE, f"Expected success but got: {data}"
    assert len(data["message"]["items"]) == 3


@pytest.mark.asyncio
async def test_delete_comment_by_owner(
    client: AsyncClient,
    db_session,
    test_user,
    test_user_token,
    fake_redis,
):
    """测试评论所有者可以删除评论。"""
    # 1. 设置token
    await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
    
    # 2. 准备数据
    category = Category(category_id=205, name="测试分类", config_json={})
    db_session.add(category)
    await db_session.flush()
    
    post = Post(
        post_id=3005,
        publisher_id=test_user.user_id,
        category_id=category.category_id,
        title="测试帖子",
        description="这是一个测试帖子",
        price=100.0,
        template_data={"max_accepters": 1},
        direction=Direction.SELL,
        urgency=UrgencyLevel.NORMAL,
        status=PostStatus.OPEN,
    )
    db_session.add(post)
    await db_session.flush()
    
    # 3. 创建评论
    comment = Comment(
        user_id=test_user.user_id,
        target_type=TargetType.POST,
        target_id=post.post_id,
        parent_id=None,
        content="待删除评论",
    )
    db_session.add(comment)
    await db_session.flush()
    
    # 4. 删除评论
    resp = await client.delete(
        f"/comments/{comment.comment_id}",
        headers={"Authorization": f"Bearer {test_user_token}"}
    )
    
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == settings.SUCCESS_CODE
    
    # 5. 验证评论已被标记为删除
    await db_session.refresh(comment)
    assert comment.is_deleted == True
    assert comment.content == "该评论已由用户删除"


@pytest.mark.asyncio
async def test_delete_comment_forbidden_for_non_owner(
    client: AsyncClient,
    db_session,
    test_user,
    test_admin_user,
    test_admin_token,
    fake_redis,
):
    """测试非所有者和非管理员无法删除评论。"""
    # 1. 设置token
    await fake_redis.set(f"token:{test_admin_token}", str(test_admin_user.user_id))
    
    # 2. 准备数据
    category = Category(category_id=206, name="测试分类", config_json={})
    db_session.add(category)
    await db_session.flush()
    
    post = Post(
        post_id=3006,
        publisher_id=test_user.user_id,
        category_id=category.category_id,
        title="测试帖子",
        description="这是一个测试帖子",
        price=100.0,
        template_data={"max_accepters": 1},
        direction=Direction.SELL,
        urgency=UrgencyLevel.NORMAL,
        status=PostStatus.OPEN,
    )
    db_session.add(post)
    await db_session.flush()
    
    # 3. 由test_user创建评论
    comment = Comment(
        user_id=test_user.user_id,
        target_type=TargetType.POST,
        target_id=post.post_id,
        parent_id=None,
        content="测试评论",
    )
    db_session.add(comment)
    await db_session.flush()
    
    # 4. 管理员可以删除其他用户的评论
    resp = await client.delete(
        f"/comments/{comment.comment_id}",
        headers={"Authorization": f"Bearer {test_admin_token}"}
    )
    assert resp.status_code == 200
    assert resp.json()["code"] == settings.SUCCESS_CODE
