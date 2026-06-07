import enum
from sqlalchemy import Column, BigInteger, Integer, Text, DateTime, ForeignKey, Index, Boolean, Enum as SAEnum
from sqlalchemy.orm import relationship

from app.core.datetime_utils import beijing_now_for_model
from app.db_base import Base


class ReviewType(enum.Enum):
    INITIAL = "INITIAL"       # 首评
    ADDITIONAL = "ADDITIONAL" # 追评
    REPLY = "REPLY"           # 官方回评（被评方的解释/申诉）


class OrderReview(Base):
    """订单评价表：支持双盲互评、单向追评与官方解释回评"""
    __tablename__ = "order_review"

    review_id = Column(BigInteger, primary_key=True, autoincrement=True, comment="评价主键")
    order_id = Column(BigInteger, ForeignKey("order.order_id"), nullable=False, index=True, comment="关联的订单ID")
    
    reviewer_id = Column(BigInteger, ForeignKey("user.user_id"), nullable=False, comment="评价人ID（动作发起方）")
    reviewee_id = Column(BigInteger, ForeignKey("user.user_id"), nullable=False, comment="被评价人ID（接收方）")
    
    # 评价类型分流
    review_type = Column(
        SAEnum(ReviewType, values_callable=lambda enum_cls: [e.value for e in enum_cls], name="review_type", native_enum=False),
        nullable=False,
        default=ReviewType.INITIAL,
        comment="评价类型：INITIAL(首评), ADDITIONAL(追评), REPLY(回评)"
    )
    
    # 树状自关联（实现追评和回评的灵魂外键）
    # 如果是首评，parent_id 为 NULL；如果是追评或回评，必须指向对应首评的 review_id
    parent_id = Column(BigInteger, ForeignKey("order_review.review_id"), nullable=True, index=True, comment="父评价ID")
    
    # 星级锁死规则
    # 只有 review_type == INITIAL 时才允许传 1-5 星。追评和回评此字段必须强制为 NULL！
    # 作用：防止商家骚扰用户改星级，或者用户事后恶意改动初始评分，确保评分大盘数据稳固。
    rating = Column(Integer, nullable=True, comment="评分 (1-5星), 仅在首评阶段有效")
    
    content = Column(Text, nullable=True, comment="评价/追评/回评的内容文本")
    is_anonymous = Column(Boolean, default=False, nullable=False, comment="是否匿名发布（仅对首评和追评有效）")
    
    # 双盲控制路障
    # 默认 False。只有当双方都提交了 INITIAL 首评，或者超过 14 天双盲期，后端才会批量将当前订单的所有首评置为 True。
    # 作用：在 is_visible 为 False 时，对方在前端是看不到你给的评分和评语的，彻底阻断报复性差评。
    is_visible = Column(Boolean, default=False, nullable=False, index=True, comment="该评价是否对外公开可见")
    
    create_time = Column(DateTime, default=beijing_now_for_model, nullable=False, index=True)

    # 关系映射
    order = relationship("Order", back_populates="reviews")
    reviewer = relationship("User", foreign_keys=[reviewer_id])
    reviewee = relationship("User", foreign_keys=[reviewee_id])
    
    # 自关联关系：方便拉取首评时，把名下的追评和回评当成套娃子列表直接 selectin 一并捞出
    parent = relationship("OrderReview", remote_side=[review_id], back_populates="children")
    children = relationship("OrderReview", back_populates="parent", cascade="all, delete-orphan")

    __table_args__ = (
        # 索引加速：用户查自己的评价大盘，或者商品详情页拉取评价流
        Index("idx_order_review_order_type", "order_id", "review_type"),
        Index("idx_order_review_reviewee_visible", "reviewee_id", "is_visible"),
    )