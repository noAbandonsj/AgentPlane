from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from agentplane.api.deps import get_db, get_identity
from agentplane.identity import IdentityContext
from agentplane.models import ChatSession, SessionMessage
from agentplane.schemas import MessageRead, SessionCreate, SessionRead
from agentplane.sessions.service import (
    create_chat_session,
    get_chat_session,
    list_messages,
    list_sessions,
)

router = APIRouter(prefix="/api/v1", tags=["sessions"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
IdentityDep = Annotated[IdentityContext, Depends(get_identity)]


@router.get("/sessions", response_model=list[SessionRead])
async def sessions_list(db: DbDep, identity: IdentityDep) -> Sequence[ChatSession]:
    return await list_sessions(db, identity)


@router.post("/sessions", response_model=SessionRead, status_code=status.HTTP_201_CREATED)
async def sessions_create(payload: SessionCreate, db: DbDep, identity: IdentityDep) -> ChatSession:
    chat_session = await create_chat_session(db, identity, payload)
    await db.commit()
    await db.refresh(chat_session)
    return chat_session


@router.get("/sessions/{session_id}", response_model=SessionRead)
async def sessions_get(session_id: UUID, db: DbDep, identity: IdentityDep) -> ChatSession:
    return await get_chat_session(db, identity, session_id)


@router.get("/sessions/{session_id}/messages", response_model=list[MessageRead])
async def sessions_messages(
    session_id: UUID, db: DbDep, identity: IdentityDep
) -> Sequence[SessionMessage]:
    return await list_messages(db, identity, session_id)
