"""意见反馈相关 Schema。"""
from typing import Optional
from pydantic import BaseModel, Field


class FeedbackCreate(BaseModel):
    """提交反馈请求。"""
    content: str = Field(..., min_length=10, max_length=2000, description="反馈内容（最少10字）")
    feedback_type: Optional[str] = Field(default=None, description="反馈类型：BUG / FEATURE / OTHER")
    contact_info: Optional[str] = Field(default=None, max_length=255, description="预留联系渠道")