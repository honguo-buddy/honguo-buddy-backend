from sqlalchemy import Column, BigInteger, Text, DateTime, ForeignKey, Index, Boolean, String
from sqlalchemy.orm import relationship

from app.core.datetime_utils import beijing_now_for_model
from app.db.base import Base


class ChatMessage(Base):
    """私信消息表：支持引用、撤回与双端单边删除。"""
    __tablename__ = "chat_message"

    message_id = Column(BigInteger, primary_key=True, autoincrement=True, comment="消息主键")
    session_id = Column(BigInteger, ForeignKey("chat_session.session_id"), nullable=False, index=True, comment="所属会话")
    sender_id = Column(BigInteger, ForeignKey("user.user_id"), nullable=False, comment="发送者ID")
    
    content = Column(Text, nullable=False, comment="消息文本内容")
    context_type = Column(String(20), nullable=True, comment="上下文类型：POST / GOODS / ORDER / None")
    context_id = Column(BigInteger, nullable=True, index=True, comment="关联的业务实体自增主键 ID")
    is_read = Column(Boolean, default=False, nullable=False, comment="对方是否已读")
    is_recalled = Column(Boolean, default=False, nullable=False, comment="是否已撤回")
    is_deleted_by_sender = Column(Boolean, default=False, nullable=False, comment="发送者单边删除")
    is_deleted_by_receiver = Column(Boolean, default=False, nullable=False, comment="接收者单边删除")
    quote_message_id = Column(BigInteger, ForeignKey("chat_message.message_id"), nullable=True, index=True, comment="引用的消息ID")
    create_time = Column(DateTime, default=beijing_now_for_model, nullable=False, index=True)

    session = relationship("ChatSession", back_populates="messages", lazy="selectin")
    sender = relationship("User", foreign_keys=[sender_id], lazy="selectin")
    quote_message = relationship("ChatMessage", remote_side=[message_id], lazy="selectin")

    __table_args__ = (
        Index("idx_chat_message_session_create_time", "session_id", "create_time"),
        Index("idx_chat_message_session_sender", "session_id", "sender_id"),
        Index("idx_chat_message_context_type_context_id", "context_type", "context_id"),
    )