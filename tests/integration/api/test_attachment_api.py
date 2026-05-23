"""附件相关的集成测试，包含上传与头像关联校验。"""

import pytest
from httpx import AsyncClient

from app.core import settings
from app.models import Attachment, AttachmentTargetType, User
from tests.helpers import assert_api_error

pytestmark = pytest.mark.asyncio


class TestAttachmentEndpoints:
    async def test_get_me_returns_avatar_url_from_attachment(
        self,
        client: AsyncClient,
        db_session,
        test_user: User,
        test_user_token: str,
        fake_redis,
    ):
        """创建附件并将其设置为用户头像后，GET /users/me 应返回附件 URL。"""
        attachment = Attachment(
            attachment_id=5001,
            target_type=AttachmentTargetType.USER,
            target_id=test_user.user_id,
            url="/static/avatar/test-avatar.png",
            creator_id=test_user.user_id,
        )
        db_session.add(attachment)
        await db_session.flush()
        test_user.avatar_id = attachment.attachment_id
        await db_session.flush()

        await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
        await fake_redis.set(f"user_token:{test_user.user_id}", test_user_token)

        response = await client.get("/users/me", headers={"Authorization": f"Bearer {test_user_token}"})

        assert response.status_code == 200
        body = response.json()
        assert body["code"] == settings.SUCCESS_CODE
        assert body["message"]["avatar"] == "/static/avatar/test-avatar.png"

    async def test_update_me_rejects_foreign_avatar_attachment(
        self,
        client: AsyncClient,
        db_session,
        test_user: User,
        test_admin_user: User,
        test_user_token: str,
        fake_redis,
    ):
        """尝试把别的用户上传的附件设为自己的头像应被拒绝（权限不足）。"""
        foreign_attachment = Attachment(
            attachment_id=5002,
            target_type=AttachmentTargetType.USER,
            target_id=test_admin_user.user_id,
            url="/static/avatar/foreign-avatar.png",
            creator_id=test_admin_user.user_id,
        )
        db_session.add(foreign_attachment)
        await db_session.flush()

        await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
        await fake_redis.set(f"user_token:{test_user.user_id}", test_user_token)

        response = await client.patch(
            "/users/me",
            headers={"Authorization": f"Bearer {test_user_token}"},
            json={"avatar_id": foreign_attachment.attachment_id},
        )

        assert response.status_code == 200
        body = response.json()
        assert_api_error(body, code=settings.INSUFFICIENT_AUTHORITY_CODE)

    async def test_upload_attachment_rejects_invalid_extension(
        self,
        client: AsyncClient,
        test_user: User,
        test_user_token: str,
        fake_redis,
    ):
        await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
        await fake_redis.set(f"user_token:{test_user.user_id}", test_user_token)

        response = await client.post(
            "/attachments/upload",
            headers={"Authorization": f"Bearer {test_user_token}"},
            files={"file": ("bad.txt", b"hello", "application/octet-stream")},
        )

        assert response.status_code == 200
        assert_api_error(response.json(), code=settings.REQ_ERROR_CODE)
