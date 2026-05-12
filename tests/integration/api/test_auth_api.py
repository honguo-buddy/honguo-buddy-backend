"""认证模块的集成测试（API 层）"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app.core import settings
from app.models import User

pytestmark = pytest.mark.asyncio


def _assert_success_payload(response_json: dict):
    if "code" in response_json:
        assert response_json["code"] == settings.SUCCESS_CODE
        return response_json.get("message", {})
    return response_json


class TestSwaggerLogin:
    async def test_swagger_login_with_debug_password_success(self, client: AsyncClient, test_user: User):
        response = await client.post(
            "/auth/swagger-login",
            json={"wx_id": test_user.wechat_openid, "password": settings.DEBUG_MASTER_PASSWORD},
        )

        assert response.status_code == 200
        payload = _assert_success_payload(response.json())
        assert "access_token" in payload or "token" in payload

    async def test_swagger_login_invalid_openid(self, client: AsyncClient):
        response = await client.post(
            "/auth/swagger-login",
            json={"wx_id": "nonexistent_openid", "password": settings.DEBUG_MASTER_PASSWORD},
        )

        assert response.status_code == 200
        assert response.json()["code"] == settings.LOGIN_FAILED_CODE

    async def test_swagger_login_wrong_password(self, client: AsyncClient, test_user: User):
        response = await client.post(
            "/auth/swagger-login",
            json={"wx_id": test_user.wechat_openid, "password": "wrong_password_12345"},
        )

        assert response.status_code == 200
        assert response.json()["code"] == settings.LOGIN_FAILED_CODE

    async def test_swagger_login_form_urlencoded(self, client: AsyncClient, test_user: User):
        response = await client.post(
            "/auth/swagger-login",
            data={"wx_id": test_user.wechat_openid, "password": settings.DEBUG_MASTER_PASSWORD},
        )

        assert response.status_code == 200
        _assert_success_payload(response.json())

    async def test_swagger_login_query_params(self, client: AsyncClient, test_user: User):
        response = await client.post(
            f"/auth/swagger-login?wx_id={test_user.wechat_openid}&password={settings.DEBUG_MASTER_PASSWORD}"
        )

        assert response.status_code == 200
        _assert_success_payload(response.json())


class TestGetCurrentUser:
    async def test_get_current_user_with_valid_token(
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
        body = response.json()
        assert body["code"] == settings.SUCCESS_CODE
        assert body["message"]["userName"] == test_user.user_name

    async def test_get_current_user_with_invalid_token(self, client: AsyncClient):
        response = await client.get("/users/info", headers={"Authorization": "Bearer invalid_token_12345"})

        assert response.status_code == 200
        assert response.json()["code"] == settings.TOKEN_INVALID_CODE

    async def test_get_current_user_without_token(self, client: AsyncClient):
        response = await client.get("/users/info")

        assert response.status_code == 200
        assert response.json()["code"] == settings.TOKEN_INVALID_CODE

    async def test_get_current_user_deleted_user(
        self,
        client: AsyncClient,
        db_session,
        test_user: User,
        test_user_token: str,
        fake_redis,
    ):
        test_user.is_deleted = True
        await db_session.flush()
        await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))

        response = await client.get("/users/info", headers={"Authorization": f"Bearer {test_user_token}"})

        assert response.status_code == 200
        assert response.json()["code"] == settings.TOKEN_INVALID_CODE

    async def test_get_current_user_inactive_user(
        self,
        client: AsyncClient,
        db_session,
        test_user: User,
        test_user_token: str,
        fake_redis,
    ):
        test_user.is_active = False
        await db_session.flush()
        await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))

        response = await client.get("/users/info", headers={"Authorization": f"Bearer {test_user_token}"})

        assert response.status_code == 200
        assert response.json()["code"] == settings.TOKEN_INVALID_CODE


class TestLogout:
    async def test_logout_with_valid_token(
        self,
        client: AsyncClient,
        test_user: User,
        test_user_token: str,
        fake_redis,
    ):
        await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
        await fake_redis.set(f"user_token:{test_user.user_id}", test_user_token)

        response = await client.post("/auth/logout", headers={"Authorization": f"Bearer {test_user_token}"})

        assert response.status_code == 200
        assert response.json()["code"] == settings.SUCCESS_CODE

    async def test_logout_without_token(self, client: AsyncClient):
        response = await client.post("/auth/logout")

        assert response.status_code == 200
        assert response.json()["code"] == settings.TOKEN_INVALID_CODE


class TestWxLogin:
    async def test_wx_login_or_register_new_user(self, client: AsyncClient):
        fake_user = MagicMock()
        fake_user.user_id = 3001
        fake_user.user_name = "用户测试"
        fake_user.user_type = None
        fake_user.is_active = True
        fake_user.is_deleted = False

        with patch("app.services.auth_service.httpx.AsyncClient") as mock_http_client, patch(
            "app.services.auth_service.AuthService._create_user", new=AsyncMock(return_value=fake_user)
        ):
            mock_response = MagicMock()
            mock_response.json.return_value = {"session_key": "test_session_key", "openid": "test_openid_123"}
            mock_response.raise_for_status = MagicMock(return_value=None)

            mock_http_instance = AsyncMock()
            mock_http_instance.get = AsyncMock(return_value=mock_response)
            mock_http_instance.__aenter__ = AsyncMock(return_value=mock_http_instance)
            mock_http_instance.__aexit__ = AsyncMock(return_value=None)
            mock_http_client.return_value = mock_http_instance

            response = await client.post("/auth/wxLogin", json={"code": "test_code_123"})

            assert response.status_code == 200
            assert response.json()["code"] == settings.SUCCESS_CODE

    async def test_wx_login_existing_user(self, client: AsyncClient, test_user: User):
        with patch("app.services.auth_service.httpx.AsyncClient") as mock_http_client:
            mock_response = MagicMock()
            mock_response.json.return_value = {"session_key": "test_session_key", "openid": test_user.wechat_openid}
            mock_response.raise_for_status = MagicMock(return_value=None)

            mock_http_instance = AsyncMock()
            mock_http_instance.get = AsyncMock(return_value=mock_response)
            mock_http_instance.__aenter__ = AsyncMock(return_value=mock_http_instance)
            mock_http_instance.__aexit__ = AsyncMock(return_value=None)
            mock_http_client.return_value = mock_http_instance

            response = await client.post("/auth/wxLogin", json={"code": "test_code_123"})

            assert response.status_code == 200
            assert response.json()["code"] == settings.SUCCESS_CODE


class TestEmailVerification:
    async def test_send_email_verify_code_with_valid_token(
        self,
        client: AsyncClient,
        test_user: User,
        test_user_token: str,
        fake_redis,
    ):
        await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
        await fake_redis.set(f"user_token:{test_user.user_id}", test_user_token)

        with patch("app.services.auth_service.send_email", return_value=True):
            response = await client.post(
                "/auth/email/send-verify-code",
                headers={"Authorization": f"Bearer {test_user_token}"},
                json={"email": "newemail@example.com"},
            )

        assert response.status_code == 200
        assert response.json()["code"] == settings.SUCCESS_CODE

    async def test_send_email_verify_code_without_token(self, client: AsyncClient):
        response = await client.post("/auth/email/send-verify-code", json={"email": "newemail@example.com"})

        assert response.status_code == 200
        assert response.json()["code"] == settings.TOKEN_INVALID_CODE

    async def test_verify_email_code_with_valid_token(
        self,
        client: AsyncClient,
        test_user: User,
        test_user_token: str,
        fake_redis,
    ):
        await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
        await fake_redis.set(f"user_token:{test_user.user_id}", test_user_token)
        await fake_redis.set(
            f"email_verify_code:{test_user.email}",
            json.dumps({"code": "123456", "timestamp": 9999999999, "attempts": 0, "user_id": test_user.user_id}),
        )

        response = await client.post(
            "/auth/email/verify-code",
            headers={"Authorization": f"Bearer {test_user_token}"},
            json={"email": test_user.email, "code": "123456"},
        )

        assert response.status_code == 200
        assert response.json()["code"] == settings.SUCCESS_CODE

    async def test_verify_email_code_without_token(self, client: AsyncClient):
        response = await client.post(
            "/auth/email/verify-code",
            json={"email": "test@example.com", "code": "123456"},
        )

        assert response.status_code == 200
        assert response.json()["code"] == settings.TOKEN_INVALID_CODE
