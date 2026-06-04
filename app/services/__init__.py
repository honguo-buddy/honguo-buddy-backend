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
from app.services.social_service import SocialService
from app.services.metrics_service import MetricsService
from app.services.reputation_service import ReputationService
from app.services.goods_service import GoodsService
from app.services.wechat_notification_service import WeChatNotificationService
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
    "SocialService",
    "MetricsService",
    "ReputationService",
    "GoodsService",
    "WeChatNotificationService",
]
