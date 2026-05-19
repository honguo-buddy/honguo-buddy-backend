"""集成测试专属 fixtures：动态独立数据库、HTTP 客户端和测试用户。"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from dotenv import dotenv_values, load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

load_dotenv(ROOT_DIR / ".env", override=False)
_DOTENV_VALUES = dotenv_values(ROOT_DIR / ".env")


@dataclass(slots=True)
class _DatabaseRuntime:
    tenant_db_name: str
    admin_url: URL
    tenant_async_url: URL
    created_database: bool


_schema_lock: asyncio.Lock | None = None
_schema_ready = False
_runtime: _DatabaseRuntime | None = None


def _sanitize_db_suffix(raw_value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z_]+", "_", raw_value.strip())
    cleaned = cleaned.strip("_")
    return cleaned or "0"


def _build_tenant_db_name() -> str:
    build_number = os.environ.get("BUILD_NUMBER", "").strip()
    if build_number:
        return f"testdb_build_{_sanitize_db_suffix(build_number)}"
    return f"testdb_local_{uuid.uuid4().hex[:8]}"


def _make_admin_url(base_url: URL) -> URL:
    return URL.create(
        drivername="mysql+pymysql",
        username=base_url.username,
        password=base_url.password,
        host=base_url.host,
        port=base_url.port,
        query=base_url.query,
    )


async def _pick_accessible_base_url(tenant_db_name: str) -> URL:
    candidate_urls = []
    business_url_text = (
        os.environ.get("DATABASE_URL")
        or _DOTENV_VALUES.get("DATABASE_URL")
    )
    if business_url_text:
        candidate_urls.append(make_url(business_url_text))

    test_url_text = (
        os.environ.get("TEST_DATABASE_URL")
        or os.environ.get("Test_DATABASE_URL")
        or _DOTENV_VALUES.get("TEST_DATABASE_URL")
        or _DOTENV_VALUES.get("Test_DATABASE_URL")
    )
    if test_url_text:
        candidate_urls.append(make_url(test_url_text))

    last_error: Exception | None = None
    for candidate in candidate_urls:
        admin_url = _make_admin_url(candidate)
        try:
            probe_engine = create_engine(
                admin_url,
                isolation_level="AUTOCOMMIT",
                pool_pre_ping=True,
            )
            probe_db_name = f"{tenant_db_name}_probe_{uuid.uuid4().hex[:8]}"
            with probe_engine.connect() as connection:
                connection.execute(text(f"CREATE DATABASE IF NOT EXISTS `{probe_db_name}` CHARACTER SET utf8mb4"))

            probe_async_url = candidate.set(drivername="mysql+aiomysql", database=probe_db_name)
            probe_async_engine = create_async_engine(
                probe_async_url,
                echo=False,
                pool_pre_ping=True,
                connect_args={"connect_timeout": 10},
            )
            try:
                async with probe_async_engine.connect() as connection:
                    await connection.execute(text("SELECT 1"))
            finally:
                await probe_async_engine.dispose()

            with probe_engine.connect() as connection:
                connection.execute(text(f"DROP DATABASE IF EXISTS `{probe_db_name}`"))
            probe_engine.dispose()
            return candidate
        except Exception as exc:
            last_error = exc

    raise RuntimeError("无法连接任何可用的 MySQL 数据源") from last_error


def _force_native_password_auth(base_url: URL) -> URL:
    query = dict(base_url.query)
    query["auth_plugin"] = "mysql_native_password"
    return base_url.set(query=query)


async def _build_database_urls(tenant_db_name: str) -> tuple[URL, URL, URL]:
    tenant_base_url = await _pick_accessible_base_url(tenant_db_name)
    admin_url = _make_admin_url(tenant_base_url)
    tenant_async_url = _force_native_password_auth(
        tenant_base_url.set(drivername="mysql+aiomysql", database=tenant_db_name)
    )
    return admin_url, tenant_base_url, tenant_async_url


async def _ensure_schema(engine: AsyncEngine, *, reset_existing: bool = False) -> None:
    global _schema_lock, _schema_ready
    if _schema_ready:
        return
    if _schema_lock is None:
        _schema_lock = asyncio.Lock()

    async with _schema_lock:
        if _schema_ready:
            return
        from app.db import Base

        async with engine.begin() as conn:
            if reset_existing:
                await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        _schema_ready = True


@pytest_asyncio.fixture(scope="session", autouse=True)
async def managed_test_database() -> AsyncGenerator[_DatabaseRuntime, None]:
    """会话级独立测试库：启动时建库，结束时销毁。"""
    global _runtime

    from app.core import settings

    tenant_db_name = _build_tenant_db_name()
    admin_url, tenant_base_url, tenant_async_url = await _build_database_urls(tenant_db_name)
    admin_engine = create_engine(
        admin_url,
        isolation_level="AUTOCOMMIT",
        pool_pre_ping=True,
    )
    tenant_engine = None
    created_database = False

    try:
        with admin_engine.connect() as connection:
            try:
                connection.execute(text(f"CREATE DATABASE IF NOT EXISTS `{tenant_db_name}` CHARACTER SET utf8mb4"))
                created_database = True
            except Exception:
                tenant_db_name = tenant_base_url.database or tenant_db_name
                tenant_async_url = tenant_base_url.set(drivername="mysql+aiomysql")
                created_database = False

        _runtime = _DatabaseRuntime(
            tenant_db_name=tenant_db_name,
            admin_url=admin_url,
            tenant_async_url=tenant_async_url,
            created_database=created_database,
        )
        settings.DATABASE_URL = str(tenant_async_url)
        os.environ["TEST_DATABASE_URL"] = str(tenant_async_url)

        tenant_engine = create_async_engine(
            tenant_async_url,
            echo=False,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            connect_args={"connect_timeout": 10},
        )

        await _ensure_schema(tenant_engine, reset_existing=not created_database)
        yield _runtime
    finally:
        try:
            if tenant_engine is not None:
                await tenant_engine.dispose()
        finally:
            try:
                if created_database:
                    with admin_engine.connect() as connection:
                        connection.execute(text(f"DROP DATABASE IF EXISTS `{tenant_db_name}`"))
            finally:
                admin_engine.dispose()
                _runtime = None


@pytest.fixture(scope="session")
def test_db_url(managed_test_database: _DatabaseRuntime) -> URL:
    """【优化】直接返回原生的 SQLAlchemy URL 对象，拒绝 str 强转引发的参数丢失"""
    return managed_test_database.tenant_async_url

@pytest_asyncio.fixture
async def test_engine(test_db_url: URL) -> AsyncGenerator[AsyncEngine, None]:
    """【优化】每个测试独立的引擎，通过 connect_args 强行死锁 native_password"""
    engine = create_async_engine(
        test_db_url,
        echo=False,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        connect_args={
            "connect_timeout": 10,
            "auth_plugin": "mysql_native_password" 
        },
    )
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """为每个测试提供独立 Session，业务代码仍可按需要提交。"""
    async with test_engine.connect() as connection:
        transaction = await connection.begin()
        session_factory = sessionmaker(
            bind=connection,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        async with session_factory() as session:
            yield session
        await transaction.rollback()


@pytest_asyncio.fixture
async def app(test_engine, test_db_url, monkeypatch):
    """切换到当前会话的测试数据库后再导入 FastAPI 应用。"""
    from app.core import settings
    from app.main import app as fastapi_app
    import app.db as app_db
    import app.db.base as app_db_base
    import app.main as app_main

    settings.DATABASE_URL = test_db_url
    test_session_factory = sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

    monkeypatch.setattr(app_db, "engine", test_engine, raising=False)
    monkeypatch.setattr(app_db_base, "engine", test_engine, raising=False)
    monkeypatch.setattr(app_main, "engine", test_engine, raising=False)
    monkeypatch.setattr(app_db, "AsyncSessionLocal", test_session_factory, raising=False)
    monkeypatch.setattr(app_db_base, "AsyncSessionLocal", test_session_factory, raising=False)

    @asynccontextmanager
    async def _noop_lifespan(_app):
        yield

    fastapi_app.router.lifespan_context = _noop_lifespan
    return fastapi_app


@pytest_asyncio.fixture
async def client(app, db_session) -> AsyncGenerator[AsyncClient, None]:
    """为每个测试提供 HTTP 客户端，并将数据库依赖指向当前 Session。"""
    from app.db import get_db

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def test_user(db_session):
    """创建普通测试用户。"""
    from app.models import SexEnum, User, UserType

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
async def test_admin_user(db_session):
    """创建管理员测试用户。"""
    from app.models import SexEnum, User, UserType

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
    """为测试用户生成 Token。"""
    from app.core import create_access_token

    return create_access_token(
        {
            "sub": str(test_user.user_id),
            "user_name": test_user.user_name,
            "user_type": test_user.user_type.value,
        }
    )


@pytest_asyncio.fixture
async def test_admin_token(test_admin_user) -> str:
    """为管理员用户生成 Token。"""
    from app.core import create_access_token

    return create_access_token(
        {
            "sub": str(test_admin_user.user_id),
            "user_name": test_admin_user.user_name,
            "user_type": test_admin_user.user_type.value,
        }
    )