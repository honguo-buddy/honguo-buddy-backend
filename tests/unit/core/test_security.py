import asyncio
import pytest
from jose import jwt

from app.core import create_access_token, get_hash_pwd, verify_pwd, generate_email_verify_token, verify_email_token, get_user_id_from_request, settings

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


async def test_get_user_id_from_request_success(monkeypatch):
    # create token with sub matching 5001
    token = create_access_token({"sub": "5001"})

    class FakeRequest:
        def __init__(self):
            self.headers = {"authorization": f"Bearer {token}"}
            self.cookies = {}
            self.query_params = {}

    async def _get(k):
        return "5001"

    # monkeypatch redis in security module
    import app.core.security as sec

    sec.redis.get = _get

    req = FakeRequest()
    user_id = await get_user_id_from_request(req)
    assert user_id == 5001
