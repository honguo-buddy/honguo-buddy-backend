"""单元测试专属的 pytest fixture。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FakeScalarResult:
	items: list | None = None
	scalar_value: object | None = None

	def scalars(self):
		return self

	def unique(self):
		return self

	def first(self):
		return (self.items or [None])[0]

	def all(self):
		return list(self.items or [])

	def scalar_one_or_none(self):
		if self.scalar_value is not None:
			return self.scalar_value
		return (self.items or [None])[0]

	def scalar_one(self):
		value = self.scalar_one_or_none()
		if value is None:
			raise LookupError("no scalar value available")
		return value


@dataclass
class FakeResult:
	rows: list | None = None
	items: list | None = None
	scalar_value: object | None = None

	def scalars(self):
		return FakeScalarResult(items=self.items, scalar_value=self.scalar_value)

	def all(self):
		return list(self.rows or [])

	def scalar_one_or_none(self):
		if self.scalar_value is not None:
			return self.scalar_value
		if self.items:
			return self.items[0]
		return None

	def scalar_one(self):
		value = self.scalar_one_or_none()
		if value is None:
			raise LookupError("no scalar value available")
		return value


class AsyncContextManager:
	def __init__(self, value):
		self.value = value

	async def __aenter__(self):
		return self.value

	async def __aexit__(self, exc_type, exc, tb):
		return None
