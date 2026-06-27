from __future__ import annotations

import json
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core import settings
from app.services.auth_service import AuthService
from tests.unit.fake_sqlalchemy import FakeResult


def test_is_campus_email_and_normalize():
    assert AuthService._is_campus_email("user@bjtu.edu.cn") is True
    assert AuthService._is_campus_email("user@example.com") is False
    assert AuthService._normalize_user_name("  alice  ") == "alice"


@pytest.mark.asyncio
async def test_gen_unique_user_name_returns_candidate(monkeypatch):
    class FakeDB:
        async def execute(self, stmt):
            return FakeResult(scalar_value=None)

    val = await AuthService._gen_unique_user_name(FakeDB())
    assert isinstance(val, str) and val.startswith("用户")


@pytest.mark.asyncio
async def test_gen_unique_user_uuid_returns_bytes():
    class FakeDB:
        async def execute(self, stmt):
            return FakeResult(scalar_value=None)

    b = await AuthService._gen_unique_user_uuid(FakeDB())
    assert isinstance(b, (bytes, bytearray)) and len(b) == 16


@pytest.mark.asyncio
async def test_token_persist_issue_and_create_user_paths(monkeypatch):
    redis_stub = SimpleNamespace(
        get=AsyncMock(side_effect=lambda key: "old-token" if key == "user_token:1" else None),
        set=AsyncMock(),
        delete=AsyncMock(),
    )
    monkeypatch.setattr("app.services.auth_service.redis", redis_stub)
    monkeypatch.setattr("app.services.auth_service.create_access_token", lambda payload: "issued-token")

    token_user = SimpleNamespace(user_id=1, user_name="alice", user_type=None)
    token = await AuthService._issue_token_for_user(token_user)
    assert token == "issued-token"
    redis_stub.delete.assert_any_await("token:old-token")

    class CreateUserDB:
        def __init__(self):
            self.added = []

        async def execute(self, stmt):
            return FakeResult(scalar_value=None)

        def add(self, obj):
            self.added.append(obj)
            if hasattr(obj, "user_id") and obj.user_id is None:
                obj.user_id = 77

        async def flush(self):
            pass

        async def refresh(self, obj):
            pass

    monkeypatch.setattr(AuthService, "_gen_unique_user_uuid", AsyncMock(return_value=b"1234567890abcdef"))
    created = await AuthService._create_user(CreateUserDB(), user_name="bob", wechat_openid="wx-openid", is_verified=True)
    assert created.user_name == "bob"
    assert created.is_verified is True


@pytest.mark.asyncio
async def test_unique_uuid_and_username_failure_paths(monkeypatch):
    class FakeUUID:
        bytes = b"1234567890abcdef"
        int = 123456789

    class CollisionDB:
        def __init__(self):
            self.calls = 0

        async def execute(self, stmt):
            self.calls += 1
            return FakeResult(items=[SimpleNamespace(user_uuid=b"x")])

    monkeypatch.setattr("app.services.auth_service.uuid.uuid4", lambda: FakeUUID())
    with pytest.raises(Exception):
        await AuthService._gen_unique_user_uuid(CollisionDB())

    class NameCollisionDB:
        def __init__(self):
            self.calls = 0

        async def execute(self, stmt):
            self.calls += 1
            return FakeResult(items=[SimpleNamespace(user_name=f"用户{self.calls}")])

    monkeypatch.setattr("app.services.auth_service.random.randint", lambda a, b: 1234567)
    fallback = await AuthService._gen_unique_user_name(NameCollisionDB())
    assert fallback.startswith("用户")


@pytest.mark.asyncio
async def test_wx_login_or_register_branches(monkeypatch):
    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, payload):
            self.payload = payload

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, params):
            return FakeResponse(self.payload)

    class FakeDB:
        def __init__(self, user=None):
            self.user = user

        async def execute(self, stmt):
            return FakeResult(items=[self.user] if self.user else [])

        async def commit(self):
            pass

    monkeypatch.setattr(settings, "WX_APP_ID", "appid", raising=False)
    monkeypatch.setattr(settings, "WX_APP_SECRET", "secret", raising=False)
    monkeypatch.setattr(settings, "WX_CODE_TO_SESSION_URL", "https://example.test/session", raising=False)

    with pytest.raises(Exception):
        await AuthService.wx_login_or_register(FakeDB(), "")

    monkeypatch.setattr("app.services.auth_service.httpx.AsyncClient", lambda timeout: FakeClient({}))
    with pytest.raises(Exception):
        await AuthService.wx_login_or_register(FakeDB(), "code1")

    new_user = SimpleNamespace(user_id=2, user_name="newbie", is_deleted=False, is_active=True)
    monkeypatch.setattr("app.services.auth_service.httpx.AsyncClient", lambda timeout: FakeClient({"openid": "openid-1"}))
    monkeypatch.setattr(AuthService, "_gen_unique_user_name", AsyncMock(return_value="新用户"))
    monkeypatch.setattr(AuthService, "_create_user", AsyncMock(return_value=new_user))
    monkeypatch.setattr(AuthService, "_issue_token_for_user", AsyncMock(return_value="token-1"))
    result = await AuthService.wx_login_or_register(FakeDB(), " code2 ")
    assert result["isNewUser"] is True
    assert result["token"] == "token-1"

    inactive_user = SimpleNamespace(user_id=3, user_name="inactive", is_deleted=False, is_active=False)
    with pytest.raises(Exception):
        await AuthService.wx_login_or_register(FakeDB(inactive_user), "code3")


@pytest.mark.asyncio
async def test_swagger_login_branches(monkeypatch):
    class FakeDB:
        def __init__(self, user=None):
            self.user = user

        async def execute(self, stmt):
            return FakeResult(items=[self.user] if self.user else [])

        async def commit(self):
            pass

    monkeypatch.setattr(settings, "DEBUG", True, raising=False)
    monkeypatch.setattr(settings, "DEBUG_SKIP_PASSWORD_CHECK", True, raising=False)
    monkeypatch.setattr(settings, "DEBUG_MASTER_PASSWORD", "master", raising=False)
    monkeypatch.setattr(AuthService, "_issue_token_for_user", AsyncMock(return_value="debug-token"))

    with pytest.raises(Exception):
        await AuthService.swagger_login(FakeDB(), "", "x", None)

    with pytest.raises(Exception):
        await AuthService.swagger_login(FakeDB(), "wx-id", "x", None)

    active_user = SimpleNamespace(user_id=4, user_name="debug", is_active=True, last_login_ip=None, last_login_time=None)
    result = await AuthService.swagger_login(FakeDB(active_user), "wx-id", "any", "127.0.0.1")
    assert result["access_token"] == "debug-token"


@pytest.mark.asyncio
async def test_email_verify_send_and_verify_matrix(monkeypatch):
    class RedisStub:
        def __init__(self):
            self.store = {}
            self.get = AsyncMock(side_effect=self._get)
            self.set = AsyncMock(side_effect=self._set)
            self.delete = AsyncMock(side_effect=self._delete)

        async def _get(self, key):
            return self.store.get(key)

        async def _set(self, key, value, ex=None):
            self.store[key] = value

        async def _delete(self, key):
            self.store.pop(key, None)

    redis_stub = RedisStub()
    monkeypatch.setattr("app.services.auth_service.redis", redis_stub)

    class EmailDB:
        def __init__(self, existing=None, user=None):
            self.existing = existing
            self.user = user

        async def execute(self, stmt):
            if self.existing is not None:
                return FakeResult(items=[self.existing])
            return FakeResult(items=[self.user] if self.user else [])

        async def commit(self):
            pass

        async def refresh(self, user):
            pass

    monkeypatch.setattr("app.services.auth_service.send_email", lambda email, subject, body: True)

    with pytest.raises(Exception):
        await AuthService.send_email_verify_code(EmailDB(), 1, "bad-email")

    with pytest.raises(Exception):
        await AuthService.send_email_verify_code(EmailDB(existing=SimpleNamespace()), 1, "u@domain.com")

    await redis_stub.set("email_verify_rate:u@domain.com", "1")
    with pytest.raises(Exception):
        await AuthService.send_email_verify_code(EmailDB(), 1, "u@domain.com")
    await redis_stub.delete("email_verify_rate:u@domain.com")

    monkeypatch.setattr("app.services.auth_service.send_email", lambda email, subject, body: False)
    with pytest.raises(Exception):
        await AuthService.send_email_verify_code(EmailDB(), 1, "u@domain.com")

    monkeypatch.setattr("app.services.auth_service.send_email", lambda email, subject, body: True)
    send_result = await AuthService.send_email_verify_code(EmailDB(), 1, "u@bjtu.edu.cn")
    assert send_result["email_masked"].endswith("@bjtu.edu.cn")

    class VerifyDB:
        def __init__(self, user=None):
            self.user = user

        async def execute(self, stmt):
            return FakeResult(items=[self.user] if self.user else [])

        async def commit(self):
            pass

        async def refresh(self, user):
            pass

    with pytest.raises(Exception):
        await AuthService.verify_email_code(VerifyDB(), 1, "u@bjtu.edu.cn", "123456")

    await redis_stub.set("email_verify_code:u@bjtu.edu.cn", "not-json")
    with pytest.raises(Exception):
        await AuthService.verify_email_code(VerifyDB(), 1, "u@bjtu.edu.cn", "123456")

    await redis_stub.set("email_verify_code:u@bjtu.edu.cn", json.dumps({"code": "123456", "timestamp": 1, "attempts": 0, "user_id": 2}, ensure_ascii=False))
    with pytest.raises(Exception):
        await AuthService.verify_email_code(VerifyDB(SimpleNamespace(user_id=1)), 1, "u@bjtu.edu.cn", "123456")

    await redis_stub.set("email_verify_code:u@bjtu.edu.cn", json.dumps({"code": "123456", "timestamp": 1, "attempts": 3, "user_id": 1}, ensure_ascii=False))
    with pytest.raises(Exception):
        await AuthService.verify_email_code(VerifyDB(SimpleNamespace(user_id=1)), 1, "u@bjtu.edu.cn", "123456")

    future = 9999999999
    monkeypatch.setattr("app.services.auth_service.get_now", lambda: SimpleNamespace(timestamp=lambda: future))
    await redis_stub.set("email_verify_code:u@bjtu.edu.cn", json.dumps({"code": "123456", "timestamp": 1, "attempts": 0, "user_id": 1}, ensure_ascii=False))
    with pytest.raises(Exception):
        await AuthService.verify_email_code(VerifyDB(SimpleNamespace(user_id=1)), 1, "u@bjtu.edu.cn", "123456")

    monkeypatch.setattr("app.services.auth_service.get_now", lambda: SimpleNamespace(timestamp=lambda: 100))
    await redis_stub.set("email_verify_code:u@bjtu.edu.cn", json.dumps({"code": "123456", "timestamp": 100, "attempts": 0, "user_id": 1}, ensure_ascii=False))
    with pytest.raises(Exception):
        await AuthService.verify_email_code(VerifyDB(SimpleNamespace(user_id=1)), 1, "u@bjtu.edu.cn", "000000")

    await redis_stub.set("email_verify_code:u@bjtu.edu.cn", json.dumps({"code": "123456", "timestamp": 100, "attempts": 0, "user_id": 1}, ensure_ascii=False))
    success = await AuthService.verify_email_code(VerifyDB(SimpleNamespace(user_id=1)), 1, "u@bjtu.edu.cn", "123456")
    assert success["detail"] == "邮箱绑定成功"


@pytest.mark.asyncio
async def test_send_email_verify_code_uses_thread_for_smtp(monkeypatch):
    import app.services.auth_service as auth_module

    class RedisStub:
        def __init__(self):
            self.store = {}
            self.get = AsyncMock(side_effect=self._get)
            self.set = AsyncMock(side_effect=self._set)
            self.delete = AsyncMock(side_effect=self._delete)

        async def _get(self, key):
            return self.store.get(key)

        async def _set(self, key, value, ex=None):
            self.store[key] = value

        async def _delete(self, key):
            self.store.pop(key, None)

    class EmailDB:
        async def execute(self, stmt):
            return FakeResult(items=[])

    redis_stub = RedisStub()
    monkeypatch.setattr("app.services.auth_service.redis", redis_stub)
    monkeypatch.setattr("app.services.auth_service.send_email", lambda email, subject, body: True)

    async def fake_to_thread(func, *args, **kwargs):
        assert func.__name__ == "<lambda>"
        assert args[0] == "thread@bjtu.edu.cn"
        return func(*args, **kwargs)

    to_thread_mock = AsyncMock(side_effect=fake_to_thread)
    monkeypatch.setattr(auth_module, "asyncio", SimpleNamespace(to_thread=to_thread_mock), raising=False)

    result = await AuthService.send_email_verify_code(EmailDB(), 1, "thread@bjtu.edu.cn")

    assert result["detail"] == "验证码已发送到你的邮箱，请在5分钟内验证"
    to_thread_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_persist_token_and_wx_config_missing_paths(monkeypatch):
    redis_stub = SimpleNamespace(
        get=AsyncMock(return_value=None),
        set=AsyncMock(),
        delete=AsyncMock(),
    )
    monkeypatch.setattr("app.services.auth_service.redis", redis_stub)
    await AuthService._persist_user_token(10, "fresh-token")
    redis_stub.delete.assert_not_awaited()

    monkeypatch.setattr(settings, "WX_APP_ID", "", raising=False)
    monkeypatch.setattr(settings, "WX_APP_SECRET", "", raising=False)

    class FakeDB:
        async def execute(self, stmt):
            return FakeResult(items=[])

    with pytest.raises(Exception):
        await AuthService.wx_login_or_register(FakeDB(), "code")


@pytest.mark.asyncio
async def test_swagger_login_debug_disabled_and_inactive_paths(monkeypatch):
    class FakeDB:
        def __init__(self, user=None):
            self.user = user

        async def execute(self, stmt):
            return FakeResult(items=[self.user] if self.user else [])

        async def commit(self):
            pass

    monkeypatch.setattr(settings, "DEBUG", False, raising=False)
    monkeypatch.setattr(settings, "DEBUG_SKIP_PASSWORD_CHECK", False, raising=False)
    monkeypatch.setattr(settings, "DEBUG_MASTER_PASSWORD", "master", raising=False)

    active_user = SimpleNamespace(user_id=5, user_name="active", is_active=True)
    with pytest.raises(Exception):
        await AuthService.swagger_login(FakeDB(active_user), "wx-id", "master", None)

    monkeypatch.setattr(settings, "DEBUG", True, raising=False)
    monkeypatch.setattr(settings, "DEBUG_SKIP_PASSWORD_CHECK", True, raising=False)
    inactive_user = SimpleNamespace(user_id=6, user_name="inactive", is_active=False, last_login_ip=None, last_login_time=None)
    with pytest.raises(Exception):
        await AuthService.swagger_login(FakeDB(inactive_user), "wx-id", "master", None)


@pytest.mark.asyncio
async def test_verify_email_code_missing_and_user_missing_paths(monkeypatch):
    class RedisStub:
        def __init__(self):
            self.store = {}
            self.get = AsyncMock(side_effect=self._get)
            self.set = AsyncMock(side_effect=self._set)
            self.delete = AsyncMock(side_effect=self._delete)

        async def _get(self, key):
            return self.store.get(key)

        async def _set(self, key, value, ex=None):
            self.store[key] = value

        async def _delete(self, key):
            self.store.pop(key, None)

    redis_stub = RedisStub()
    monkeypatch.setattr("app.services.auth_service.redis", redis_stub)

    class VerifyDB:
        def __init__(self, user=None):
            self.user = user

        async def execute(self, stmt):
            return FakeResult(items=[self.user] if self.user else [])

        async def commit(self):
            pass

        async def refresh(self, user):
            pass

    with pytest.raises(Exception):
        await AuthService.verify_email_code(VerifyDB(), 1, "nobody@bjtu.edu.cn", "123456")

    monkeypatch.setattr("app.services.auth_service.get_now", lambda: SimpleNamespace(timestamp=lambda: 100))
    await redis_stub.set("email_verify_code:nobody@bjtu.edu.cn", json.dumps({"code": "123456", "timestamp": 100, "attempts": 0, "user_id": 1}, ensure_ascii=False))
    with pytest.raises(Exception):
        await AuthService.verify_email_code(VerifyDB(), 1, "nobody@bjtu.edu.cn", "123456")


@pytest.mark.asyncio
async def test_persist_user_token_concurrent_logins_keep_single_active_token(monkeypatch):
    class ConcurrentRedisStub:
        def __init__(self):
            self.store = {
                "user_token:1": "old-token",
                "token:old-token": "1",
            }

        async def get(self, key):
            if key == "user_token:1":
                snapshot = self.store.get(key)
                await asyncio.sleep(0.01)
                return snapshot
            return self.store.get(key)

        async def set(self, key, value, ex=None, nx=False):
            if nx and key in self.store:
                return None
            self.store[key] = str(value)
            return True

        async def delete(self, *keys):
            removed = 0
            for key in keys:
                if key in self.store:
                    removed += 1
                    self.store.pop(key, None)
            return removed

        async def eval(self, script, numkeys, *args):
            lock_key = args[0]
            lock_token = args[1]
            if self.store.get(lock_key) == lock_token:
                self.store.pop(lock_key, None)
                return 1
            return 0

    redis_stub = ConcurrentRedisStub()
    monkeypatch.setattr("app.services.auth_service.redis", redis_stub)

    await asyncio.gather(
        AuthService._persist_user_token(1, "token-a"),
        AuthService._persist_user_token(1, "token-b"),
    )

    active_token = redis_stub.store.get("user_token:1")
    assert active_token in {"token-a", "token-b"}
    assert redis_stub.store.get(f"token:{active_token}") == "1"
    stale_tokens = {"token-a", "token-b"} - {active_token}
    for stale_token in stale_tokens:
        assert f"token:{stale_token}" not in redis_stub.store
    assert "token:old-token" not in redis_stub.store


@pytest.mark.asyncio
async def test_send_email_verify_code_concurrent_requests_only_one_succeeds(monkeypatch):
    class ConcurrentRedisStub:
        def __init__(self):
            self.store = {}

        async def get(self, key):
            if key == "email_verify_rate:race@bjtu.edu.cn":
                snapshot = self.store.get(key)
                await asyncio.sleep(0.01)
                return snapshot
            return self.store.get(key)

        async def set(self, key, value, ex=None, nx=False):
            if nx and key in self.store:
                return None
            self.store[key] = value
            return True

        async def delete(self, *keys):
            for key in keys:
                self.store.pop(key, None)

    class EmailDB:
        async def execute(self, stmt):
            return FakeResult(items=[])

    redis_stub = ConcurrentRedisStub()
    monkeypatch.setattr("app.services.auth_service.redis", redis_stub)
    monkeypatch.setattr("app.services.auth_service.send_email", lambda email, subject, body: True)

    results = await asyncio.gather(
        AuthService.send_email_verify_code(EmailDB(), 1, "race@bjtu.edu.cn"),
        AuthService.send_email_verify_code(EmailDB(), 1, "race@bjtu.edu.cn"),
        return_exceptions=True,
    )

    success_count = sum(1 for item in results if isinstance(item, dict))
    error_count = sum(1 for item in results if isinstance(item, Exception))
    assert success_count == 1
    assert error_count == 1
    assert redis_stub.store.get("email_verify_rate:race@bjtu.edu.cn") == "1"


@pytest.mark.asyncio
async def test_verify_admin_login_code_concurrent_replay_only_allows_once(monkeypatch):
    class ConcurrentRedisStub:
        def __init__(self):
            self.store = {
                "admin:login:code:admin@bjtu.edu.cn": "123456",
                "admin:login:lock:admin@bjtu.edu.cn": "1",
            }

        async def get(self, key):
            if key == "admin:login:code:admin@bjtu.edu.cn":
                snapshot = self.store.get(key)
                await asyncio.sleep(0.01)
                return snapshot
            return self.store.get(key)

        async def set(self, key, value, ex=None, nx=False):
            if nx and key in self.store:
                return None
            self.store[key] = str(value)
            return True

        async def delete(self, *keys):
            removed = 0
            for key in keys:
                if key in self.store:
                    removed += 1
                    self.store.pop(key, None)
            return removed

        async def eval(self, script, numkeys, *args):
            lock_key = args[0]
            lock_token = args[1]
            if self.store.get(lock_key) == lock_token:
                self.store.pop(lock_key, None)
                return 1
            return 0

    class AdminDB:
        async def execute(self, stmt):
            return FakeResult(items=[SimpleNamespace(user_id=9, user_name="admin", is_admin=True, user_type=None)])

    redis_stub = ConcurrentRedisStub()
    monkeypatch.setattr("app.services.auth_service.create_access_token", lambda payload: f"token-{payload['sub']}")
    persist_mock = AsyncMock()
    monkeypatch.setattr(AuthService, "_persist_user_token", persist_mock)

    results = await asyncio.gather(
        AuthService.verify_admin_login_code(AdminDB(), redis_stub, "admin@bjtu.edu.cn", "123456"),
        AuthService.verify_admin_login_code(AdminDB(), redis_stub, "admin@bjtu.edu.cn", "123456"),
        return_exceptions=True,
    )

    success_count = sum(1 for item in results if isinstance(item, dict))
    error_count = sum(1 for item in results if isinstance(item, Exception))
    assert success_count == 1
    assert error_count == 1
    persist_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_cleanup_stale_token_keys_only_removes_non_current_tokens():
    class CleanupRedisStub:
        def __init__(self):
            self._data = {
                "user_token:1": "token-live-1",
                "token:token-live-1": "1",
                "token:token-stale-1": "1",
                "user_token:2": "token-live-2",
                "token:token-live-2": "2",
                "token:token-orphan": "3",
            }

        async def get(self, key):
            return self._data.get(key)

        async def set(self, key, value, ex=None, nx=False):
            if nx and key in self._data:
                return None
            self._data[key] = str(value)
            return True

        async def delete(self, *keys):
            removed = 0
            for key in keys:
                if key in self._data:
                    removed += 1
                    self._data.pop(key, None)
            return removed

        async def eval(self, script, numkeys, *args):
            lock_key = args[0]
            lock_token = args[1]
            if self._data.get(lock_key) == lock_token:
                self._data.pop(lock_key, None)
                return 1
            return 0

    redis_stub = CleanupRedisStub()

    result = await AuthService.cleanup_stale_token_keys(redis_stub)

    assert result == {
        "scanned": 4,
        "deleted": 2,
        "kept": 2,
        "skipped_locked": 0,
        "missing_mapping": 0,
    }
    assert redis_stub._data["user_token:1"] == "token-live-1"
    assert redis_stub._data["token:token-live-1"] == "1"
    assert redis_stub._data["user_token:2"] == "token-live-2"
    assert redis_stub._data["token:token-live-2"] == "2"
    assert "token:token-stale-1" not in redis_stub._data
    assert "token:token-orphan" not in redis_stub._data


@pytest.mark.asyncio
async def test_cleanup_stale_token_keys_skips_busy_user_lock():
    class LockedCleanupRedisStub:
        def __init__(self):
            self._data = {
                "user_token:1": "token-live-1",
                "token:token-live-1": "1",
                "token:token-stale-1": "1",
                "lock:user_token:1": "occupied",
            }

        async def get(self, key):
            return self._data.get(key)

        async def set(self, key, value, ex=None, nx=False):
            if nx and key in self._data:
                return None
            self._data[key] = str(value)
            return True

        async def delete(self, *keys):
            removed = 0
            for key in keys:
                if key in self._data:
                    removed += 1
                    self._data.pop(key, None)
            return removed

        async def eval(self, script, numkeys, *args):
            lock_key = args[0]
            lock_token = args[1]
            if self._data.get(lock_key) == lock_token:
                self._data.pop(lock_key, None)
                return 1
            return 0

    redis_stub = LockedCleanupRedisStub()

    result = await AuthService.cleanup_stale_token_keys(redis_stub)

    assert result == {
        "scanned": 2,
        "deleted": 0,
        "kept": 0,
        "skipped_locked": 2,
        "missing_mapping": 0,
    }
    assert redis_stub._data["token:token-live-1"] == "1"
    assert redis_stub._data["token:token-stale-1"] == "1"
