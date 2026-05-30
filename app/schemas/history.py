"""历史足迹相关的请求和响应模型。"""
from typing import Literal, Optional

from pydantic import BaseModel, model_validator


class HistoryDeletePayload(BaseModel):
    """历史记录清理载荷：支持单条、范围、全量三种清理意图。"""

    action_type: Literal["SINGLE", "RANGE", "CLEAR_ALL"] = "SINGLE"
    target_type: Optional[str] = None
    target_id: Optional[int] = None
    start_time: Optional[int] = None
    end_time: Optional[int] = None

    @model_validator(mode="after")
    def validate_boundary(self):
        """校验清理参数边界逻辑。"""
        if self.action_type == "SINGLE":
            if self.target_type is None or self.target_id is None:
                raise ValueError("SINGLE 模式必须同时提供 target_type 和 target_id")
        elif self.action_type == "RANGE":
            if self.start_time is None or self.end_time is None:
                raise ValueError("RANGE 模式必须同时提供 start_time 和 end_time")
            if self.start_time > self.end_time:
                raise ValueError("开始时间不得大于结束时间")
        elif self.action_type == "CLEAR_ALL":
            pass
        return self


class HistoryDeleteResponse(BaseModel):
    """历史记录清理响应。"""

    action_type: str
    message: str
    deleted_count: int = 0
