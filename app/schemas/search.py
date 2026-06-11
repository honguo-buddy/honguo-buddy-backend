"""全局搜索相关 Schema。"""

import enum
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.schemas.user import UserRead


class SearchTab(str, enum.Enum):
    """全局搜索 Tab。"""

    ALL = "ALL"
    BUY_POST = "BUY_POST"
    SELL_POST = "SELL_POST"
    GOODS = "GOODS"


class SearchSort(str, enum.Enum):
    """全局搜索排序方式。"""

    DEFAULT = "DEFAULT"
    FAVORITE = "FAVORITE"
    COMMENT = "COMMENT"
    VIEW = "VIEW"


class SearchTime(str, enum.Enum):
    """全局搜索时间跨度。"""

    ALL = "ALL"
    ONE_DAY = "1D"
    SEVEN_DAYS = "7D"
    HALF_YEAR = "180D"


class GlobalSearchItem(BaseModel):
    """全局搜索统一卡片。"""

    id: int
    item_type: str = Field(description="BUY_POST / SELL_POST / GOODS")
    title: str
    description: Optional[str] = None
    price: Optional[float] = None
    status: str
    create_time: datetime
    template_data: dict[str, Any] = Field(default_factory=dict, description="由分类模板驱动的业务自定义动态键值对")
    hit_tips: Optional[str] = Field(None, description="非标题/描述字段命中时的动态中文高亮提示语，如：在【取件地址】中匹配到")
    view_count: int = 0
    favorite_count: int = 0
    comment_count: int = 0
    publisher: Optional[UserRead] = None


class GlobalSearchResponse(BaseModel):
    """全局搜索分页响应。"""

    total: int
    page: int
    page_size: int
    list: list[GlobalSearchItem]
