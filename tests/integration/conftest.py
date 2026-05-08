"""集成测试专属 python fixtures。"""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.core import create_access_token, settings
from app.db import Base, get_db
from app.main import app
from app.models import SexEnum, User, UserType

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


class FakeRedis:
	def __init__(self):
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

# 函数作用域的 fixture，测试函数执行前创建，执行后销毁
@pytest.fixture(scope="function")
def fake_redis() -> FakeRedis:
	return FakeRedis()


@pytest.fixture(autouse=True)
def patch_test_settings(monkeypatch, fake_redis):
	settings.DEBUG = True
	settings.DEBUG_MASTER_PASSWORD = "test-master-password"
	settings.DEBUG_SKIP_PASSWORD_CHECK = False
	settings.WX_APP_ID = "test-wx-app-id"
	settings.WX_APP_SECRET = "test-wx-app-secret"

	monkeypatch.setattr("app.db.redis", fake_redis, raising=False)
	monkeypatch.setattr("app.api.auth.redis", fake_redis, raising=False)
	monkeypatch.setattr("app.services.auth_service.redis", fake_redis, raising=False)
	monkeypatch.setattr("app.core.security.redis", fake_redis, raising=False)
	monkeypatch.setattr("app.core.log_middleware.redis", fake_redis, raising=False)

	async def noop_save_log_to_db(log_data: dict):
		return None

	monkeypatch.setattr("app.core.log_middleware.save_log_to_db", noop_save_log_to_db, raising=False)


@pytest_asyncio.fixture(scope="function")
async def test_engine():
	engine = create_async_engine(
		TEST_DATABASE_URL,
		echo=False,
		connect_args={"check_same_thread": False},
	)

	async with engine.begin() as conn:
		await conn.run_sync(Base.metadata.create_all)

	yield engine

	async with engine.begin() as conn:
		await conn.run_sync(Base.metadata.drop_all)

	await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
	test_session_local = sessionmaker(
		test_engine,
		class_=AsyncSession,
		expire_on_commit=False,
	)

	async with test_session_local() as session:
		yield session
		await session.rollback()


@pytest_asyncio.fixture
async def client(db_session) -> AsyncGenerator[AsyncClient, None]:
	async def override_get_db():
		yield db_session

	app.dependency_overrides[get_db] = override_get_db

	transport = ASGITransport(app=app)
	async with AsyncClient(transport=transport, base_url="http://test") as ac:
		yield ac

	app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def test_user(db_session) -> User:
	user = User(
		user_id=1001,
		user_uuid=b"1234567890123456",
		user_name="测试用户",
		email="test@example.com",
		phonenumber="13800138000",
		sex=SexEnum.UNKNOWN,
		user_type=UserType.USER,
		is_verified=False,
		is_active=True,
		is_admin=False,
		is_deleted=False,
		credit_score=100,
		wechat_openid="test_openid_12345",
	)
	db_session.add(user)
	await db_session.flush()
	return user


@pytest_asyncio.fixture
async def test_admin_user(db_session) -> User:
	user = User(
		user_id=1002,
		user_uuid=b"2345678901234567",
		user_name="管理员用户",
		email="admin@example.com",
		phonenumber="13900139000",
		sex=SexEnum.UNKNOWN,
		user_type=UserType.ADMIN,
		is_verified=True,
		is_active=True,
		is_admin=True,
		is_deleted=False,
		credit_score=100,
		wechat_openid="admin_openid_12345",
	)
	db_session.add(user)
	await db_session.flush()
	return user


@pytest_asyncio.fixture
async def test_user_token(test_user) -> str:
	return create_access_token(
		{
			"sub": str(test_user.user_id),
			"user_name": test_user.user_name,
			"user_type": test_user.user_type.value,
		}
	)


@pytest_asyncio.fixture
async def authenticated_client(client, fake_redis, test_user, test_user_token):
	await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
	await fake_redis.set(f"user_token:{test_user.user_id}", test_user_token)
	yield client


@pytest_asyncio.fixture
async def test_admin_token(test_admin_user) -> str:
	return create_access_token(
		{
			"sub": str(test_admin_user.user_id),
			"user_name": test_admin_user.user_name,
			"user_type": test_admin_user.user_type.value,
		}
	)
