from sqlalchemy import Column, BigInteger, Text, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import relationship
from app.db_base import Base

class ChatSession(Base):
    """私信会话表：维护双人会话与最后消息预览。"""
    __tablename__ = "chat_session"

    session_id = Column(BigInteger, primary_key=True, autoincrement=True, comment="会话主键")
    user_one_id = Column(BigInteger, ForeignKey("user.user_id"), nullable=False, index=True, comment="发起方/较小用户ID")
    user_two_id = Column(BigInteger, ForeignKey("user.user_id"), nullable=False, index=True, comment="接收方/较大用户ID")
    context_type = Column(String(20), nullable=True, comment="业务上下文类型")
    context_id = Column(BigInteger, nullable=True, comment="业务上下文ID")
    
    last_message_content = Column(Text, nullable=True, comment="最后一条消息快照（用于消息列表预览）")
    last_message_time = Column(DateTime, nullable=True, comment="最后一条消息时间（用于排序）")
    
    # 建立联合唯一索引，死锁任意两个用户之间有且仅有一个永久会话
    __table_args__ = (
        Index("idx_user_one_two", "user_one_id", "user_two_id", unique=True),
        Index("idx_chat_session_last_message_time", "last_message_time"),
    )

    user_one = relationship("User", foreign_keys=[user_one_id], overlaps="chat_sessions_as_user_one")
    user_two = relationship("User", foreign_keys=[user_two_id], overlaps="chat_sessions_as_user_two")
    messages = relationship("ChatMessage", back_populates="session", cascade="all, delete-orphan")