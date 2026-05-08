"""用户信息模块的集成测试（API 层）"""

import pytest
from httpx import AsyncClient

from app.core import settings
from app.models import User

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

        response = await client.get("/user/info", headers={"Authorization": f"Bearer {test_user_token}"})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == settings.SUCCESS_CODE
        assert data["message"]["userName"] == test_user.user_name
        assert data["message"]["isAdmin"] == test_user.is_admin
        assert data["message"]["isVerified"] == test_user.is_verified

    async def test_get_profile_without_token(self, client: AsyncClient):
        response = await client.get("/user/info")

        assert response.status_code == 200
        assert response.json()["code"] == settings.TOKEN_INVALID_CODE

    async def test_get_profile_with_invalid_token(self, client: AsyncClient):
        response = await client.get("/user/info", headers={"Authorization": "Bearer invalid_token_xyz"})

        assert response.status_code == 200
        assert response.json()["code"] == settings.TOKEN_INVALID_CODE

    async def test_get_profile_with_malformed_bearer(self, client: AsyncClient):
        response = await client.get("/user/info", headers={"Authorization": "Bearer"})

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

        response = await client.get("/user/info", headers={"Authorization": f"Bearer {test_admin_token}"})

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

        response = await client.get("/user/info", headers={"Authorization": f"Bearer {test_user_token}"})

        assert response.status_code == 200
        data = response.json()
        assert data["code"] == settings.SUCCESS_CODE
        assert data["message"]["isVerified"] is True
