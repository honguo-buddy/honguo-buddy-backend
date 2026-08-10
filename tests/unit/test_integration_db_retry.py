from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError

from tests.integration.conftest import _connect_test_engine_with_retry


class _FakeOrigError(Exception):
    pass


def _build_operational_error(message: str) -> OperationalError:
    return OperationalError("SELECT 1", {}, _FakeOrigError(message))


@pytest.mark.asyncio
async def test_connect_test_engine_with_retry_recovers_from_transient_mysql_disconnect():
    success_connection = SimpleNamespace()

    class FakeEngine:
        def __init__(self):
            self.calls = 0

        async def connect(self):
            self.calls += 1
            if self.calls < 3:
                raise _build_operational_error(
                    "Lost connection to MySQL server during query ([WinError 121])"
                )
            return success_connection

    engine = FakeEngine()

    result = await _connect_test_engine_with_retry(engine, max_attempts=3, retry_delay_seconds=0)

    assert result is success_connection
    assert engine.calls == 3


@pytest.mark.asyncio
async def test_connect_test_engine_with_retry_does_not_swallow_non_transient_errors():
    class FakeEngine:
        async def connect(self):
            raise _build_operational_error("Unknown column 'x' in 'field list'")

    with pytest.raises(OperationalError):
        await _connect_test_engine_with_retry(FakeEngine(), max_attempts=3, retry_delay_seconds=0)
