"""Schema validation unit tests."""

from types import SimpleNamespace

import pytest

from app.schemas import user as UserSchema

pytestmark = pytest.mark.asyncio


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
