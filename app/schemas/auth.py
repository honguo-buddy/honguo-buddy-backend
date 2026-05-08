from pydantic import BaseModel, EmailStr, Field


class WxLoginRequest(BaseModel):
    # 微信侧唯一标识（openid/code）
    code: str = Field(..., min_length=1, max_length=128)


class EmailSendVerifyCodeRequest(BaseModel):
    email: EmailStr


class EmailVerifyCodeRequest(BaseModel):
    email: EmailStr
    code: str = Field(..., min_length=4, max_length=10)


class SwaggerDebugLoginRequest(BaseModel):
    wx_id: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=1, max_length=128)
