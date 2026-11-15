"""Scheduled call placer: dials Beniel's phone through agentring, saves the transcript.

One call at a time (Redis lock). The voice AI only gets the MCP tools of app/mcp_server.py:
the agentring subprocess runs with `--agents none` (no agents.yaml, no command agents), an
explicit `--tools` allowlist, and a minimal environment (no DB / Clerk / Telegram secrets).
"""
import asyncio
import json
import logging
import os
import re
import tempfile
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.db import SessionLocal
from app.jobs import queue
from app.models import (
    AuditEvent,
    CallKind,
    CallStatus,
    Checkin,
    CosSettings,
    ScheduledCall,
    Task,
)
from app.routes.cos import _in_quiet, get_or_create_settings

log = logging.getLogger(__name__)

LOCK_KEY = "cos:call-lock"
CALL_TIMEOUT = 20 * 60
LOCK_SECONDS = CALL_TIMEOUT + 300  # outlives the call, so it can never expire mid-call
STALE_AFTER = timedelta(minutes=30)  # past due this long: too late to ring
CREATE_GRACE = timedelta(minutes=5)  # daily rows are not created once their time is this old
TRANSCRIPT_CAP = 200_000
TOOLS = "list_tasks,add_task,update_task,schedule_callback,save_request"
PROMPTS = Path(__file__).parent / "prompts"
VOICE_ACTOR = "agent:voice"
SAFE_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
PHONE_REASON = {
    CallKind.MORNING: "Morning check-in",
    CallKind.EVENING: "Evening check-in",
    CallKind.CALLBACK: "Calling back as you asked",
}
LABEL = {CallKind.MORNING: "morning", CallKind.EVENING: "evening",
         CallKind.CALLBACK: "callback", CallKind.NOTIFY: "Jarvis note"}

# (returncode, stdout); the real one runs agentring, tests replace it
Runner = Callable[[list[str], dict[str, str], float], Awaitable[tuple[int, str]]]


def now() -> datetime:
    """Module-level clock so tests can monkeypatch it."""
    return datetime.now(UTC)


@dataclass
class Claim:
    call_id: uuid.UUID
    kind: CallKind
    reason: str | None
    tz: str
    started_at: datetime


# ---------------------------------------------------------------- scheduling (sync, DB)


def ensure_daily_rows(session: Session, tenant_id: uuid.UUID, s: CosSettings, at: datetime) -> None:
    """Today's morning/evening rows, created once (even if cancelled, they are not re-made)."""
    tz = ZoneInfo(s.timezone)
    local = at.astimezone(tz)
    day_start = datetime.combine(local.date(), datetime.min.time(), tzinfo=tz)
    for kind, t in ((CallKind.MORNING, s.morning_time), (CallKind.EVENING, s.evening_time)):
        due = datetime.combine(local.date(), t, tzinfo=tz)
        if at - due > CREATE_GRACE:
            continue
        exists = session.scalar(select(func.count()).select_from(ScheduledCall).where(
            ScheduledCall.tenant_id == tenant_id, ScheduledCall.kind == kind,
            ScheduledCall.due_at >= day_start, ScheduledCall.due_at < day_start + timedelta(days=1),
        ))
        if not exists:  # the unique daily-slot index guards the race
            session.execute(insert(ScheduledCall).values(
                tenant_id=tenant_id, kind=kind, due_at=due, status=CallStatus.PENDING,
                created_by="system:scheduler",
            ).on_conflict_do_nothing())


def _skip_reason(session: Session, s: CosSettings, call: ScheduledCall, at: datetime) -> str | None:
    if s.stopped:
        return "stopped"
    if at - call.due_at > STALE_AFTER:
        return "late"
    tz = ZoneInfo(s.timezone)
    local = at.astimezone(tz)
    if _in_quiet(local.time(), s.quiet_start, s.quiet_end):
        return "quiet"
    day_start = datetime.combine(local.date(), datetime.min.time(), tzinfo=tz)
    done = (CallStatus.PLACED, CallStatus.COMPLETED)
    count = session.scalar(select(func.count()).select_from(ScheduledCall).where(
        ScheduledCall.tenant_id == call.tenant_id, ScheduledCall.status.in_(done),
        ScheduledCall.due_at >= day_start, ScheduledCall.due_at < day_start + timedelta(days=1),
    ))
    if count >= s.max_calls_per_day:
        return "limit"
    last = session.scalar(select(func.max(ScheduledCall.due_at)).where(
        ScheduledCall.tenant_id == call.tenant_id, ScheduledCall.status.in_(done),
    ))
    if last is not None and at < last + timedelta(minutes=s.min_gap_minutes):
        return "gap"
    return None


def prepare(
    factory: sessionmaker[Session], tenant_id: uuid.UUID
) -> tuple[Claim | None, list[str]]:
    """Take the earliest due pending call (marking it placed) and re-check every limit first.
    Calls that must not ring are cancelled/missed; returns (claim, skipped call_skipped refs)."""
    skipped: list[str] = []
    with factory() as session:
        s = get_or_create_settings(session, tenant_id)
        at = now()
        while True:
            call = session.scalars(
                select(ScheduledCall).where(
                    ScheduledCall.tenant_id == tenant_id,
                    ScheduledCall.status == CallStatus.PENDING, ScheduledCall.due_at <= at,
                ).order_by(ScheduledCall.due_at).limit(1).with_for_update()
            ).first()
            if call is None:
                session.commit()
                return None, skipped
            why = _skip_reason(session, s, call, at)
            if why:
                call.status = CallStatus.MISSED if why == "late" else CallStatus.CANCELLED
                skipped.append(f"{call.id}:{why}")
                continue
            call.status = CallStatus.PLACED
            session.commit()
            return Claim(call.id, call.kind, call.reason, s.timezone, at), skipped


# ---------------------------------------------------------------- the phone call


def _clean(text: str | None, limit: int) -> str:
    text = re.sub(r"[\x00-\x1f\x7f-\x9f]+", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()[:limit]


def render_instructions(kind: CallKind, tz: str, reason: str | None, at: datetime) -> str:
    local = at.astimezone(ZoneInfo(tz))
    common = (PROMPTS / "common.md").read_text().replace(
        "{now}", f"{local:%A %d %B %Y, %H:%M} ({tz})"
    )
    text = (PROMPTS / f"{kind.value}.md").read_text()
    if kind == CallKind.NOTIFY:  # the note comes from an agent: fenced, and cannot close the fence
        note = _clean(re.sub(r"</?note>", "", reason or "", flags=re.IGNORECASE), 300)
        text = text.replace("{note}", note)
    return f"{common}\n{text}"


def phone_reason(kind: CallKind, reason: str | None) -> str:
    """One line shown on the phone. Never starts with '-', so it cannot look like a flag."""
    if kind == CallKind.NOTIFY:
        return f"Jarvis note: {_clean(reason, 150)}"
    return PHONE_REASON[kind]


def build_argv(claim: Claim, instructions_file: str) -> list[str]:
    return [
        settings.agentring_bin, "call", phone_reason(claim.kind, claim.reason),
        "--ai", "--provider", settings.voice_provider,
        "--mcp", settings.voice_mcp_url, "--tools", TOOLS,
        "--instructions", f"@{instructions_file}",
        "--agents", "none",  # never load agents.yaml: no command agents (security audit)
        "--no-fallback",  # we report a missed call on Telegram ourselves
        "--agent-name", "Jarvis", "--json",
    ]


def minimal_env() -> dict[str, str]:
    env = {"PATH": SAFE_PATH, "HOME": os.environ.get("HOME", "")}
    if os.environ.get("AGENTRING_HOME"):
        env["AGENTRING_HOME"] = os.environ["AGENTRING_HOME"]
    return env


async def run_agentring(argv: list[str], env: dict[str, str], timeout: float) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(  # argv list, never a shell
        *argv, env=env, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, ""
    return proc.returncode or 0, out.decode("utf-8", "replace")


def parse_result(stdout: str) -> dict | None:
    """agentring --json prints the CallResult: call_id, answered, duration_s, end_reason,
    transcript [[user|agent, text], ...], tools, participants, fallback_msg_id."""
    for text in (stdout, stdout[stdout.find("{"): stdout.rfind("}") + 1]):
        try:
            data = json.loads(text)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return None


def format_transcript(turns: list) -> str:
    lines = []
    for turn in turns:
        try:
            role, text = turn
        except (TypeError, ValueError):
            continue
        lines.append(f"{'You' if role == 'user' else 'Jarvis'}: {text}")
    return "\n".join(lines)[:TRANSCRIPT_CAP]


# ---------------------------------------------------------------- after the call (sync, DB)


def _local_hm(dt: datetime, tz: str) -> str:
    return f"{dt.astimezone(ZoneInfo(tz)):%H:%M}"


def summarize_changes(
    session: Session, tenant_id: uuid.UUID, tz: str, start: datetime, end: datetime
) -> str:
    """What the voice AI changed during the call, from the audit log (no LLM)."""
    events = session.scalars(select(AuditEvent).where(
        AuditEvent.tenant_id == tenant_id, AuditEvent.actor == VOICE_ACTOR,
        AuditEvent.at >= start - timedelta(seconds=2), AuditEvent.at <= end + timedelta(seconds=5),
    ).order_by(AuditEvent.id))
    added: list[str] = []
    done: list[str] = []
    other: list[str] = []
    callback = None
    for e in events:
        ref = e.data.get("id")
        if e.action in ("callback_scheduled", "callback_moved"):
            call = session.scalar(select(ScheduledCall).where(
                ScheduledCall.id == uuid.UUID(ref), ScheduledCall.tenant_id == tenant_id))
            callback = _local_hm(call.due_at, tz) if call else callback
        elif e.action in ("task_created", "task_updated"):
            task = session.scalar(select(Task).where(
                Task.id == uuid.UUID(ref), Task.tenant_id == tenant_id))
            if task is None:
                continue
            title = _clean(task.title, 60)
            if e.action == "task_created":
                added.append(title)
            elif "status" in e.data.get("fields", []) and e.data.get("status") == "done":
                done.append(title)
            else:
                other.append(title)
    parts = [f"{label}: {', '.join(items)}" for label, items in
             (("Added", added), ("Done", done), ("Updated", other)) if items]
    if callback:
        parts.append(f"Callback: {callback}")
    return "; ".join(parts) + "." if parts else "No changes."


def finish(
    factory: sessionmaker[Session], tenant_id: uuid.UUID, claim: Claim, result: dict | None
) -> tuple[str, str]:
    """Save the outcome; returns the Telegram (kind, ref) to send."""
    ended = now()
    with factory() as session:
        call = session.scalars(select(ScheduledCall).where(
            ScheduledCall.id == claim.call_id, ScheduledCall.tenant_id == tenant_id
        )).one()
        if result is None or not result.get("answered"):
            call.status = CallStatus.MISSED
            session.commit()
            return "call_skipped", f"{call.id}:{'error' if result is None else 'missed'}"
        call.status = CallStatus.COMPLETED
        checkin = Checkin(
            tenant_id=tenant_id, kind=claim.kind, call_id=_clean(str(result.get("call_id")), 200),
            started_at=claim.started_at, ended_at=ended,
            transcript=format_transcript(result.get("transcript") or []),
            summary=summarize_changes(session, tenant_id, claim.tz, claim.started_at, ended),
        )
        session.add(checkin)
        session.flush()
        ref = str(checkin.id)
        session.commit()
        return "call_summary", ref


# ---------------------------------------------------------------- the job


async def _place_one(tenant_id: uuid.UUID, runner: Runner) -> None:
    claim, skipped = await run_in_threadpool(prepare, SessionLocal, tenant_id)
    for ref in skipped:
        await queue.enqueue_notify("call_skipped", ref)
    if claim is None:
        return
    result = None
    path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".md", prefix="jarvis-", delete=False, encoding="utf-8"  # mode 0600
        ) as f:
            path = f.name
            f.write(render_instructions(claim.kind, claim.tz, claim.reason, claim.started_at))
        code, out = await runner(build_argv(claim, path), minimal_env(), CALL_TIMEOUT)
        result = parse_result(out) if code == 0 else None
    except Exception as e:  # the row is already "placed": always finish it
        log.warning("call failed: %s", type(e).__name__)
    finally:
        if path:
            Path(path).unlink(missing_ok=True)
    kind, ref = await run_in_threadpool(finish, SessionLocal, tenant_id, claim, result)
    await queue.enqueue_notify(kind, ref)


async def place_due_calls(ctx, runner: Runner = run_agentring) -> None:
    """Every minute (and right after "call me now"): ring for the earliest due call."""
    tenant_id = settings.telegram_owner_tenant_id
    if tenant_id is None:
        return

    def ensure() -> None:
        with SessionLocal() as session:
            ensure_daily_rows(session, tenant_id, get_or_create_settings(session, tenant_id), now())
            session.commit()

    try:
        await run_in_threadpool(ensure)
        redis = ctx["redis"]
        if not await redis.set(LOCK_KEY, "1", nx=True, ex=LOCK_SECONDS):
            return  # a call is running
        try:
            await _place_one(tenant_id, runner)
        finally:
            await redis.delete(LOCK_KEY)
    except Exception:  # never let arq retry this job: a retry could ring twice
        log.exception("place_due_calls failed")
