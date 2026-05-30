import pytest
from pydantic import ValidationError

from app.schemas.category import CategoryCreate, CategoryUpdate


def test_category_create_accepts_valid_payload():
    schema = CategoryCreate.model_validate(
        {"name": "跑腿", "item_type": "goods", "config_json": {"fields": ["address"]}}
    )

    assert schema.item_type == "GOODS"
    assert schema.config_json == {"fields": ["address"]}


def test_category_create_empty_config_json_normalizes():
    """Empty config_json is normalized to {"fields": []} instead of rejected."""
    schema = CategoryCreate.model_validate({"name": "跑腿", "config_json": {}})
    assert schema.config_json == {"fields": []}


def test_category_create_rejects_invalid_item_type():
    with pytest.raises(ValidationError) as exc_info:
        CategoryCreate.model_validate({"name": "跑腿", "item_type": "invalid", "config_json": {"x": 1}})

    assert "必须为 POST 或 GOODS" in str(exc_info.value)


def test_category_create_validator_can_be_called_directly():
    with pytest.raises(ValueError) as exc_info:
        CategoryCreate.validate_config_json([])

    assert "config_json 必须是对象" in str(exc_info.value)


def test_category_update_accepts_optional_item_type_none():
    """item_type=None is accepted; config_json gets normalized if missing 'fields'."""
    schema = CategoryUpdate.model_validate({"config_json": {"fields": ["price"]}})
    assert schema.item_type is None
    assert schema.config_json == {"fields": ["price"]}

    # config without 'fields' key gets normalized
    schema2 = CategoryUpdate.model_validate({"config_json": {"enabled": True}})
    assert schema2.config_json == {"fields": []}


def test_category_update_rejects_non_dict_config_json():
    with pytest.raises(ValueError) as exc_info:
        CategoryUpdate.validate_config_json([])

    assert "config_json 必须是对象" in str(exc_info.value)


def test_category_update_rejects_invalid_item_type():
    with pytest.raises(ValidationError) as exc_info:
        CategoryUpdate.model_validate({"item_type": "other", "config_json": {"enabled": True}})

    assert "item_type 必须为 POST 或 GOODS" in str(exc_info.value)