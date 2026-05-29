from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index
from sqlalchemy.orm import relationship

from app.core.datetime_utils import beijing_now_for_model
from app.db.base import Base


class UserFollow(Base):
    __tablename__ = "user_follow"

    follow_id = Column(BigInteger, primary_key=True, autoincrement=True, comment="关注关系主键")
    follower_id = Column(BigInteger, ForeignKey("user.user_id"), nullable=False, index=True, comment="发起关注的用户ID")
    following_id = Column(BigInteger, ForeignKey("user.user_id"), nullable=False, index=True, comment="被关注的用户ID")
    create_time = Column(DateTime, default=beijing_now_for_model, nullable=False, comment="关注时间")

    follower = relationship(
        "User",
        foreign_keys=[follower_id],
        back_populates="followings",
        lazy="selectin",
        overlaps="followings,followers",
    )
    following = relationship(
        "User",
        foreign_keys=[following_id],
        back_populates="followers",
        lazy="selectin",
        overlaps="followings,followers",
    )

    __table_args__ = (
        Index("uq_user_follow", "follower_id", "following_id", unique=True),
    )
