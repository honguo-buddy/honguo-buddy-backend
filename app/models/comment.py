import enum

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Enum as SAEnum, ForeignKey, Index, Text
from sqlalchemy.orm import relationship

from app.core.datetime_utils import beijing_now_for_model
from app.db_base import Base


class TargetType(enum.Enum):
    POST = "POST"
    GOODS = "GOODS"
    ORDER = "ORDER"


class Comment(Base):
    __tablename__ = "comment"

    comment_id = Column(BigInteger, primary_key=True, autoincrement=True, comment="评论主键")
    user_id = Column(BigInteger, ForeignKey("user.user_id"), nullable=False, index=True, comment="评论用户ID")
    target_type = Column(
        SAEnum(TargetType, values_callable=lambda enum_cls: [e.value for e in enum_cls], name="comment_target_type", native_enum=False),
        nullable=False,
        comment="目标类型",
    )
    target_id = Column(BigInteger, nullable=False, comment="目标ID")
    parent_id = Column(BigInteger, ForeignKey("comment.comment_id"), nullable=True, index=True, comment="父评论ID")
    content = Column(Text, nullable=False, comment="评论内容")
    is_deleted = Column(Boolean, default=False, nullable=False, comment="是否软删除")
    create_time = Column(DateTime, default=beijing_now_for_model, nullable=False, comment="创建时间")
    update_time = Column(DateTime, default=beijing_now_for_model, onupdate=beijing_now_for_model, nullable=False, comment="更新时间")

    user = relationship("User", back_populates="comments", lazy="selectin")
    parent = relationship("Comment", remote_side=[comment_id], back_populates="replies", lazy="selectin")
    replies = relationship("Comment", back_populates="parent", cascade="all, delete-orphan", single_parent=True, lazy="selectin")

    __table_args__ = (
        Index("idx_comment_target_type_target_id", "target_type", "target_id"),
        Index("idx_comment_parent_id", "parent_id"),
    )