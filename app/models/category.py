import enum
from sqlalchemy import Boolean, Column, DateTime, Index, JSON, BigInteger, String, Enum as SAEnum
from sqlalchemy.orm import relationship

from app.core.datetime_utils import beijing_now_for_model
from app.db_base import Base

# 为了保持与系统中其他多态类型一致，复用 ItemType 枚举
from app.models.order import ItemType




class CategoryDirection(str, enum.Enum):
    """交易方向枚举。"""
    SELL = "SELL"
    BUY = "BUY"

class Category(Base):
    __tablename__ = "category"

    category_id = Column(BigInteger, primary_key=True, autoincrement=True, comment="分类主键")
    name = Column(String(100), nullable=False, comment="分类名称")
    icon = Column(String(255), nullable=True, comment="分类图标")
    config_json = Column(JSON, nullable=False, comment="模板配置JSON")
    direction = Column(String(20), nullable=False, default=CategoryDirection.SELL.value, comment="交易方向：SELL/BUY")
    item_type = Column(
        SAEnum(ItemType, values_callable=lambda enum_cls: [e.value for e in enum_cls], name="category_item_type", native_enum=False),
        nullable=False,
        default=ItemType.POST,
        comment="适用业务类型：POST/GOODS",
    )
    is_deleted = Column(Boolean, default=False, nullable=False, comment="是否软删除")
    create_time = Column(DateTime, default=beijing_now_for_model, nullable=False, comment="创建时间")
    update_time = Column(DateTime, default=beijing_now_for_model, onupdate=beijing_now_for_model, nullable=False, comment="更新时间")

    posts = relationship("Post", back_populates="category")
    goods = relationship("Goods", back_populates="category")

    __table_args__ = (
        Index("idx_category_name", "name"),
        Index("idx_category_item_type", "item_type"),
    )
