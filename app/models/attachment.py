import enum

from sqlalchemy import BigInteger, Column, DateTime, Enum as SAEnum, Index, String, func, Boolean
from sqlalchemy.orm import relationship

from app.db.base import Base


class AttachmentTargetType(enum.Enum):
    USER = "USER"
    POST = "POST"
    GOODS = "GOODS"


class Attachment(Base):
    """附件资源表：用于存放用户头像、帖子图片、商品图片等统一管理的附件记录。"""
    __tablename__ = "attachment"

    attachment_id = Column(BigInteger, primary_key=True, autoincrement=True, comment="附件主键")
    # 目标类型（多态）：USER / POST / GOODS
    target_type = Column(
        SAEnum(AttachmentTargetType, values_callable=lambda enum_cls: [e.value for e in enum_cls], name="attachment_target_type", native_enum=False),
        nullable=False,
        comment="目标类型",
    )
    # 目标 ID 使用表的自增主键（BigInteger），上传时可先不绑定（NULL）
    target_id = Column(BigInteger, nullable=True, index=True, comment="目标表的主键 ID，可为空，后续绑定")

    # 附件存储的相对 URL（例如 /static/avatar/goods_20260512.png）
    url = Column(String(500), nullable=False, comment="附件访问路径(相对)")

    # 谁上传的（creator_id 使用 user.user_id）
    creator_id = Column(BigInteger, nullable=False, index=True, comment="上传者的用户主键 ID")

    # 审计字段
    create_time = Column(DateTime, default=func.now(), nullable=False, comment="创建时间")
    update_time = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False, comment="更新时间")
    is_deleted = Column(Boolean, default=False, nullable=False, comment="是否软删除")

    avatar_users = relationship("User", back_populates="avatar_attachment", lazy="selectin")

    __table_args__ = (Index("idx_attachment_target_type_target_id", "target_type", "target_id"),)