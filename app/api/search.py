"""全局搜索路由。"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import get_current_user_optional
from app.core import BusinessHTTPException, settings
from app.db import get_db, get_redis
from app.schemas import GlobalSearchResponse, ResponseModel, SearchSort, SearchTab, SearchTime, UserRead
from app.services import BlacklistService, SearchService

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/global", response_model=ResponseModel[GlobalSearchResponse])
async def global_search(
    keyword: Optional[str] = Query("", max_length=100, description="搜索关键词，为空或全空格时执行大厅流兜底查询"),
    tab: SearchTab = Query(SearchTab.ALL, description="搜索 Tab"),
    sort_by: SearchSort = Query(SearchSort.DEFAULT, description="排序方式"),
    time_range: SearchTime = Query(SearchTime.ALL, description="时间范围"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    current_user: Optional[UserRead] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
    redis_client=Depends(get_redis),
):
    """全局聚合搜索：公开访问，登录时自动排除双向黑名单内容。"""
    try:
        normalized_keyword = (keyword or "").strip()
        tokens = [token.strip() for token in normalized_keyword.split() if token.strip()]
        blocker_ids = []
        blocked_target_ids = []
        if current_user:
            blocker_ids = await BlacklistService.get_blocker_ids(db, current_user.user_id)
            blocked_target_ids = await BlacklistService.get_blocked_target_ids(db, current_user.user_id)
        exclude_ids = list(set(blocker_ids + blocked_target_ids))

        items, total = await SearchService.search_global(
            db,
            redis_client,
            tokens=tokens,
            tab=tab,
            sort_by=sort_by,
            time_range=time_range,
            page=page,
            page_size=page_size,
            exclude_publisher_ids=exclude_ids if exclude_ids else None,
        )
        return ResponseModel(
            code=settings.SUCCESS_CODE,
            message=GlobalSearchResponse(
                total=total,
                page=page,
                page_size=page_size,
                list=items,
            ),
        )
    except BusinessHTTPException:
        raise
    except Exception as exc:
        logger.error("全局搜索失败: %s", exc, exc_info=True)
        raise BusinessHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="全局搜索失败") from exc
