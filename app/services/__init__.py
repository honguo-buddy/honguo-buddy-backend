"""Services 模块统一入口。"""

from app.services.auth_service import AuthService

try:
    from app.services.sms_service import SMSService
except ImportError:
    SMSService = None

__all__ = [
    "AuthService",
    "SMSService",
]
