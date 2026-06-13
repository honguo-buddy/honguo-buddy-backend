"""Goods Pydantic schemas."""
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from typing import Optional, List
from datetime import datetime
from app.models.goods import GoodsCondition, GoodsStatus
from app.schemas.user import UserRead


class GoodsAttachmentBriefRead(BaseModel):
    """商品轻量附件明细响应模型。"""

    id: int = Field(..., validation_alias=AliasChoices("id", "attachment_id"), description="附件ID")
    url: str = Field(..., description="附件URL")

    model_config = ConfigDict(from_attributes=True)


class GoodsBase(BaseModel):
    name: str = Field(..., description="goods name")
    description: Optional[str] = Field(None, description="goods description")
    price: Optional[float] = Field(None, ge=0, description="price, None=negotiable")
    category_id: int = Field(..., description="category template ID")
    condition: GoodsCondition = Field(GoodsCondition.BRAND_NEW, description="condition level")
    template_data: Optional[dict] = Field(default_factory=dict, description="dynamic spec data from category")


class GoodsCreate(GoodsBase):
    attachment_ids: List[int] = Field(default_factory=list, description="attachment ID list")
    expire_time: Optional[str] = Field(default=None, description="expire time ISO format")
    phone: Optional[str] = Field(default=None, max_length=25, description="contact phone")
    wx: Optional[str] = Field(default=None, max_length=255, description="contact wechat")
    qq: Optional[str] = Field(default=None, max_length=255, description="contact qq")


class GoodsUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = None
    condition: Optional[GoodsCondition] = None
    status: Optional[GoodsStatus] = None
    template_data: Optional[dict] = None
    attachment_ids: Optional[List[int]] = None
    expire_time: Optional[str] = None
    phone: Optional[str] = None
    wx: Optional[str] = None
    qq: Optional[str] = None


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
    template_data: Optional[dict] = Field(default_factory=dict, description="dynamic spec data from category")
    price: Optional[float] = None
    condition: GoodsCondition
    status: GoodsStatus
    create_time: datetime
    expire_time: Optional[datetime] = None
    attachment_urls: List[str] = []
    attachments: List[GoodsAttachmentBriefRead] = []
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
    expire_time: Optional[datetime] = None
    attachment_urls: List[str] = []
    attachments: List[GoodsAttachmentBriefRead] = []
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


class GoodsApplicationApplicantRead(UserRead):
    """商品申请人信息。"""

    completed_order_count: int = Field(default=0, description="历史已完成订单数")


class GoodsApplicationItem(BaseModel):
    """商品申请记录。"""

    application_id: int
    goods_id: int
    applicant: GoodsApplicationApplicantRead
    note: Optional[str] = Field(default=None, description="申请备注")
    status: str
    created_at: str


class GoodsApplicationListResponse(BaseModel):
    """商品申请列表响应模型。"""

    applications: List[GoodsApplicationItem] = Field(default_factory=list)
