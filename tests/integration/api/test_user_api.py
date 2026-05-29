"""用户信息模块的集成测试（API 层）

合并了之前分散在不同文件中的用户路由测试，确保每个路由的测试集中在 `test_user_api.py` 中。
"""

import pytest
import uuid
from httpx import AsyncClient

from app.core import settings
from app.models import User, Attachment, AttachmentTargetType, Category, Post
from tests.helpers import assert_api_error

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

    async def test_follow_unfollow_and_list_followings(
        self,
        client: AsyncClient,
        db_session,
        test_user: User,
        test_user_token: str,
        fake_redis,
    ):
        """目的：关注 / 取消关注用户，并能在关注列表中查询到对方。"""
        other_user = User(
            user_id=2002,
            user_uuid=uuid.uuid4().bytes,
            user_name="other_user",
            wechat_openid="openid-other-user",
        )
        db_session.add(other_user)
        await db_session.flush()

        await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
        await fake_redis.set(f"user_token:{test_user.user_id}", test_user_token)

        follow_resp = await client.post(
            "/users/follow",
            headers={"Authorization": f"Bearer {test_user_token}"},
            json={"following_id": other_user.user_id},
        )
        assert follow_resp.status_code == 200
        follow_body = follow_resp.json()
        assert follow_body["code"] == settings.SUCCESS_CODE
        assert follow_body["message"]["is_following"] is True

        list_resp = await client.get(
            "/users/me/followings",
            headers={"Authorization": f"Bearer {test_user_token}"},
        )
        assert list_resp.status_code == 200
        list_body = list_resp.json()
        assert list_body["code"] == settings.SUCCESS_CODE
        assert list_body["message"]["total"] == 1
        assert list_body["message"]["list"][0]["user"]["user_id"] == other_user.user_id

        unfollow_resp = await client.post(
            "/users/follow",
            headers={"Authorization": f"Bearer {test_user_token}"},
            json={"following_id": other_user.user_id},
        )
        assert unfollow_resp.status_code == 200
        unfollow_body = unfollow_resp.json()
        assert unfollow_body["code"] == settings.SUCCESS_CODE
        assert unfollow_body["message"]["is_following"] is False

    async def test_post_favorite_and_list_favorites(
        self,
        client: AsyncClient,
        db_session,
        test_user: User,
        test_user_token: str,
        fake_redis,
    ):
        """目的：收藏帖子后能在我的收藏列表中查到该帖子。"""
        category = Category(category_id=9001, name="收藏分类", config_json={})
        db_session.add(category)
        await db_session.flush()

        post = Post(
            post_id=9001,
            publisher_id=test_user.user_id,
            category_id=category.category_id,
            title="收藏测试帖子",
            description="这是一个用于测试收藏功能的帖子",
        )
        db_session.add(post)
        await db_session.flush()

        await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
        await fake_redis.set(f"user_token:{test_user.user_id}", test_user_token)

        favorite_resp = await client.post(
            "/users/favorite",
            headers={"Authorization": f"Bearer {test_user_token}"},
            json={"target_type": "POST", "target_id": post.post_id},
        )
        assert favorite_resp.status_code == 200
        favorite_body = favorite_resp.json()
        assert favorite_body["code"] == settings.SUCCESS_CODE
        assert favorite_body["message"]["is_favorite"] is True

        list_resp = await client.get(
            "/users/me/favorites",
            headers={"Authorization": f"Bearer {test_user_token}"},
        )
        assert list_resp.status_code == 200
        list_body = list_resp.json()
        assert list_body["code"] == settings.SUCCESS_CODE
        assert list_body["message"]["total"] == 1
        fav_item = list_body["message"]["list"][0]
        assert fav_item["target_id"] == post.post_id
        # 验证新字段
        assert "is_full" in fav_item
        assert "create_time" in fav_item
        assert isinstance(fav_item["create_time"], int)  # 13位毫秒时间戳
        assert 1000000000000 <= fav_item["create_time"] <= 9999999999999  # 13位范围
        assert "publisher" in fav_item
        if fav_item["publisher"]:
            assert "user_name" in fav_item["publisher"]
            assert "avatar" in fav_item["publisher"]

    async def test_view_post_detail_records_history(
        self,
        client: AsyncClient,
        db_session,
        test_user: User,
        test_user_token: str,
        fake_redis,
    ):
        """目的：浏览帖子详情后生成历史记录。"""
        category = Category(category_id=9002, name="历史分类", config_json={})
        db_session.add(category)
        await db_session.flush()

        post = Post(
            post_id=9002,
            publisher_id=test_user.user_id,
            category_id=category.category_id,
            title="历史测试帖子",
            description="这是一个用于测试历史功能的帖子",
        )
        db_session.add(post)
        await db_session.flush()

        await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
        await fake_redis.set(f"user_token:{test_user.user_id}", test_user_token)

        detail_resp = await client.get(
            f"/posts/{post.post_id}",
            headers={"Authorization": f"Bearer {test_user_token}"},
        )
        assert detail_resp.status_code == 200

        history_resp = await client.get(
            "/users/me/histories",
            headers={"Authorization": f"Bearer {test_user_token}"},
        )
        assert history_resp.status_code == 200
        history_body = history_resp.json()
        assert history_body["code"] == settings.SUCCESS_CODE
        assert history_body["message"]["total"] == 1
        hist_item = history_body["message"]["list"][0]
        assert hist_item["target_id"] == post.post_id
        # 验证新字段
        assert "is_full" in hist_item
        assert "view_time" in hist_item
        assert isinstance(hist_item["view_time"], int)  # 13位毫秒时间戳
        assert 1000000000000 <= hist_item["view_time"] <= 9999999999999  # 13位范围
        assert "publisher" in hist_item
        if hist_item["publisher"]:
            assert "user_name" in hist_item["publisher"]
            assert "avatar" in hist_item["publisher"]

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

    async def test_admin_cannot_delete_self(self, client: AsyncClient, test_admin_user, test_admin_token, fake_redis):
        await fake_redis.set(f"token:{test_admin_token}", str(test_admin_user.user_id))
        await fake_redis.set(f"user_token:{test_admin_user.user_id}", test_admin_token)

        resp = await client.delete(f"/users/{test_admin_user.user_id}", headers={"Authorization": f"Bearer {test_admin_token}"})

        assert resp.status_code == 200
        message = assert_api_error(resp.json(), code=settings.INSUFFICIENT_AUTHORITY_CODE)
        assert "无法删除自己的账号" in message["msg"]
