import enum

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Enum as SAEnum, ForeignKey, Index, Numeric, String, Text, JSON,and_
from sqlalchemy.orm import foreign, relationship

from app.core.datetime_utils import beijing_now_for_model
from app.db.base import Base

# 商品成色枚举
class GoodsCondition(enum.Enum):
    BRAND_NEW = "全新"
    NEAR_NEW = "准新/99新"
    USED_WELL = "常用/无明显瑕疵"
    USED_HEAVILY = "陈旧/明显瑕疵"


class Goods(Base):
    __tablename__ = "goods"

    goods_id = Column(BigInteger, primary_key=True, autoincrement=True, comment="商品主键")
    publisher_id = Column(BigInteger, ForeignKey("user.user_id"), nullable=False, index=True, comment="发布者ID")
    category_id = Column(BigInteger, ForeignKey("category.category_id"), nullable=False, index=True, comment="分类ID")
    
    name = Column(String(255), nullable=False, comment="商品名称")
    description = Column(Text, nullable=True, comment="简短描述/帖子内容")
    price = Column(Numeric(10, 2), nullable=True, comment="商品价格，NULL表示面议")
    
    condition = Column(
        SAEnum(GoodsCondition, values_callable=lambda enum_cls: [e.value for e in enum_cls], name="goods_condition", native_enum=False),
        default=GoodsCondition.BRAND_NEW,
        nullable=False,
        comment="粗略成色等级",
    )
    
    template_data = Column(JSON, nullable=True, comment="由 category 驱动的详细成色/规格数据")
    
    is_sold = Column(Boolean, default=False, nullable=False, comment="是否已售出")
    is_deleted = Column(Boolean, default=False, nullable=False, comment="是否软删除")
    create_time = Column(DateTime, default=beijing_now_for_model, nullable=False, comment="创建时间")
    update_time = Column(DateTime, default=beijing_now_for_model, onupdate=beijing_now_for_model, nullable=False, comment="更新时间")

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

    attachments = relationship(
        "Attachment",
        primaryjoin="and_(foreign(Attachment.target_id) == Goods.goods_id, Attachment.target_type == 'GOODS')",
        lazy="selectin",
        cascade="all, delete-orphan", #帖子删除时，附件也删除
    )
    
    __table_args__ = (
        Index("idx_goods_is_sold_deleted_create_time", "is_sold", "is_deleted", create_time.desc()),
    )