from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.ai.service import DEFAULT_FACTS, DEFAULT_TONE
from app.auth import Current, get_current
from app.db import get_db
from app.models import AuditEvent, Tenant, UserRole

router = APIRouter(prefix="/v1/tenant")


class AgencySettings(BaseModel):
    agency_facts: str = Field(max_length=5000)
    tone: str = Field(max_length=200)


def _view(tenant: Tenant) -> AgencySettings:
    s = tenant.settings or {}
    return AgencySettings(
        agency_facts=s.get("agency_facts") or DEFAULT_FACTS, tone=s.get("tone") or DEFAULT_TONE
    )


@router.get("/settings")
def get_settings(current: Annotated[Current, Depends(get_current)]) -> AgencySettings:
    return _view(current.tenant)


@router.put("/settings")
def put_settings(
    body: AgencySettings,
    current: Annotated[Current, Depends(get_current)],
    session: Annotated[Session, Depends(get_db)],
) -> AgencySettings:
    if current.user.role != UserRole.OWNER:
        raise HTTPException(status_code=403, detail="Only the owner can edit agency settings")
    tenant = session.get(Tenant, current.tenant.id)
    tenant.settings = {**(tenant.settings or {}), **body.model_dump()}  # new dict: JSONB change
    session.add(
        AuditEvent(
            tenant_id=tenant.id,
            lead_id=None,
            actor=f"user:{current.user.id}",
            action="settings_updated",
            data={"fields": sorted(body.model_dump())},
        )
    )
    return _view(tenant)
