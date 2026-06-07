import enum

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Enum as SAEnum, ForeignKey, Index, Numeric, String, Text, JSON,and_
from sqlalchemy.orm import foreign, relationship

from app.core.datetime_utils import beijing_now_for_model
from app.db_base import Base

# 1. 闲置商品三种状态枚举
class GoodsStatus(enum.Enum):
    ON_SALE = "上架中"    # 状态一：大厅正常可见，允许评论、发起私信，完全激活
    OFF_SHELF = "已下架"  # 状态二：卖家手动暂存/下架，大厅隐藏，仅自己可见，可随时“重新上架”
    SOLD = "已售出"       # 状态三：已被买家拿下，大厅隐藏或置灰，转为历史信用画像凭证

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
    
    # 用统一的业务状态机
    status = Column(
        SAEnum(GoodsStatus, values_callable=lambda enum_cls: [e.value for e in enum_cls], name="goods_status", native_enum=False),
        default=GoodsStatus.ON_SALE,
        nullable=False,
        comment="商品业务状态（上架中/已下架/已售出）",
    )
    
    is_deleted = Column(Boolean, default=False, nullable=False, comment="是否软删除（作为底层物理防御）")
    create_time = Column(DateTime, default=beijing_now_for_model, nullable=False, comment="创建时间")
    update_time = Column(DateTime, default=beijing_now_for_model, onupdate=beijing_now_for_model, nullable=False, comment="更新时间")

# ------------------------------------------------------------------
    # 像素级严格对齐的 ORM 关系映射大盘（完美防御多态组件）
    # ------------------------------------------------------------------
    user = relationship("User", back_populates="goods")
    
    @property
    def publisher(self):
        """无缝映射给 Pydantic 响应 Schema 中的 publisher 字段"""
        return self.user
    
    category = relationship("Category", back_populates="goods")
    
    # 订单多态关系对齐：锁定项语义为 'GOODS'，开启只读视图，拒绝交叉污染
    orders = relationship(
        "Order",
        primaryjoin="and_(foreign(Order.item_id) == Goods.goods_id, Order.item_type == 'GOODS')",
        viewonly=True,
    )
    
    # 评论多态关系对齐：锁定盖楼目标为 'GOODS'
    comments = relationship(
        "Comment",
        primaryjoin="and_(foreign(Comment.target_id) == Goods.goods_id, Comment.target_type == 'GOODS')",
        viewonly=True,
    )

    # 级联附件红线对齐：商品软删/硬删时，其关联的多态媒体附件执行全自动全生命周期‘孤儿清理机制’
    attachments = relationship(
        "Attachment",
        primaryjoin="and_(foreign(Attachment.target_id) == Goods.goods_id, Attachment.target_type == 'GOODS')",
        overlaps="attachments",
        cascade="all, delete-orphan",
    )
    
    __table_args__ = (
        # 极致性能组合索引：覆盖大厅高频条件过滤、软删防御及时间倒序排序
        Index("idx_goods_status_deleted_create_time", "status", "is_deleted", create_time.desc()),
        # 商品标题搜索索引：完美支撑校园集市全词前缀级高效扫描
        Index("idx_goods_name", "name"),
    )

class GoodsMetrics(Base):
    """商品计数器分表：物理隔离 goods 主表，避免写放大污染业务查询。

    与 goods 表通过 goods_id 保持 1:1 锁死关联。
    """
    __tablename__ = "goods_metrics"

    goods_id = Column(BigInteger, ForeignKey("goods.goods_id", ondelete="CASCADE"), primary_key=True, comment="商品ID（1:1关联goods表）")
    view_count = Column(BigInteger, default=0, nullable=False, comment="浏览次数")
    favorite_count = Column(BigInteger, default=0, nullable=False, comment="收藏次数")
    comment_count = Column(BigInteger, default=0, nullable=False, comment="评论次数")
    create_time = Column(DateTime, default=beijing_now_for_model, nullable=False, comment="创建时间")
    update_time = Column(DateTime, default=beijing_now_for_model, onupdate=beijing_now_for_model, nullable=False, comment="更新时间")