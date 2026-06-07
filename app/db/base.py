from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from redis.asyncio import Redis

from app.core import settings

#异步引擎连接数据库(echo表输出日志)
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_pre_ping=False,        # 封死 aiomysql.ping() 签名报错
    pool_recycle=3600,          # 连接回收时间（秒），避免使用超时的连接
    pool_size=10,               # 连接池大小
    max_overflow=20,            # 超出 pool_size 后最多再创建的连接数
    pool_timeout=30,            # 获取连接的超时时间（秒）
    connect_args={
        "connect_timeout": 10 ,  # MySQL 连接超时（秒）
        "autocommit": True # 确保自动提交
    }
)

#事务处理
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

#全局Base
from app.db_base import Base

#Redis数据库连接（使用配置）
redis = Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, decode_responses=True, password=settings.REDIS_PASSWORD)

# 统一导出模型，避免硬编码到单个模型文件
from app.models import (  # noqa: E402,F401
    Attachment,
    Category,
    Comment,
    ChatMessage,
    ChatSession,
    CreditLog,
    Goods,
    ItemType,
    Order,
    OrderStatus,
    Post,
    PostStatus,
    SexEnum,
    TargetType,
    User,
    UserAccessLog,
    UserType,
    parse_user_type,
)

#异步获取事务函数
async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
            
async def get_redis():
    """依赖注入用的 Redis 客户端。返回模块级的 `redis` 实例，避免在每次请求时创建/关闭连接。

    注意：不要在此处关闭全局客户端，否则会影响其他使用者。
    """
    yield redis
 