from app.api.attachment import ATTACHMENT_UPLOAD_MAX_PART_SIZE
from app.core import settings


def test_attachment_upload_max_part_size_covers_business_limit():
    assert ATTACHMENT_UPLOAD_MAX_PART_SIZE == settings.ATTACHMENT_UPLOAD_MAX_PART_SIZE
    assert settings.ATTACHMENT_MAX_FILE_SIZE == 10 * 1024 * 1024
    assert ATTACHMENT_UPLOAD_MAX_PART_SIZE >= settings.ATTACHMENT_MAX_FILE_SIZE
