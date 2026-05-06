"""
Schemas 模块 - 统一导出所有 Pydantic 请求/响应模型

包含：
  - auth: 认证相关请求模型
  - response: 统一响应和异常返回模型
  - user: 用户相关数据模型
"""

from app.schemas.auth import (
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
)

__all__ = [
    # auth
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
]
