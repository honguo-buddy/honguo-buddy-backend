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

    async def get(self, key: str):
        return self._data.get(key)

    async def set(self, key: str, value, ex=None):
        self._data[key] = str(value)
        return True

    async def setex(self, key: str, ex, value):
        self._data[key] = str(value)
        return True

    async def delete(self, *keys):
        for key in keys:
            self._data.pop(key, None)
        return len(keys)

    async def exists(self, key: str):
        return 1 if key in self._data else 0

    async def ping(self):
        return True

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
    monkeypatch.setattr("app.main.redis", fake_redis, raising=False)
    monkeypatch.setattr("app.api.auth.redis", fake_redis, raising=False)
    monkeypatch.setattr("app.services.auth_service.redis", fake_redis, raising=False)
    monkeypatch.setattr("app.core.security.redis", fake_redis, raising=False)
    monkeypatch.setattr("app.core.log_middleware.redis", fake_redis, raising=False)

    async def noop_save_log_to_db(log_data: dict):
        return None

    monkeypatch.setattr("app.core.log_middleware.save_log_to_db", noop_save_log_to_db, raising=False)


@pytest.fixture
def fake_redis() -> FakeRedis:
    """单独暴露一个可复用的 Redis 替身。"""

    return FakeRedis()
