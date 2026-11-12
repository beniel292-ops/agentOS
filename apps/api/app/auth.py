from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated

import jwt
from fastapi import Depends, Header, HTTPException
from jwt import PyJWKClient
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import Tenant, User, UserRole

LEEWAY_SECONDS = 30


@dataclass
class Current:
    tenant: Tenant
    user: User
    org_role: str


@lru_cache
def _jwks_client() -> PyJWKClient:
    return PyJWKClient(settings.clerk_jwks_url, cache_keys=True)


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=401, detail="Invalid or missing token")


def verify_token(authorization: Annotated[str | None, Header()] = None) -> dict:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise _unauthorized()
    try:
        key = _jwks_client().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            key.key,
            algorithms=["RS256"],
            issuer=settings.clerk_issuer,
            leeway=LEEWAY_SECONDS,
            options={"require": ["exp", "iss", "sub"]},
        )
    except (jwt.PyJWTError, ValueError):
        raise _unauthorized() from None
    azp = claims.get("azp")
    if azp is not None and azp not in settings.clerk_authorized_parties:
        raise _unauthorized()
    return claims


def _org(claims: dict) -> tuple[str, str, str | None] | None:
    """(org id, role without 'org:' prefix, slug) from session token v2 (`o`) or v1."""
    if isinstance(claims.get("o"), dict) and claims["o"].get("id"):
        o = claims["o"]
        return o["id"], o.get("rol") or "", o.get("slg")
    if claims.get("org_id"):
        return claims["org_id"], claims.get("org_role") or "", claims.get("org_slug")
    return None


def get_current(
    claims: Annotated[dict, Depends(verify_token)],
    session: Annotated[Session, Depends(get_db)],
) -> Current:
    org = _org(claims)
    if org is None:
        raise HTTPException(status_code=403, detail="Select or create an organization")
    org_id, org_role, slug = org
    org_role = org_role.removeprefix("org:")
    role = UserRole.OWNER if org_role == "admin" else UserRole.MEMBER

    # INSERT .. ON CONFLICT DO NOTHING, then SELECT: safe for concurrent first requests.
    session.execute(
        insert(Tenant)
        .values(clerk_org_id=org_id, name=slug or "Agency", settings={})
        .on_conflict_do_nothing(index_elements=["clerk_org_id"])
    )
    tenant = session.scalars(select(Tenant).where(Tenant.clerk_org_id == org_id)).one()

    session.execute(
        insert(User)
        .values(tenant_id=tenant.id, clerk_user_id=claims["sub"], role=role)
        .on_conflict_do_nothing(index_elements=["tenant_id", "clerk_user_id"])
    )
    user = session.scalars(
        select(User).where(User.tenant_id == tenant.id, User.clerk_user_id == claims["sub"])
    ).one()
    if user.role != role:
        session.execute(update(User).where(User.id == user.id).values(role=role))
        session.refresh(user)
    return Current(tenant=tenant, user=user, org_role=org_role)
