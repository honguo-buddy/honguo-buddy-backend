"""Goods Pydantic schemas."""
from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, List
from datetime import datetime
from app.models.goods import GoodsCondition, GoodsStatus


class GoodsBase(BaseModel):
    name: str = Field(..., description="goods name")
    description: Optional[str] = Field(None, description="goods description")
    price: Optional[float] = Field(None, ge=0, description="price, None=negotiable")
    category_id: int = Field(..., description="category template ID")
    condition: GoodsCondition = Field(GoodsCondition.BRAND_NEW, description="condition level")
    template_data: Optional[dict] = Field(default_factory=dict, description="dynamic spec data from category")


class GoodsCreate(GoodsBase):
    attachment_ids: List[int] = Field(default_factory=list, description="attachment ID list")


class GoodsUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = None
    condition: Optional[GoodsCondition] = None
    status: Optional[GoodsStatus] = None
    template_data: Optional[dict] = None
    attachment_ids: Optional[List[int]] = None


class GoodsPublisherSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: int
    user_name: str
    avatar: Optional[str] = None


class GoodsRead(BaseModel):
    """List card response for goods lobby / my published."""
    model_config = ConfigDict(from_attributes=True)

    goods_id: int
    category_id: int
    name: str
    price: Optional[float] = None
    condition: GoodsCondition
    status: GoodsStatus
    create_time: datetime
    attachment_urls: List[str] = []
    publisher: Optional[GoodsPublisherSchema] = None

    view_count: int = 0
    favorite_count: int = 0
    comment_count: int = 0

# 专治嵌套断链的评论 Schema
class GoodsCommentSchema(BaseModel):
    """商品详情页专用的轻量级内嵌评论响应契约"""
    model_config = ConfigDict(from_attributes=True)

    comment_id: int = Field(..., description="评论ID")
    user_id: int = Field(..., description="评论发表人ID")
    content: str = Field(..., description="评论内容")
    create_time: datetime = Field(..., description="评论时间")
        

class GoodsDetailRead(GoodsBase):
    """用于商品详情页面的响应 Schema"""
    goods_id: int
    status: GoodsStatus
    create_time: datetime
    attachment_urls: List[str] = []
    publisher: Optional[GoodsPublisherSchema] = None
    
    model_config = ConfigDict(from_attributes=True)

    # 详情页指标回填
    view_count: int = 0
    favorite_count: int = 0
    comment_count: int = 0


class GoodsListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    list: List[GoodsRead]