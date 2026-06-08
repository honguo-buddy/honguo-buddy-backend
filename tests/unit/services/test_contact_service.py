"""ContactService 单元测试。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exception_handler import BusinessHTTPException, ResourceHTTPException
from app.services.contact_service import ContactService


pytestmark = pytest.mark.asyncio


def _make_result(value=None):
    """构造一个 sync 风格的 mock result，避免 AsyncMock 属性返回协程。"""
    result = MagicMock()
    result.scalars.return_value.all.return_value = value or []
    result.scalar_one_or_none.return_value = None
    return result


class TestContactService:

    async def test_list_contacts_empty(self):
        db = MagicMock()
        db.execute = AsyncMock(return_value=_make_result([]))
        result = await ContactService.list_contacts(db, 1)
        assert result == []

    async def test_list_contacts(self):
        fake = [MagicMock(contact_id=1), MagicMock(contact_id=2)]
        db = MagicMock()
        db.execute = AsyncMock(return_value=_make_result(fake))
        result = await ContactService.list_contacts(db, 1)
        assert len(result) == 2

    async def test_upsert_contact_create_new(self):
        db = MagicMock()
        db.execute = AsyncMock(return_value=_make_result())  # scalar_one_or_none → None
        db.add = MagicMock()
        db.commit = AsyncMock()
        db.refresh = AsyncMock()

        contact = await ContactService.upsert_contact(db, 1, "PHONE", "13800138000", True)
        db.add.assert_called_once()
        db.commit.assert_called_once()

    async def test_upsert_contact_update_existing(self):
        existing = MagicMock(contact_id=1, user_id=1, contact_type="PHONE")
        result = MagicMock()
        result.scalar_one_or_none.return_value = existing

        db = MagicMock()
        db.execute = AsyncMock(return_value=result)
        db.commit = AsyncMock()
        db.refresh = AsyncMock()

        await ContactService.upsert_contact(db, 1, "PHONE", "13900139000", False)
        assert existing.contact_value == "13900139000"
        assert existing.is_public is False

    async def test_delete_contact_not_found(self):
        db = MagicMock()
        db.execute = AsyncMock(return_value=_make_result())  # scalar_one_or_none → None

        with pytest.raises(ResourceHTTPException):
            await ContactService.delete_contact(db, 1, 999)

    async def test_delete_contact_success(self):
        result = MagicMock()
        result.scalar_one_or_none.return_value = MagicMock()
        db = MagicMock()
        db.execute = AsyncMock(return_value=result)
        db.delete = AsyncMock()
        db.commit = AsyncMock()

        await ContactService.delete_contact(db, 1, 1)
        db.delete.assert_called_once()