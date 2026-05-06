from sqlalchemy import Boolean, Column, DateTime, Index, JSON, BigInteger, String, func
from sqlalchemy.orm import relationship

from app.db.base import Base


class Category(Base):
    __tablename__ = "category"

    category_id = Column(BigInteger, primary_key=True, autoincrement=True, comment="分类主键")
    name = Column(String(100), nullable=False, comment="分类名称")
    icon = Column(String(255), nullable=True, comment="分类图标")
    config_json = Column(JSON, nullable=True, comment="模板配置JSON")
    is_deleted = Column(Boolean, default=False, nullable=False, comment="是否软删除")
    create_time = Column(DateTime, default=func.now(), nullable=False, comment="创建时间")
    update_time = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False, comment="更新时间")

    posts = relationship("Post", back_populates="category", lazy="selectin")
    goods = relationship("Goods", back_populates="category", lazy="selectin")

    __table_args__ = (Index("idx_category_name", "name"),)