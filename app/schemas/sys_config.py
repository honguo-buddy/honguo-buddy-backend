"""系统动态配置 Schema。"""

from pydantic import BaseModel, Field


class SysConfigUpdateRequest(BaseModel):
    """动态配置更新请求。"""

    config_value: str = Field(..., min_length=1, max_length=512, description="新的配置值")


class SysConfigRead(BaseModel):
    """动态配置响应。"""

    config_key: str
    config_value: str
    config_type: str
    description: str

    model_config = {"from_attributes": True}
