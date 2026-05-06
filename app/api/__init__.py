"""API 模块统一入口。"""

from app.api import auth
from app.api.auth import get_current_user
from app.api import user

__all__ = [
    "auth",
    "user",
    "get_current_user",
]
