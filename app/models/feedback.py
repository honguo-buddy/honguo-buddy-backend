"""意见反馈表。

收集用户对系统的反馈建议。支持匿名提交（user_id 可空），content 最少10字。
"""
from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import relationship

from app.core.datetime_utils import beijing_now_for_model
from app.db_base import Base


class Feedback(Base):
    __tablename__ = "feedback"

    feedback_id = Column(BigInteger, primary_key=True, autoincrement=True, comment="反馈主键")
    user_id = Column(BigInteger, ForeignKey("user.user_id"), nullable=True, index=True, comment="提交用户ID（匿名时为空）")
    content = Column(Text, nullable=False, comment="反馈内容（最少10字）")
    feedback_type = Column(String(50), nullable=True, comment="反馈类型：BUG / FEATURE / OTHER")
    contact_info = Column(String(255), nullable=True, comment="预留联系渠道")
    create_time = Column(DateTime, default=beijing_now_for_model, nullable=False, comment="提交时间")

    user = relationship("User", back_populates="feedbacks")