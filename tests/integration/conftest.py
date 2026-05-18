"""集成测试专属 fixtures - 优先使用 Testcontainers MySQL（需要 Docker），回退到修复的 SQLite"""

from collections.abc import AsyncGenerator
from pathlib import Path
import os
import sys

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
	sys.path.insert(0, str(ROOT_DIR))

from app.core import create_access_token, settings
from app.db import Base, get_db
from app.main import app
from app.models import SexEnum, User, UserType

# =========================================================================
# Docker/Testcontainers 检测
# =========================================================================

_DOCKER_AVAILABLE = False
_MYSQL_CONTAINER = None


def _check_docker_available():
	"""检查 Docker 是否可用"""
	try:
		from testcontainers.core.docker_client import DockerClient
		docker_client = DockerClient()
		docker_client.client.ping()
		return True
	except Exception:
		return False


_DOCKER_AVAILABLE = _check_docker_available()

if _DOCKER_AVAILABLE:
	from testcontainers.mysql import MySqlContainer

	@pytest_asyncio.fixture(scope="session")
	async def mysql_container():
		"""启动 MySQL 容器（会话级生命周期）- 仅当 Docker 可用时"""
		print("\n🚀 Docker 可用，启动 MySQL 8.0 Testcontainers...")
		container = MySqlContainer("mysql:8.0.36", username="testuser", password="testpass123", dbname="testdb")
		container.start()
		yield container
		print("\n🛑 停止 MySQL 8.0 容器...")
		container.stop()

	@pytest_asyncio.fixture(scope="session")
	async def test_db_url(mysql_container) -> str:
		"""获取测试数据库 URL（转换为 aiomysql）"""
		sync_url = mysql_container.get_connection_url()  # mysql+pymysql://...
		# 转换为 aiomysql 异步驱动
		async_url = sync_url.replace("mysql+pymysql://", "mysql+aiomysql://")
		print(f"\n📦 测试数据库 URL: {async_url}")
		return async_url

else:
	print("\n⚠️  Docker 不可用，使用修复后的 SQLite（支持 BigInteger 自增）")

	@pytest_asyncio.fixture(scope="session")
	async def test_db_url() -> str:
		"""使用修复后的 SQLite URL（当 Docker 不可用时）"""
		# 关键修复：禁用外键并设置 SQLite 兼容性选项
		db_url = "sqlite+aiosqlite:///:memory:?timeout=20&check_same_thread=False"
		print(f"\n📦 测试数据库 URL: {db_url}")
		return db_url


# =========================================================================
# 数据库引擎 Fixture（每个测试独立创建）
# =========================================================================


@pytest_asyncio.fixture
async def test_engine(test_db_url):
	"""创建测试数据库引擎并建表（每个测试独立）"""
	if _DOCKER_AVAILABLE:
		engine = create_async_engine(
			test_db_url,
			echo=False,
			pool_pre_ping=True,
			pool_size=5,
			max_overflow=10,
		)
	else:
		# SQLite 特殊配置
		engine = create_async_engine(
			test_db_url,
			echo=False,
			connect_args={"timeout": 20, "check_same_thread": False},
			# 禁用行检查以支持 BigInteger 自增
			pool_pre_ping=False,
		)

	# 建表
	print("\n📋 初始化数据库表结构...")
	async with engine.begin() as conn:
		await conn.run_sync(Base.metadata.create_all)

	yield engine

	# 清理
	print("\n🧹 清理数据库...")
	async with engine.begin() as conn:
		await conn.run_sync(Base.metadata.drop_all)

	await engine.dispose()


# =========================================================================
# 测试函数级 Fixtures（每个测试都有独立会话和回滚）
# =========================================================================


@pytest_asyncio.fixture
async def db_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
	"""为每个测试提供独立的数据库会话（自动回滚）"""
	test_session_maker = sessionmaker(
		bind=test_engine,
		class_=AsyncSession,
		expire_on_commit=False,
	)

	async with test_session_maker() as session:
		yield session
		await session.rollback()


@pytest_asyncio.fixture
async def client(db_session) -> AsyncGenerator[AsyncClient, None]:
	"""为每个测试提供 HTTP 客户端，内部使用测试数据库"""

	async def override_get_db():
		yield db_session

	app.dependency_overrides[get_db] = override_get_db

	transport = ASGITransport(app=app)
	async with AsyncClient(transport=transport, base_url="http://test") as ac:
		yield ac

	app.dependency_overrides.clear()


# =========================================================================
# 测试用户 Fixtures
# =========================================================================


@pytest_asyncio.fixture
async def test_user(db_session) -> User:
	"""创建一个普通测试用户"""
	user = User(
		user_id=1001,
		user_uuid=b"1234567890123456",
		user_name="testuser",
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
	"""创建一个管理员测试用户"""
	user = User(
		user_id=1002,
		user_uuid=b"2345678901234567",
		user_name="adminuser",
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
	"""为测试用户生成 Token"""
	return create_access_token(
		{
			"sub": str(test_user.user_id),
			"user_name": test_user.user_name,
			"user_type": test_user.user_type.value,
		}
	)


@pytest_asyncio.fixture
async def authenticated_client(client, fake_redis, test_user, test_user_token):
	"""创建已认证的 HTTP 客户端"""
	await fake_redis.set(f"token:{test_user_token}", str(test_user.user_id))
	await fake_redis.set(f"user_token:{test_user.user_id}", test_user_token)
	yield client


@pytest_asyncio.fixture
async def test_admin_token(test_admin_user) -> str:
	"""为管理员用户生成 Token"""
	return create_access_token(
		{
			"sub": str(test_admin_user.user_id),
			"user_name": test_admin_user.user_name,
			"user_type": test_admin_user.user_type.value,
		}
	)


# =========================================================================
# Mock Redis Fixture
# =========================================================================


class FakeRedis:
	"""模拟 Redis 用于测试（无需真实 Redis 容器）"""

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


@pytest.fixture(scope="function")
def fake_redis() -> FakeRedis:
	"""为每个测试提供模拟 Redis"""
	return FakeRedis()


@pytest.fixture(autouse=True)
def patch_test_settings(monkeypatch, fake_redis):
	"""自动为所有测试注入测试配置和模拟 Redis"""
	settings.DEBUG = True
	settings.DEBUG_MASTER_PASSWORD = "test-master-password"
	settings.DEBUG_SKIP_PASSWORD_CHECK = False
	settings.WX_APP_ID = "test-wx-app-id"
	settings.WX_APP_SECRET = "test-wx-app-secret"

	# 注入模拟 Redis 到所有需要的模块
	monkeypatch.setattr("app.db.redis", fake_redis, raising=False)
	monkeypatch.setattr("app.api.auth.redis", fake_redis, raising=False)
	monkeypatch.setattr("app.services.auth_service.redis", fake_redis, raising=False)
	monkeypatch.setattr("app.core.security.redis", fake_redis, raising=False)
	monkeypatch.setattr("app.core.log_middleware.redis", fake_redis, raising=False)

	async def noop_save_log_to_db(log_data: dict):
		return None

	monkeypatch.setattr("app.core.log_middleware.save_log_to_db", noop_save_log_to_db, raising=False)
