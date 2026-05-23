"""
DB 模块 - 统一导出数据库引擎、Session 和所有模型

包含：
  - base: 数据库配置、引擎、Session 工厂和所有 ORM 模型
"""

from app.db.base import (
    Base,
    engine,
    AsyncSessionLocal,
    redis,
    get_db,
    get_redis,
)
# 模型导出（从 app.models 包导入）
from app.models import (
    Attachment,
    AttachmentTargetType,
    Category,
    Comment,
    ChatMessage,
    ChatSession,
    CreditLog,
    Goods,
    GoodsCondition,
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

__all__ = [
    "Base",
    "engine",
    "AsyncSessionLocal",
    "redis",
    "get_db",
    "get_redis",
    # 模型
    "Attachment",
    "AttachmentTargetType",
    "Category",
    "Comment",
    "ChatMessage",
    "ChatSession",
    "CreditLog",
    "Goods",
    "GoodsCondition",
    "ItemType",
    "Order",
    "OrderStatus",
    "Post",
    "PostStatus",
    "SexEnum",
    "TargetType",
    "User",
    "UserAccessLog",
    "UserType",
    "parse_user_type",
]
