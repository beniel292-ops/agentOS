"""Permission layer for the Chief of Staff API: Clerk members/owners or scoped agent keys."""
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current, verify_token
from app.db import get_db
from app.intake import sha256_hex
from app.models import AgentKey, CosSettings, UserRole

SCOPES = frozenset({
    "tasks:read", "tasks:write", "callbacks:write", "checkins:write", "opportunities:read",
    "opportunities:write", "memory:read", "memory:write", "stop",
})
READ_SCOPES = frozenset(s for s in SCOPES if s.endswith(":read"))
MEMBER_SCOPES = READ_SCOPES | {"tasks:write", "callbacks:write", "stop"}


@dataclass
class Principal:
    tenant_id: uuid.UUID
    actor: str
    is_owner: bool  # only ever true for a Clerk owner, never for an agent key
    scopes: set[str]


def get_principal(
    session: Annotated[Session, Depends(get_db)],
    x_agent_key: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    if x_agent_key:
        key = session.scalars(
            select(AgentKey).where(
                AgentKey.key_hash == sha256_hex(x_agent_key), AgentKey.revoked_at.is_(None)
            )
        ).first()
        if key is None:
            raise HTTPException(status_code=401, detail="Invalid or missing key")
        return Principal(key.tenant_id, f"agent:{key.name}", False, set(key.scopes))
    # raises 401 when the bearer token is missing or invalid
    current = get_current(verify_token(authorization), session)
    owner = current.user.role == UserRole.OWNER
    return Principal(
        current.tenant.id, f"user:{current.user.id}", owner, set(SCOPES if owner else MEMBER_SCOPES)
    )


def require(scope: str) -> Callable[..., Principal]:
    def dep(p: Annotated[Principal, Depends(get_principal)]) -> Principal:
        if scope not in p.scopes:
            raise HTTPException(status_code=403, detail=f"Missing scope: {scope}")
        return p

    return dep


def require_owner(p: Annotated[Principal, Depends(get_principal)]) -> Principal:
    if not p.is_owner:
        raise HTTPException(status_code=403, detail="Only the owner can do this")
    return p


def _ensure_running(session: Session, tenant_id: uuid.UUID) -> None:
    stopped = session.scalar(
        select(CosSettings.stopped).where(CosSettings.tenant_id == tenant_id)
    )
    if stopped:
        raise HTTPException(status_code=423, detail="Chief of Staff is stopped")


def require_write(scope: str) -> Callable[..., Principal]:
    """Like require(), and refuses with 423 while the kill switch is on."""

    def dep(
        p: Annotated[Principal, Depends(require(scope))],
        session: Annotated[Session, Depends(get_db)],
    ) -> Principal:
        _ensure_running(session, p.tenant_id)
        return p

    return dep


def require_owner_write(
    p: Annotated[Principal, Depends(require_owner)],
    session: Annotated[Session, Depends(get_db)],
) -> Principal:
    _ensure_running(session, p.tenant_id)
    return p
