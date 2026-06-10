"""系统动态配置模型。"""

from sqlalchemy import Column, String

from app.db_base import Base


class SysConfig(Base):
    """存储可热更新的业务配置项。"""

    __tablename__ = "sys_config"

    config_key = Column(String(64), primary_key=True, comment="配置键")
    config_value = Column(String(512), nullable=False, comment="配置值")
    config_type = Column(String(20), nullable=False, comment="配置类型：int/float/str/bool")
    description = Column(String(255), nullable=False, default="", comment="配置说明")
