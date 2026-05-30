"""History 相关 Schema 单元测试。"""
import pytest
from pydantic import ValidationError


class TestHistoryDeletePayload:
    def test_single_mode_requires_target_fields(self):
        from app.schemas.history import HistoryDeletePayload

        with pytest.raises(ValidationError) as exc_info:
            HistoryDeletePayload.model_validate({"action_type": "SINGLE"})
        assert "target_type" in str(exc_info.value) or "target_id" in str(exc_info.value)

    def test_single_mode_valid(self):
        from app.schemas.history import HistoryDeletePayload

        payload = HistoryDeletePayload.model_validate({
            "action_type": "SINGLE",
            "target_type": "POST",
            "target_id": 1001,
        })
        assert payload.action_type == "SINGLE"
        assert payload.target_type == "POST"
        assert payload.target_id == 1001

    def test_range_mode_requires_time_fields(self):
        from app.schemas.history import HistoryDeletePayload

        with pytest.raises(ValidationError):
            HistoryDeletePayload.model_validate({"action_type": "RANGE"})

    def test_range_mode_start_greater_than_end(self):
        from app.schemas.history import HistoryDeletePayload

        with pytest.raises(ValidationError) as exc_info:
            HistoryDeletePayload.model_validate({
                "action_type": "RANGE",
                "start_time": 1700000002000,
                "end_time": 1700000001000,
            })
        assert "开始时间不得大于结束时间" in str(exc_info.value)

    def test_range_mode_valid(self):
        from app.schemas.history import HistoryDeletePayload

        payload = HistoryDeletePayload.model_validate({
            "action_type": "RANGE",
            "start_time": 1700000000000,
            "end_time": 1700000001000,
        })
        assert payload.action_type == "RANGE"
        assert payload.start_time == 1700000000000

    def test_clear_all_mode_no_extra_fields_required(self):
        from app.schemas.history import HistoryDeletePayload

        payload = HistoryDeletePayload.model_validate({"action_type": "CLEAR_ALL"})
        assert payload.action_type == "CLEAR_ALL"
        assert payload.target_type is None
        assert payload.target_id is None

    def test_invalid_action_type(self):
        from app.schemas.history import HistoryDeletePayload

        with pytest.raises(ValidationError):
            HistoryDeletePayload.model_validate({"action_type": "INVALID"})
