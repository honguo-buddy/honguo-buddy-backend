"""
Schemas 模块 - 统一导出所有 Pydantic 请求/响应模型

包含：
  - auth: 认证相关请求模型
  - response: 统一响应和异常返回模型
  - user: 用户相关数据模型
  - comment: 评论相关数据模型
"""

from app.schemas.auth import (
    AdminCodeSendRequest,
    AdminLoginRequest,
    WxLoginRequest,
    EmailSendVerifyCodeRequest,
    EmailVerifyCodeRequest,
    SwaggerDebugLoginRequest,
)
from app.schemas.response import (
    ResponseModel,
    UnknownErrorResponse,
    HTTPErrorResponse,
    RequestValidationErrorResponse,
    AuthErrorResponse,
    StatisticsErrorResponse,
    ResourceErrorResponse,
    BusinessErrorResponse,
    LoginResponse,
    UsersListResponse,
    SingleUserResponse,
    DeleteResponse,
    RegisterResponse,
    TokenErrorResponse,
    UpdateUserResponse,
    UserRoleResponse,
    UpdateUserRoleResponse,
)
from app.schemas.history import (
    HistoryDeletePayload,
    HistoryDeleteResponse,
)
from app.schemas.user import (
    userBase,
    userCreate,
    PatientLogin,
    user,
    Token,
    TokenData,
    PasswordUpdate,
    PasswordChangeConfirmInput,
    UserUpdate,
    UserRoleUpdate,
    UserProfileResponse,
    UserPublicResponse,
    UserSelfUpdateRequest,
    PhoneSendCodeRequest,
    PhoneBindRequest,
    UserRead,
    UserFollowToggleRequest,
    UserFollowToggleResponse,
    UserFollowItem,
    UserFollowListResponse,
    FavoriteRequest,
    FavoriteResponse,
    FavoriteItem,
    FavoriteListResponse,
    HistoryItem,
    HistoryListResponse,
    UserUnreadCountsResponse,
)
from app.schemas.comment import (
    CommentCreateRequest,
    CommentResponse,
    CommentListResponse,
    CommentReplyListResponse,
    CommentWithReplyCountResponse,
    CommentReplyPreview,
)
from app.schemas.chat import (
    ChatSessionInitRequest,
    ChatMessageCreateRequest,
    ChatSessionRead,
    ChatSessionListResponse,
    ChatMessageRead,
    ChatMessageListResponse,
    ChatRecallResponse,
    ChatBroadcastRequest
)
from app.schemas.category import (
    CategoryCreate,
    CategoryUpdate,
    CategoryRead,
)
from app.schemas.post import (
    PostCreate,
    PostUpdate,
    PostRead,
    PostList,
    PostDetailRead,
    PostBatchAcceptRequest,
    PostBatchAcceptResultItem,
    PostBatchAcceptErrorItem,
    PostBatchAcceptResponse,
    PostApplicationApplicantRead,
    PostApplicationItem,
    PostApplicationListResponse,
    PostBulletinUpdate
)
from app.schemas.order import (
    OrderRead,
    OrderList,
    OrderItemList,
)
from app.schemas.order_review import (
    OrderReviewCreateRequest,
    OrderReviewRead,
    OrderReviewListResponse,
)


from app.schemas.user_contact import (
    ContactCreate,
    ContactRead,
    ContactListResponse,
)
from app.schemas.user_blacklist import (
    BlacklistCreate,
    BlacklistItem,
    BlacklistListResponse,
)
from app.schemas.feedback import (
    FeedbackCreate,
)
from app.schemas.goods import (
    GoodsBase,
    GoodsCreate,
    GoodsUpdate,
    GoodsPublisherSchema,
    GoodsRead,
    GoodsDetailRead,
    GoodsListResponse,
)
from app.schemas.sys_config import (
    SysConfigRead,
    SysConfigUpdateRequest,
)

__all__ = [
    # auth
    "AdminCodeSendRequest",
    "AdminLoginRequest",
    "WxLoginRequest",
    "EmailSendVerifyCodeRequest",
    "EmailVerifyCodeRequest",
    "SwaggerDebugLoginRequest",
    # response
    "ResponseModel",
    "UnknownErrorResponse",
    "HTTPErrorResponse",
    "RequestValidationErrorResponse",
    "AuthErrorResponse",
    "StatisticsErrorResponse",
    "ResourceErrorResponse",
    "BusinessErrorResponse",
    "LoginResponse",
    "UsersListResponse",
    "SingleUserResponse",
    "DeleteResponse",
    "RegisterResponse",
    "TokenErrorResponse",
    "UpdateUserResponse",
    "UserRoleResponse",
    "UpdateUserRoleResponse",
    # user
    "userBase",
    "userCreate",
    "PatientLogin",
    "user",
    "Token",
    "TokenData",
    "PasswordUpdate",
    "PasswordChangeConfirmInput",
    "UserUpdate",
    "UserRoleUpdate",
    "UserProfileResponse",
    "UserPublicResponse",
    "UserSelfUpdateRequest",
    "UserRead",
    "UserFollowToggleRequest",
    "UserFollowToggleResponse",
    "UserFollowItem",
    "UserFollowListResponse",
    "FavoriteRequest",
    "FavoriteResponse",
    "FavoriteItem",
    "FavoriteListResponse",
    "HistoryItem",
    "HistoryListResponse",
    "UserUnreadCountsResponse",
    # category
    "CategoryCreate",
    "CategoryUpdate",
    "CategoryRead",
    # post
    "PostCreate",
    "PostUpdate",
    "PostRead",
    "PostList",
    "PostDetailRead",
    "PostBatchAcceptRequest",
    "PostBatchAcceptResultItem",
    "PostBatchAcceptErrorItem",
    "PostBatchAcceptResponse",
    "PostApplicationApplicantRead",
    "PostApplicationItem",
    "PostApplicationListResponse",
    # order
    "OrderRead",
    "OrderList",
    "OrderItemList",
    # order_review
    "OrderReviewCreateRequest",
    "OrderReviewRead",
    "OrderReviewListResponse",
    # goods
    "GoodsBase",
    "GoodsCreate",
    "GoodsUpdate",
    "GoodsPublisherSchema",
    "GoodsRead",
    "GoodsDetailRead",
    "GoodsListResponse",
    "SysConfigRead",
    "SysConfigUpdateRequest",
]
