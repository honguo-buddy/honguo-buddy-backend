import enum

from sqlalchemy import BigInteger, Column, DateTime, Enum as SAEnum, Index, String, func
from sqlalchemy.orm import relationship

from app.db.base import Base


class AttachmentTargetType(enum.Enum):
    POST = "POST"
    GOODS = "GOODS"


class Attachment(Base):
    __tablename__ = "attachment"

    attachment_id = Column(BigInteger, primary_key=True, autoincrement=True, comment="附件主键")
    target_type = Column(
        SAEnum(AttachmentTargetType, values_callable=lambda enum_cls: [e.value for e in enum_cls], name="attachment_target_type", native_enum=False),
        nullable=False,
        comment="目标类型",
    )
    target_id = Column(BigInteger, nullable=False, comment="目标ID")
    url = Column(String(500), nullable=False, comment="附件URL")
    create_time = Column(DateTime, default=func.now(), nullable=False, comment="创建时间")

    __table_args__ = (Index("idx_attachment_target_type_target_id", "target_type", "target_id"),)