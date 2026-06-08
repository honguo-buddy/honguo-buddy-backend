"""联系方式相关 Schema。"""
from typing import Optional
from pydantic import BaseModel, Field


class ContactCreate(BaseModel):
    """新增/覆盖联系方式请求。"""
    contact_type: str = Field(..., min_length=1, max_length=20, description="联系方式类型：PHONE / WECHAT / QQ")
    contact_value: str = Field(..., min_length=1, max_length=255, description="联系方式明文值")
    is_public: bool = Field(default=True, description="是否公开可见")


class ContactRead(BaseModel):
    """联系方式响应。"""
    contact_id: int
    user_id: int
    contact_type: str
    contact_value: str
    is_public: bool

    model_config = {"from_attributes": True}


class ContactListResponse(BaseModel):
    """联系方式列表响应。"""
    list: list[ContactRead]