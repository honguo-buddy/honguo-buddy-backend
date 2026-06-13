import pytest
from pydantic import ValidationError

from app.schemas.post import AttachmentBriefRead, PostCreate, PostDetailRead, PostUpdate


def test_post_create_accepts_valid_template_filters():
    schema = PostCreate.model_validate(
        {
            "title": "合规委托",
            "price": 10.5,
            "direction": "SELL",
            "urgency": "NORMAL",
            "template_filters": {"pickup_address": "宿舍楼"},
        }
    )

    assert schema.price == 10.5
    assert schema.template_filters == {"pickup_address": "宿舍楼"}


def test_post_create_rejects_non_dict_template_filters():
    with pytest.raises(ValueError) as exc_info:
        PostCreate.validate_template_filters([1, 2, 3])

    assert "template_filters 必须是对象" in str(exc_info.value)


def test_post_update_accepts_none_template_filters():
    schema = PostUpdate.model_validate({"title": "局部更新", "template_filters": None})

    assert schema.title == "局部更新"
    assert schema.template_filters is None


def test_post_update_rejects_non_dict_template_filters():
    with pytest.raises(ValueError) as exc_info:
        PostUpdate.validate_template_filters("bad")

    assert "template_filters 必须是对象" in str(exc_info.value)


def test_post_detail_supports_attachment_briefs():
    schema = PostDetailRead(
        post_id=1,
        title="详情",
        direction="SELL",
        urgency="NORMAL",
        status="OPEN",
        max_accepters=1,
        publisher_id=1,
        create_time="2026-06-16T10:00:00",
        attachment_urls=["/static/post/a.webp"],
        attachments=[AttachmentBriefRead(id=101, url="/static/post/a.webp")],
    )

    assert schema.attachments[0].id == 101
