"""订单相关请求与响应模型。"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.schemas.user import UserRead


class OrderRead(BaseModel):
    """订单列表/详情统一响应模型。"""

    order_id: int
    item_type: str
    item_id: int
    status: str
    buyer_id: int
    seller_id: int
    initiator_id: Optional[int] = None
    trigger_type: Optional[str] = None
    accepted_time: Optional[str] = None
    create_time: Optional[str] = None
    update_time: Optional[str] = None
    meta_data: Optional[Dict[str, Any]] = None
    buyer: Optional[UserRead] = None
    seller: Optional[UserRead] = None
    curr_accepters: Optional[int] = None
    bulletin: Optional[str] = None

    model_config = {"from_attributes": True}


class OrderList(BaseModel):
    total: int = Field(description="总数")
    page: int = Field(description="当前页")
    page_size: int = Field(description="每页数量")
    list: List[OrderRead] = Field(description="订单列表")


class OrderItemList(BaseModel):
    item_id: int
    item_type: str
    list: List[OrderRead]
