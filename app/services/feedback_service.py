"""意见反馈业务服务层。"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Feedback


class FeedbackService:
    """意见反馈服务。"""

    @staticmethod
    async def create_feedback(
        db: AsyncSession,
        content: str,
        feedback_type: str | None = None,
        contact_info: str | None = None,
        user_id: int | None = None,
    ) -> Feedback:
        """提交反馈并落库。"""
        feedback = Feedback(
            user_id=user_id,
            content=content,
            feedback_type=feedback_type,
            contact_info=contact_info,
        )
        db.add(feedback)
        await db.commit()
        await db.refresh(feedback)
        return feedback