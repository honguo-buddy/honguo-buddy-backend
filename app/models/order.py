import enum

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Enum as SAEnum, ForeignKey, Index, JSON
from sqlalchemy.orm import relationship

from app.core.datetime_utils import beijing_now_for_model
from app.db.base import Base


class ItemType(enum.Enum):
    POST = "POST"    # 委托任务类
    GOODS = "GOODS"  # 二手商品类


class OrderTriggerType(enum.Enum):
    """订单触发/生成模式"""
    DIRECT = "DIRECT"           # 直接生成（如：确定性的下单）
    APPLICATION = "APPLICATION"  # 申请制（如：我请求接你的单，需你同意，即“双向确认”）
    COLLECTIVE = "COLLECTIVE"    # 征集制（如：乐跑征集，到点自动批量撮合）


class OrderStatus(enum.Enum):
    """
    PENDING: 待处理（申请中/征集中）
    ONGOING: 进行中（双方已达成一致，任务/交易履行中）
    CONFIRMED: 已确认（一方已完成，等待另一方确认）
    COMPLETED: 已完成（流程终结）
    CANCELED: 已取消（未达成协议或中途流产）
    DISPUTED: 争议中
    """
    PENDING = "PENDING"
    ONGOING = "ONGOING"
    CONFIRMED = "CONFIRMED"
    COMPLETED = "COMPLETED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    DISPUTED = "DISPUTED"


class Order(Base):
    __tablename__ = "order"

    order_id = Column(BigInteger, primary_key=True, autoincrement=True, comment="订单主键")
    
    # 核心参与方
    buyer_id = Column(BigInteger, ForeignKey("user.user_id"), nullable=False, index=True, comment="买家/需求方ID")
    seller_id = Column(BigInteger, ForeignKey("user.user_id"), nullable=False, index=True, comment="卖家/服务提供方ID")
    initiator_id = Column(BigInteger, nullable=True, comment="订单发起者ID（标识是谁主动发起的动作）")

    # 多态关联：关联 Post 或 Goods
    item_type = Column(
        SAEnum(ItemType, values_callable=lambda enum_cls: [e.value for e in enum_cls], name="order_item_type", native_enum=False),
        nullable=False,
        comment="项目类型",
    )
    item_id = Column(BigInteger, nullable=False, comment="项目ID")

    # 业务驱动核心：触发模式
    trigger_type = Column(
        SAEnum(OrderTriggerType, values_callable=lambda enum_cls: [e.value for e in enum_cls], name="order_trigger_type", native_enum=False),
        default=OrderTriggerType.DIRECT,
        nullable=False,
        comment="触发类型：直接/申请/征集",
    )

    # 状态与协议时间
    status = Column(
        SAEnum(OrderStatus, values_callable=lambda enum_cls: [e.value for e in enum_cls], name="order_status", native_enum=False),
        default=OrderStatus.PENDING,
        nullable=False,
        comment="订单状态",
    )
    accepted_time = Column(DateTime, nullable=True, comment="双方契约达成时间（双向确认时间/征集成功时间）")

    # 扩展字段：存放非标数据（如：征集批次号、接单备注、特殊要求）
    meta_data = Column(JSON, nullable=True, comment="订单元数据/扩展配置")

    # 基础元数据
    is_deleted = Column(Boolean, default=False, nullable=False, comment="是否软删除")
    create_time = Column(DateTime, default=beijing_now_for_model, nullable=False, comment="创建时间")
    update_time = Column(DateTime, default=beijing_now_for_model, onupdate=beijing_now_for_model, nullable=False, comment="更新时间")

    # 关系映射
    buyer = relationship("User", foreign_keys=[buyer_id], back_populates="orders_as_buyer", lazy="selectin")
    seller = relationship("User", foreign_keys=[seller_id], back_populates="orders_as_seller", lazy="selectin")
    
    comments = relationship(
        "Comment",
        primaryjoin="and_(foreign(Comment.target_id) == Order.order_id, Comment.target_type == 'ORDER')",
        viewonly=True,
        lazy="selectin",
    )
    reviews = relationship(
        "OrderReview",
        back_populates="order",
        lazy="selectin",
    )

    __table_args__ = (
        # 索引 1：用于通过项目查订单（如查看某个商品下所有的历史单据）
        Index("idx_order_item", "item_type", "item_id"),
        # 索引 2：用于用户中心查询我的买入/卖出单据
        Index("idx_order_buyer_id", "buyer_id"),
        Index("idx_order_seller_id", "seller_id"),
        # 索引 3：用于定时任务查询“待撮合”或“待确认”的单据，提高后台扫描效率
        Index("idx_order_status_trigger", "status", "trigger_type"),
    )