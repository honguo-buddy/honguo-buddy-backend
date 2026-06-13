"""FeedbackService 单元测试。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.feedback_service import FeedbackService


pytestmark = pytest.mark.asyncio


class TestFeedbackService:

    async def test_create_feedback_anonymous(self):
        db = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()
        db.refresh = AsyncMock()

        feedback = await FeedbackService.create_feedback(
            db, content="测试反馈内容至少十字", feedback_type="BUG", contact_info="test@test.com"
        )
        db.add.assert_called_once()
        db.commit.assert_called_once()
        assert feedback is not None

    async def test_create_feedback_with_user(self):
        db = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()
        db.refresh = AsyncMock()

        feedback = await FeedbackService.create_feedback(
            db, content="用户提交的反馈至少十个字符", feedback_type="FEATURE", user_id=1
        )
        db.add.assert_called_once()
        db.commit.assert_called_once()
        assert feedback is not None