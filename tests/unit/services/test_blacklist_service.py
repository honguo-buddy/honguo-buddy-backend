"""BlacklistService 单元测试。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exception_handler import BusinessHTTPException, ResourceHTTPException
from app.services.blacklist_service import BlacklistService


pytestmark = pytest.mark.asyncio


def _make_scalar_result(value=None):
    r = MagicMock()
    r.scalar_one_or_none.return_value = value
    return r


class TestBlacklistService:

    async def test_add_self_raises(self):
        db = MagicMock()
        with pytest.raises(BusinessHTTPException) as exc_info:
            await BlacklistService.add_to_blacklist(db, 1, 1)
        assert "不能拉黑自己" in exc_info.value.detail["msg"]

    async def test_add_target_not_found(self):
        db = MagicMock()
        db.get = AsyncMock(return_value=None)
        with pytest.raises(ResourceHTTPException):
            await BlacklistService.add_to_blacklist(db, 1, 999)

    async def test_add_already_blacklisted(self):
        db = MagicMock()
        db.get = AsyncMock(return_value=MagicMock(is_deleted=False))
        db.execute = AsyncMock(return_value=_make_scalar_result(MagicMock()))
        with pytest.raises(BusinessHTTPException) as exc_info:
            await BlacklistService.add_to_blacklist(db, 1, 2)
        assert "已在黑名单中" in exc_info.value.detail["msg"]

    async def test_add_success(self):
        db = MagicMock()
        db.get = AsyncMock(return_value=MagicMock(is_deleted=False))
        db.execute = AsyncMock(return_value=_make_scalar_result(None))  # 不存在
        db.add = MagicMock()
        db.commit = AsyncMock()
        db.refresh = AsyncMock()

        entry = await BlacklistService.add_to_blacklist(db, 1, 2)
        db.add.assert_called_once()
        db.commit.assert_called_once()

    async def test_remove_not_found(self):
        db = MagicMock()
        db.execute = AsyncMock(return_value=_make_scalar_result(None))
        with pytest.raises(ResourceHTTPException):
            await BlacklistService.remove_from_blacklist(db, 1, 999)

    async def test_remove_success(self):
        db = MagicMock()
        db.execute = AsyncMock(return_value=_make_scalar_result(MagicMock()))
        db.delete = AsyncMock()
        db.commit = AsyncMock()

        await BlacklistService.remove_from_blacklist(db, 1, 2)
        db.delete.assert_called_once()

    async def test_list_blacklist_empty(self):
        db = MagicMock()
        count_result = MagicMock()
        count_result.scalar_one.return_value = 0
        entries_result = MagicMock()
        entries_result.scalars.return_value.all.return_value = []

        db.execute = AsyncMock(side_effect=[count_result, entries_result])
        result = await BlacklistService.list_blacklist(db, 1)
        assert result["total"] == 0
        assert result["list"] == []

    async def test_list_blacklist_with_entries(self):
        target = MagicMock(user_id=2, user_name="testuser", avatar_id=10, is_deleted=False)
        entry = MagicMock(user_id=1, target_id=2, target=target, create_time=MagicMock(isoformat=MagicMock(return_value="2026-01-01T00:00:00")))

        count_result = MagicMock()
        count_result.scalar_one.return_value = 1
        entries_result = MagicMock()
        entries_result.scalars.return_value.all.return_value = [entry]

        db = MagicMock()
        db.execute = AsyncMock(side_effect=[count_result, entries_result])

        with patch("app.services.blacklist_service.AttachmentService.get_urls_by_target", new_callable=AsyncMock) as mock_urls:
            mock_urls.return_value = {10: ["/static/av.png"]}
            result = await BlacklistService.list_blacklist(db, 1)

        assert result["total"] == 1
        assert result["list"][0]["target_name"] == "testuser"
        assert result["list"][0]["target_avatar"] == "/static/av.png"