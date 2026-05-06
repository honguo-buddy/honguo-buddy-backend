import enum

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Enum as SAEnum, ForeignKey, Index, Numeric, String, Text, and_, func
from sqlalchemy.orm import foreign, relationship

from app.db.base import Base


class GoodsCondition(enum.Enum):
    NEW = "NEW"
    EXCELLENT = "EXCELLENT"
    GOOD = "GOOD"
    FAIR = "FAIR"


class Goods(Base):
    __tablename__ = "goods"

    goods_id = Column(BigInteger, primary_key=True, autoincrement=True, comment="商品主键")
    publisher_id = Column(BigInteger, ForeignKey("user.user_id"), nullable=False, index=True, comment="发布者ID")
    category_id = Column(BigInteger, ForeignKey("category.category_id"), nullable=False, index=True, comment="分类ID")
    name = Column(String(255), nullable=False, comment="商品名称")
    description = Column(Text, nullable=True, comment="商品描述")
    price = Column(Numeric(10, 2), nullable=False, comment="商品价格")
    condition = Column(
        SAEnum(GoodsCondition, values_callable=lambda enum_cls: [e.value for e in enum_cls], name="goods_condition", native_enum=False),
        default=GoodsCondition.GOOD,
        nullable=False,
        comment="成色",
    )
    is_sold = Column(Boolean, default=False, nullable=False, comment="是否已售出")
    is_deleted = Column(Boolean, default=False, nullable=False, comment="是否软删除")
    create_time = Column(DateTime, default=func.now(), nullable=False, comment="创建时间")
    update_time = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False, comment="更新时间")

    user = relationship("User", back_populates="goods", lazy="selectin")
    category = relationship("Category", back_populates="goods", lazy="selectin")
    orders = relationship(
        "Order",
        primaryjoin="and_(foreign(Order.item_id) == Goods.goods_id, Order.item_type == 'GOODS')",
        viewonly=True,
        lazy="selectin",
    )
    comments = relationship(
        "Comment",
        primaryjoin="and_(foreign(Comment.target_id) == Goods.goods_id, Comment.target_type == 'GOODS')",
        viewonly=True,
        lazy="selectin",
    )

    __table_args__ = (
        Index("idx_goods_is_sold_deleted_create_time", "is_sold", "is_deleted", create_time.desc()),
    )