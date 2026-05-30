"""纯净 Base 声明模块 — 仅定义 SQLAlchemy declarative_base，不导入任何模型。

与 app/db/base.py 的职责边界：
- base_class.py：只声明 Base，供所有 model 文件安全导入，绝无循环依赖风险
- base.py：导入 Base + 所有模型 + 引擎/Redis/依赖注入，供 Alembic 和 app 入口使用
"""
from sqlalchemy.orm import declarative_base

Base = declarative_base()
