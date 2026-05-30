import enum

from sqlalchemy import Boolean, BigInteger, Column, DateTime, Enum as SAEnum, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.mysql import BINARY
from sqlalchemy.orm import relationship
from app.core.datetime_utils import beijing_now_for_model
from app.db_base import Base


class SexEnum(enum.Enum):
    MALE = "男"
    FEMALE = "女"
    UNKNOWN = "未知"


class UserType(enum.Enum):
    USER = "user"
    ADMIN = "admin"


class User(Base):
    __tablename__ = "user"

    user_id = Column(BigInteger, primary_key=True, autoincrement=True, comment="用户主键")
    user_uuid = Column(BINARY(16), unique=True, index=True, nullable=False, comment="用户UUID，外部唯一标识")
    user_name = Column(String(255), unique=True, index=True, nullable=True, comment="用户名")
    avatar_id = Column(BigInteger, ForeignKey("attachment.attachment_id"), nullable=True, index=True, comment="用户头像附件ID")
    sex = Column(
        SAEnum(SexEnum, values_callable=lambda enum_cls: [e.value for e in enum_cls], name="user_sex", native_enum=False),
        default=SexEnum.UNKNOWN,
        nullable=False,
        comment="用户性别",
    )
    email = Column(String(255), unique=True, index=True, nullable=True, comment="邮箱")
    phonenumber = Column(String(25), unique=True, index=True, nullable=True, comment="手机号")
    user_type = Column(
        SAEnum(UserType, values_callable=lambda enum_cls: [e.value for e in enum_cls], name="user_type", native_enum=False),
        default=UserType.USER,
        nullable=False,
        comment="用户类型",
    )
    credit_score = Column(Integer, default=0, nullable=False, comment="信用分")
    is_verified = Column(Boolean, default=False, nullable=False, comment="是否完成认证")
    is_active = Column(Boolean, default=True, nullable=False, comment="是否可用")
    is_admin = Column(Boolean, default=False, nullable=False, comment="是否超级管理员")
    is_deleted = Column(Boolean, default=False, nullable=False, comment="是否软删除")
    last_login_ip = Column(String(64), nullable=True, comment="最近登录IP")
    last_login_time = Column(BigInteger, nullable=True, comment="最近登录时间戳")
    wechat_openid = Column(String(128), unique=True, index=True, nullable=False, comment="微信 openid")
    wechat_session_key = Column(String(256), nullable=True, comment="微信 session_key")
    wechat_unionid = Column(String(128), nullable=True, comment="微信 unionid")
    wechat_bind_time = Column(DateTime, nullable=True, comment="微信绑定时间")
    create_time = Column(DateTime, default=beijing_now_for_model, nullable=False, comment="创建时间")
    update_time = Column(DateTime, default=beijing_now_for_model, onupdate=beijing_now_for_model, nullable=False, comment="更新时间")

    posts = relationship("Post", back_populates="user", lazy="selectin")
    goods = relationship("Goods", back_populates="user", lazy="selectin")
    orders_as_buyer = relationship("Order", foreign_keys="Order.buyer_id", back_populates="buyer", lazy="selectin")
    orders_as_seller = relationship("Order", foreign_keys="Order.seller_id", back_populates="seller", lazy="selectin")
    comments = relationship("Comment", back_populates="user", lazy="selectin")
    credit_logs = relationship("CreditLog", back_populates="user", lazy="selectin")
    user_access_logs = relationship("UserAccessLog", back_populates="user", lazy="selectin")
    avatar_attachment = relationship("Attachment", foreign_keys=[avatar_id], lazy="selectin")
    chat_sessions_as_user_one = relationship("ChatSession", foreign_keys="ChatSession.user_one_id", lazy="selectin", overlaps="user_one")
    chat_sessions_as_user_two = relationship("ChatSession", foreign_keys="ChatSession.user_two_id", lazy="selectin", overlaps="user_two")
    chat_messages_sent = relationship("ChatMessage", foreign_keys="ChatMessage.sender_id", lazy="selectin", overlaps="sender")
    followings = relationship("UserFollow", foreign_keys="UserFollow.follower_id", back_populates="follower", lazy="selectin")
    followers = relationship("UserFollow", foreign_keys="UserFollow.following_id", back_populates="following", lazy="selectin")
    favorites = relationship("UserFavorite", back_populates="user", lazy="selectin")


def parse_user_type(value: str) -> UserType:
    if not value:
        return UserType.USER

    normalized = str(value).strip()
    for member in UserType:
        if normalized == member.value or normalized.upper() == member.name:
            return member
    return UserType.USER
    


class UserReputation(Base):
    """用户声誉画像表：物理隔离 users 主表，存储双角色评分与标签。

    与 users 表通过 user_id 保持 1:1 锁死关联。
    """
    __tablename__ = "user_reputation"

    user_id = Column(BigInteger, ForeignKey("user.user_id", ondelete="CASCADE"), primary_key=True, comment="用户ID（1:1关联users表）")
    carrier_score = Column(Numeric(3, 1), default=5.0, nullable=False, comment="接单人平均评分（默认5.0）")
    carrier_order_count = Column(BigInteger, default=0, nullable=False, comment="作为接单人完成的订单数")
    client_score = Column(Numeric(3, 1), default=5.0, nullable=False, comment="发单人平均评分（默认5.0）")
    client_order_count = Column(BigInteger, default=0, nullable=False, comment="作为发单人完成的订单数")
    tags_json = Column(String(1024), default="{}", nullable=False, comment="高频印象标签（JSON格式）")
    create_time = Column(DateTime, default=beijing_now_for_model, nullable=False, comment="创建时间")
    update_time = Column(DateTime, default=beijing_now_for_model, onupdate=beijing_now_for_model, nullable=False, comment="更新时间")