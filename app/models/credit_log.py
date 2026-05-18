from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.core.datetime_utils import beijing_now_for_model
from app.db.base import Base


class CreditLog(Base):
    __tablename__ = "credit_log"

    log_id = Column(BigInteger, primary_key=True, autoincrement=True, comment="流水主键")
    user_id = Column(BigInteger, ForeignKey("user.user_id"), nullable=False, index=True, comment="用户ID")
    change_amount = Column(Integer, nullable=False, comment="变动分值")
    reason = Column(String(255), nullable=False, comment="变动原因")
    create_time = Column(DateTime, default=beijing_now_for_model, nullable=False, comment="创建时间")

    user = relationship("User", back_populates="credit_logs", lazy="selectin")