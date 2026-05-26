"""评论相关 Pydantic 模型。"""

from typing import Optional, List
from pydantic import BaseModel, ConfigDict, Field
from datetime import datetime


# ===== 请求模型 =====

class CommentCreateRequest(BaseModel):
    """发布评论/回复的请求模型。"""
    target_type: str = Field(..., description="目标类型: POST, GOODS, ORDER")
    target_id: int = Field(..., description="目标ID")
    parent_id: Optional[int] = Field(default=None, description="父评论ID（可选，为null则为根评论）")
    content: str = Field(..., description="评论内容")
    attachment_ids: Optional[List[int]] = Field(default=None, description="评论附件ID列表（可选）")


# ===== 响应模型 =====

class CommentReplyPreview(BaseModel):
    """回复预览（用于根评论列表中显示的最新回复片段）。"""
    model_config = ConfigDict(from_attributes=True)
    
    comment_id: int
    user_id: int
    content: str
    create_time: datetime


class CommentResponse(BaseModel):
    """单条评论的响应模型。"""
    model_config = ConfigDict(from_attributes=True)
    
    comment_id: int
    user_id: int
    target_type: str
    target_id: int
    parent_id: Optional[int]
    content: str
    is_deleted: bool
    create_time: datetime
    update_time: datetime
    attachment_urls: List[str] = Field(default_factory=list, description="评论附件相对 URL 列表")


class CommentWithReplyCountResponse(BaseModel):
    """带有回复计数和预览的根评论响应。"""
    model_config = ConfigDict(from_attributes=True)
    
    comment_id: int
    user_id: int
    target_type: str
    target_id: int
    content: str
    is_deleted: bool
    create_time: datetime
    update_time: datetime
    reply_count: int = Field(default=0, description="该评论的回复总数")
    preview_replies: List[CommentReplyPreview] = Field(default_factory=list, description="最新的2-3条回复预览")
    attachment_urls: List[str] = Field(default_factory=list, description="评论附件相对 URL 列表")


class CommentListResponse(BaseModel):
    """分页获取评论列表的响应。"""
    items: List[CommentWithReplyCountResponse]
    next_cursor: Optional[int] = Field(default=None, description="下一页的游标（评论ID），null表示无更多数据")


class CommentReplyListResponse(BaseModel):
    """获取单条根评论下的所有回复的响应。"""
    items: List[CommentResponse]
    next_cursor: Optional[int] = Field(default=None, description="下一页的游标，null表示无更多数据")
