"""API 模块统一入口。"""

from app.api import auth
from app.api.auth import get_current_user
from app.api.auth import get_current_user_optional
from app.api import user
from app.api import attachment
from app.api import category
from app.api import post
from app.api import order
from app.api import comment
from app.api import chat

__all__ = [
    "auth",
    "user",
    "attachment",
    "category",
    "post",
    "order",
    "comment",
    "chat",
    "get_current_user",
    "get_current_user_optional",
]
