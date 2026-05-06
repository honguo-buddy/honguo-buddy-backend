from pydantic import BaseModel, EmailStr, Field, field_validator
from pydantic_extra_types.phone_numbers import PhoneNumber
from typing import Optional
from uuid import UUID

#USER数据模型

class userBase(BaseModel):
    identifier: Optional[str] = Field(default=None, max_length=50, description="工号（员工必填，患者可选）")
    email: Optional[EmailStr] = Field(default=None, description="邮箱（可选）")
    phonenumber: Optional[str] = Field(default=None, max_length=14, description="手机号（可选）")


class userCreate(userBase):
    password: str = Field(max_length=18, description="密码（必填）")

# 患者端登录 - 使用手机号和密码
class PatientLogin(BaseModel):
    phonenumber: str = Field(max_length=25, description="手机号")
    password: str = Field(max_length=18, description="密码")

class user(userBase):
    user_id: int
    user_uuid: str
    user_name: str | None = None
    email: str | None = None
    is_admin: bool
    is_verified: bool
    last_login_ip: str | None = None
    last_login_time: int | None = None
    user_type: str | None = None

    @field_validator('user_uuid', mode='before')
    @classmethod
    def convert_user_uuid(cls, v):
        """将 BINARY(16) 的 bytes 转换为 UUID 字符串。"""
        if isinstance(v, bytes) and len(v) == 16:
            return str(UUID(bytes=v))
        elif isinstance(v, str):
            return v
        elif isinstance(v, UUID):
            return str(v)
        return v

    class Config:
        from_attributes = True
        orm_mode = True

#登入Token
class Token(BaseModel):
    access_token: str
    token_type: str

#邮箱唯一确定
class TokenData(BaseModel):
    email: str | None = None

class PasswordUpdate(BaseModel):
    user_id : int #用户id
    old_password : str #旧密码
    new_password : str #新密码
    confirm_password : str #再次确认密码

class PasswordChangeConfirmInput(BaseModel):
    user_id : int #用户id
    code: str #验证码
class UserUpdate(BaseModel):
    # username 已移除
    email: EmailStr | None = None
    phonenumber: str | None = Field(default=None, max_length=14)
    
class UserRoleUpdate(BaseModel):
    is_admin: bool
