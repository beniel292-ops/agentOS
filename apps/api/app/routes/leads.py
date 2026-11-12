import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import Current, get_current
from app.db import get_db
from app.models import Lead, LeadStatus

router = APIRouter(prefix="/v1")


class LeadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    email: str
    company: str | None
    message: str
    source: str
    status: LeadStatus
    created_at: datetime


@router.get("/leads/{lead_id}")
def get_lead(
    lead_id: uuid.UUID,
    current: Annotated[Current, Depends(get_current)],
    session: Annotated[Session, Depends(get_db)],
) -> LeadOut:
    lead = session.scalars(
        select(Lead).where(Lead.id == lead_id, Lead.tenant_id == current.tenant.id)
    ).one_or_none()
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    return LeadOut.model_validate(lead)
