"""Goods Pydantic schemas."""
from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, List
from datetime import datetime


class GoodsBase(BaseModel):
    name: str = Field(..., description="goods name")
    description: Optional[str] = Field(None, description="goods description")
    price: Optional[float] = Field(None, ge=0, description="price, None=negotiable")
    category_id: int = Field(..., description="category template ID")
    condition: str = Field("\u5168\u65b0", description="condition level")
    template_data: Optional[dict] = Field(default_factory=dict, description="dynamic spec data from category")


class GoodsCreate(GoodsBase):
    attachment_ids: List[int] = Field(default_factory=list, description="attachment ID list")


class GoodsUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = None
    condition: Optional[str] = None
    status: Optional[str] = None
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
    condition: str
    status: str
    create_time: datetime
    attachment_urls: List[str] = []
    publisher: Optional[GoodsPublisherSchema] = None

    view_count: int = 0
    favorite_count: int = 0
    comment_count: int = 0


class GoodsDetailRead(BaseModel):
    """Detail page response for goods."""
    model_config = ConfigDict(from_attributes=True)

    goods_id: int
    category_id: int
    name: str
    description: Optional[str] = None
    price: Optional[float] = None
    condition: str
    status: str
    template_data: Optional[dict] = None
    create_time: datetime
    attachment_urls: List[str] = []
    publisher: Optional[GoodsPublisherSchema] = None
    comments: List[dict] = []

    view_count: int = 0
    favorite_count: int = 0
    comment_count: int = 0


class GoodsListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    list: List[GoodsRead]