"""测试断言辅助函数。"""

from app.core import settings


def assert_api_success(body: dict, *, code: int = settings.SUCCESS_CODE):
    assert body["code"] == code
    assert "message" in body
    return body["message"]


def assert_api_error(body: dict, *, code: int):
    assert body["code"] == code
    assert isinstance(body["message"], dict)
    assert "error" in body["message"]
    assert "msg" in body["message"]
    return body["message"]