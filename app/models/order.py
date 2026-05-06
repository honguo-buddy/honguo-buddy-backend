import enum

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Enum as SAEnum, ForeignKey, Index, Numeric, func
from sqlalchemy.orm import relationship

from app.db.base import Base


class ItemType(enum.Enum):
    POST = "POST"
    GOODS = "GOODS"


class OrderStatus(enum.Enum):
    PENDING = "PENDING"
    ONGOING = "ONGOING"
    CONFIRMED = "CONFIRMED"
    COMPLETED = "COMPLETED"
    DISPUTED = "DISPUTED"


class Order(Base):
    __tablename__ = "order"

    order_id = Column(BigInteger, primary_key=True, autoincrement=True, comment="订单主键")
    buyer_id = Column(BigInteger, ForeignKey("user.user_id"), nullable=False, index=True, comment="买家ID")
    seller_id = Column(BigInteger, ForeignKey("user.user_id"), nullable=False, index=True, comment="卖家ID")
    item_type = Column(
        SAEnum(ItemType, values_callable=lambda enum_cls: [e.value for e in enum_cls], name="order_item_type", native_enum=False),
        nullable=False,
        comment="项目类型",
    )
    item_id = Column(BigInteger, nullable=False, comment="项目ID")
    amount = Column(Numeric(10, 2), nullable=False, comment="金额")
    status = Column(
        SAEnum(OrderStatus, values_callable=lambda enum_cls: [e.value for e in enum_cls], name="order_status", native_enum=False),
        default=OrderStatus.PENDING,
        nullable=False,
        comment="订单状态",
    )
    is_deleted = Column(Boolean, default=False, nullable=False, comment="是否软删除")
    create_time = Column(DateTime, default=func.now(), nullable=False, comment="创建时间")
    update_time = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False, comment="更新时间")

    buyer = relationship("User", foreign_keys=[buyer_id], back_populates="orders_as_buyer", lazy="selectin")
    seller = relationship("User", foreign_keys=[seller_id], back_populates="orders_as_seller", lazy="selectin")
    comments = relationship(
        "Comment",
        primaryjoin="and_(foreign(Comment.target_id) == Order.order_id, Comment.target_type == 'ORDER')",
        viewonly=True,
        lazy="selectin",
    )

    __table_args__ = (
        Index("idx_order_item_type_item_id", "item_type", "item_id"),
        Index("idx_order_buyer_id", "buyer_id"),
        Index("idx_order_seller_id", "seller_id"),
    )