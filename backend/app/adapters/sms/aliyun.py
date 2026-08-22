from __future__ import annotations

import logging
from typing import Any

from backend.app.application.phone_crypto import normalize_mainland_phone
from backend.app.application.sms_verification import SmsPurpose

logger = logging.getLogger(__name__)


class AliyunDypnsapiProvider:
    """Aliyun Dypnsapi seam.

    The optional SDK is imported lazily. This keeps local/test installs small while
    ensuring a production process fails clearly if the provider dependency is absent.
    """

    def __init__(
        self,
        *,
        access_key_id: str,
        access_key_secret: str,
        sign_name: str,
        template_code: str,
        template_param: str = '{"code":"##code##","min":"5"}',
        endpoint: str = "dypnsapi.aliyuncs.com",
    ) -> None:
        if not all((access_key_id, access_key_secret, sign_name, template_code)):
            raise ValueError("Aliyun Dypnsapi credentials and template are required")
        self._access_key_id = access_key_id
        self._access_key_secret = access_key_secret
        self._sign_name = sign_name
        self._template_code = template_code
        self._template_param = template_param
        self._endpoint = endpoint

    def _client(self) -> Any:
        try:
            from alibabacloud_dypnsapi20170525.client import Client
            from alibabacloud_tea_openapi.models import Config
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError("install alibabacloud-dypnsapi20170525 for Aliyun SMS") from exc
        return Client(Config(access_key_id=self._access_key_id, access_key_secret=self._access_key_secret, endpoint=self._endpoint))

    async def send(self, phone: str, purpose: SmsPurpose) -> str:
        del purpose
        normalized = normalize_mainland_phone(phone).removeprefix("+86")
        try:
            from alibabacloud_dypnsapi20170525 import models
            request = models.SendSmsVerifyCodeRequest(
                country_code="86",
                code_length=6,
                code_type=1,
                duplicate_policy=1,
                interval=60,
                phone_number=normalized,
                sign_name=self._sign_name,
                template_code=self._template_code,
                template_param=self._template_param,
                valid_time=300,
            )
            response = await self._client().send_sms_verify_code_async(request)
            body = getattr(response, "body", response)
            provider_code = str(getattr(body, "code", ""))
            if provider_code and provider_code not in {"OK", "200", "Success"}:
                logger.warning(
                    "aliyun sms provider rejected request",
                    extra={
                        "provider_code": provider_code,
                        "provider_message": str(getattr(body, "message", "")),
                        "provider_request_id": str(getattr(body, "request_id", "")),
                    },
                )
                raise RuntimeError(f"Aliyun SMS rejected request: {provider_code}")
            return str(getattr(body, "request_id", "aliyun-request"))
        except Exception as exc:
            if not (isinstance(exc, RuntimeError) and str(exc).startswith("Aliyun SMS rejected")):
                logger.exception("aliyun sms provider call failed")
            raise RuntimeError("Aliyun SMS send failed") from exc

    async def check(self, phone: str, purpose: SmsPurpose, code: str) -> bool:
        del purpose
        normalized = normalize_mainland_phone(phone).removeprefix("+86")
        try:
            from alibabacloud_dypnsapi20170525 import models
            request = models.CheckSmsVerifyCodeRequest(phone_number=normalized, verify_code=code)
            response = await self._client().check_sms_verify_code_async(request)
            body = getattr(response, "body", response)
            return str(getattr(body, "code", "")) in {"OK", "200", "Success"}
        except Exception as exc:
            raise RuntimeError("Aliyun SMS check failed") from exc
