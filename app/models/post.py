import enum

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Enum as SAEnum, ForeignKey, Index, JSON, Numeric, String, Text, and_
from sqlalchemy.orm import foreign, relationship

from app.core.datetime_utils import beijing_now_for_model
from app.db.base import Base


class PostStatus(enum.Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class Direction(enum.Enum):
    SELL = "SELL"
    BUY = "BUY"


class UrgencyLevel(str, enum.Enum):
    """紧急程度枚举，作为一级字段存储在 post 表中"""
    NORMAL = "NORMAL"        # 普通
    URGENT = "URGENT"        # 紧急
    EMERGENCY = "EMERGENCY"  # 特急


class Post(Base):
    __tablename__ = "post"

    post_id = Column(BigInteger, primary_key=True, autoincrement=True, comment="发布主键")
    publisher_id = Column(BigInteger, ForeignKey("user.user_id"), nullable=False, index=True, comment="发布者ID")
    category_id = Column(BigInteger, ForeignKey("category.category_id"), nullable=False, index=True, comment="分类ID")
    title = Column(String(255), nullable=False, comment="标题")
    description = Column(Text, nullable=True, comment="详细描述")
    price = Column(Numeric(10, 2), nullable=True, comment="悬赏金额（单位：元，精度到分）")
    template_data = Column(JSON, nullable=True, comment="模板表单数据（如max_accepters、属性、紧急度等）")
    status = Column(
        SAEnum(PostStatus, values_callable=lambda enum_cls: [e.value for e in enum_cls], name="post_status", native_enum=False),
        default=PostStatus.OPEN,
        nullable=False,
        comment="状态",
    )
    direction = Column(
        SAEnum(Direction, values_callable=lambda enum_cls: [e.value for e in enum_cls], name="post_direction", native_enum=False),
        default=Direction.SELL,
        nullable=False,
        comment="方向",
    )
    urgency = Column(
        SAEnum(UrgencyLevel, values_callable=lambda enum_cls: [e.value for e in enum_cls], name="urgency_level", native_enum=False),
        default=UrgencyLevel.NORMAL,
        nullable=False,
        comment="紧急程度",
    )
    expire_time = Column(DateTime, nullable=True, comment="过期时间")
    is_deleted = Column(Boolean, default=False, nullable=False, comment="是否软删除")
    create_time = Column(DateTime, default=beijing_now_for_model, nullable=False, comment="创建时间")
    update_time = Column(DateTime, default=beijing_now_for_model, onupdate=beijing_now_for_model, nullable=False, comment="更新时间")

    user = relationship("User", back_populates="posts", lazy="selectin")
    category = relationship("Category", back_populates="posts", lazy="selectin")
    orders = relationship(
        "Order",
        primaryjoin="and_(foreign(Order.item_id) == Post.post_id, Order.item_type == 'POST')",
        viewonly=True,
        lazy="selectin",
    )
    comments = relationship(
        "Comment",
        primaryjoin="and_(foreign(Comment.target_id) == Post.post_id, Comment.target_type == 'POST')",
        viewonly=True,
        lazy="selectin",
    )

    attachments = relationship(
        "Attachment",
        primaryjoin="and_(foreign(Attachment.target_id) == Post.post_id, Attachment.target_type == 'POST')",
        lazy="selectin",
        cascade="all, delete-orphan", # 当帖子删除时，自动清理附件记录
    )
    
    __table_args__ = (
        Index("idx_post_status_deleted_create_time", "status", "is_deleted", create_time.desc()),
        Index("idx_post_urgency_status_deleted_create", "urgency", "status", "is_deleted", create_time.desc()),
        Index("idx_post_direction_status_deleted", "direction", "status", "is_deleted"),
        Index("idx_post_title", "title"),  # 支持标题前缀搜索
    )

    @property
    def max_accepters(self) -> int:
        """从 template_data 中安全读取最大接单数，默认 1。"""
        try:
            if not self.template_data:
                return 1
            val = self.template_data.get("max_accepters") or self.template_data.get("max_acceptors")
            return int(val) if val is not None else 1
        except Exception:
            return 1