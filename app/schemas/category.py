import enum
"""Category（模板分类）相关请求与响应模型。"""

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, field_validator


class CategoryDirection(str, enum.Enum):
    """交易方向枚举。"""
    SELL = "SELL"
    BUY = "BUY"


class CategoryCreate(BaseModel):
    """创建模板分类请求。"""

    name: str = Field(..., min_length=1, max_length=100, description="分类名称")
    icon: Optional[str] = Field(default=None, max_length=255, description="分类图标（可选）")
    item_type: str = Field(default="POST", description="业务类型：POST/GOODS")
    direction: Optional[CategoryDirection] = Field(default=None, description="交易方向：SELL/BUY（POST可选，GOODS强制SELL）")
    # 给 config_json 赋予默认 factory，前端不传时自动默认为空字典，不卡接口
    config_json: Optional[Dict[str, Any]] = Field(default_factory=dict, description="模板配置 JSON")

    @field_validator("config_json")
    @classmethod
    def validate_config_json(cls, value: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        # 如果是 None，自动初始化为空字典
        if value is None:
            value = {}
            
        if not isinstance(value, dict):
            raise ValueError("config_json 必须是对象")
            
        # 如果前端传了 {} 或空，后端在校验层全自动将其“升维归一”为标准的无模版格式
        # 这样既过了非空校验，又保证了落地入库的数据结构绝对安全，前端 map 循环永不崩
        if not value or "fields" not in value:
            return {"fields": []}
            
        return value

    @field_validator("item_type")
    @classmethod
    def validate_item_type(cls, v: str) -> str:
        if v is None:
            return "POST"
        text = str(v).upper()
        if text not in {"POST", "GOODS"}:
            raise ValueError("item_type 必须为 POST 或 GOODS")
        return text


class CategoryUpdate(BaseModel):
    """更新模板分类请求。"""

    name: Optional[str] = Field(default=None, min_length=1, max_length=100, description="分类名称")
    item_type: Optional[str] = Field(default=None, description="业务类型：POST/GOODS（可选）")
    icon: Optional[str] = Field(default=None, max_length=255, description="分类图标（可选）")
    # 更新时同样赋予全自动防腐兜底
    config_json: Optional[Dict[str, Any]] = Field(default_factory=dict, description="模板配置 JSON")

    @field_validator("config_json")
    @classmethod
    def validate_config_json(cls, value: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if value is None:
            value = {}
        if not isinstance(value, dict):
            raise ValueError("config_json 必须是对象")
            
        # 同步自动归一化
        if not value or "fields" not in value:
            return {"fields": []}
            
        return value

    @field_validator("item_type")
    @classmethod
    def validate_item_type(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        text = str(v).upper()
        if text not in {"POST", "GOODS"}:
            raise ValueError("item_type 必须为 POST 或 GOODS")
        return text


class CategoryRead(BaseModel):
    """模板分类响应模型。"""

    category_id: int
    name: str
    icon: Optional[str] = None
    item_type: str
    direction: str
    config_json: Dict[str, Any]
    create_time: datetime
    update_time: datetime

    model_config = {"from_attributes": True}
