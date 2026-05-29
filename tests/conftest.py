"""测试全局配置：公共环境、Redis 替身与共享测试夹具。"""

from pathlib import Path
import os
import sys

import pytest



os.environ.setdefault("DEBUG", "true")
os.environ.setdefault("DEBUG_MASTER_PASSWORD", "test-master-password")
os.environ.setdefault("DEBUG_SKIP_PASSWORD_CHECK", "false")
os.environ.setdefault("DATABASE_URL", "mysql+aiomysql://placeholder:placeholder@127.0.0.1:3306/placeholder")
os.environ.setdefault("EMAIL_FROM", "test@example.com")
os.environ.setdefault("SMTP_SERVER", "smtp.example.com")
os.environ.setdefault("SMTP_PORT", "465")
os.environ.setdefault("SMTP_USER", "test@example.com")
os.environ.setdefault("SMTP_PASSWORD", "test-password")
os.environ.setdefault("REDIS_HOST", "127.0.0.1")
os.environ.setdefault("REDIS_PORT", "6379")
os.environ.setdefault("REDIS_PASSWORD", "")
os.environ.setdefault("WX_APP_ID", "test-wx-app-id")
os.environ.setdefault("WX_APP_SECRET", "test-wx-app-secret")


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


class FakeRedis:
    """测试用 Redis 替身。"""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self._zsets: dict[str, dict[str, float]] = {}

    async def get(self, key: str):
        return self._data.get(key)

    async def set(self, key: str, value, ex=None):
        self._data[key] = str(value)
        return True

    async def setex(self, key: str, ex, value):
        self._data[key] = str(value)
        return True
    
    async def ttl(self, name: str) -> int:
        #默认返回 -2 (代表 key 不存在/已过期)，让测试顺畅通过冷却期判定
        return -2
    
    async def delete(self, *keys):
        for key in keys:
            self._data.pop(key, None)
            self._zsets.pop(key, None)
        return len(keys)

    async def exists(self, key: str):
        return 1 if key in self._data else 0

    async def ping(self):
        return True

    async def zadd(self, key: str, mapping: dict[str, float]):
        zset = self._zsets.setdefault(key, {})
        added = 0
        for member, score in mapping.items():
            if member not in zset:
                added += 1
            zset[str(member)] = float(score)
        return added

    async def zrangebyscore(self, key: str, min=0, max=0, start: int = 0, num: int | None = None):
        zset = self._zsets.get(key, {})

        def _normalize(value):
            if value in ("-inf", b"-inf"):
                return float("-inf")
            if value in ("+inf", "+infinity", b"+inf", b"+infinity"):
                return float("inf")
            return float(value)

        lower = _normalize(min)
        upper = _normalize(max)
        ordered = [member for member, score in sorted(zset.items(), key=lambda item: (item[1], item[0])) if lower <= score <= upper]
        if start:
            ordered = ordered[start:]
        if num is not None:
            ordered = ordered[:num]
        return ordered

    async def zremrangebyrank(self, key: str, start: int, end: int):
        zset = self._zsets.get(key, {})
        if not zset:
            return 0
        ordered = sorted(zset.items(), key=lambda item: (item[1], item[0]))
        if end < 0:
            end = len(ordered) + end
        to_remove = [member for idx, (member, _) in enumerate(ordered) if idx >= start and idx <= end]
        for member in to_remove:
            zset.pop(member, None)
        return len(to_remove)

    async def zcard(self, key: str):
        return len(self._zsets.get(key, {}))

    async def zrevrange(self, key: str, start: int, end: int, withscores: bool = False):
        zset = self._zsets.get(key, {})
        ordered = sorted(zset.items(), key=lambda item: (-item[1], item[0]))
        sliced = ordered[start:end + 1 if end is not None else None]
        if withscores:
            return [(member, score) for member, score in sliced]
        return [member for member, _ in sliced]

    async def expire(self, key: str, seconds: int):
        return True

    async def zrem(self, key: str, *members):
        zset = self._zsets.get(key, {})
        removed = 0
        for member in members:
            member_key = str(member)
            if member_key in zset:
                removed += 1
                del zset[member_key]
        return removed

    async def zscore(self, key: str, member: str):
        zset = self._zsets.get(key, {})
        return zset.get(str(member))

    async def aclose(self):
        return None


@pytest.fixture(autouse=True)
def patch_test_settings(monkeypatch, fake_redis):
    """为测试注入统一配置与 Redis 替身。"""

    from app.core import settings

    settings.DEBUG = True
    settings.DEBUG_MASTER_PASSWORD = "test-master-password"
    settings.DEBUG_SKIP_PASSWORD_CHECK = False
    settings.WX_APP_ID = "test-wx-app-id"
    settings.WX_APP_SECRET = "test-wx-app-secret"

    monkeypatch.setattr("app.db.redis", fake_redis, raising=False)
    monkeypatch.setattr("app.db.base.redis", fake_redis, raising=False)
    monkeypatch.setattr("app.main.redis", fake_redis, raising=False)
    monkeypatch.setattr("app.api.auth.redis", fake_redis, raising=False)
    monkeypatch.setattr("app.api.post.redis", fake_redis, raising=False)
    monkeypatch.setattr("app.api.user.redis", fake_redis, raising=False)
    monkeypatch.setattr("app.services.auth_service.redis", fake_redis, raising=False)
    try:
        monkeypatch.setattr("app.services.sms_service.redis", fake_redis, raising=False)
    except ImportError:
        pass
    monkeypatch.setattr("app.core.security.redis", fake_redis, raising=False)
    monkeypatch.setattr("app.core.log_middleware.redis", fake_redis, raising=False)

    async def noop_save_log_to_db(log_data: dict):
        return None

    monkeypatch.setattr("app.core.log_middleware.save_log_to_db", noop_save_log_to_db, raising=False)


@pytest.fixture
def fake_redis() -> FakeRedis:
    """单独暴露一个可复用的 Redis 替身。"""

    return FakeRedis()
