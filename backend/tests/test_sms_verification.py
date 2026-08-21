from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from backend.app.application.sms_verification import (
    InMemoryVerificationAttemptLimiter,
    SmsPurpose,
    SmsVerificationAttemptLimits,
    SmsVerificationService,
)


class ToggleSmsProvider:
    def __init__(self) -> None:
        self.valid = False
        self.check_calls = 0

    async def send(self, phone: str, purpose: SmsPurpose) -> str:
        return "test-request"

    async def check(self, phone: str, purpose: SmsPurpose, code: str) -> bool:
        self.check_calls += 1
        return self.valid


class BlockingSmsProvider(ToggleSmsProvider):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def check(self, phone: str, purpose: SmsPurpose, code: str) -> bool:
        self.check_calls += 1
        self.started.set()
        await self.release.wait()
        return self.valid


@pytest.mark.asyncio
async def test_failed_code_checks_lock_phone_and_purpose_without_calling_provider() -> None:
    now = [datetime(2026, 8, 21, tzinfo=UTC)]
    provider = ToggleSmsProvider()
    service = SmsVerificationService(
        provider,
        attempt_limiter=InMemoryVerificationAttemptLimiter(
            SmsVerificationAttemptLimits(max_attempts=5, window_seconds=900, lock_seconds=900),
            clock=lambda: now[0],
        ),
    )

    for _ in range(5):
        assert not await service.check(
            phone="13800138000", purpose=SmsPurpose.REGISTER, code="000000"
        )
    assert provider.check_calls == 5

    assert not await service.check(
        phone="13800138000", purpose=SmsPurpose.REGISTER, code="000000"
    )
    assert provider.check_calls == 5

    now[0] += timedelta(minutes=15, seconds=1)
    provider.valid = True
    assert await service.check(
        phone="13800138000", purpose=SmsPurpose.REGISTER, code="123456"
    )
    assert provider.check_calls == 6


@pytest.mark.asyncio
async def test_successful_check_clears_failed_attempts() -> None:
    provider = ToggleSmsProvider()
    service = SmsVerificationService(provider)

    for _ in range(4):
        assert not await service.check(
            phone="13900139000", purpose=SmsPurpose.PASSWORD_RESET, code="000000"
        )
    provider.valid = True
    assert await service.check(
        phone="13900139000", purpose=SmsPurpose.PASSWORD_RESET, code="123456"
    )
    provider.valid = False
    for _ in range(5):
        assert not await service.check(
            phone="13900139000", purpose=SmsPurpose.PASSWORD_RESET, code="000000"
        )
    assert provider.check_calls == 10


@pytest.mark.asyncio
async def test_concurrent_code_checks_do_not_repeat_provider_call() -> None:
    provider = BlockingSmsProvider()
    service = SmsVerificationService(provider)
    first = asyncio.create_task(
        service.check(phone="13700137000", purpose=SmsPurpose.REGISTER, code="000000")
    )
    await provider.started.wait()

    assert not await service.check(
        phone="13700137000", purpose=SmsPurpose.REGISTER, code="000000"
    )
    assert provider.check_calls == 1

    provider.release.set()
    assert not await first
