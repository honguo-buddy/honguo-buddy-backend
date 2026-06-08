"""联系方式设置表。

每个用户可配置多种联系方式（手机号、微信、QQ），并控制各渠道的公开可见性。
"""
from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import relationship

from app.core.datetime_utils import beijing_now_for_model
from app.db_base import Base


class UserContact(Base):
    __tablename__ = "user_contact"

    contact_id = Column(BigInteger, primary_key=True, autoincrement=True, comment="联系方式主键")
    user_id = Column(BigInteger, ForeignKey("user.user_id"), nullable=False, comment="所属用户ID")
    contact_type = Column(String(20), nullable=False, comment="联系方式类型：PHONE / WECHAT / QQ")
    contact_value = Column(String(255), nullable=False, comment="联系方式明文值")
    is_public = Column(Boolean, default=True, nullable=False, comment="是否公开可见")
    create_time = Column(DateTime, default=beijing_now_for_model, nullable=False, comment="创建时间")
    update_time = Column(DateTime, default=beijing_now_for_model, onupdate=beijing_now_for_model, nullable=False, comment="更新时间")

    user = relationship("User", back_populates="contacts")

    __table_args__ = (
        Index("idx_user_type", "user_id", "contact_type", unique=True),
    )