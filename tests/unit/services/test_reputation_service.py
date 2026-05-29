"""ReputationService 单元测试。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest




class TestReputationService:
    @pytest.mark.asyncio
    async def test_get_user_reputation_from_cache_hit(self):
        from app.services.reputation_service import ReputationService
        import json

        db = SimpleNamespace()
        redis_fake = MagicMock()
        cached = json.dumps({"user_id": 1001, "carrier_score": 4.5, "carrier_order_count": 10,
                              "client_score": 4.8, "client_order_count": 5, "tags_json": "{}"})
        redis_fake.get = AsyncMock(return_value=cached)
        redis_fake.setex = AsyncMock()

        result = await ReputationService.get_user_reputation(redis_fake, db, 1001)
        assert result["user_id"] == 1001
        assert result["carrier_score"] == 4.5
        # Should not have called setex since cache hit
        redis_fake.setex.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_user_reputation_from_db_miss(self):
        """缓存未命中时回数据库重算（使用 mock 数据库）。"""
        from app.services.reputation_service import ReputationService

        db = MagicMock()
        # Mock the aggregate query results
        row1 = SimpleNamespace(cnt=5, avg_rating=4.2)
        db.execute = AsyncMock(return_value=MagicMock(one=MagicMock(return_value=row1), scalar_one=MagicMock(return_value=0)))

        redis_fake = MagicMock()
        redis_fake.get = AsyncMock(return_value=None)
        redis_fake.setex = AsyncMock()

        result = await ReputationService.get_user_reputation(redis_fake, db, 1001)
        assert result["user_id"] == 1001
        # Should have written cache since it was a miss
        redis_fake.setex.assert_called_once()

    def test_mask_name_single_char(self):
        from app.services.reputation_service import _mask_name

        assert _mask_name("张") == "张**"
        assert _mask_name("李明") == "李**"
        assert _mask_name("张晓明") == "张**"
        assert _mask_name("匿名用户") == "匿名用户"
        assert _mask_name("") == "匿名用户"
        assert _mask_name(None) == "匿名用户"
