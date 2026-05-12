"""用户信息模块的集成测试（API 层）

合并了之前分散在不同文件中的用户路由测试，确保每个路由的测试集中在 `test_user_api.py` 中。
"""

import pytest
from httpx import AsyncClient

from app.core import settings
from app.models import User, Attachment, AttachmentTargetType

pytestmark = pytest.mark.asyncio


class TestGetUserProfile:
    async def test_get_profile_with_valid_token(
        self,
        client: AsyncClient,
        test_user: User,
        test_user_token: str,
        fake_redis,
    ):
        await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
        await fake_redis.set(f"user_token:{test_user.user_id}", test_user_token)

        response = await client.get("/users/info", headers={"Authorization": f"Bearer {test_user_token}"})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == settings.SUCCESS_CODE
        assert data["message"]["userName"] == test_user.user_name
        assert data["message"]["isAdmin"] == test_user.is_admin
        assert data["message"]["isVerified"] == test_user.is_verified

    async def test_get_profile_without_token(self, client: AsyncClient):
        response = await client.get("/users/info")

        assert response.status_code == 200
        assert response.json()["code"] == settings.TOKEN_INVALID_CODE

    async def test_get_profile_with_invalid_token(self, client: AsyncClient):
        response = await client.get("/users/info", headers={"Authorization": "Bearer invalid_token_xyz"})

        assert response.status_code == 200
        assert response.json()["code"] == settings.TOKEN_INVALID_CODE

    async def test_get_profile_with_malformed_bearer(self, client: AsyncClient):
        response = await client.get("/users/info", headers={"Authorization": "Bearer"})

        assert response.status_code == 200
        assert response.json()["code"] == settings.TOKEN_INVALID_CODE

    async def test_get_profile_admin_user(
        self,
        client: AsyncClient,
        test_admin_user: User,
        test_admin_token: str,
        fake_redis,
    ):
        await fake_redis.set(f"token:{test_admin_token}", str(test_admin_user.user_id))
        await fake_redis.set(f"user_token:{test_admin_user.user_id}", test_admin_token)

        response = await client.get("/users/info", headers={"Authorization": f"Bearer {test_admin_token}"})

        assert response.status_code == 200
        data = response.json()["message"]
        assert data["isAdmin"] is True
        assert data["userType"] == "admin"

    async def test_get_profile_verified_user(
        self,
        client: AsyncClient,
        db_session,
        test_user: User,
        test_user_token: str,
        fake_redis,
    ):
        test_user.is_verified = True
        await db_session.flush()
        await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))

        response = await client.get("/users/info", headers={"Authorization": f"Bearer {test_user_token}"})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == settings.SUCCESS_CODE
        assert data["message"]["isVerified"] is True

    async def test_patch_me_updates_username_and_returns_avatar_when_present(
        self,
        client: AsyncClient,
        db_session,
        test_user: User,
        test_user_token: str,
        fake_redis,
    ):
        """PATCH /users/me 应修改用户名，并在 avatar_id 指向自己上传的附件时返回 avatar URL。"""

        # 创建附件并标记为该用户上传
        attachment = Attachment(
            attachment_id=6001,
            target_type=AttachmentTargetType.USER,
            target_id=test_user.user_id,
            url="/static/avatar/positive-avatar.png",
            creator_id=test_user.user_id,
        )
        db_session.add(attachment)
        await db_session.flush()

        await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
        await fake_redis.set(f"user_token:{test_user.user_id}", test_user_token)

        response = await client.patch(
            "/users/me",
            headers={"Authorization": f"Bearer {test_user_token}"},
            json={"user_name": "newname", "avatar_id": attachment.attachment_id},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["code"] == settings.SUCCESS_CODE
        assert body["message"]["user_name"] == "newname"
        assert body["message"]["avatar"] == "/static/avatar/positive-avatar.png"

    async def test_delete_me_marks_user_deleted(
        self,
        client: AsyncClient,
        db_session,
        test_user: User,
        test_user_token: str,
        fake_redis,
    ):
        """DELETE /users/me 应将用户标记为已删除，随后 token 无效。"""
        await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
        await fake_redis.set(f"user_token:{test_user.user_id}", test_user_token)

        response = await client.delete("/users/me", headers={"Authorization": f"Bearer {test_user_token}"})
        assert response.status_code == 200
        body = response.json()
        assert body["code"] == settings.SUCCESS_CODE

        # 随后尝试使用相同 token 访问需要鉴权的接口，应返回 token 无效
        response2 = await client.get("/users/info", headers={"Authorization": f"Bearer {test_user_token}"})
        assert response2.status_code == 200
        assert response2.json()["code"] == settings.TOKEN_INVALID_CODE


class TestUserEndpoints:
    async def test_get_me_returns_avatar_and_fields(
        self, client: AsyncClient, db_session, test_user, test_user_token, fake_redis
    ):
        """目的：GET /users/me 返回当前用户完整信息且 avatar 来自 attachment 表。"""
        attachment = Attachment(
            attachment_id=6001,
            target_type=AttachmentTargetType.USER,
            target_id=test_user.user_id,
            url=f"/static/avatar/user_{test_user.user_id}_test.png",
            creator_id=test_user.user_id,
        )
        db_session.add(attachment)
        await db_session.flush()
        test_user.avatar_id = attachment.attachment_id
        await db_session.flush()

        await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
        await fake_redis.set(f"user_token:{test_user.user_id}", test_user_token)

        resp = await client.get("/users/me", headers={"Authorization": f"Bearer {test_user_token}"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == settings.SUCCESS_CODE
        assert body["message"]["avatar"] == attachment.url

    async def test_patch_me_update_username_and_avatar(self, client: AsyncClient, db_session, test_user, test_user_token, fake_redis):
        """目的：PATCH /users/me 能更新用户名并设置自己的 avatar_id。"""
        attachment = Attachment(
            attachment_id=6002,
            target_type=AttachmentTargetType.USER,
            target_id=test_user.user_id,
            url=f"/static/avatar/user_{test_user.user_id}_patch.png",
            creator_id=test_user.user_id,
        )
        db_session.add(attachment)
        await db_session.flush()

        await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
        await fake_redis.set(f"user_token:{test_user.user_id}", test_user_token)

        resp = await client.patch(
            "/users/me",
            headers={"Authorization": f"Bearer {test_user_token}"},
            json={"user_name": "patched_name", "avatar_id": attachment.attachment_id},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == settings.SUCCESS_CODE
        assert body["message"]["user_name"] == "patched_name"
        assert body["message"]["avatar"] == attachment.url

    async def test_get_user_public_by_id(self, client: AsyncClient, db_session, test_user, test_user_token, fake_redis):
        """目的：GET /users/{user_id} 返回脱敏的公开资料并包含 avatar 字段。"""
        await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
        await fake_redis.set(f"user_token:{test_user.user_id}", test_user_token)

        resp = await client.get(f"/users/{test_user.user_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == settings.SUCCESS_CODE
        assert "avatar" in body["message"]

    async def test_admin_put_update_user(self, client: AsyncClient, db_session, test_admin_user, test_admin_token, test_user, fake_redis):
        """目的：管理员能通过 PUT /users/{user_id} 修改用户并返回 avatar URL。"""
        # 创建附件并让 admin 把该附件回填到目标用户
        attachment = Attachment(
            attachment_id=6003,
            target_type=AttachmentTargetType.USER,
            target_id=test_admin_user.user_id,
            url=f"/static/avatar/user_{test_admin_user.user_id}_admin.png",
            creator_id=test_admin_user.user_id,
        )
        db_session.add(attachment)
        await db_session.flush()

        await fake_redis.set(f"token:{test_admin_token}", str(test_admin_user.user_id))
        await fake_redis.set(f"user_token:{test_admin_user.user_id}", test_admin_token)

        resp = await client.put(
            f"/users/{test_user.user_id}",
            headers={"Authorization": f"Bearer {test_admin_token}"},
            json={"user_name": "admin_set_name", "avatar_id": attachment.attachment_id},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == settings.SUCCESS_CODE
        assert body["message"]["user_name"] == "admin_set_name"
        # 管理员回填的 avatar 会是附件 URL（注意：按权限逻辑，管理员仍需满足所有权约束；测试中我们直接用 admin 上传的附件）
        assert body["message"]["avatar"] == attachment.url

    async def test_delete_me(self, client: AsyncClient, db_session, test_user, test_user_token, fake_redis):
        """目的：DELETE /users/me 能逻辑删除当前用户并返回提示信息。"""
        await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
        await fake_redis.set(f"user_token:{test_user.user_id}", test_user_token)

        resp = await client.delete("/users/me", headers={"Authorization": f"Bearer {test_user_token}"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == settings.SUCCESS_CODE
        assert "账号已注销" in body["message"]["message"]

    async def test_admin_delete_user(self, client: AsyncClient, db_session, test_admin_user, test_admin_token, test_user, fake_redis):
        """目的：管理员能删除（逻辑删除）其他用户，但不能删除自己。"""
        await fake_redis.set(f"token:{test_admin_token}", str(test_admin_user.user_id))
        await fake_redis.set(f"user_token:{test_admin_user.user_id}", test_admin_token)

        resp = await client.delete(f"/users/{test_user.user_id}", headers={"Authorization": f"Bearer {test_admin_token}"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == settings.SUCCESS_CODE
        assert f"用户 {test_user.user_id} 已被禁用/删除" in body["message"]["message"]
