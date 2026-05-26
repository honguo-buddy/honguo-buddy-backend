"""Post 相关的请求和响应模型。"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator

from app.schemas.user import UserRead


class PostCreate(BaseModel):
    """发布悬赏帖的请求模型。"""
    
    title: str = Field(..., min_length=1, max_length=255, description="帖子标题")
    description: Optional[str] = Field(default=None, description="详细描述")
    price: Optional[float] = Field(default=None, gt=0, description="悬赏金额（单位：元，精度到分）")
    direction: str = Field(default="SELL", description="方向: SELL(出让/委托) 或 BUY(求购/接单)")
    urgency: str = Field(default="NORMAL", description="紧急程度: NORMAL(普通), URGENT(紧急), EMERGENCY(特急)")
    max_accepters: int = Field(default=1, ge=1, description="最大接单人数")
    category_id: Optional[int] = Field(default=None, description="分类ID（可选）")
    template_filters: Dict[str, Any] = Field(
        default_factory=dict, 
        description="模板相关筛选字段（JSON），根据选择的模板而定（如 pickup_address、dropoff_address 等）"
    )

    @field_validator("template_filters")
    @classmethod
    def validate_template_filters(cls, v):
        """确保 template_filters 是有效的字典。"""
        if not isinstance(v, dict):
            raise ValueError("template_filters 必须是对象")
        return v

    model_config = {"from_attributes": True}


class PostUpdate(BaseModel):
    """帖子局部更新请求模型。"""

    title: Optional[str] = Field(default=None, min_length=1, max_length=255, description="帖子标题")
    description: Optional[str] = Field(default=None, description="详细描述")
    price: Optional[float] = Field(default=None, gt=0, description="悬赏金额（单位：元，精度到分）")
    direction: Optional[str] = Field(default=None, description="方向: SELL(出让/委托) 或 BUY(求购/接单)")
    urgency: Optional[str] = Field(default=None, description="紧急程度: NORMAL(普通), URGENT(紧急), EMERGENCY(特急)")
    max_accepters: Optional[int] = Field(default=None, ge=1, description="最大接单人数")
    category_id: Optional[int] = Field(default=None, description="分类ID")
    template_filters: Optional[Dict[str, Any]] = Field(default=None, description="模板相关筛选字段（JSON）")

    @field_validator("template_filters")
    @classmethod
    def validate_template_filters(cls, v):
        if v is None:
            return v
        if not isinstance(v, dict):
            raise ValueError("template_filters 必须是对象")
        return v

    model_config = {"from_attributes": True}


class PostRead(BaseModel):
    """发布悬赏帖的响应模型。"""
    
    post_id: int
    category_id: Optional[int] = Field(default=None, description="模板/分类ID")
    title: str
    description: Optional[str] = None
    price: Optional[float] = Field(default=None, description="悬赏金额（单位：元）")
    direction: str = Field(description="方向: SELL 或 BUY")
    urgency: str = Field(description="紧急程度: NORMAL, URGENT, EMERGENCY")
    status: str = Field(description="状态: OPEN, PENDING, ACCEPTED, IN_PROGRESS, CLOSED, CANCELLED")
    template_data: Optional[Dict[str, Any]] = None
    max_accepters: int = 1
    publisher: Optional[UserRead] = None
    publisher_id: int
    current_accepters: int = 0  # 当前接单人数
    create_time: str
    attachment_urls: List[str] = Field(default_factory=list, description="附件 URL 列表")

    model_config = {"from_attributes": True}


class PostList(BaseModel):
    """任务列表响应模型。"""
    
    total: int = Field(description="总数")
    page: int = Field(description="当前页")
    page_size: int = Field(description="每页数量")
    list: List[PostRead] = Field(description="帖子列表")


class PostDetailRead(PostRead):
    """任务详情响应模型（扩展了 PostRead）。"""
    
    comments: List[Dict[str, Any]] = Field(default_factory=list, description="评论列表")


class PostBatchAcceptRequest(BaseModel):
    """批量接单请求模型。"""

    post_ids: List[int] = Field(default_factory=list, description="待申请的帖子 ID 列表")

    model_config = {"from_attributes": True}


class PostBatchAcceptResultItem(BaseModel):
    """批量接单成功项。"""

    post_id: int
    order_id: int
    status: str


class PostBatchAcceptErrorItem(BaseModel):
    """批量接单失败项。"""

    post_id: int
    error: str
    message: str


class PostBatchAcceptResponse(BaseModel):
    """批量接单响应模型。"""

    results: List[PostBatchAcceptResultItem] = Field(default_factory=list)
    errors: List[PostBatchAcceptErrorItem] = Field(default_factory=list)


class PostApplicationApplicantRead(UserRead):
    """帖子申请人信息。"""

    completed_order_count: int = Field(default=0, description="历史已完成订单数")


class PostApplicationItem(BaseModel):
    """帖子接单申请记录。"""

    application_id: int
    post_id: int
    applicant: PostApplicationApplicantRead
    note: Optional[str] = Field(default=None, description="申请备注")
    status: str
    created_at: str


class PostApplicationListResponse(BaseModel):
    """帖子申请列表响应模型。"""

    applications: List[PostApplicationItem] = Field(default_factory=list)
