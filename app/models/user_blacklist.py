"""用户黑名单表。

记录用户主动拉黑的目标用户。复合主键 (user_id, target_id) 直接卡位防重复，
保留 target_id 反向索引用于判定"对方是否把我拉黑了"。
"""
from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index, PrimaryKeyConstraint
from sqlalchemy.orm import relationship

from app.core.datetime_utils import beijing_now_for_model
from app.db_base import Base


class UserBlacklist(Base):
    __tablename__ = "user_blacklist"

    user_id = Column(BigInteger, ForeignKey("user.user_id"), nullable=False, comment="发起拉黑的用户ID")
    target_id = Column(BigInteger, ForeignKey("user.user_id"), nullable=False, comment="被拉黑的用户ID")
    create_time = Column(DateTime, default=beijing_now_for_model, nullable=False, comment="拉黑时间")

    user = relationship("User", foreign_keys=[user_id], back_populates="blacklist_entries")
    target = relationship("User", foreign_keys=[target_id])

    __table_args__ = (
        PrimaryKeyConstraint("user_id", "target_id"),
        Index("idx_blacklist_target", "target_id"),
    )