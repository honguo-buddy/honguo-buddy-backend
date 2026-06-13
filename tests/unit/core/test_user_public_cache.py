from app.api.user import (
    _build_public_profile_cache_payload,
    _extract_valid_public_profile_cache_payload,
)
from app.core import settings


def test_public_profile_cache_payload_carries_version_and_public_fields():
    payload = _build_public_profile_cache_payload(
        {
            "user_id": 5,
            "user_uuid": "uuid-5",
            "user_name": "wyq",
            "avatar": "/static/avatar/user_5.webp",
            "sex": "男",
            "bio": "我是泉此方",
            "credit_score": 60,
            "is_verified": True,
            "user_type": "user",
        },
        5,
    )

    assert payload["_cache_version"] == settings.USER_PUBLIC_PROFILE_CACHE_VERSION
    assert payload["user_id"] == 5
    assert payload["user_name"] == "wyq"
    assert payload["bio"] == "我是泉此方"


def test_public_profile_cache_payload_rejects_legacy_cache_without_version():
    legacy_payload = {
        "user_id": 5,
        "user_uuid": "uuid-5",
        "user_name": None,
        "avatar": None,
        "sex": None,
        "credit_score": 0,
        "is_verified": False,
        "user_type": None,
    }

    assert _extract_valid_public_profile_cache_payload(legacy_payload) is None


def test_public_profile_cache_payload_accepts_current_version_and_strips_marker():
    cached_payload = {
        "_cache_version": settings.USER_PUBLIC_PROFILE_CACHE_VERSION,
        "user_id": 5,
        "user_uuid": "uuid-5",
        "user_name": "wyq",
        "avatar": "/static/avatar/user_5.webp",
        "sex": "男",
        "bio": "我是泉此方",
        "credit_score": 60,
        "is_verified": True,
        "user_type": "user",
    }

    result = _extract_valid_public_profile_cache_payload(cached_payload)

    assert result is not None
    assert "_cache_version" not in result
    assert result["user_name"] == "wyq"
    assert result["bio"] == "我是泉此方"
