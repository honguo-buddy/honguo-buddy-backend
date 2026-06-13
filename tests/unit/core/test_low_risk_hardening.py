import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core import settings
import app.services.wechat_notification_service as wechat_notification_module
from app.services import WeChatNotificationService


ROOT_DIR = Path(__file__).resolve().parents[3]


def _load_function_node(file_path: Path, function_name: str) -> ast.AsyncFunctionDef | ast.FunctionDef:
    tree = ast.parse(file_path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == function_name:
            return node
    raise AssertionError(f"未找到函数: {function_name}")


def test_auto_suspend_job_has_no_function_local_imports():
    """定时任务函数不得存在函数内延迟导入。"""
    node = _load_function_node(ROOT_DIR / "app" / "main.py", "_auto_suspend_expired_items_job")

    nested_imports = [
        item
        for item in ast.walk(node)
        if isinstance(item, (ast.Import, ast.ImportFrom))
    ]

    assert nested_imports == []


def test_chat_send_message_has_no_function_local_imports():
    """聊天发信路由不得存在函数内延迟导入。"""
    node = _load_function_node(ROOT_DIR / "app" / "api" / "chat.py", "send_message")

    nested_imports = [
        item
        for item in ast.walk(node)
        if isinstance(item, (ast.Import, ast.ImportFrom))
    ]

    assert nested_imports == []


def test_cors_origins_are_loaded_from_settings():
    """CORS 允许来源必须从 settings.CORS_ALLOW_ORIGINS 读取。"""
    main_source = (ROOT_DIR / "app" / "main.py").read_text(encoding="utf-8")

    assert "allow_origins=settings.CORS_ALLOW_ORIGINS" in main_source
    assert 'allow_origins=["http://localhost:5000", "http://localhost:3000"]' not in main_source
    assert settings.CORS_ALLOW_ORIGINS == ["http://localhost:5000", "http://localhost:3000"]


def test_lifespan_shutdown_closes_wechat_httpx_client():
    """应用 shutdown 分支必须主动关闭微信通知服务的 httpx client。"""
    main_source = (ROOT_DIR / "app" / "main.py").read_text(encoding="utf-8")

    assert "await WeChatNotificationService.close_httpx_client()" in main_source


def test_lifespan_startup_ensures_attachment_sort_order_column():
    """应用 startup 分支必须补齐 attachment.sort_order 物理列。"""
    main_source = (ROOT_DIR / "app" / "main.py").read_text(encoding="utf-8")

    assert "await AttachmentService.ensure_sort_order_column(db)" in main_source


@pytest.mark.asyncio
async def test_wechat_notification_httpx_client_can_be_closed():
    """应用 shutdown 时必须能关闭微信通知服务的模块级 httpx client。"""
    close_calls = []

    async def fake_aclose() -> None:
        close_calls.append("closed")

    wechat_notification_module._httpx_client = SimpleNamespace(aclose=fake_aclose)

    await WeChatNotificationService.close_httpx_client()

    assert close_calls == ["closed"]
    assert wechat_notification_module._httpx_client is None
