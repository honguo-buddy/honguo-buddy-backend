"""聊天 API 路由层。"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import get_current_user
from app.core import settings
from app.db import get_db
from app.schemas import (
	ChatBroadcastRequest,
	ChatMessageCreateRequest,
	ChatMessageListResponse,
	ChatMessageRead,
	ChatRecallResponse,
	ChatSessionInitRequest,
	ChatSessionListResponse,
	ChatSessionRead,
	ResponseModel,
)
from app.schemas.user import user as UserSchema
from app.services import ChatService

router = APIRouter()


def _build_session_read(session, current_user_id: int) -> ChatSessionRead:
	peer_id = session.user_two_id if session.user_one_id == current_user_id else session.user_one_id
	return ChatSessionRead(
		session_id=session.session_id,
		user_one_id=session.user_one_id,
		user_two_id=session.user_two_id,
		peer_id=peer_id,
		context_type=session.context_type,
		context_id=session.context_id,
		last_message_content=session.last_message_content,
		last_message_time=session.last_message_time,
		unread_count=int(getattr(session, "unread_count", 0)),
	)


def _build_message_read(message, attachment_urls: list[str]) -> ChatMessageRead:
	return ChatMessageRead(
		message_id=message.message_id,
		session_id=message.session_id,
		sender_id=message.sender_id,
		content=message.content,
		context_type=message.context_type,
		context_id=message.context_id,
		is_read=message.is_read,
		is_recalled=message.is_recalled,
		is_deleted_by_sender=message.is_deleted_by_sender,
		is_deleted_by_receiver=message.is_deleted_by_receiver,
		quote_message_id=message.quote_message_id,
		create_time=message.create_time,
		attachment_urls=attachment_urls,
	)


@router.post("/sessions/init", response_model=ResponseModel[ChatSessionRead])
async def init_session(
	req: ChatSessionInitRequest,
	current_user: UserSchema = Depends(get_current_user),
	db: AsyncSession = Depends(get_db),
):
	session = await ChatService.init_session(
		db=db,
		current_user_id=current_user.user_id,
		peer_id=req.peer_id,
		context_type=req.context_type,
		context_id=req.context_id,
	)
	session.unread_count = 0  # type: ignore[attr-defined]
	return ResponseModel(code=settings.SUCCESS_CODE, message=_build_session_read(session, current_user.user_id))


@router.get("/sessions", response_model=ResponseModel[ChatSessionListResponse])
async def list_sessions(
	current_user: UserSchema = Depends(get_current_user),
	db: AsyncSession = Depends(get_db),
):
	sessions = await ChatService.list_sessions(db=db, current_user_id=current_user.user_id)
	items = [_build_session_read(session, current_user.user_id) for session in sessions]
	return ResponseModel(code=settings.SUCCESS_CODE, message=ChatSessionListResponse(items=items))


@router.post("/messages", response_model=ResponseModel[ChatMessageRead])
async def send_message(
	req: ChatMessageCreateRequest,
	current_user: UserSchema = Depends(get_current_user),
	db: AsyncSession = Depends(get_db),
):
	message = await ChatService.send_message(
		db=db,
		current_user_id=current_user.user_id,
		session_id=req.session_id,
		content=req.content,
		attachment_ids=req.attachment_ids,
		quote_message_id=req.quote_message_id,
		context_type=req.context_type,
		context_id=req.context_id,
	)
	attachment_urls_map = await ChatService.get_message_attachment_urls_map(db, [message.message_id])
	return ResponseModel(
		code=settings.SUCCESS_CODE,
		message=_build_message_read(message, attachment_urls_map.get(message.message_id, [])),
	)


@router.get("/sessions/{session_id}/messages", response_model=ResponseModel[ChatMessageListResponse])
async def get_messages(
	session_id: int,
	cursor: Optional[int] = Query(None, description="游标：上一页最后一条消息ID"),
	size: int = Query(20, ge=1, le=100, description="每页大小"),
	current_user: UserSchema = Depends(get_current_user),
	db: AsyncSession = Depends(get_db),
):
	messages, next_cursor = await ChatService.get_messages(
		db=db,
		current_user_id=current_user.user_id,
		session_id=session_id,
		cursor=cursor,
		size=size,
	)
	attachment_urls_map = await ChatService.get_message_attachment_urls_map(db, [m.message_id for m in messages])
	items = [_build_message_read(m, attachment_urls_map.get(m.message_id, [])) for m in messages]
	return ResponseModel(code=settings.SUCCESS_CODE, message=ChatMessageListResponse(items=items, next_cursor=next_cursor))


@router.patch("/sessions/{session_id}/read", response_model=ResponseModel[dict])
async def read_session(
	session_id: int,
	current_user: UserSchema = Depends(get_current_user),
	db: AsyncSession = Depends(get_db),
):
	unread_count = await ChatService.mark_session_read(db=db, current_user_id=current_user.user_id, session_id=session_id)
	return ResponseModel(code=settings.SUCCESS_CODE, message={"session_id": session_id, "unread_count": unread_count})


@router.patch("/messages/{message_id}/recall", response_model=ResponseModel[ChatRecallResponse])
async def recall_message(
	message_id: int,
	current_user: UserSchema = Depends(get_current_user),
	db: AsyncSession = Depends(get_db),
):
	message = await ChatService.recall_message(db=db, current_user_id=current_user.user_id, message_id=message_id)
	return ResponseModel(
		code=settings.SUCCESS_CODE,
		message=ChatRecallResponse(message_id=message.message_id, is_recalled=message.is_recalled, content=message.content),
	)


@router.delete("/messages/{message_id}/local", response_model=ResponseModel[dict])
async def delete_local_message(
	message_id: int,
	current_user: UserSchema = Depends(get_current_user),
	db: AsyncSession = Depends(get_db),
):
	message = await ChatService.delete_local_message(db=db, current_user_id=current_user.user_id, message_id=message_id)
	return ResponseModel(
		code=settings.SUCCESS_CODE,
		message={
			"message_id": message.message_id,
			"is_deleted_by_sender": message.is_deleted_by_sender,
			"is_deleted_by_receiver": message.is_deleted_by_receiver,
		},
	)

@router.post("/messages/broadcast-post", response_model=ResponseModel[dict])
async def broadcast_post_message(
    payload: ChatBroadcastRequest,
    current_user: UserSchema = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """发帖人向所有已录用买家群发 1v1 私信（流式扇出）。"""
    result = await ChatService.broadcast_post_message(
        db=db,
        post_id=payload.post_id,
        sender_id=current_user.user_id,
        content=payload.content,
        attachment_ids=payload.attachment_ids,
    )
    return ResponseModel(code=settings.SUCCESS_CODE, message=result)

