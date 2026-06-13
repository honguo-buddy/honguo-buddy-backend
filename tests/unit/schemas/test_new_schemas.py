"""user_contact / user_blacklist / feedback Schema 单元测试。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.user_contact import ContactCreate, ContactRead, ContactListResponse
from app.schemas.user_blacklist import BlacklistCreate, BlacklistItem, BlacklistListResponse
from app.schemas.feedback import FeedbackCreate
from app.schemas.user import PhoneSendCodeRequest, PhoneBindRequest


class TestContactSchema:

    def test_contact_create_valid(self):
        c = ContactCreate(contact_type="WECHAT", contact_value="wx_abc", is_public=True)
        assert c.contact_type == "WECHAT"

    def test_contact_create_empty_type_fails(self):
        with pytest.raises(ValidationError):
            ContactCreate(contact_type="", contact_value="x")

    def test_contact_read_from_dict(self):
        c = ContactRead.model_validate({"contact_id": 1, "user_id": 1, "contact_type": "QQ", "contact_value": "123", "is_public": False})
        assert c.contact_id == 1
        assert c.is_public is False

    def test_contact_list_response(self):
        resp = ContactListResponse(list=[])
        assert resp.list == []


class TestBlacklistSchema:

    def test_blacklist_create(self):
        b = BlacklistCreate(target_id=5)
        assert b.target_id == 5

    def test_blacklist_item(self):
        item = BlacklistItem(user_id=1, target_id=2, target_name="test", create_time="2026-01-01T00:00:00")
        assert item.user_id == 1

    def test_blacklist_list_response(self):
        resp = BlacklistListResponse(total=0, page=1, page_size=20, list=[])
        assert resp.total == 0


class TestFeedbackSchema:

    def test_feedback_create_valid(self):
        f = FeedbackCreate(content="至少十个字的测试反馈内容", feedback_type="BUG")
        assert f.content is not None

    def test_feedback_create_too_short(self):
        with pytest.raises(ValidationError):
            FeedbackCreate(content="短")

    def test_feedback_create_optional_fields(self):
        f = FeedbackCreate(content="至少十个字的测试反馈啊啊啊啊")
        assert f.feedback_type is None
        assert f.contact_info is None


class TestPhoneSchema:

    def test_phone_send_code_valid(self):
        p = PhoneSendCodeRequest(phone="13800138000")
        assert p.phone == "13800138000"

    def test_phone_send_code_invalid(self):
        with pytest.raises(ValidationError):
            PhoneSendCodeRequest(phone="12345")

    def test_phone_bind_valid(self):
        p = PhoneBindRequest(phone="13800138000", code="123456")
        assert p.code == "123456"

    def test_phone_bind_code_too_short(self):
        with pytest.raises(ValidationError):
            PhoneBindRequest(phone="13800138000", code="123")