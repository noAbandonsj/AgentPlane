from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentplane.config import Settings
from agentplane.errors import ApiError
from agentplane.identity import ApplicationIdentityContext, IdentityContext
from agentplane.models import (
    AgentDefinition,
    AgentLifecycle,
    AgentVersion,
    ApplicationAgentGrant,
    ApplicationConversation,
    ApplicationToolGrant,
    AppUser,
    CallingApplication,
    ChatSession,
    ExternalUserMapping,
    Invocation,
    InvocationDecision,
    RunStatus,
    TaskRun,
    UserAgentGrant,
    UserStatus,
    UserToolGrant,
)
from agentplane.schemas import (
    InvocationAdminStatus,
    InvocationCreate,
    InvocationRead,
    RunCreate,
)
from agentplane.services import create_authorized_run


@dataclass(frozen=True, slots=True)
class InvocationCreationResult:
    invocation: Invocation
    run: TaskRun | None


def _fingerprint(payload: InvocationCreate) -> str:
    serialized = json.dumps(
        payload.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _denial_status(code: str) -> int:
    return {
        "AGENT_NOT_AVAILABLE": 404,
        "AGENT_NOT_PUBLISHED": 409,
        "AGENT_VERSION_MISSING": 409,
        "APPLICATION_AGENT_NOT_GRANTED": 403,
        "USER_AGENT_NOT_GRANTED": 403,
        "CONVERSATION_AGENT_MISMATCH": 409,
        "CONVERSATION_USER_CHANGED": 409,
        "ACTIVE_RUN_EXISTS": 409,
        "SESSION_ARCHIVED": 409,
        "MODEL_NOT_CONFIGURED": 503,
        "EXTERNAL_USER_NOT_MAPPED": 403,
        "REPRESENTED_USER_DISABLED": 403,
    }.get(code, 403)


def invocation_denial_error(invocation: Invocation) -> ApiError:
    return ApiError(
        _denial_status(invocation.decision_code),
        invocation.decision_code,
        invocation.decision_message,
        {"invocation_id": str(invocation.id)},
    )


async def _find_existing(
    db: AsyncSession,
    identity: ApplicationIdentityContext,
    payload: InvocationCreate,
    request_fingerprint: str,
) -> InvocationCreationResult | None:
    invocation = await db.scalar(
        select(Invocation).where(
            Invocation.tenant_id == identity.tenant_id,
            Invocation.application_id == identity.application_id,
            Invocation.external_request_id == payload.external_request_id,
        )
    )
    if invocation is None:
        return None
    if invocation.request_fingerprint != request_fingerprint:
        raise ApiError(
            409,
            "IDEMPOTENCY_KEY_REUSED",
            "外部请求号已被不同请求内容使用",
            {"invocation_id": str(invocation.id)},
        )
    run = await db.get(TaskRun, invocation.run_id) if invocation.run_id is not None else None
    return InvocationCreationResult(invocation=invocation, run=run)


async def _deny(
    db: AsyncSession,
    identity: ApplicationIdentityContext,
    payload: InvocationCreate,
    request_fingerprint: str,
    code: str,
    message: str,
    *,
    user_id: UUID | None = None,
    agent_version_id: UUID | None = None,
) -> InvocationCreationResult:
    invocation = Invocation(
        tenant_id=identity.tenant_id,
        application_id=identity.application_id,
        credential_id=identity.credential_id,
        external_request_id=payload.external_request_id,
        request_fingerprint=request_fingerprint,
        external_user_id=payload.external_user_id,
        user_id=user_id,
        conversation_key=payload.conversation_key,
        requested_agent_id=payload.agent_id,
        agent_version_id=agent_version_id,
        decision=InvocationDecision.DENIED,
        decision_code=code,
        decision_message=message,
        effective_tool_keys=[],
    )
    db.add(invocation)
    await db.flush()
    return InvocationCreationResult(invocation=invocation, run=None)


async def create_invocation(
    db: AsyncSession,
    identity: ApplicationIdentityContext,
    payload: InvocationCreate,
    settings: Settings,
) -> InvocationCreationResult:
    request_fingerprint = _fingerprint(payload)
    await db.execute(
        select(CallingApplication.id)
        .where(
            CallingApplication.tenant_id == identity.tenant_id,
            CallingApplication.id == identity.application_id,
        )
        .with_for_update()
    )
    existing = await _find_existing(db, identity, payload, request_fingerprint)
    if existing is not None:
        return existing

    mapping_row = (
        await db.execute(
            select(ExternalUserMapping, AppUser)
            .join(
                AppUser,
                (AppUser.tenant_id == ExternalUserMapping.tenant_id)
                & (AppUser.id == ExternalUserMapping.user_id),
            )
            .where(
                ExternalUserMapping.tenant_id == identity.tenant_id,
                ExternalUserMapping.application_id == identity.application_id,
                ExternalUserMapping.external_user_id == payload.external_user_id,
                ExternalUserMapping.active.is_(True),
            )
        )
    ).one_or_none()
    if mapping_row is None:
        return await _deny(
            db,
            identity,
            payload,
            request_fingerprint,
            "EXTERNAL_USER_NOT_MAPPED",
            "外部用户未建立有效映射",
        )
    _mapping, user = mapping_row
    if user.status != UserStatus.ACTIVE:
        return await _deny(
            db,
            identity,
            payload,
            request_fingerprint,
            "REPRESENTED_USER_DISABLED",
            "被代表用户未启用",
            user_id=user.id,
        )

    agent = await db.scalar(
        select(AgentDefinition).where(
            AgentDefinition.tenant_id == identity.tenant_id,
            AgentDefinition.id == payload.agent_id,
        )
    )
    if agent is None or agent.lifecycle != AgentLifecycle.ACTIVE:
        return await _deny(
            db,
            identity,
            payload,
            request_fingerprint,
            "AGENT_NOT_AVAILABLE",
            "Agent 不存在或不可用",
            user_id=user.id,
        )

    user_agent_granted = await db.scalar(
        select(UserAgentGrant.agent_definition_id).where(
            UserAgentGrant.tenant_id == identity.tenant_id,
            UserAgentGrant.user_id == user.id,
            UserAgentGrant.agent_definition_id == agent.id,
        )
    )
    if user_agent_granted is None:
        return await _deny(
            db,
            identity,
            payload,
            request_fingerprint,
            "USER_AGENT_NOT_GRANTED",
            "被代表用户未获得该 Agent 的使用权限",
            user_id=user.id,
        )
    application_agent_granted = await db.scalar(
        select(ApplicationAgentGrant.agent_definition_id).where(
            ApplicationAgentGrant.tenant_id == identity.tenant_id,
            ApplicationAgentGrant.application_id == identity.application_id,
            ApplicationAgentGrant.agent_definition_id == agent.id,
        )
    )
    if application_agent_granted is None:
        return await _deny(
            db,
            identity,
            payload,
            request_fingerprint,
            "APPLICATION_AGENT_NOT_GRANTED",
            "调用应用未获得该 Agent 的使用权限",
            user_id=user.id,
        )

    chat_session: ChatSession | None = None
    conversation = await db.scalar(
        select(ApplicationConversation).where(
            ApplicationConversation.tenant_id == identity.tenant_id,
            ApplicationConversation.application_id == identity.application_id,
            ApplicationConversation.external_user_id == payload.external_user_id,
            ApplicationConversation.conversation_key == payload.conversation_key,
        )
    )
    if conversation is not None:
        if conversation.user_id != user.id:
            return await _deny(
                db,
                identity,
                payload,
                request_fingerprint,
                "CONVERSATION_USER_CHANGED",
                "连续会话对应的平台用户已变化，请使用新的会话标识",
                user_id=user.id,
            )
        if conversation.agent_definition_id != agent.id:
            return await _deny(
                db,
                identity,
                payload,
                request_fingerprint,
                "CONVERSATION_AGENT_MISMATCH",
                "同一连续会话不能切换 Agent",
                user_id=user.id,
            )
        chat_session = await db.get(ChatSession, conversation.session_id)
        if chat_session is None:
            return await _deny(
                db,
                identity,
                payload,
                request_fingerprint,
                "SESSION_ARCHIVED",
                "连续会话不可用",
                user_id=user.id,
            )
        agent_version_id = chat_session.agent_version_id
    else:
        if agent.latest_published_version_id is None:
            return await _deny(
                db,
                identity,
                payload,
                request_fingerprint,
                "AGENT_NOT_PUBLISHED",
                "Agent 尚未发布可用版本",
                user_id=user.id,
            )
        agent_version_id = agent.latest_published_version_id

    version = await db.scalar(
        select(AgentVersion).where(
            AgentVersion.tenant_id == identity.tenant_id,
            AgentVersion.id == agent_version_id,
            AgentVersion.agent_definition_id == agent.id,
        )
    )
    if version is None:
        return await _deny(
            db,
            identity,
            payload,
            request_fingerprint,
            "AGENT_VERSION_MISSING",
            "Agent 发布版本不存在",
            user_id=user.id,
        )
    if not settings.model_configured:
        return await _deny(
            db,
            identity,
            payload,
            request_fingerprint,
            "MODEL_NOT_CONFIGURED",
            "模型接口尚未配置",
            user_id=user.id,
            agent_version_id=version.id,
        )

    if conversation is None:
        chat_session = ChatSession(
            tenant_id=identity.tenant_id,
            user_id=user.id,
            agent_definition_id=agent.id,
            agent_version_id=agent_version_id,
            title=payload.conversation_key[:300],
        )
        db.add(chat_session)
        await db.flush()
        conversation = ApplicationConversation(
            tenant_id=identity.tenant_id,
            application_id=identity.application_id,
            external_user_id=payload.external_user_id,
            user_id=user.id,
            conversation_key=payload.conversation_key,
            agent_definition_id=agent.id,
            session_id=chat_session.id,
        )
        db.add(conversation)

    if chat_session is None:
        raise ApiError(409, "INVOCATION_SESSION_MISSING", "Invocation 会话创建失败")

    user_tool_keys = set(
        (
            await db.scalars(
                select(UserToolGrant.tool_key).where(
                    UserToolGrant.tenant_id == identity.tenant_id,
                    UserToolGrant.user_id == user.id,
                )
            )
        ).all()
    )
    application_tool_keys = set(
        (
            await db.scalars(
                select(ApplicationToolGrant.tool_key).where(
                    ApplicationToolGrant.tenant_id == identity.tenant_id,
                    ApplicationToolGrant.application_id == identity.application_id,
                )
            )
        ).all()
    )
    effective_tool_keys = [
        key for key in version.tool_keys if key in user_tool_keys and key in application_tool_keys
    ]

    represented_identity = IdentityContext(
        tenant_id=identity.tenant_id,
        user_id=user.id,
    )
    try:
        run = await create_authorized_run(
            db,
            represented_identity,
            chat_session.id,
            RunCreate(input=payload.input),
            settings,
            effective_tool_keys,
        )
    except ApiError as exc:
        if exc.code not in {"ACTIVE_RUN_EXISTS", "SESSION_ARCHIVED", "AGENT_VERSION_MISSING"}:
            raise
        return await _deny(
            db,
            identity,
            payload,
            request_fingerprint,
            exc.code,
            exc.message,
            user_id=user.id,
            agent_version_id=version.id,
        )

    invocation = Invocation(
        tenant_id=identity.tenant_id,
        application_id=identity.application_id,
        credential_id=identity.credential_id,
        external_request_id=payload.external_request_id,
        request_fingerprint=request_fingerprint,
        external_user_id=payload.external_user_id,
        user_id=user.id,
        conversation_key=payload.conversation_key,
        requested_agent_id=payload.agent_id,
        agent_version_id=version.id,
        decision=InvocationDecision.ALLOWED,
        decision_code="ALLOWED",
        decision_message="调用已通过权限校验",
        effective_tool_keys=effective_tool_keys,
        session_id=chat_session.id,
        run_id=run.id,
    )
    db.add(invocation)
    await db.flush()
    return InvocationCreationResult(invocation=invocation, run=run)


async def get_invocation(
    db: AsyncSession,
    identity: ApplicationIdentityContext,
    invocation_id: UUID,
) -> InvocationCreationResult:
    invocation = await db.scalar(
        select(Invocation).where(
            Invocation.id == invocation_id,
            Invocation.tenant_id == identity.tenant_id,
            Invocation.application_id == identity.application_id,
        )
    )
    if invocation is None:
        raise ApiError(404, "INVOCATION_NOT_FOUND", "Invocation 不存在")
    run = await db.get(TaskRun, invocation.run_id) if invocation.run_id is not None else None
    return InvocationCreationResult(invocation=invocation, run=run)


async def list_admin_invocations(
    db: AsyncSession,
    identity: IdentityContext,
    *,
    application_id: UUID | None = None,
    external_request_id: str | None = None,
    external_user_id: str | None = None,
    agent_id: UUID | None = None,
    decision: InvocationDecision | None = None,
    status: InvocationAdminStatus | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[InvocationCreationResult]:
    statement = (
        select(Invocation, TaskRun)
        .outerjoin(
            TaskRun,
            and_(
                TaskRun.tenant_id == Invocation.tenant_id,
                TaskRun.id == Invocation.run_id,
            ),
        )
        .where(Invocation.tenant_id == identity.tenant_id)
    )
    if application_id is not None:
        statement = statement.where(Invocation.application_id == application_id)
    if external_request_id:
        statement = statement.where(
            Invocation.external_request_id.contains(external_request_id.strip())
        )
    if external_user_id:
        statement = statement.where(Invocation.external_user_id.contains(external_user_id.strip()))
    if agent_id is not None:
        statement = statement.where(Invocation.requested_agent_id == agent_id)
    if decision is not None:
        statement = statement.where(Invocation.decision == decision)
    if status == "REJECTED":
        statement = statement.where(Invocation.decision == InvocationDecision.DENIED)
    elif status is not None:
        statement = statement.where(TaskRun.status == RunStatus(status))
    if created_from is not None:
        statement = statement.where(Invocation.created_at >= created_from)
    if created_to is not None:
        statement = statement.where(Invocation.created_at <= created_to)
    rows = (
        await db.execute(
            statement.order_by(Invocation.created_at.desc(), Invocation.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return [InvocationCreationResult(invocation=invocation, run=run) for invocation, run in rows]


async def get_admin_invocation(
    db: AsyncSession,
    identity: IdentityContext,
    invocation_id: UUID,
) -> InvocationCreationResult:
    row = (
        await db.execute(
            select(Invocation, TaskRun)
            .outerjoin(
                TaskRun,
                and_(
                    TaskRun.tenant_id == Invocation.tenant_id,
                    TaskRun.id == Invocation.run_id,
                ),
            )
            .where(
                Invocation.id == invocation_id,
                Invocation.tenant_id == identity.tenant_id,
            )
        )
    ).one_or_none()
    if row is None:
        raise ApiError(404, "INVOCATION_NOT_FOUND", "Invocation 不存在")
    invocation, run = row
    return InvocationCreationResult(invocation=invocation, run=run)


def invocation_response(result: InvocationCreationResult) -> InvocationRead:
    invocation = result.invocation
    run = result.run
    status = "REJECTED" if invocation.decision == InvocationDecision.DENIED else "ACCEPTED"
    if run is not None:
        status = run.status.value
    return InvocationRead(
        id=invocation.id,
        application_id=invocation.application_id,
        credential_id=invocation.credential_id,
        external_request_id=invocation.external_request_id,
        external_user_id=invocation.external_user_id,
        user_id=invocation.user_id,
        conversation_key=invocation.conversation_key,
        agent_id=invocation.requested_agent_id,
        agent_version_id=invocation.agent_version_id,
        decision=invocation.decision,
        decision_code=invocation.decision_code,
        decision_message=invocation.decision_message,
        effective_tool_keys=list(invocation.effective_tool_keys),
        session_id=invocation.session_id,
        run_id=invocation.run_id,
        status=status,
        output=run.output_text if run is not None else None,
        error_code=run.error_code if run is not None else None,
        error_message=run.error_message if run is not None else None,
        created_at=invocation.created_at,
    )
