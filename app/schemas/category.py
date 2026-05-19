"""Category（模板分类）相关请求与响应模型。"""

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, field_validator


class CategoryCreate(BaseModel):
    """创建模板分类请求。"""

    name: str = Field(..., min_length=1, max_length=100, description="分类名称")
    icon: Optional[str] = Field(default=None, max_length=255, description="分类图标（可选）")
    item_type: str = Field(default="POST", description="业务类型：POST/GOODS")
    config_json: Dict[str, Any] = Field(..., description="模板配置 JSON（必填，且不能为空对象）")

    @field_validator("config_json")
    @classmethod
    def validate_config_json(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("config_json 必须是对象")
        if not value:
            raise ValueError("config_json 不能为空")
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
    config_json: Dict[str, Any] = Field(..., description="模板配置 JSON（必填，且不能为空对象）")

    @field_validator("config_json")
    @classmethod
    def validate_config_json(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("config_json 必须是对象")
        if not value:
            raise ValueError("config_json 不能为空")
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
    config_json: Dict[str, Any]
    create_time: datetime
    update_time: datetime

    model_config = {"from_attributes": True}
