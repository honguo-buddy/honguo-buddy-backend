"""Services 模块统一入口。"""

from app.services.auth_service import AuthService
from app.services.user_service import UserService
from app.services.attachment_service import AttachmentService
from app.services.category_service import CategoryService
from app.services.order_service import OrderService
from app.services.order_review_service import OrderReviewService
from app.services.post_service import PostService
from app.services.comment_service import CommentService
from app.services.chat_service import ChatService
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
    "OrderService",
    "OrderReviewService",
    "PostService",
    "CommentService",
    "ChatService",
]
