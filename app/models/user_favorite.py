import enum

from sqlalchemy import BigInteger, Column, DateTime, Enum as SAEnum, ForeignKey, Index, String
from sqlalchemy.orm import relationship

from app.core.datetime_utils import beijing_now_for_model
from app.db_base import Base


class FavoriteTargetType(enum.Enum):
    POST = "POST"
    GOODS = "GOODS"


class UserFavorite(Base):
    __tablename__ = "user_favorite"

    favorite_id = Column(BigInteger, primary_key=True, autoincrement=True, comment="收藏主键")
    user_id = Column(BigInteger, ForeignKey("user.user_id"), nullable=False, index=True, comment="收藏用户ID")
    target_type = Column(
        SAEnum(FavoriteTargetType, values_callable=lambda enum_cls: [e.value for e in enum_cls], name="favorite_target_type", native_enum=False),
        nullable=False,
        comment="收藏目标类型",
    )
    target_id = Column(BigInteger, nullable=False, index=True, comment="收藏目标ID")
    create_time = Column(DateTime, default=beijing_now_for_model, nullable=False, comment="收藏时间")

    user = relationship("User", back_populates="favorites")

    __table_args__ = (
        Index("uq_user_favorite_user_target", "user_id", "target_type", "target_id", unique=True),
        Index("idx_user_favorite_target", "target_type", "target_id"),
    )
