
from typing import Generic, TypeVar, Optional, List
from pydantic import BaseModel
from pydantic.generics import GenericModel
from app.schemas.user import user, Token

T = TypeVar("T")


# 通用响应模型
class ResponseModel(GenericModel, Generic[T]):
    code: int
    message: Optional[T]
    
# ====== 全局异常相关返回类型 ======
class UnknownErrorResponse(BaseModel):
    error: str
    detail: str

class HTTPErrorResponse(BaseModel):
    error: str
    detail: str

class RequestValidationErrorResponse(BaseModel):
    error: str
    detail: list

#认证异常
class AuthErrorResponse(BaseModel):
    error: str
    msg: str
    
# 统计数据异常
class StatisticsErrorResponse(BaseModel):
    error: str
    msg: str

#资源操作异常
class ResourceErrorResponse(BaseModel):
    error: str
    msg: str

#业务逻辑异常    
class BusinessErrorResponse(BaseModel):
    error: str
    msg: str
    
# ====== AUTH认证模块相关返回类型 ======


# 登录成功返回的数据模型
class LoginResponse(BaseModel):
    userid: int
    access_token: str
    token_type: str
    user_type: str  # 用户类型：student, teacher, doctor, admin, external

# 获取所有用户返回的数据模型
class UsersListResponse(BaseModel):
    users: List[user]

# 获取单个用户返回的数据模型
class SingleUserResponse(BaseModel):
    user: user

# 删除成功返回的数据模型
class DeleteResponse(BaseModel):
    detail: str

# 注册成功返回的数据模型
class RegisterResponse(BaseModel):
    detail: str

# Token失效返回的数据模型
class TokenErrorResponse(BaseModel):
    error: str

# 更新用户信息返回的数据模型
class UpdateUserResponse(BaseModel):
    user: user

# 获取当前用户角色返回的数据模型
class UserRoleResponse(BaseModel):
    role: str

# 更新用户角色返回的数据模型
class UpdateUserRoleResponse(BaseModel):
    detail: str
                
