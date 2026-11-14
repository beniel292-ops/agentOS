"""The AI step for one lead: extract -> score -> draft. Sync code; the worker runs it in a thread.

No DB session is held while the (slow) AI calls run. Every state change goes through transition().
"""
import logging
import uuid

from arq import Retry
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.ai import AIError
from app.ai.service import draft_reply, extract_lead
from app.models import Draft, Lead, LeadAnalysis, LeadStatus, Tenant
from app.workflow.transitions import ConcurrentTransition, transition

log = logging.getLogger(__name__)
MAX_TRIES = 3
LOW_FIT_BELOW = 20
ACTOR = "worker"


def _error_text(exc: Exception) -> str:
    # Never include lead text or keys: AIError.attempts only holds model names and short reasons.
    if isinstance(exc, AIError):
        detail = "; ".join(f"{m} {r}" for m, r in exc.attempts) or "no models configured"
        return f"AIError: {detail}"[:500]
    return f"{type(exc).__name__}: unexpected error"


def _processing_lead(session: Session, lead_id: uuid.UUID) -> Lead | None:
    lead = session.get(Lead, lead_id)
    if lead is None or lead.status != LeadStatus.PROCESSING:
        log.info("lead %s is no longer PROCESSING; stopping", lead_id)
        return None
    return lead


def _start(factory: sessionmaker[Session], lead_id: uuid.UUID, job_try: int):
    """Move NEW -> PROCESSING (or resume PROCESSING). Returns (lead, tenant) or None to skip."""
    with factory() as session:
        lead = session.get(Lead, lead_id)
        if lead is None:
            log.warning("process_lead: lead %s not found", lead_id)
            return None
        if lead.status == LeadStatus.NEW:
            try:
                transition(session, lead, LeadStatus.PROCESSING, ACTOR, {"try": job_try})
            except ConcurrentTransition:
                session.rollback()
                log.info("process_lead: lead %s was picked up elsewhere", lead_id)
                return None
        elif lead.status != LeadStatus.PROCESSING:
            log.info("process_lead: skip lead %s in status %s", lead_id, lead.status)
            return None
        lead.attempts = job_try
        tenant = session.get(Tenant, lead.tenant_id)
        session.commit()
        return lead, tenant  # loaded and detached on close (expire_on_commit=False)


def _save_analysis(session: Session, lead: Lead, extraction, model: str, raw: dict) -> None:
    values = {
        "intent": extraction.intent,
        "service": extraction.service,
        "budget": extraction.budget,
        "urgency": extraction.urgency,
        "fit_score": extraction.fit_score,
        "reasoning": extraction.reasoning,
        "model": model,
        "raw_json": raw,
    }
    stmt = insert(LeadAnalysis).values(tenant_id=lead.tenant_id, lead_id=lead.id, **values)
    session.execute(stmt.on_conflict_do_update(index_elements=["lead_id"], set_=values))


def _save_draft(session: Session, lead: Lead, draft) -> None:
    stmt = insert(Draft).values(
        tenant_id=lead.tenant_id, lead_id=lead.id, subject=draft.subject, body=draft.body
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["lead_id"],
            set_={"subject": draft.subject, "body": draft.body},
            where=Draft.decision.is_(None),  # never overwrite a decided draft
        )
    )


def _record_failure(
    factory: sessionmaker[Session], lead_id: uuid.UUID, exc: Exception, job_try: int
) -> None:
    message = _error_text(exc)
    log.warning("process_lead %s try %s failed: %s", lead_id, job_try, message)
    with factory() as session:
        session.rollback()
        lead = _processing_lead(session, lead_id)
        if lead is None:
            return
        lead.last_error = message
        if job_try >= MAX_TRIES:
            transition(session, lead, LeadStatus.FAILED, ACTOR, {"error": message[:200]})
        session.commit()


def run(
    factory: sessionmaker[Session], lead_id: str, job_try: int, force: bool = False
) -> None:
    lid = uuid.UUID(lead_id)
    started = _start(factory, lid, job_try)
    if started is None:
        return
    lead, tenant = started
    try:
        extraction, x_model, x_raw = extract_lead(lead)  # slow; no session open
        with factory() as session:
            current = _processing_lead(session, lid)
            if current is None:
                return
            _save_analysis(session, current, extraction, x_model, x_raw)
            if extraction.fit_score < LOW_FIT_BELOW and not force:
                transition(
                    session, current, LeadStatus.LOW_FIT, ACTOR, {"fit_score": extraction.fit_score}
                )
                current.last_error = None
                session.commit()
                return
            session.commit()  # keep the analysis even if drafting fails

        draft, d_model, _ = draft_reply(lead, extraction, tenant)  # slow; no session open
        with factory() as session:
            current = _processing_lead(session, lid)
            if current is None:
                return
            _save_draft(session, current, draft)
            transition(
                session, current, LeadStatus.DRAFT_READY, ACTOR,
                {"model": d_model, "fit_score": extraction.fit_score},
            )
            current.last_error = None
            session.commit()
    except Exception as exc:  # noqa: BLE001 - any failure counts as a failed try
        _record_failure(factory, lid, exc, job_try)
        if job_try < MAX_TRIES:
            raise Retry(defer=30 * job_try) from exc
