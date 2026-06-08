"""黑名单相关 Schema。"""
from typing import Optional
from pydantic import BaseModel, Field


class BlacklistCreate(BaseModel):
    """拉黑请求。"""
    target_id: int = Field(..., description="被拉黑的用户ID")


class BlacklistItem(BaseModel):
    """黑名单条目响应（含被拉黑用户简影）。"""
    user_id: int
    target_id: int
    target_name: Optional[str] = None
    target_avatar: Optional[str] = None
    create_time: Optional[str] = None

    model_config = {"from_attributes": True}


class BlacklistListResponse(BaseModel):
    """黑名单分页列表响应。"""
    total: int
    page: int
    page_size: int
    list: list[BlacklistItem]