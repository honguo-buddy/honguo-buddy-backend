from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from app.core import settings
from app.core.exception_handler import register_exception_handlers


pytestmark = pytest.mark.asyncio


async def test_global_exception_handler_masks_raw_exception_detail():
    app = FastAPI()
    register_exception_handlers(app)
    handler = app.exception_handlers[Exception]

    response = await handler(
        SimpleNamespace(),
        RuntimeError("secret-db-password leaked from driver"),
    )

    assert response.status_code == 200
    assert response.body
    body_text = response.body.decode("utf-8")
    assert str(settings.UNKNOWN_ERROR_CODE) in body_text
    assert "secret-db-password" not in body_text
    assert "leaked from driver" not in body_text
    assert "服务器内部错误，请稍后重试" in body_text
