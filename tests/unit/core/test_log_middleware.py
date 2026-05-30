from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.datastructures import URL

from app.core.log_middleware import LogMiddleware, save_log_to_db


pytestmark = pytest.mark.asyncio


async def test_save_log_to_db_commit_and_rollback(monkeypatch):
    ok_session = SimpleNamespace(add=MagicMock(), commit=AsyncMock(), refresh=AsyncMock(), rollback=AsyncMock())

    async def ok_get_db():
        yield ok_session

    monkeypatch.setattr("app.core.log_middleware.get_db", ok_get_db)
    await save_log_to_db({"user_id": 1, "url": "/x", "ip": "127.0.0.1", "ua": "ua", "method": "GET", "status_code": 200, "response_code": 0, "duration_ms": 10})
    assert ok_session.commit.await_count == 1

    bad_session = SimpleNamespace(
        add=MagicMock(side_effect=RuntimeError("db failed")),
        commit=AsyncMock(),
        refresh=AsyncMock(),
        rollback=AsyncMock(),
    )

    async def bad_get_db():
        yield bad_session

    monkeypatch.setattr("app.core.log_middleware.get_db", bad_get_db)
    await save_log_to_db({"user_id": 1, "url": "/x", "ip": "127.0.0.1", "ua": "ua", "method": "GET", "status_code": 200, "response_code": 0, "duration_ms": 10})
    assert bad_session.rollback.await_count == 1


async def test_log_middleware_dispatch_dedup_and_response_parse(monkeypatch, fake_redis):
    middleware = LogMiddleware(app=MagicMock())
    monkeypatch.setattr("app.core.log_middleware.redis", fake_redis)
    monkeypatch.setattr("app.core.log_middleware.get_user_id_from_request", AsyncMock(return_value=123))
    save_mock = AsyncMock()
    monkeypatch.setattr("app.core.log_middleware.save_log_to_db", save_mock)

    request = SimpleNamespace(
        url=URL("http://test.local/hello"),
        client=SimpleNamespace(host="127.0.0.1"),
        headers={"user-agent": "pytest"},
        method="GET",
        cookies={},
        query_params={},
    )

    async def iterator_json():
        yield json.dumps({"code": 0, "message": {"ok": True}}).encode("utf-8")

    response = SimpleNamespace(status_code=200, body_iterator=iterator_json())

    async def call_next(_request):
        return response

    result = await middleware.dispatch(request, call_next)
    assert result.status_code == 200
    assert save_mock.await_count == 1

    chunks = []
    async for chunk in result.body_iterator:
        chunks.append(chunk)
    assert b"\"code\": 0" in b"".join(chunks)

    save_mock.reset_mock()
    result2 = await middleware.dispatch(request, call_next)
    assert result2.status_code == 200
    assert save_mock.await_count == 0
