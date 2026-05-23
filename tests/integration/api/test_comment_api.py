"""Comment API 集成测试套件。"""

import pytest
from httpx import AsyncClient

from app.api import get_current_user
from app.models import Attachment, AttachmentTargetType, Post, Category, PostStatus, Direction, UrgencyLevel, Comment, TargetType
from app.core import settings
from tests.helpers import assert_api_error


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
async def test_create_comment_with_invalid_target_type_returns_error(
    client: AsyncClient,
    test_user,
    test_user_token,
    fake_redis,
):
    await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
    await fake_redis.set(f"user_token:{test_user.user_id}", test_user_token)

    resp = await client.post(
        "/comments",
        headers={"Authorization": f"Bearer {test_user_token}"},
        json={
            "target_type": "INVALID",
            "target_id": 1,
            "parent_id": None,
            "content": "无效目标类型",
        },
    )

    assert resp.status_code == 200
    message = assert_api_error(resp.json(), code=settings.REQ_ERROR_CODE)
    assert "无效的目标类型" in message["msg"]


@pytest.mark.asyncio
async def test_comment_attachment_binding_and_listing(
    client: AsyncClient,
    db_session,
    test_user,
    test_user_token,
    fake_redis,
):
    """测试评论附件延迟绑定与读取。"""
    await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))

    category = Category(category_id=9101, name="评论附件分类", config_json={})
    db_session.add(category)
    await db_session.flush()

    post = Post(
        post_id=9102,
        publisher_id=test_user.user_id,
        category_id=category.category_id,
        title="评论附件帖子",
        description="用于评论附件测试",
        price=10.0,
        template_data={"max_accepters": 1},
        direction=Direction.SELL,
        urgency=UrgencyLevel.NORMAL,
        status=PostStatus.OPEN,
    )
    db_session.add(post)
    await db_session.flush()

    attachment = Attachment(
        attachment_id=9103,
        target_type=AttachmentTargetType.USER,
        target_id=None,
        url="/static/comment/comment-attachment.png",
        creator_id=test_user.user_id,
    )
    db_session.add(attachment)
    await db_session.flush()

    create_resp = await client.post(
        "/comments",
        headers={"Authorization": f"Bearer {test_user_token}"},
        json={
            "target_type": "POST",
            "target_id": post.post_id,
            "parent_id": None,
            "content": "带附件的评论",
            "attachment_ids": [attachment.attachment_id],
        },
    )
    assert create_resp.status_code == 200
    create_body = create_resp.json()
    assert create_body["code"] == settings.SUCCESS_CODE
    assert create_body["message"]["attachment_urls"] == ["/static/comment/comment-attachment.png"]
    comment_id = create_body["message"]["comment_id"]

    await db_session.refresh(attachment)
    assert attachment.target_type == AttachmentTargetType.COMMENT
    assert attachment.target_id == comment_id

    root_resp = await client.get(f"/comments/POST/{post.post_id}")
    assert root_resp.status_code == 200
    root_body = root_resp.json()
    assert root_body["code"] == settings.SUCCESS_CODE
    assert root_body["message"]["items"][0]["attachment_urls"] == ["/static/comment/comment-attachment.png"]

    reply_attachment = Attachment(
        attachment_id=9104,
        target_type=AttachmentTargetType.USER,
        target_id=None,
        url="/static/comment/reply-attachment.png",
        creator_id=test_user.user_id,
    )
    db_session.add(reply_attachment)
    await db_session.flush()

    reply_resp = await client.post(
        "/comments",
        headers={"Authorization": f"Bearer {test_user_token}"},
        json={
            "target_type": "POST",
            "target_id": post.post_id,
            "parent_id": comment_id,
            "content": "回复附件评论",
            "attachment_ids": [reply_attachment.attachment_id],
        },
    )
    assert reply_resp.status_code == 200
    reply_body = reply_resp.json()
    assert reply_body["message"]["attachment_urls"] == ["/static/comment/reply-attachment.png"]

    replies_resp = await client.get(f"/comments/{comment_id}/replies")
    assert replies_resp.status_code == 200
    replies_body = replies_resp.json()
    assert replies_body["code"] == settings.SUCCESS_CODE
    assert replies_body["message"]["items"][0]["attachment_urls"] == ["/static/comment/reply-attachment.png"]


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


@pytest.mark.asyncio
async def test_delete_comment_rejects_unrelated_user(
    client: AsyncClient,
    db_session,
    test_user,
    fake_redis,
):
    from app.core import create_access_token
    from app.models import SexEnum, User, UserType

    other_user = User(
        user_id=3901,
        user_uuid=b"eeeeeeeeeeeeeeee",
        user_name="other-user",
        email="other@example.com",
        phonenumber="13800000999",
        sex=SexEnum.UNKNOWN,
        user_type=UserType.USER,
        is_verified=True,
        is_active=True,
        is_admin=False,
        is_deleted=False,
        credit_score=100,
        wechat_openid="other-openid",
    )
    db_session.add(other_user)
    await db_session.flush()

    token = create_access_token({"sub": str(other_user.user_id), "user_name": other_user.user_name, "user_type": other_user.user_type.value})
    await fake_redis.set(f"token:{token}", str(other_user.user_id))
    await fake_redis.set(f"user_token:{other_user.user_id}", token)

    category = Category(category_id=2061, name="测试分类", config_json={})
    db_session.add(category)
    await db_session.flush()
    post = Post(
        post_id=3061,
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
    comment = Comment(
        user_id=test_user.user_id,
        target_type=TargetType.POST,
        target_id=post.post_id,
        parent_id=None,
        content="测试评论",
    )
    db_session.add(comment)
    await db_session.flush()

    resp = await client.delete(f"/comments/{comment.comment_id}", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    message = assert_api_error(resp.json(), code=settings.INSUFFICIENT_AUTHORITY_CODE)
    assert "无权删除他人评论" in message["msg"]
