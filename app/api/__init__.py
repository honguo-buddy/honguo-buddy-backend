"""API 模块统一入口。"""

from app.api import auth
from app.api.auth import get_current_user
from app.api import user
from app.api import attachment
from app.api import category

__all__ = [
    "auth",
    "user",
    "attachment",
    "category",
    "get_current_user",
]
