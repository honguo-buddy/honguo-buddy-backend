import asyncio
import pytest
from jose import jwt
from unittest.mock import MagicMock

from app.core import create_access_token, get_hash_pwd, verify_pwd, generate_email_verify_token, verify_email_token, get_user_id_from_request, send_email, settings

pytestmark = pytest.mark.asyncio


async def test_create_access_token_payload():
    payload = {"sub": "123", "user_name": "u"}
    token = create_access_token(payload)

    decoded = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.TOKEN_ALGORITHM])
    assert decoded["sub"] == "123"
    assert "exp" in decoded


async def test_generate_and_verify_email_token():
    email = "test@example.com"
    token = generate_email_verify_token(email)
    result = verify_email_token(token)
    assert result == email


async def test_verify_email_token_invalid_returns_none():
    assert verify_email_token("not-a-valid-token") is None


async def test_hash_and_verify_password(monkeypatch):
    # passlib bcrypt backend in some CI/envs can raise on internal checks;
    # mock pwd_context to keep this unit test isolated and deterministic
    import app.core.security as sec

    def fake_hash(p):
        return "fakehash:" + p[:72]

    def fake_verify(plain, hashed):
        return hashed == ("fakehash:" + plain[:72])

    monkeypatch.setattr(sec.pwd_context, "hash", fake_hash, raising=False)
    monkeypatch.setattr(sec.pwd_context, "verify", fake_verify, raising=False)

    pwd = "my-secret-pwd"
    hashed = get_hash_pwd(pwd)
    assert verify_pwd(pwd, hashed) is True
    assert verify_pwd("wrong", hashed) is False


async def test_send_email_sets_smtp_timeout(monkeypatch):
    import app.core.security as sec

    smtp_instance = MagicMock()
    smtp_instance.__enter__.return_value = smtp_instance
    smtp_instance.__exit__.return_value = None
    smtp_ssl_mock = MagicMock(return_value=smtp_instance)
    monkeypatch.setattr(sec.smtplib, "SMTP_SSL", smtp_ssl_mock)

    result = send_email("to@example.com", "测试主题", "<p>测试内容</p>")

    assert result is True
    smtp_ssl_mock.assert_called_once_with(settings.SMTP_SERVER, settings.SMTP_PORT, timeout=0.5)
    smtp_instance.login.assert_called_once_with(settings.SMTP_USER, settings.SMTP_PASSWORD)


async def test_get_user_id_from_request_success(monkeypatch):
    # create token with sub matching 5001
    token = create_access_token({"sub": "5001"})

    class FakeRequest:
        def __init__(self):
            self.headers = {"authorization": f"Bearer {token}"}
            self.cookies = {}
            self.query_params = {}

    async def _get(k):
        if k == f"token:{token}":
            return "5001"
        if k == "user_token:5001":
            return token
        return None

    # monkeypatch redis in security module
    import app.core.security as sec

    sec.redis.get = _get

    req = FakeRequest()
    user_id = await get_user_id_from_request(req)
    assert user_id == 5001


async def test_get_user_id_from_request_cookie_fallback(monkeypatch):
    token = create_access_token({"sub": "5002"})

    class FakeRequest:
        def __init__(self):
            self.headers = {}
            self.cookies = {"token": token}
            self.query_params = {}

    async def _get(k):
        if k == f"token:{token}":
            return "5002"
        if k == "user_token:5002":
            return token
        return None

    import app.core.security as sec

    sec.redis.get = _get

    user_id = await get_user_id_from_request(FakeRequest())
    assert user_id == 5002


async def test_get_user_id_from_request_invalid_token_returns_none(monkeypatch):
    class FakeRequest:
        def __init__(self):
            self.headers = {"authorization": "Bearer broken-token"}
            self.cookies = {}
            self.query_params = {}

    async def _get(k):
        return None

    import app.core.security as sec

    sec.redis.get = _get

    assert await get_user_id_from_request(FakeRequest()) is None


async def test_get_user_id_from_request_rejects_stale_token_when_user_token_points_elsewhere(monkeypatch):
    active_token = create_access_token({"sub": "5003", "nonce": "active"})
    stale_token = create_access_token({"sub": "5003", "nonce": "stale"})

    class FakeRequest:
        def __init__(self):
            self.headers = {"authorization": f"Bearer {stale_token}"}
            self.cookies = {}
            self.query_params = {}

    async def _get(key):
        if key == f"token:{stale_token}":
            return "5003"
        if key == "user_token:5003":
            return active_token
        return None

    import app.core.security as sec

    sec.redis.get = _get

    assert await get_user_id_from_request(FakeRequest()) is None
