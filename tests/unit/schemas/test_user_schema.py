"""Schema validation unit tests."""

from types import SimpleNamespace
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.schemas.user import UserSelfUpdateRequest, user as UserSchema


async def test_user_schema_converts_uuid_bytes_to_string():
    obj = SimpleNamespace(
        user_id=1,
        user_uuid=b"1234567890123456",
        user_name="schema-user",
        email="schema@example.com",
        is_admin=False,
        is_verified=True,
        user_type="user",
    )

    schema = UserSchema.model_validate(obj)

    assert schema.user_id == 1
    assert schema.user_uuid == "31323334-3536-3738-3930-313233343536"
    assert schema.user_name == "schema-user"
    assert schema.is_verified is True


async def test_user_schema_accepts_uuid_object():
    obj = SimpleNamespace(
        user_id=2,
        user_uuid=UUID("12345678-1234-5678-1234-567812345678"),
        user_name="uuid-user",
        email="uuid@example.com",
        is_admin=False,
        is_verified=False,
        user_type="user",
    )

    schema = UserSchema.model_validate(obj)

    assert schema.user_uuid == "12345678-1234-5678-1234-567812345678"


def test_user_self_update_rejects_invalid_sex():
    with pytest.raises(ValidationError) as exc_info:
        UserSelfUpdateRequest.model_validate({"sex": "外星人"})

    assert "性别必须为以下之一" in str(exc_info.value)


def test_user_self_update_accepts_none_and_valid_sex():
    none_schema = UserSelfUpdateRequest.model_validate({"sex": None})
    valid_schema = UserSelfUpdateRequest.model_validate({"sex": "男"})

    assert none_schema.sex is None
    assert valid_schema.sex == "男"
