"""订单评价相关 Pydantic 模型。"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class OrderReviewCreateRequest(BaseModel):
    """创建订单评价/追评/回评的请求模型。"""

    order_id: int = Field(..., description="订单ID")
    reviewee_id: int = Field(..., description="被评价人ID")
    review_type: str = Field(default="INITIAL", description="评价类型: INITIAL / ADDITIONAL / REPLY")
    parent_id: Optional[int] = Field(default=None, description="父评价ID，追评/回评时可选")
    rating: Optional[int] = Field(default=None, ge=1, le=5, description="评分，首评时必填")
    content: Optional[str] = Field(default=None, max_length=2000, description="评价内容")
    is_anonymous: bool = Field(default=False, description="是否匿名")
    attachment_ids: Optional[List[int]] = Field(default=None, description="评价附件 ID 列表，可在评价创建后自动绑定")

    @field_validator("review_type")
    @classmethod
    def _normalize_review_type(cls, value: str) -> str:
        normalized = str(value or "").strip().upper()
        if not normalized:
            raise ValueError("review_type 不能为空")
        return normalized

    model_config = ConfigDict(from_attributes=True)


class OrderReviewRead(BaseModel):
    """订单评价响应模型。"""

    review_id: int
    order_id: int
    reviewer_id: int
    reviewee_id: int
    review_type: str
    parent_id: Optional[int] = None
    rating: Optional[int] = None
    content: Optional[str] = None
    is_anonymous: bool
    is_visible: bool
    create_time: datetime
    attachment_urls: List[str] = Field(default_factory=list, description="评价附件 URL 列表")

    model_config = ConfigDict(from_attributes=True)


class OrderReviewListResponse(BaseModel):
    """订单评价列表响应。"""

    items: List[OrderReviewRead]
