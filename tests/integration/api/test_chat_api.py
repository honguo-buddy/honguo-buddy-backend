"""聊天接口集成测试。"""

import pytest
from httpx import AsyncClient

from app.core import settings
from app.models import Attachment, AttachmentTargetType, Category, ChatMessage, ChatSession, Direction, Post, PostStatus, UrgencyLevel
from tests.helpers import assert_api_error


@pytest.mark.asyncio
async def test_chat_end_to_end_flow(
    client: AsyncClient,
    db_session,
    test_user,
    test_admin_user,
    test_user_token,
    test_admin_token,
    fake_redis,
):
    await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
    await fake_redis.set(f"token:{test_admin_token}", str(test_admin_user.user_id))

    category = Category(category_id=9201, name="聊天上下文分类", config_json={})
    db_session.add(category)
    await db_session.flush()

    post = Post(
        post_id=9202,
        publisher_id=test_user.user_id,
        category_id=category.category_id,
        title="聊天上下文帖子",
        description="用于聊天上下文",
        price=20.0,
        template_data={"max_accepters": 1},
        direction=Direction.SELL,
        urgency=UrgencyLevel.NORMAL,
        status=PostStatus.OPEN,
    )
    db_session.add(post)
    await db_session.flush()

    attachment = Attachment(
        attachment_id=9203,
        target_type=AttachmentTargetType.USER,
        target_id=None,
        url="/static/chat/chat-message.png",
        creator_id=test_user.user_id,
    )
    db_session.add(attachment)
    await db_session.flush()

    init_resp = await client.post(
        "/chats/sessions/init",
        headers={"Authorization": f"Bearer {test_user_token}"},
        json={"peer_id": test_admin_user.user_id, "context_type": "POST", "context_id": post.post_id},
    )
    assert init_resp.status_code == 200
    init_body = init_resp.json()
    assert init_body["code"] == settings.SUCCESS_CODE
    session_id = init_body["message"]["session_id"]
    assert init_body["message"]["peer_id"] == test_admin_user.user_id

    send_resp = await client.post(
        "/chats/messages",
        headers={"Authorization": f"Bearer {test_user_token}"},
        json={
            "session_id": session_id,
            "content": "第一条聊天消息",
            "attachment_ids": [attachment.attachment_id],
            "context_type": "POST",
            "context_id": post.post_id,
        },
    )
    assert send_resp.status_code == 200
    send_body = send_resp.json()
    assert send_body["code"] == settings.SUCCESS_CODE
    assert send_body["message"]["context_type"] == "POST"
    assert send_body["message"]["context_id"] == post.post_id
    assert send_body["message"]["attachment_urls"] == ["/static/chat/chat-message.png"]
    first_message_id = send_body["message"]["message_id"]

    await db_session.refresh(attachment)
    assert attachment.target_type == AttachmentTargetType.CHAT
    assert attachment.target_id == first_message_id

    quote_resp = await client.post(
        "/chats/messages",
        headers={"Authorization": f"Bearer {test_admin_token}"},
        json={
            "session_id": session_id,
            "content": "引用回复第一条消息",
            "quote_message_id": first_message_id,
        },
    )
    assert quote_resp.status_code == 200
    quote_body = quote_resp.json()
    assert quote_body["message"]["context_type"] == "POST"
    assert quote_body["message"]["context_id"] == post.post_id
    second_message_id = quote_body["message"]["message_id"]

    recalled_message = await db_session.get(ChatMessage, second_message_id)
    assert recalled_message is not None
    session = await db_session.get(ChatSession, session_id)
    assert session is not None
    session.last_message_time = recalled_message.create_time
    await db_session.flush()

    sessions_resp = await client.get(
        "/chats/sessions",
        headers={"Authorization": f"Bearer {test_user_token}"},
    )
    assert sessions_resp.status_code == 200
    sessions_body = sessions_resp.json()
    assert sessions_body["code"] == settings.SUCCESS_CODE
    assert sessions_body["message"]["items"][0]["unread_count"] == 1
    assert sessions_body["message"]["items"][0]["last_message_content"] == "引用回复第一条消息"

    messages_resp = await client.get(
        f"/chats/sessions/{session_id}/messages",
        headers={"Authorization": f"Bearer {test_user_token}"},
        params={"size": 20},
    )
    assert messages_resp.status_code == 200
    messages_body = messages_resp.json()
    assert messages_body["code"] == settings.SUCCESS_CODE
    assert len(messages_body["message"]["items"]) == 2
    assert messages_body["message"]["items"][0]["context_type"] == "POST"
    assert messages_body["message"]["items"][0]["context_id"] == post.post_id
    assert messages_body["message"]["items"][0]["attachment_urls"] == ["/static/chat/chat-message.png"]

    read_resp = await client.patch(
        f"/chats/sessions/{session_id}/read",
        headers={"Authorization": f"Bearer {test_user_token}"},
    )
    assert read_resp.status_code == 200
    read_body = read_resp.json()
    assert read_body["message"]["unread_count"] == 0

    recall_resp = await client.patch(
        f"/chats/messages/{second_message_id}/recall",
        headers={"Authorization": f"Bearer {test_admin_token}"},
    )
    assert recall_resp.status_code == 200
    recall_body = recall_resp.json()
    assert recall_body["message"]["is_recalled"] is True
    assert recall_body["message"]["content"] == "对方撤回了一条消息"

    refreshed_session = await db_session.get(ChatSession, session_id)
    assert refreshed_session is not None
    assert refreshed_session.last_message_content == "对方撤回了一条消息"

    local_delete_resp = await client.delete(
        f"/chats/messages/{first_message_id}/local",
        headers={"Authorization": f"Bearer {test_user_token}"},
    )
    assert local_delete_resp.status_code == 200
    delete_body = local_delete_resp.json()
    assert delete_body["message"]["is_deleted_by_sender"] is True

    hidden_messages_resp = await client.get(
        f"/chats/sessions/{session_id}/messages",
        headers={"Authorization": f"Bearer {test_user_token}"},
        params={"size": 20},
    )
    hidden_messages_body = hidden_messages_resp.json()
    assert len(hidden_messages_body["message"]["items"]) == 1
    assert hidden_messages_body["message"]["items"][0]["message_id"] == second_message_id


@pytest.mark.asyncio
async def test_chat_init_rejects_self_chat(client: AsyncClient, test_user, test_user_token, fake_redis):
    await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
    await fake_redis.set(f"user_token:{test_user.user_id}", test_user_token)

    response = await client.post(
        "/chats/sessions/init",
        headers={"Authorization": f"Bearer {test_user_token}"},
        json={"peer_id": test_user.user_id, "context_type": "POST", "context_id": 1},
    )

    assert response.status_code == 200
    message = assert_api_error(response.json(), code=settings.REQ_ERROR_CODE)
    assert "不能和自己创建会话" in message["msg"]