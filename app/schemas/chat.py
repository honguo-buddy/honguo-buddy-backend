"""聊天相关 Pydantic 模型。"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ChatSessionInitRequest(BaseModel):
    peer_id: int = Field(..., description="对方用户ID")
    context_type: Optional[str] = Field(default=None, max_length=20, description="业务上下文类型，可选")
    context_id: Optional[int] = Field(default=None, description="业务上下文ID，可选")


class ChatMessageCreateRequest(BaseModel):
    session_id: int = Field(..., description="会话ID")
    content: str = Field(..., min_length=1, max_length=4000, description="消息内容")
    attachment_ids: Optional[List[int]] = Field(default=None, description="消息附件ID列表，可选")
    quote_message_id: Optional[int] = Field(default=None, description="引用消息ID，可选")
    context_type: Optional[str] = Field(default=None, max_length=20, description="上下文类型，可选")
    context_id: Optional[int] = Field(default=None, description="上下文ID，可选")


class ChatSessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    session_id: int
    user_one_id: int
    user_two_id: int
    peer_id: int
    context_type: Optional[str] = None
    context_id: Optional[int] = None
    last_message_content: Optional[str] = None
    last_message_time: Optional[datetime] = None
    unread_count: int = Field(default=0, description="未读消息数")


class ChatSessionListResponse(BaseModel):
    items: List[ChatSessionRead]


class ChatMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    message_id: int
    session_id: int
    sender_id: int
    content: str
    context_type: Optional[str] = None
    context_id: Optional[int] = None
    is_read: bool
    is_recalled: bool
    is_deleted_by_sender: bool
    is_deleted_by_receiver: bool
    quote_message_id: Optional[int] = None
    create_time: datetime
    attachment_urls: List[str] = Field(default_factory=list, description="消息附件相对 URL 列表")


class ChatMessageListResponse(BaseModel):
    items: List[ChatMessageRead]
    next_cursor: Optional[int] = Field(default=None, description="下一页游标")


class ChatRecallResponse(BaseModel):
    message_id: int
    is_recalled: bool
    content: str

class ChatBroadcastRequest(BaseModel):
    post_id: int = Field(default=None, description="帖子ID")
    content: str = Field(default=None, min_length=1, max_length=4000, description="消息内容")
    attachment_ids: Optional[List[int]] = Field(default=None, description="附件ID列表，可选")

