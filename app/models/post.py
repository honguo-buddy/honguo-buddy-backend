import enum

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Enum as SAEnum, ForeignKey, Index, JSON, Numeric, String, Text, and_, func
from sqlalchemy.orm import foreign, relationship

from app.db.base import Base


class PostStatus(enum.Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class Direction(enum.Enum):
    SELL = "SELL"
    BUY = "BUY"


class Post(Base):
    __tablename__ = "post"

    post_id = Column(BigInteger, primary_key=True, autoincrement=True, comment="发布主键")
    publisher_id = Column(BigInteger, ForeignKey("user.user_id"), nullable=False, index=True, comment="发布者ID")
    category_id = Column(BigInteger, ForeignKey("category.category_id"), nullable=False, index=True, comment="分类ID")
    title = Column(String(255), nullable=False, comment="标题")
    price = Column(Numeric(10, 2), nullable=False, comment="金额")
    template_data = Column(JSON, nullable=True, comment="模板表单数据")
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
    expire_time = Column(DateTime, nullable=True, comment="过期时间")
    is_deleted = Column(Boolean, default=False, nullable=False, comment="是否软删除")
    create_time = Column(DateTime, default=func.now(), nullable=False, comment="创建时间")
    update_time = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False, comment="更新时间")

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
    )