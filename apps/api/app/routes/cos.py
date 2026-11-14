"""Chief of Staff API: tasks, callbacks, check-ins, opportunities, memory, kill switch, keys."""
import hashlib
import re
import secrets
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.cos_auth import (
    SCOPES,
    Principal,
    require,
    require_owner,
    require_owner_write,
    require_write,
)
from app.db import get_db
from app.intake import sha256_hex
from app.models import (
    AgentKey,
    AuditEvent,
    CallKind,
    CallStatus,
    Checkin,
    CosSettings,
    MemoryKind,
    MemoryNote,
    Opportunity,
    OppStatus,
    ScheduledCall,
    Task,
    TaskSource,
    TaskStatus,
    Verification,
)

router = APIRouter(prefix="/v1/cos")

Db = Annotated[Session, Depends(get_db)]
OPEN = (TaskStatus.PENDING, TaskStatus.IN_PROGRESS)


def now() -> datetime:
    """Module-level clock so tests can monkeypatch it."""
    return datetime.now(UTC)


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def _audit(session: Session, p: Principal, action: str, **data: Any) -> None:
    # ids and field names only: never free text (audit rows are append-only)
    session.add(AuditEvent(tenant_id=p.tenant_id, lead_id=None, actor=p.actor, action=action,
                           data=data))


def _insert_once(session: Session, model, values: dict, index: list[str], where, found):
    """INSERT .. ON CONFLICT DO NOTHING; returns (row, created). `found` selects the winner."""
    for _ in range(2):  # the winner could be deleted/closed between the two statements
        stmt = insert(model).values(**values).on_conflict_do_nothing(
            index_elements=index, index_where=where
        ).returning(model.id)
        new_id = session.scalar(stmt)
        if new_id is not None:
            return session.get(model, new_id), True
        row = session.scalars(found).first()
        if row is not None:
            return row, False
    raise HTTPException(status_code=409, detail="Conflict, retry")


def get_or_create_settings(session: Session, tenant_id: uuid.UUID) -> CosSettings:
    session.execute(
        insert(CosSettings).values(tenant_id=tenant_id).on_conflict_do_nothing(
            index_elements=["tenant_id"]
        )
    )
    return session.scalars(select(CosSettings).where(CosSettings.tenant_id == tenant_id)).one()


def _get(session: Session, model, tenant_id: uuid.UUID, obj_id: uuid.UUID):
    row = session.scalars(
        select(model).where(model.id == obj_id, model.tenant_id == tenant_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    return row


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------- agent keys (owner, Clerk only)

KEY_NAME = Field(min_length=1, max_length=50, pattern=r"^[a-z0-9][a-z0-9_-]*$")


class KeyIn(BaseModel):
    name: str = KEY_NAME
    scopes: list[str] = Field(min_length=1)

    @field_validator("scopes")
    @classmethod
    def _known(cls, v: list[str]) -> list[str]:
        bad = sorted(set(v) - SCOPES)
        if bad:
            raise ValueError(f"unknown scopes: {', '.join(bad)}")
        return sorted(set(v))


class KeyOut(Out):
    id: uuid.UUID
    name: str
    scopes: list[str]
    key_prefix: str
    created_at: datetime
    revoked_at: datetime | None


class KeyCreated(KeyOut):
    key: str  # plaintext, shown once


@router.post("/agent-keys", status_code=201)
def create_agent_key(
    data: KeyIn, p: Annotated[Principal, Depends(require_owner)], session: Db
) -> KeyCreated:
    plain = "cos_" + secrets.token_urlsafe(32)
    row = AgentKey(tenant_id=p.tenant_id, name=data.name, scopes=data.scopes,
                   key_hash=sha256_hex(plain), key_prefix=plain[:12])
    try:
        with session.begin_nested():
            session.add(row)
    except IntegrityError:
        raise HTTPException(status_code=409, detail="An active key with this name exists") from None
    _audit(session, p, "agent_key_created", id=str(row.id), name=row.name, scopes=row.scopes)
    return KeyCreated(**KeyOut.model_validate(row).model_dump(), key=plain)


@router.get("/agent-keys")
def list_agent_keys(p: Annotated[Principal, Depends(require_owner)], session: Db) -> list[KeyOut]:
    rows = session.scalars(
        select(AgentKey).where(AgentKey.tenant_id == p.tenant_id).order_by(AgentKey.created_at)
    )
    return [KeyOut.model_validate(r) for r in rows]


@router.post("/agent-keys/{key_id}/revoke")
def revoke_agent_key(
    key_id: uuid.UUID, p: Annotated[Principal, Depends(require_owner)], session: Db
) -> KeyOut:
    row = _get(session, AgentKey, p.tenant_id, key_id)
    if row.revoked_at is None:
        row.revoked_at = now()
        _audit(session, p, "agent_key_revoked", id=str(row.id), name=row.name)
    return KeyOut.model_validate(row)


# ---------------------------------------------------------------- settings, stop / resume


def _tz(name: str) -> str:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise ValueError("unknown timezone") from None
    return name


class SettingsOut(Out):
    stopped: bool
    stopped_at: datetime | None
    stopped_by: str | None
    timezone: str
    morning_time: time
    evening_time: time
    quiet_start: time
    quiet_end: time
    max_calls_per_day: int
    min_gap_minutes: int


class SettingsIn(BaseModel):
    """Partial update: only the fields sent are changed."""
    timezone: str | None = None
    morning_time: time | None = None
    evening_time: time | None = None
    quiet_start: time | None = None
    quiet_end: time | None = None
    max_calls_per_day: int | None = Field(default=None, ge=1, le=20)
    min_gap_minutes: int | None = Field(default=None, ge=5, le=240)

    @field_validator("timezone")
    @classmethod
    def _valid_tz(cls, v: str | None) -> str | None:
        return None if v is None else _tz(v)


@router.get("/settings")
def get_settings(p: Annotated[Principal, Depends(require("tasks:read"))], session: Db):
    return SettingsOut.model_validate(get_or_create_settings(session, p.tenant_id))


@router.put("/settings")
def put_settings(
    data: SettingsIn, p: Annotated[Principal, Depends(require_owner_write)], session: Db
) -> SettingsOut:
    s = get_or_create_settings(session, p.tenant_id)
    changes = data.model_dump(exclude_none=True)
    for k, v in changes.items():
        setattr(s, k, v)
    if s.quiet_start == s.quiet_end:
        raise HTTPException(status_code=422, detail="quiet_start and quiet_end must differ")
    _audit(session, p, "settings_updated", fields=sorted(changes))
    return SettingsOut.model_validate(s)


@router.post("/stop")
def stop(p: Annotated[Principal, Depends(require("stop"))], session: Db) -> SettingsOut:
    s = get_or_create_settings(session, p.tenant_id)
    s.stopped, s.stopped_at, s.stopped_by = True, now(), p.actor
    _audit(session, p, "cos_stopped")
    return SettingsOut.model_validate(s)


@router.post("/resume")
def resume(p: Annotated[Principal, Depends(require_owner)], session: Db) -> SettingsOut:
    s = get_or_create_settings(session, p.tenant_id)
    s.stopped, s.stopped_at, s.stopped_by = False, None, None
    _audit(session, p, "cos_resumed")
    return SettingsOut.model_validate(s)


# ---------------------------------------------------------------- tasks


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    notes: str | None = Field(default=None, max_length=5000)
    priority: int = Field(default=2, ge=1, le=3)
    due_date: date | None = None
    project: str | None = Field(default=None, max_length=200)
    source: TaskSource

    @field_validator("title")
    @classmethod
    def _title(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("title must not be blank")
        return v


class TaskPatch(BaseModel):
    status: TaskStatus | None = None
    priority: int | None = Field(default=None, ge=1, le=3)
    due_date: date | None = None
    notes: str | None = Field(default=None, max_length=5000)
    title: str | None = Field(default=None, min_length=1, max_length=300)


class TaskOut(Out):
    id: uuid.UUID
    title: str
    notes: str | None
    status: TaskStatus
    priority: int
    due_date: date | None
    project: str | None
    source: TaskSource
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class TaskCreated(TaskOut):
    duplicate: bool


def _tenant_today(session: Session, tenant_id: uuid.UUID) -> date:
    tz = ZoneInfo(get_or_create_settings(session, tenant_id).timezone)
    return now().astimezone(tz).date()


@router.get("/tasks")
def list_tasks(
    p: Annotated[Principal, Depends(require("tasks:read"))],
    session: Db,
    status: TaskStatus | None = None,
    due: Annotated[str, Query(pattern="^(today|overdue|all)$")] = "all",
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[TaskOut]:
    q = select(Task).where(Task.tenant_id == p.tenant_id)
    if status:
        q = q.where(Task.status == status)
    if due != "all":
        today = _tenant_today(session, p.tenant_id)
        q = q.where(Task.status.in_(OPEN))
        if due == "overdue":
            q = q.where(Task.due_date < today)
        else:
            q = q.where(or_(Task.due_date <= today, Task.status == TaskStatus.IN_PROGRESS))
    q = q.order_by(Task.priority, Task.due_date.asc().nulls_last(), Task.created_at).limit(limit)
    return [TaskOut.model_validate(t) for t in session.scalars(q)]


@router.post("/tasks", status_code=201)
def create_task(
    data: TaskIn,
    response: Response,
    p: Annotated[Principal, Depends(require_write("tasks:write"))],
    session: Db,
) -> TaskCreated:
    norm = _norm(data.title)
    values = data.model_dump() | {
        "tenant_id": p.tenant_id, "title_norm": norm, "status": TaskStatus.PENDING,
    }
    task, created = _insert_once(
        session, Task, values, ["tenant_id", "title_norm"],
        text("status IN ('pending','in_progress')"),
        select(Task).where(
            Task.tenant_id == p.tenant_id, Task.title_norm == norm, Task.status.in_(OPEN)
        ),
    )
    if created:
        _audit(session, p, "task_created", id=str(task.id), source=data.source.value)
    else:
        response.status_code = 200
    return TaskCreated(**TaskOut.model_validate(task).model_dump(), duplicate=not created)


@router.patch("/tasks/{task_id}")
def patch_task(
    task_id: uuid.UUID,
    data: TaskPatch,
    p: Annotated[Principal, Depends(require_write("tasks:write"))],
    session: Db,
) -> TaskOut:
    task = _get(session, Task, p.tenant_id, task_id)
    sent = data.model_fields_set
    changed: list[str] = []
    for field in ("status", "priority", "title"):
        value = getattr(data, field)
        if field in sent and value is not None:
            setattr(task, field, value)
            changed.append(field)
            if field == "title":
                task.title = value.strip()
                task.title_norm = _norm(value)
    for field in ("due_date", "notes"):  # these may be cleared with null
        if field in sent:
            setattr(task, field, getattr(data, field))
            changed.append(field)
    if "status" in changed:
        task.completed_at = now() if task.status == TaskStatus.DONE else None
    if not task.title.strip():
        raise HTTPException(status_code=422, detail="title must not be blank")
    try:
        with session.begin_nested():
            session.flush()
    except IntegrityError:
        raise HTTPException(status_code=409, detail="An open task with this title exists") from None
    _audit(session, p, "task_updated", id=str(task.id), fields=changed, status=task.status.value)
    return TaskOut.model_validate(task)


# ---------------------------------------------------------------- scheduled calls / callbacks


class CallbackIn(BaseModel):
    minutes: int = Field(ge=5, le=240)  # the usual 10/20/30, or custom
    reason: str | None = Field(default=None, max_length=300)


class CallOut(Out):
    id: uuid.UUID
    kind: CallKind
    due_at: datetime
    status: CallStatus
    reason: str | None
    created_by: str
    created_at: datetime
    updated_at: datetime


class CallbackOut(CallOut):
    moved: bool


def _in_quiet(t: time, start: time, end: time) -> bool:
    return start <= t < end if start <= end else (t >= start or t < end)


@router.post("/callbacks", status_code=201)
def schedule_callback(
    data: CallbackIn,
    response: Response,
    p: Annotated[Principal, Depends(require_write("callbacks:write"))],
    session: Db,
) -> CallbackOut:
    s = get_or_create_settings(session, p.tenant_id)
    tz = ZoneInfo(s.timezone)
    due_at = now() + timedelta(minutes=data.minutes)
    local = due_at.astimezone(tz)
    if _in_quiet(local.time(), s.quiet_start, s.quiet_end):
        raise HTTPException(
            status_code=409,
            detail=f"Quiet hours ({s.quiet_start:%H:%M}-{s.quiet_end:%H:%M} {s.timezone}): "
                   "pick a later time",
        )
    pending = session.scalars(
        select(ScheduledCall).where(
            ScheduledCall.tenant_id == p.tenant_id, ScheduledCall.kind == CallKind.CALLBACK,
            ScheduledCall.status == CallStatus.PENDING,
        ).with_for_update()
    ).first()

    day_start = datetime.combine(local.date(), time.min, tzinfo=tz)
    day_end = datetime.combine(local.date() + timedelta(days=1), time.min, tzinfo=tz)
    counted = select(func.count()).select_from(ScheduledCall).where(
        ScheduledCall.tenant_id == p.tenant_id,
        ScheduledCall.status.in_([CallStatus.PENDING, CallStatus.PLACED, CallStatus.COMPLETED]),
        ScheduledCall.due_at >= day_start, ScheduledCall.due_at < day_end,
    )
    if pending is not None:
        counted = counted.where(ScheduledCall.id != pending.id)  # moving it adds no call
    if session.scalar(counted) >= s.max_calls_per_day:
        raise HTTPException(status_code=429, detail="Daily call limit reached")

    last = session.scalar(
        select(func.max(ScheduledCall.due_at)).where(
            ScheduledCall.tenant_id == p.tenant_id,
            ScheduledCall.status.in_([CallStatus.PLACED, CallStatus.COMPLETED]),
        )
    )
    if last is not None and due_at < last + timedelta(minutes=s.min_gap_minutes):
        raise HTTPException(
            status_code=409, detail=f"Calls must be at least {s.min_gap_minutes} minutes apart"
        )

    if pending is not None:
        pending.due_at, pending.reason = due_at, data.reason
        _audit(session, p, "callback_moved", id=str(pending.id), minutes=data.minutes)
        response.status_code = 200
        return CallbackOut(**CallOut.model_validate(pending).model_dump(), moved=True)
    call = ScheduledCall(tenant_id=p.tenant_id, kind=CallKind.CALLBACK, due_at=due_at,
                         reason=data.reason, created_by=p.actor)
    try:
        with session.begin_nested():
            session.add(call)
    except IntegrityError:  # a concurrent request created the pending callback first
        raise HTTPException(status_code=409, detail="Callback already pending, retry") from None
    _audit(session, p, "callback_scheduled", id=str(call.id), minutes=data.minutes)
    return CallbackOut(**CallOut.model_validate(call).model_dump(), moved=False)


@router.delete("/callbacks/pending")
def cancel_callback(
    p: Annotated[Principal, Depends(require_write("callbacks:write"))], session: Db
) -> CallOut:
    call = session.scalars(
        select(ScheduledCall).where(
            ScheduledCall.tenant_id == p.tenant_id, ScheduledCall.kind == CallKind.CALLBACK,
            ScheduledCall.status == CallStatus.PENDING,
        )
    ).first()
    if call is None:
        raise HTTPException(status_code=404, detail="No pending callback")
    call.status = CallStatus.CANCELLED
    _audit(session, p, "callback_cancelled", id=str(call.id))
    return CallOut.model_validate(call)


@router.get("/calls")
def list_calls(
    p: Annotated[Principal, Depends(require("tasks:read"))],
    session: Db,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> list[CallOut]:
    rows = session.scalars(
        select(ScheduledCall).where(ScheduledCall.tenant_id == p.tenant_id)
        .order_by(ScheduledCall.due_at.desc()).limit(limit)
    )
    return [CallOut.model_validate(r) for r in rows]


# ---------------------------------------------------------------- check-ins


class CheckinIn(BaseModel):
    kind: CallKind
    call_id: str | None = Field(default=None, max_length=200)
    started_at: datetime
    ended_at: datetime | None = None
    transcript: str | None = Field(default=None, max_length=200_000)
    summary: str | None = Field(default=None, max_length=10_000)


class CheckinOut(Out):
    id: uuid.UUID
    kind: CallKind
    call_id: str | None
    started_at: datetime
    ended_at: datetime | None
    transcript: str | None
    summary: str | None
    created_at: datetime


@router.post("/checkins", status_code=201)
def create_checkin(
    data: CheckinIn,
    p: Annotated[Principal, Depends(require_write("checkins:write"))],
    session: Db,
) -> CheckinOut:
    row = Checkin(tenant_id=p.tenant_id, **data.model_dump())
    session.add(row)
    session.flush()
    _audit(session, p, "checkin_created", id=str(row.id), kind=row.kind.value)
    return CheckinOut.model_validate(row)


@router.get("/checkins")
def list_checkins(
    p: Annotated[Principal, Depends(require("tasks:read"))],
    session: Db,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> list[CheckinOut]:
    rows = session.scalars(
        select(Checkin).where(Checkin.tenant_id == p.tenant_id)
        .order_by(Checkin.started_at.desc()).limit(limit)
    )
    return [CheckinOut.model_validate(r) for r in rows]


# ---------------------------------------------------------------- opportunities


class OppIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    source: str = Field(min_length=1, max_length=200)
    url: str | None = Field(default=None, min_length=1, max_length=2000)
    description: str | None = Field(default=None, max_length=5000)
    relevance: int = Field(ge=0, le=100)
    estimated_value: str | None = Field(default=None, max_length=200)
    deadline: date | None = None
    next_action: str | None = Field(default=None, max_length=1000)


class OppPatch(BaseModel):
    status: OppStatus | None = None
    verification_status: Verification | None = None


class OppOut(Out):
    id: uuid.UUID
    title: str
    source: str
    url: str | None
    description: str | None
    relevance: int
    estimated_value: str | None
    deadline: date | None
    next_action: str | None
    verification_status: Verification
    status: OppStatus
    created_by: str
    created_at: datetime


class OppCreated(OppOut):
    duplicate: bool


@router.post("/opportunities", status_code=201)
def create_opportunity(
    data: OppIn,
    response: Response,
    p: Annotated[Principal, Depends(require_write("opportunities:write"))],
    session: Db,
) -> OppCreated:
    url = data.url.strip() if data.url else None
    values = data.model_dump() | {"url": url, "tenant_id": p.tenant_id, "created_by": p.actor,
                                  "status": OppStatus.NEW,
                                  "verification_status": Verification.UNVERIFIED}
    if url is None:  # no URL, nothing to dedupe on
        row = Opportunity(**values)
        session.add(row)
        session.flush()
        created = True
    else:
        row, created = _insert_once(
            session, Opportunity, values, ["tenant_id", "url"], text("url IS NOT NULL"),
            select(Opportunity).where(Opportunity.tenant_id == p.tenant_id, Opportunity.url == url),
        )
    if created:
        _audit(session, p, "opportunity_created", id=str(row.id))
    else:
        response.status_code = 200
    return OppCreated(**OppOut.model_validate(row).model_dump(), duplicate=not created)


@router.get("/opportunities")
def list_opportunities(
    p: Annotated[Principal, Depends(require("opportunities:read"))],
    session: Db,
    status: OppStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[OppOut]:
    q = select(Opportunity).where(Opportunity.tenant_id == p.tenant_id)
    if status:
        q = q.where(Opportunity.status == status)
    q = q.order_by(Opportunity.relevance.desc(), Opportunity.created_at.desc()).limit(limit)
    return [OppOut.model_validate(r) for r in session.scalars(q)]


@router.patch("/opportunities/{opp_id}")
def patch_opportunity(
    opp_id: uuid.UUID,
    data: OppPatch,
    p: Annotated[Principal, Depends(require_write("opportunities:write"))],
    session: Db,
) -> OppOut:
    row = _get(session, Opportunity, p.tenant_id, opp_id)
    changed = []
    for field in ("status", "verification_status"):
        value = getattr(data, field)
        if value is not None:
            setattr(row, field, value)
            changed.append(field)
    _audit(session, p, "opportunity_updated", id=str(row.id), fields=changed,
           status=row.status.value, verification_status=row.verification_status.value)
    return OppOut.model_validate(row)


# ---------------------------------------------------------------- memory notes


class MemoryIn(BaseModel):
    kind: MemoryKind
    text: str = Field(min_length=1, max_length=2000)
    source: str = Field(min_length=1, max_length=200)


class MemoryOut(Out):
    id: uuid.UUID
    kind: MemoryKind
    text: str
    source: str
    created_at: datetime


class MemoryCreated(MemoryOut):
    duplicate: bool


@router.post("/memory", status_code=201)
def create_memory(
    data: MemoryIn,
    response: Response,
    p: Annotated[Principal, Depends(require_write("memory:write"))],
    session: Db,
) -> MemoryCreated:
    digest = hashlib.sha256(_norm(data.text).encode()).hexdigest()
    row, created = _insert_once(
        session, MemoryNote,
        data.model_dump() | {"tenant_id": p.tenant_id, "content_hash": digest},
        ["tenant_id", "content_hash"], None,
        select(MemoryNote).where(
            MemoryNote.tenant_id == p.tenant_id, MemoryNote.content_hash == digest
        ),
    )
    if created:
        _audit(session, p, "memory_created", id=str(row.id), kind=row.kind.value)
    else:
        response.status_code = 200
    return MemoryCreated(**MemoryOut.model_validate(row).model_dump(), duplicate=not created)


@router.get("/memory")
def list_memory(
    p: Annotated[Principal, Depends(require("memory:read"))],
    session: Db,
    kind: MemoryKind | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[MemoryOut]:
    q = select(MemoryNote).where(MemoryNote.tenant_id == p.tenant_id)
    if kind:
        q = q.where(MemoryNote.kind == kind)
    q = q.order_by(MemoryNote.created_at.desc()).limit(limit)
    return [MemoryOut.model_validate(r) for r in session.scalars(q)]
