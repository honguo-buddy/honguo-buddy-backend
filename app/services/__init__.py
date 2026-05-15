"""Services 模块统一入口。"""

from app.services.auth_service import AuthService
from app.services.user_service import UserService
from app.services.attachment_service import AttachmentService
from app.services.category_service import CategoryService

try:
    from app.services.sms_service import SMSService
except ImportError:
    SMSService = None

__all__ = [
    "AuthService",
    "UserService",
    "SMSService",
    "AttachmentService",
    "CategoryService",
]
