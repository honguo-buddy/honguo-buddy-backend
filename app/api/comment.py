"""评论 API 路由层。"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import get_current_user, get_current_user_optional
from app.core import settings
from app.db import get_db
from app.schemas import ResponseModel
from app.schemas.comment import (
    CommentCreateRequest,
    CommentResponse,
    CommentListResponse,
    CommentReplyListResponse,
    CommentWithReplyCountResponse,
    CommentReplyPreview,
)
from app.services import BlacklistService, CommentService
from app.schemas.user import user as UserSchema

router = APIRouter()


def _build_comment_response(comment, attachment_urls: list[str]) -> CommentResponse:
    return CommentResponse(
        comment_id=comment.comment_id,
        user_id=comment.user_id,
        target_type=comment.target_type.value,
        target_id=comment.target_id,
        parent_id=comment.parent_id,
        content=comment.content,
        is_deleted=comment.is_deleted,
        create_time=comment.create_time,
        update_time=comment.update_time,
        attachment_urls=attachment_urls,
    )


def _build_comment_with_reply_count_response(comment, reply_count: int, preview_replies, attachment_urls: list[str]) -> CommentWithReplyCountResponse:
    return CommentWithReplyCountResponse(
        comment_id=comment.comment_id,
        user_id=comment.user_id,
        target_type=comment.target_type.value,
        target_id=comment.target_id,
        content=comment.content,
        is_deleted=comment.is_deleted,
        create_time=comment.create_time,
        update_time=comment.update_time,
        reply_count=reply_count,
        preview_replies=preview_replies,
        attachment_urls=attachment_urls,
    )


@router.post("", response_model=ResponseModel[CommentResponse])
async def create_comment(
    req: CommentCreateRequest,
    current_user: UserSchema = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """发布评论或回复。
    
    - 如果 parent_id 为 null，则发布主题根评论
    - 如果 parent_id 不为 null，则发布对该评论的回复
    
    需要登录。
    """
    comment = await CommentService.create_comment(
        db=db,
        user_id=current_user.user_id,
        target_type=req.target_type,
        target_id=req.target_id,
        content=req.content,
        parent_id=req.parent_id,
        attachment_ids=req.attachment_ids,
    )

    attachment_urls_map = await CommentService.get_comment_attachment_urls_map(db, [comment.comment_id])
    
    return ResponseModel(
        code=settings.SUCCESS_CODE,
        message=_build_comment_response(comment, attachment_urls_map.get(comment.comment_id, [])),
    )


@router.delete("/{comment_id}", response_model=ResponseModel[dict])
async def delete_comment(
    comment_id: int,
    current_user: UserSchema = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """软删除评论。
    
    仅限评论所有者（comment.user_id == current_user.user_id）或管理员可操作。
    删除后，该评论及其子回复的内容将被替换为"该评论已由用户删除"。
    
    需要登录。
    """
    await CommentService.delete_comment(
        db=db,
        comment_id=comment_id,
        current_user_id=current_user.user_id,
        is_admin=current_user.is_admin,
    )
    
    return ResponseModel(
        code=settings.SUCCESS_CODE,
        message={"message": "评论已删除"},
    )


@router.get("/{comment_id}/replies", response_model=ResponseModel[CommentReplyListResponse])
async def get_replies(
    comment_id: int,
    cursor: Optional[int] = Query(None, description="游标：上一页最后一条回复的ID"),
    size: int = Query(20, ge=1, le=100, description="每页大小"),
    current_user: Optional[UserSchema] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """获取单条根评论下的所有回复（按时间正序）。
    
    当用户点击"查看全部回复"时调用此接口。
    返回平铺的回复列表，按创建时间正序排列。
    
    公开接口，无需登录。
    """
    # 黑名单过滤
    blocker_ids = []
    blocked_target_ids = []
    if current_user:
        blocker_ids = await BlacklistService.get_blocker_ids(db, current_user.user_id)
        blocked_target_ids = await BlacklistService.get_blocked_target_ids(db, current_user.user_id)
    exclude_user_ids = list(set(blocker_ids + blocked_target_ids))

    replies, next_cursor = await CommentService.get_replies(
        db=db,
        comment_id=comment_id,
        cursor=cursor,
        size=size,
        exclude_user_ids=exclude_user_ids if exclude_user_ids else None,
    )
    
    reply_ids = [r.comment_id for r in replies]
    attachment_urls_map = await CommentService.get_comment_attachment_urls_map(db, reply_ids)
    raw_items = [
        {
            "comment_id": r.comment_id,
            "user_id": r.user_id,
            "target_type": r.target_type.value,
            "target_id": r.target_id,
            "parent_id": r.parent_id,
            "content": r.content,
            "is_deleted": r.is_deleted,
            "create_time": r.create_time,
            "update_time": r.update_time,
            "attachment_urls": attachment_urls_map.get(r.comment_id, []),
        }
        for r in replies
    ]
    items = [CommentResponse.model_validate(d) for d in raw_items]

    return ResponseModel(
        code=settings.SUCCESS_CODE,
        message=CommentReplyListResponse(items=items, next_cursor=next_cursor),
    )


@router.get("/{target_type}/{target_id}", response_model=ResponseModel[CommentListResponse])
async def get_root_comments(
    target_type: str,
    target_id: int,
    cursor: Optional[int] = Query(None, description="游标：上一页最后一条评论的ID"),
    size: int = Query(20, ge=1, le=100, description="每页大小"),
    current_user: Optional[UserSchema] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """获取目标的根评论列表（游标分页）。
    
    仅返回 parent_id 为 null 且 is_deleted=False 的顶级楼主评论。
    每个根评论包含该评论的回复总数和最新的2-3条回复预览。
    
    公开接口，无需登录。
    """
    # 黑名单过滤：获取拉黑了当前用户的用户 ID 列表
    blocker_ids = []
    blocked_target_ids = []
    if current_user:
        blocker_ids = await BlacklistService.get_blocker_ids(db, current_user.user_id)
        blocked_target_ids = await BlacklistService.get_blocked_target_ids(db, current_user.user_id)
    exclude_user_ids = list(set(blocker_ids + blocked_target_ids))

    comments, next_cursor = await CommentService.get_root_comments(
        db=db,
        target_type=target_type,
        target_id=target_id,
        cursor=cursor,
        size=size,
        exclude_user_ids=exclude_user_ids if exclude_user_ids else None,
    )
    
    # Batch fetch reply counts, attachment URLs, and preview replies in 3 queries total
    comment_ids = [c.comment_id for c in comments]
    reply_count_map = await CommentService.get_reply_count_map(db, comment_ids)
    attachment_urls_map = await CommentService.get_comment_attachment_urls_map(db, comment_ids)
    preview_replies_map = await CommentService.get_preview_replies_map(db, comment_ids, limit=3)

    # Linear dict pipeline: raw dict -> final validate once per item
    raw_items = []
    for comment in comments:
        cid = comment.comment_id
        preview_rows = preview_replies_map.get(cid, [])
        raw_items.append({
            "comment_id": cid,
            "user_id": comment.user_id,
            "target_type": comment.target_type.value,
            "target_id": comment.target_id,
            "content": comment.content,
            "is_deleted": comment.is_deleted,
            "create_time": comment.create_time,
            "update_time": comment.update_time,
            "reply_count": reply_count_map.get(cid, 0),
            "preview_replies": [
                CommentReplyPreview.model_validate(r) for r in preview_rows
            ],
            "attachment_urls": attachment_urls_map.get(cid, []),
        })

    items = [CommentWithReplyCountResponse.model_validate(d) for d in raw_items]
    
    return ResponseModel(
        code=settings.SUCCESS_CODE,
        message=CommentListResponse(items=items, next_cursor=next_cursor),
    )
