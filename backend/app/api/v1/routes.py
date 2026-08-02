from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Header, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from backend.app.api.dependencies import get_identity_service, get_service
from backend.app.application.identity import IdentityService, IdentityUser, IssuedSession
from backend.app.application.service import PlatformService
from backend.app.core.config import Settings, get_settings
from backend.app.core.context import request_id_var
from backend.app.core.errors import AppError
from backend.app.core.security import Principal, get_principal, require_reviewer
from backend.app.schemas.common import Envelope, ErrorEnvelope, Page
from backend.app.schemas.events import SseEventSchema
from backend.app.schemas.resources import (
    AuthSession,
    AuthUser,
    CancelRunRequest,
    CancelRunResult,
    ChangePasswordRequest,
    CreateFeedbackRequest,
    CreateSessionRequest,
    DeleteMemoryResult,
    Feedback,
    KnowledgeFile,
    LiveStatus,
    LoginRequest,
    Memory,
    Message,
    NewChatRequest,
    ReadyStatus,
    RegisterRequest,
    RetryChatRequest,
    ReviewDecisionRequest,
    ReviewDecisionResult,
    ReviewTask,
    RunTrace,
    Session,
    UpdateMemoryRequest,
    UpdateProfileRequest,
)

router = APIRouter()

IdempotencyKey = Annotated[
    str, Header(alias="Idempotency-Key", min_length=16, max_length=128, pattern=r"^[\x21-\x7E]+$")
]


def error_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    descriptions = {
        400: "请求语义或游标不合法",
        401: "缺少或无效身份",
        403: "角色或对象级权限不足",
        404: "资源不存在或不可见",
        409: "资源、幂等键或状态冲突",
        410: "SSE 重放窗口已过期",
        422: "请求结构校验失败",
        429: "请求频率超过限制",
        500: "内部错误",
        503: "必需依赖不可用",
    }
    return {
        status: {"model": ErrorEnvelope, "description": descriptions[status]}
        for status in status_codes
    }


def json_result(status: int, body: Envelope[Any], replayed: bool) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content=body.model_dump(mode="json"),
        headers={"Idempotency-Replayed": str(replayed).lower()},
    )


def auth_user(user: IdentityUser) -> AuthUser:
    return AuthUser(
        id=user.id,
        username=user.username,
        nickname=user.nickname,
        avatar_url=user.avatar_url,
        role=user.role,
        created_at=user.created_at,
    )


def auth_session(grant: IssuedSession) -> AuthSession:
    return AuthSession(
        access_token=grant.access_token,
        expires_at=grant.expires_at,
        user=auth_user(grant.user),
    )


@router.post(
    "/auth/register",
    response_model=Envelope[AuthSession],
    status_code=201,
    operation_id="registerUser",
    tags=["Authentication"],
    responses=error_responses(409, 422, 500),
)
async def register(
    request: RegisterRequest,
    identity: IdentityService = Depends(get_identity_service),
) -> Envelope[AuthSession]:
    grant = await identity.register(
        username=request.username,
        password=request.password,
        nickname=request.nickname,
        avatar_url=request.avatar_url,
    )
    return Envelope(data=auth_session(grant), request_id=request_id_var.get())


@router.post(
    "/auth/login",
    response_model=Envelope[AuthSession],
    operation_id="loginUser",
    tags=["Authentication"],
    responses=error_responses(401, 422, 500),
)
async def login(
    request: LoginRequest,
    identity: IdentityService = Depends(get_identity_service),
) -> Envelope[AuthSession]:
    grant = await identity.login(username=request.username, password=request.password)
    return Envelope(data=auth_session(grant), request_id=request_id_var.get())


@router.get(
    "/auth/me",
    response_model=Envelope[AuthUser],
    operation_id="getCurrentUser",
    tags=["Authentication"],
    responses=error_responses(401, 404, 500),
)
async def current_user(
    principal: Principal = Depends(get_principal),
    identity: IdentityService = Depends(get_identity_service),
) -> Envelope[AuthUser]:
    user = await identity.profile(principal.subject_id)
    return Envelope(data=auth_user(user), request_id=request_id_var.get())


@router.patch(
    "/auth/me",
    response_model=Envelope[AuthUser],
    operation_id="updateCurrentUser",
    tags=["Authentication"],
    responses=error_responses(401, 404, 422, 500),
)
async def update_current_user(
    request: UpdateProfileRequest,
    principal: Principal = Depends(get_principal),
    identity: IdentityService = Depends(get_identity_service),
) -> Envelope[AuthUser]:
    user = await identity.update_profile(
        principal.subject_id, nickname=request.nickname, avatar_url=request.avatar_url
    )
    return Envelope(data=auth_user(user), request_id=request_id_var.get())


@router.patch(
    "/auth/me/password",
    response_model=Envelope[AuthUser],
    operation_id="changeCurrentUserPassword",
    tags=["Authentication"],
    responses=error_responses(401, 404, 422, 500),
)
async def change_current_user_password(
    payload: ChangePasswordRequest,
    principal: Principal = Depends(get_principal),
    identity: IdentityService = Depends(get_identity_service),
) -> Envelope[AuthUser]:
    user = await identity.change_password(
        principal.subject_id,
        current_password=payload.current_password,
        new_password=payload.new_password,
    )
    return Envelope(data=auth_user(user), request_id=request_id_var.get())


@router.put(
    "/auth/me/avatar",
    response_model=Envelope[AuthUser],
    operation_id="uploadCurrentUserAvatar",
    tags=["Authentication"],
    responses=error_responses(401, 404, 422, 500),
)
async def upload_current_user_avatar(
    request: Request,
    payload: bytes = Body(),
    principal: Principal = Depends(get_principal),
    identity: IdentityService = Depends(get_identity_service),
) -> Envelope[AuthUser]:
    from backend.app.adapters.uploads.avatar_store import AvatarStore

    store = request.app.state.avatar_store
    if not isinstance(store, AvatarStore):
        raise RuntimeError("avatar storage is not configured")
    try:
        user_id = UUID(principal.subject_id)
    except ValueError as error:
        raise AppError("UNAUTHORIZED", "invalid user identity", 401) from error
    avatar_url = store.save(
        user_id=user_id,
        content_type=request.headers.get("content-type", ""),
        payload=payload,
    )
    user = await identity.update_profile(principal.subject_id, nickname=None, avatar_url=avatar_url)
    return Envelope(data=auth_user(user), request_id=request_id_var.get())


@router.post(
    "/knowledge/files",
    response_model=Envelope[KnowledgeFile],
    status_code=201,
    operation_id="uploadKnowledgeFile",
    tags=["Knowledge"],
    responses=error_responses(400, 401, 403, 422, 500),
)
async def upload_knowledge_file(
    request: Request,
    filename: Annotated[str, Query(min_length=1, max_length=180)],
    payload: bytes = Body(media_type="text/plain"),
    principal: Principal = Depends(get_principal),
    service: PlatformService = Depends(get_service),
    settings: Settings = Depends(get_settings),
) -> Envelope[KnowledgeFile]:
    return await service.upload_knowledge_file(
        principal,
        filename=filename,
        content_type=request.headers.get("content-type", ""),
        payload=payload,
        uploads_dir=settings.uploads_dir,
    )


@router.get(
    "/health/live",
    response_model=Envelope[LiveStatus],
    operation_id="getLiveness",
    tags=["Health"],
    responses=error_responses(500),
)
async def liveness() -> Envelope[LiveStatus]:
    return Envelope(data=LiveStatus(), request_id=request_id_var.get())


@router.get(
    "/health/ready",
    response_model=Envelope[ReadyStatus],
    operation_id="getReadiness",
    tags=["Health"],
    responses=error_responses(503),
)
async def readiness(service: PlatformService = Depends(get_service)) -> Envelope[ReadyStatus]:
    return await service.readiness()


@router.get(
    "/sessions",
    response_model=Envelope[Page[Session]],
    operation_id="listSessions",
    tags=["Sessions"],
    responses=error_responses(400, 401, 403, 422, 429, 500, 503),
)
async def list_sessions(
    cursor: Annotated[str | None, Query(min_length=1, max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    principal: Principal = Depends(get_principal),
    service: PlatformService = Depends(get_service),
) -> Envelope[Page[Session]]:
    return await service.list_sessions(principal, cursor, limit)


@router.post(
    "/sessions",
    response_model=Envelope[Session],
    status_code=201,
    operation_id="createSession",
    tags=["Sessions"],
    responses=error_responses(400, 401, 403, 409, 422, 429, 500, 503),
)
async def create_session(
    request: CreateSessionRequest,
    key: IdempotencyKey,
    principal: Principal = Depends(get_principal),
    service: PlatformService = Depends(get_service),
) -> JSONResponse:
    status, body, replayed = await service.create_session(principal, key, request)
    return json_result(status, body, replayed)


@router.get(
    "/sessions/{session_id}",
    response_model=Envelope[Session],
    operation_id="getSession",
    tags=["Sessions"],
    responses=error_responses(401, 403, 404, 422, 500, 503),
)
async def get_session(
    session_id: UUID,
    principal: Principal = Depends(get_principal),
    service: PlatformService = Depends(get_service),
) -> Envelope[Session]:
    return await service.get_session(principal, session_id)


@router.get(
    "/sessions/{session_id}/messages",
    response_model=Envelope[Page[Message]],
    operation_id="listSessionMessages",
    tags=["Sessions"],
    responses=error_responses(400, 401, 403, 404, 422, 500, 503),
)
async def list_messages(
    session_id: UUID,
    cursor: Annotated[str | None, Query(min_length=1, max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    principal: Principal = Depends(get_principal),
    service: PlatformService = Depends(get_service),
) -> Envelope[Page[Message]]:
    return await service.list_messages(principal, session_id, cursor, limit)


@router.post(
    "/chat/stream",
    operation_id="streamChat",
    tags=["Chat"],
    responses={
        200: {
            "description": "Agent SSE 事件流",
            "content": {
                "text/event-stream": {
                    "schema": SseEventSchema.model_json_schema(
                        ref_template="#/components/schemas/{model}"
                    )
                }
            },
        },
        **error_responses(400, 401, 403, 404, 409, 410, 422, 429, 500, 503),
    },
)
async def stream_chat(
    request: NewChatRequest | RetryChatRequest,
    key: IdempotencyKey,
    last_event_id: Annotated[int, Header(alias="Last-Event-ID", ge=0)] = 0,
    principal: Principal = Depends(get_principal),
    service: PlatformService = Depends(get_service),
) -> StreamingResponse:
    session_id, run_id, replayed, stream = await service.prepare_stream(
        principal, key, request, last_event_id
    )
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={
            "X-Request-ID": request_id_var.get(),
            "X-Session-ID": str(session_id),
            "X-Run-ID": str(run_id),
            "Idempotency-Replayed": str(replayed).lower(),
            "Cache-Control": "no-cache",
        },
    )


@router.post(
    "/chat/{message_id}/feedback",
    response_model=Envelope[Feedback],
    status_code=201,
    operation_id="createMessageFeedback",
    tags=["Chat"],
    responses=error_responses(400, 401, 403, 404, 409, 422, 429, 500, 503),
)
async def create_feedback(
    message_id: UUID,
    request: CreateFeedbackRequest,
    key: IdempotencyKey,
    principal: Principal = Depends(get_principal),
    service: PlatformService = Depends(get_service),
) -> JSONResponse:
    status, body, replayed = await service.create_feedback(principal, message_id, key, request)
    return json_result(status, body, replayed)


@router.post(
    "/runs/{run_id}/cancel",
    operation_id="cancelRun",
    tags=["Runs"],
    responses={
        202: {
            "model": Envelope[CancelRunResult],
            "description": "取消意图已持久化",
        },
        **error_responses(401, 403, 404, 409, 422, 429, 500, 503),
    },
)
async def cancel_run(
    run_id: UUID,
    key: IdempotencyKey,
    request: CancelRunRequest | None = None,
    principal: Principal = Depends(get_principal),
    service: PlatformService = Depends(get_service),
) -> JSONResponse:
    status, body, replayed = await service.cancel_run(
        principal, run_id, key, request.reason if request else None
    )
    return json_result(status, body, replayed)


@router.get(
    "/runs/{run_id}/trace",
    response_model=Envelope[RunTrace],
    operation_id="getRunTrace",
    tags=["Runs"],
    responses=error_responses(401, 403, 404, 422, 500, 503),
)
async def get_trace(
    run_id: UUID,
    principal: Principal = Depends(get_principal),
    service: PlatformService = Depends(get_service),
) -> Envelope[RunTrace]:
    return await service.get_trace(principal, run_id)


@router.get(
    "/memories",
    response_model=Envelope[Page[Memory]],
    operation_id="listMemories",
    tags=["Memories"],
    responses=error_responses(400, 401, 403, 422, 500, 503),
)
async def list_memories(
    cursor: Annotated[str | None, Query(min_length=1, max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    status: Literal["active", "inactive"] | None = None,
    memory_type: Literal["user_fact", "preference", "task_summary"] | None = None,
    principal: Principal = Depends(get_principal),
    service: PlatformService = Depends(get_service),
) -> Envelope[Page[Memory]]:
    return await service.list_memories(principal, cursor, limit, status, memory_type)


@router.patch(
    "/memories/{memory_id}",
    response_model=Envelope[Memory],
    operation_id="updateMemory",
    tags=["Memories"],
    responses=error_responses(400, 401, 403, 404, 409, 422, 500, 503),
)
async def update_memory(
    memory_id: UUID,
    request: UpdateMemoryRequest,
    key: IdempotencyKey,
    principal: Principal = Depends(get_principal),
    service: PlatformService = Depends(get_service),
) -> JSONResponse:
    status, body, replayed = await service.update_memory(principal, memory_id, key, request)
    return json_result(status, body, replayed)


@router.delete(
    "/memories/{memory_id}",
    response_model=Envelope[DeleteMemoryResult],
    operation_id="deleteMemory",
    tags=["Memories"],
    responses=error_responses(401, 403, 404, 409, 422, 500, 503),
)
async def delete_memory(
    memory_id: UUID,
    key: IdempotencyKey,
    if_match: Annotated[str, Header(alias="If-Match", pattern=r"^[1-9][0-9]*$")],
    principal: Principal = Depends(get_principal),
    service: PlatformService = Depends(get_service),
) -> JSONResponse:
    status, body, replayed = await service.delete_memory(principal, memory_id, key, int(if_match))
    return json_result(status, body, replayed)


@router.get(
    "/reviews",
    response_model=Envelope[Page[ReviewTask]],
    operation_id="listReviews",
    tags=["Reviews"],
    responses=error_responses(400, 401, 403, 422, 500, 503),
)
async def list_reviews(
    cursor: Annotated[str | None, Query(min_length=1, max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    status: Literal["pending", "approved", "rejected", "edited_and_published"] | None = None,
    _: Principal = Depends(require_reviewer),
    service: PlatformService = Depends(get_service),
) -> Envelope[Page[ReviewTask]]:
    return await service.list_reviews(cursor, limit, status)


@router.post(
    "/reviews/{review_id}/decision",
    response_model=Envelope[ReviewDecisionResult],
    operation_id="decideReview",
    tags=["Reviews"],
    responses=error_responses(400, 401, 403, 404, 409, 422, 429, 500, 503),
)
async def decide_review(
    review_id: UUID,
    request: ReviewDecisionRequest,
    key: IdempotencyKey,
    principal: Principal = Depends(require_reviewer),
    service: PlatformService = Depends(get_service),
) -> JSONResponse:
    status, body, replayed = await service.decide_review(principal, review_id, key, request)
    return json_result(status, body, replayed)
