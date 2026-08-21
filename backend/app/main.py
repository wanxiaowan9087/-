from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from time import perf_counter

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.middleware.base import RequestResponseEndpoint

from backend.app.adapters.auth.memory import MemoryIdentityStore
from backend.app.adapters.auth.sql import SqlIdentityStore
from backend.app.adapters.redis.client import OptionalRedisAdapter
from backend.app.adapters.sql.database import create_engine, create_session_factory
from backend.app.adapters.sql.repository import SqlPlatformRepository
from backend.app.adapters.uploads.avatar_store import AvatarStore
from backend.app.api.v1.routes import router
from backend.app.application.identity import IdentityService
from backend.app.application.phone_crypto import PhoneProtector
from backend.app.application.ports import RunExecutorPort
from backend.app.application.service import PlatformService
from backend.app.application.sms_verification import (
    InMemoryRateLimiter,
    InMemoryVerificationAttemptLimiter,
    RedisRateLimiter,
    RedisVerificationAttemptLimiter,
    SmsLimits,
    SmsVerificationAttemptLimits,
    SmsVerificationService,
)
from backend.app.application.usage_summary import UsageSummaryWorker
from backend.app.bootstrap import build_run_executor, build_sms_provider
from backend.app.core.config import Settings, get_settings
from backend.app.core.context import bind_context, new_request_id
from backend.app.core.errors import (
    AppError,
    app_error_handler,
    internal_error_handler,
    validation_error_handler,
)
from backend.app.core.logging import configure_logging
from backend.app.rag.knowledge_catalog import KnowledgeCatalog
from backend.app.repositories.ports import PlatformRepository

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    repository: PlatformRepository | None = None,
    executor: RunExecutorPort | None = None,
    redis_adapter: OptionalRedisAdapter | None = None,
    identity_service: IdentityService | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    engine: AsyncEngine | None = None
    if repository is None:
        engine = create_engine(settings)
        session_factory = create_session_factory(engine)
        repository_adapter: PlatformRepository = SqlPlatformRepository(
            session_factory, engine
        )
        resolved_identity_service = identity_service or IdentityService(
            SqlIdentityStore(session_factory), token_ttl_seconds=settings.auth_token_ttl_seconds
        )
    else:
        repository_adapter = repository
        resolved_identity_service = identity_service or IdentityService(
            MemoryIdentityStore(), token_ttl_seconds=settings.auth_token_ttl_seconds
        )
    executor_adapter = executor or build_run_executor(settings, repository_adapter, engine)
    knowledge_indexer = getattr(executor_adapter, "knowledge_indexer", None)
    redis_adapter = redis_adapter or OptionalRedisAdapter(
        settings.redis_url, settings.redis_timeout_seconds
    )
    service = PlatformService(
        repository_adapter,
        executor_adapter,
        cursor_secret=settings.cursor_signing_secret,
        idempotency_ttl_seconds=settings.idempotency_ttl_seconds,
        stream_retention_seconds=settings.stream_retention_seconds,
        redis_probe=redis_adapter,
        vector_probe=getattr(executor_adapter, "vector_probe", None),
        knowledge_indexer=knowledge_indexer,
    )
    if not settings.phone_encryption_key or not settings.phone_lookup_hmac_key:
        if settings.environment == "production":
            raise ValueError("phone protection keys are required in production")
        settings.phone_encryption_key = (
            settings.phone_encryption_key or "dev-phone-encryption-key-32bytes"
        )
        settings.phone_lookup_hmac_key = (
            settings.phone_lookup_hmac_key or "dev-phone-lookup-hmac-key-32bytes"
        )
    phone_protector = PhoneProtector(
        settings.phone_encryption_key,
        settings.phone_lookup_hmac_key,
        settings.phone_key_version,
    )
    sms_limits = SmsLimits(
        settings.sms_send_cooldown_seconds,
        settings.sms_hourly_limit,
        settings.sms_daily_limit,
    )
    sms_attempt_limits = SmsVerificationAttemptLimits(
        settings.sms_verify_max_attempts,
        settings.sms_verify_attempt_window_seconds,
        settings.sms_verify_lock_seconds,
    )
    redis_client = redis_adapter.client()
    if (
        settings.environment == "production"
        and settings.sms_provider == "aliyun"
        and redis_client is None
    ):
        raise ValueError("production SMS rate limiting requires Redis")
    sms_limiter = (
        RedisRateLimiter(redis_client, sms_limits)
        if redis_client is not None
        else InMemoryRateLimiter(sms_limits)
    )
    sms_attempt_limiter = (
        RedisVerificationAttemptLimiter(redis_client, sms_attempt_limits)
        if redis_client is not None
        else InMemoryVerificationAttemptLimiter(sms_attempt_limits)
    )
    sms_service = SmsVerificationService(
        build_sms_provider(settings), sms_limiter, sms_attempt_limiter
    )
    usage_worker = UsageSummaryWorker(repository_adapter)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        admin_user = None
        if settings.admin_username and settings.admin_password:
            admin_user = await resolved_identity_service.ensure_admin(
                username=settings.admin_username,
                password=settings.admin_password,
                nickname=settings.admin_nickname,
            )
            # The bundled records are synthetic demo data. Seed only the
            # configured admin mapping so the report flow works out of the
            # box; normal accounts still require an explicit admin mapping.
            if settings.agent_report_default_external_user_id:
                async with repository_adapter.transaction() as tx:
                    existing_mapping = await tx.resolve_external_user_id(str(admin_user.id))
                    if existing_mapping is None:
                        await tx.upsert_external_identity_mapping(
                            admin_user.id,
                            settings.agent_report_default_external_user_id,
                            admin_user.id,
                            datetime.now(UTC),
                        )
        initialize_knowledge_index = getattr(executor_adapter, "initialize_knowledge_index", None)
        if initialize_knowledge_index is not None:
            await initialize_knowledge_index()
        catalog_document = getattr(executor_adapter, "robot_catalog_document", None)
        if catalog_document is not None and knowledge_indexer is not None:
            try:
                await knowledge_indexer.ingest(catalog_document)
            except Exception:
                logger.exception(
                    "robot catalog indexing failed",
                    extra={"error_code": "CATALOG_INDEX_FAILED"},
                )
        await usage_worker.start()
        yield
        await usage_worker.close()
        await redis_adapter.close()
        await service.close()

    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    generated_openapi = app.openapi

    def contract_openapi() -> dict[str, object]:
        document = generated_openapi()
        document["servers"] = [{"url": settings.api_prefix}]
        prefix = settings.api_prefix
        document["paths"] = {
            (path[len(prefix) :] if path.startswith(prefix) else path): value
            for path, value in document["paths"].items()
        }
        components = document.setdefault("components", {})
        security_schemes = components.setdefault("securitySchemes", {})
        if "HTTPBearer" in security_schemes:
            scheme = security_schemes.pop("HTTPBearer")
            scheme["bearerFormat"] = "JWT"
            scheme["description"] = (
                "Authentication Adapter supplies subject and role; roles are user, "
                "reviewer, or admin."
            )
            security_schemes["bearerAuth"] = scheme
        document["security"] = [{"bearerAuth": []}]
        required_roles = {
            ("get", "/health/live"): "anonymous",
            ("get", "/health/ready"): "anonymous",
            ("post", "/auth/register"): "anonymous",
            ("post", "/auth/login"): "anonymous",
            ("get", "/auth/me"): "user",
            ("patch", "/auth/me"): "user",
            ("patch", "/auth/me/password"): "user",
            ("put", "/auth/me/avatar"): "user",
            ("get", "/sessions"): "user",
            ("post", "/sessions"): "user",
            ("get", "/sessions/{session_id}"): "user",
            ("get", "/sessions/{session_id}/messages"): "user",
            ("post", "/chat/stream"): "user",
            ("post", "/chat/{message_id}/feedback"): "user",
            ("post", "/knowledge/files"): "admin",
            ("get", "/knowledge/files"): "admin",
            ("post", "/knowledge/reindex"): "admin",
            ("put", "/admin/external-identity-mappings"): "admin",
            ("post", "/runs/{run_id}/cancel"): "user",
            ("get", "/runs/{run_id}/trace"): "user-or-linked-reviewer",
            ("get", "/memories"): "user",
            ("get", "/me/usage-summary"): "user",
            ("post", "/me/usage-events"): "user",
            ("patch", "/memories/{memory_id}"): "user",
            ("delete", "/memories/{memory_id}"): "user",
            ("get", "/reviews"): "reviewer",
            ("post", "/reviews/{review_id}/decision"): "reviewer",
        }
        for path_item in document["paths"].values():
            for operation in path_item.values():
                if isinstance(operation, dict) and operation.get("security") == [
                    {"HTTPBearer": []}
                ]:
                    operation["security"] = [{"bearerAuth": []}]
        for (method, path), role in required_roles.items():
            operation = document["paths"][path][method]
            operation["x-required-role"] = role
            if role == "anonymous":
                operation["security"] = []
        stream_content = document["paths"]["/chat/stream"]["post"]["responses"]["200"]["content"]
        stream_content.pop("application/json", None)
        return document

    app.openapi = contract_openapi  # type: ignore[method-assign]
    app.state.platform_service = service
    app.state.identity_service = resolved_identity_service
    app.state.phone_protector = phone_protector
    app.state.sms_service = sms_service
    app.state.avatar_store = AvatarStore(settings.uploads_dir)
    app.state.knowledge_catalog = KnowledgeCatalog(settings.uploads_dir, knowledge_indexer)
    app.state.usage_summary_worker = usage_worker
    app.dependency_overrides[get_settings] = lambda: settings
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, internal_error_handler)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "If-Match",
            "Last-Event-ID",
            "X-Request-ID",
        ],
    )
    app.mount("/uploads", StaticFiles(directory=settings.uploads_dir), name="uploads")

    @app.middleware("http")
    async def request_context(request: Request, call_next: RequestResponseEndpoint) -> Response:
        candidate = request.headers.get("X-Request-ID")
        request_id = (
            candidate
            if candidate
            and 8 <= len(candidate) <= 128
            and all(char.isalnum() or char in "_-" for char in candidate)
            else new_request_id()
        )
        tokens = bind_context(request_id)
        started = perf_counter()
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            logger.info(
                "http request completed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": round((perf_counter() - started) * 1000, 2),
                },
            )
            return response
        finally:
            tokens.reset()

    app.include_router(router, prefix=settings.api_prefix)
    return app


app = create_app()
