"""聊天服务：会话初始化、消息发送、历史拉取、已读与撤回。"""

from __future__ import annotations

from datetime import timedelta
from typing import Optional, Sequence

from sqlalchemy import and_, case, func, not_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import BusinessHTTPException, ResourceHTTPException, get_now_naive, settings
from app.models import Attachment, AttachmentTargetType, ChatMessage, ChatSession, ItemType, Order, OrderStatus, Post, User
from app.services.attachment_service import AttachmentService


class ChatService:
    """IM 私信业务层。"""

    RECALL_TTL_SECONDS = 120
    RECALL_TEXT = "对方撤回了一条消息"

    @staticmethod
    def _normalize_pair(user_one_id: int, user_two_id: int) -> tuple[int, int]:
        first_id = min(user_one_id, user_two_id)
        second_id = max(user_one_id, user_two_id)
        return first_id, second_id

    @staticmethod
    async def _get_session_or_none(db: AsyncSession, user_one_id: int, user_two_id: int) -> ChatSession | None:
        stmt = select(ChatSession).where(
            ChatSession.user_one_id == user_one_id,
            ChatSession.user_two_id == user_two_id,
        )
        result = await db.execute(stmt)
        return result.scalars().first()

    @staticmethod
    async def init_session(
        db: AsyncSession,
        current_user_id: int,
        peer_id: int,
        context_type: Optional[str] = None,
        context_id: Optional[int] = None,
    ) -> ChatSession:
        if peer_id == current_user_id:
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="不能和自己创建会话")

        peer_stmt = select(User.user_id).where(User.user_id == peer_id, User.is_deleted == False)
        peer_res = await db.execute(peer_stmt)
        if peer_res.scalar_one_or_none() is None:
            raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="对方用户不存在")

        user_one_id, user_two_id = ChatService._normalize_pair(current_user_id, peer_id)
        existing = await ChatService._get_session_or_none(db, user_one_id, user_two_id)
        if existing:
            return existing

        session = ChatSession(
            user_one_id=user_one_id,
            user_two_id=user_two_id,
            context_type=context_type,
            context_id=context_id,
            last_message_content=None,
            last_message_time=None,
        )
        db.add(session)
        await db.flush()
        await db.refresh(session)
        await db.commit()
        return session

    @staticmethod
    async def list_sessions(db: AsyncSession, current_user_id: int, exclude_peer_ids: list[int] | None = None) -> list[ChatSession]:
        unread_subquery = (
            select(
                ChatMessage.session_id.label("session_id"),
                func.count(ChatMessage.message_id).label("unread_count"),
            )
            .where(
                ChatMessage.sender_id != current_user_id,
                ChatMessage.is_read == False,
            )
            .group_by(ChatMessage.session_id)
            .subquery()
        )

        stmt = (
            select(ChatSession, func.coalesce(unread_subquery.c.unread_count, 0).label("unread_count"))
            .outerjoin(unread_subquery, unread_subquery.c.session_id == ChatSession.session_id)
            .where(
                or_(
                    ChatSession.user_one_id == current_user_id,
                    ChatSession.user_two_id == current_user_id,
                )
            )
            .order_by(
                case((ChatSession.last_message_time.is_(None), 1), else_=0),
                ChatSession.last_message_time.desc(),
                ChatSession.session_id.desc(),
            )
        )
        result = await db.execute(stmt)
        rows = result.all()
        sessions: list[ChatSession] = []
        for session, unread_count in rows:
            session.unread_count = int(unread_count or 0)  # type: ignore[attr-defined]
            session.peer_id = session.user_two_id if session.user_one_id == current_user_id else session.user_one_id  # type: ignore[attr-defined]
            sessions.append(session)
        # 黑名单过滤：排除会话对方拉黑了当前用户的会话
        if exclude_peer_ids:
            sessions = [s for s in sessions if s.peer_id not in exclude_peer_ids]
        return sessions

    @staticmethod
    async def get_total_unread_count(db: AsyncSession, current_user_id: int) -> int:
        """聚合当前用户所有私信会话中的未读消息总数。"""
        session_ids_stmt = select(ChatSession.session_id).where(
            or_(
                ChatSession.user_one_id == current_user_id,
                ChatSession.user_two_id == current_user_id,
            )
        )
        session_ids_res = await db.execute(session_ids_stmt)
        session_ids = [int(row[0]) for row in session_ids_res.all() if row[0] is not None]
        if not session_ids:
            return 0

        unread_stmt = select(func.count()).select_from(ChatMessage).where(
            ChatMessage.session_id.in_(session_ids),
            ChatMessage.sender_id != current_user_id,
            ChatMessage.is_read == False,
        )
        unread_res = await db.execute(unread_stmt)
        return int(unread_res.scalar_one() or 0)

    @staticmethod
    async def _validate_session_membership(db: AsyncSession, session_id: int, current_user_id: int) -> ChatSession:
        stmt = select(ChatSession).where(ChatSession.session_id == session_id)
        result = await db.execute(stmt)
        session = result.scalars().first()
        if not session:
            raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="会话不存在")
        if current_user_id not in {session.user_one_id, session.user_two_id}:
            raise BusinessHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="无权访问该会话")
        return session

    @staticmethod
    async def send_message(
        db: AsyncSession,
        current_user_id: int,
        session_id: int,
        content: str,
        attachment_ids: Optional[list[int]] = None,
        quote_message_id: Optional[int] = None,
        context_type: Optional[str] = None,
        context_id: Optional[int] = None,
    ) -> ChatMessage:
        session = await ChatService._validate_session_membership(db, session_id, current_user_id)

        resolved_context_type = context_type if context_type is not None else session.context_type
        resolved_context_id = context_id if context_id is not None else session.context_id

        quote_message = None
        if quote_message_id is not None:
            quote_stmt = select(ChatMessage).where(ChatMessage.message_id == quote_message_id)
            quote_res = await db.execute(quote_stmt)
            quote_message = quote_res.scalars().first()
            if not quote_message or quote_message.session_id != session_id:
                raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="引用消息不属于当前会话")

        message = ChatMessage(
            session_id=session_id,
            sender_id=current_user_id,
            content=content,
            context_type=resolved_context_type,
            context_id=resolved_context_id,
            is_read=False,
            is_recalled=False,
            is_deleted_by_sender=False,
            is_deleted_by_receiver=False,
            quote_message_id=quote_message.message_id if quote_message else quote_message_id,
        )
        db.add(message)
        await db.flush()

        if attachment_ids:
            await AttachmentService.bind_attachments_to_target(
                db=db,
                attachment_ids=attachment_ids,
                target_type=AttachmentTargetType.CHAT.value,
                target_id=message.message_id,
                creator_id=current_user_id,
            )

        session_stmt = select(ChatSession).where(ChatSession.session_id == session_id)
        session_res = await db.execute(session_stmt)
        session = session_res.scalars().first()
        if session:
            session.last_message_content = content
            session.last_message_time = get_now_naive()

        await db.flush()
        await db.refresh(message)
        await db.commit()
        return message

    @staticmethod
    async def get_messages(
        db: AsyncSession,
        current_user_id: int,
        session_id: int,
        cursor: Optional[int] = None,
        size: int = 20,
    ) -> tuple[list[ChatMessage], Optional[int]]:
        await ChatService._validate_session_membership(db, session_id, current_user_id)

        visible_clause = or_(
            and_(ChatMessage.sender_id == current_user_id, ChatMessage.is_deleted_by_sender == False),
            and_(ChatMessage.sender_id != current_user_id, ChatMessage.is_deleted_by_receiver == False),
        )
        conditions = [ChatMessage.session_id == session_id, visible_clause]
        if cursor is not None:
            conditions.append(ChatMessage.message_id < cursor)

        stmt = (
            select(ChatMessage)
            .where(and_(*conditions))
            .order_by(ChatMessage.message_id.desc())
            .limit(size + 1)
        )
        result = await db.execute(stmt)
        messages = result.scalars().all()

        next_cursor = None
        if len(messages) > size:
            messages = messages[:size]
            next_cursor = messages[-1].message_id

        return list(reversed(messages)), next_cursor

    @staticmethod
    async def mark_session_read(db: AsyncSession, current_user_id: int, session_id: int) -> int:
        session = await ChatService._validate_session_membership(db, session_id, current_user_id)
        peer_id = session.user_two_id if session.user_one_id == current_user_id else session.user_one_id

        count_stmt = select(func.count()).select_from(ChatMessage).where(
            ChatMessage.session_id == session_id,
            ChatMessage.sender_id == peer_id,
            ChatMessage.is_read == False,
        )
        count_res = await db.execute(count_stmt)
        unread_count = int(count_res.scalar_one() or 0)
        if unread_count == 0:
            await db.commit()
            return 0

        stmt = (
            update(ChatMessage)
            .where(
                ChatMessage.session_id == session_id,
                ChatMessage.sender_id == peer_id,
                ChatMessage.is_read == False,
            )
            .values(is_read=True)
            .execution_options(synchronize_session=False)
        )
        await db.execute(stmt)
        await db.commit()
        return unread_count

    @staticmethod
    async def recall_message(db: AsyncSession, current_user_id: int, message_id: int) -> ChatMessage:
        stmt = select(ChatMessage).where(ChatMessage.message_id == message_id)
        result = await db.execute(stmt)
        message = result.scalars().first()
        if not message:
            raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="消息不存在")
        if message.sender_id != current_user_id:
            raise BusinessHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="只能撤回自己发送的消息")

        now = get_now_naive()
        if now - message.create_time > timedelta(seconds=ChatService.RECALL_TTL_SECONDS):
            raise BusinessHTTPException(code=settings.REQ_ERROR_CODE, msg="消息已超过2分钟撤回时限")

        message.is_recalled = True
        message.content = ChatService.RECALL_TEXT

        session_stmt = select(ChatSession).where(ChatSession.session_id == message.session_id)
        session_res = await db.execute(session_stmt)
        session = session_res.scalars().first()
        if session and session.last_message_time == message.create_time:
            session.last_message_content = ChatService.RECALL_TEXT

        await db.flush()
        await db.commit()
        return message

    @staticmethod
    async def delete_local_message(db: AsyncSession, current_user_id: int, message_id: int) -> ChatMessage:
        stmt = select(ChatMessage).where(ChatMessage.message_id == message_id)
        result = await db.execute(stmt)
        message = result.scalars().first()
        if not message:
            raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="消息不存在")

        if message.sender_id == current_user_id:
            message.is_deleted_by_sender = True
        else:
            await ChatService._validate_session_membership(db, message.session_id, current_user_id)
            message.is_deleted_by_receiver = True

        await db.flush()
        await db.commit()
        return message

    @staticmethod
    async def get_message_attachment_urls_map(db: AsyncSession, message_ids: Sequence[int]) -> dict[int, list[str]]:
        if not message_ids:
            return {}

        stmt = (
            select(Attachment.target_id, Attachment.url)
            .where(
                Attachment.target_type == AttachmentTargetType.CHAT,
                Attachment.target_id.in_(list(message_ids)),
                Attachment.is_deleted == False,
            )
            .order_by(Attachment.attachment_id.asc())
        )
        result = await db.execute(stmt)
        rows = result.all()
        attachment_map: dict[int, list[str]] = {int(message_id): [] for message_id in message_ids}
        for target_id, url in rows:
            if target_id is None:
                continue
            attachment_map.setdefault(int(target_id), []).append(AttachmentService.to_public_url(url) or url)
        return attachment_map

    @staticmethod
    async def broadcast_post_message(
        db: AsyncSession,
        post_id: int,
        sender_id: int,
        content: str,
        attachment_ids: Optional[list[int]] = None,
    ) -> dict:
        """流式扇出：发帖人向所有已录用买家逐一发送 1v1 私信。"""
        post_stmt = select(Post).where(Post.post_id == post_id, Post.is_deleted == False)
        post_res = await db.execute(post_stmt)
        post = post_res.scalars().first()
        if not post:
            raise ResourceHTTPException(code=settings.DATA_GET_FAILED_CODE, msg="帖子不存在")
        if post.publisher_id != sender_id:
            raise BusinessHTTPException(code=settings.INSUFFICIENT_AUTHORITY_CODE, msg="仅发帖人可以群发消息")
        order_stmt = (
            select(Order.buyer_id)
            .where(
                Order.item_type == ItemType.POST,
                Order.item_id == post_id,
                Order.status == OrderStatus.ONGOING,
                Order.is_deleted == False,
            )
            .distinct()
        )
        order_res = await db.execute(order_stmt)
        buyer_ids = [int(row[0]) for row in order_res.all() if row[0] is not None]
        sent_count = 0
        for buyer_id in buyer_ids:
            try:
                session = await ChatService.init_session(
                    db=db, current_user_id=sender_id, peer_id=buyer_id,
                    context_type="POST", context_id=post_id,
                )
                await ChatService.send_message(
                    db=db, current_user_id=sender_id, session_id=session.session_id,
                    content=content, attachment_ids=attachment_ids,
                    context_type="POST", context_id=post_id,
                )
                sent_count += 1
            except Exception:
                continue
        return {"sent_count": sent_count, "buyer_ids": buyer_ids}
