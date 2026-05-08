"""测试全局最小配置（仅保留跨层通用环境变量）。"""

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def setup_test_env():
    """设置所有测试共享的最小环境变量。"""
    os.environ.setdefault("DEBUG", "true")
    os.environ.setdefault("DEBUG_MASTER_PASSWORD", "test-master-password")
    os.environ.setdefault("DEBUG_SKIP_PASSWORD_CHECK", "false")

    yield
