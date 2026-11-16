"""Send the approved reply for one lead. Sync code; the worker runs it in a thread.

Only APPROVED leads with an approved draft are ever sent. No DB transaction is open during the
HTTP call. The draft's send_idempotency_key makes a retried send a no-op at Resend, and
provider_message_id records a send whose DB commit was lost.
"""
import logging
import uuid

from arq import Retry
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app import email
from app.config import settings
from app.models import Draft, DraftDecision, Lead, LeadStatus
from app.workflow.process import ACTOR, MAX_TRIES
from app.workflow.transitions import ConcurrentTransition, transition

log = logging.getLogger(__name__)
DEFAULT_SUBJECT = "Re: your enquiry"


def _load(session: Session, lead_id: uuid.UUID, tenant_id: uuid.UUID, lock: bool = False):
    q = select(Lead).where(Lead.id == lead_id, Lead.tenant_id == tenant_id)
    lead = session.scalars(q.with_for_update() if lock else q).first()
    if lead is None:
        return None, None
    draft = session.scalars(
        select(Draft).where(Draft.lead_id == lead.id, Draft.tenant_id == tenant_id)
    ).first()
    return lead, draft


def _sendable(lead: Lead | None, draft: Draft | None) -> bool:
    return (
        lead is not None and lead.status == LeadStatus.APPROVED
        and draft is not None and draft.decision == DraftDecision.APPROVED
    )


def run(factory: sessionmaker[Session], lead_id: str, job_try: int) -> str | None:
    """Returns the notification kind ("lead_sent" / "send_failed") once committed."""
    lid = uuid.UUID(lead_id)
    if not settings.email_configured:
        log.info("send: email not configured; lead %s stays APPROVED", lid)
        return None
    with factory() as session:  # tx 1: read only
        tenant_id = session.scalar(select(Lead.tenant_id).where(Lead.id == lid))
        lead, draft = _load(session, lid, tenant_id) if tenant_id else (None, None)
        if not _sendable(lead, draft):
            log.warning("send: refusing lead %s (not APPROVED with an approved draft)", lid)
            return None
        to, subject = lead.email, draft.subject or DEFAULT_SUBJECT
        body, key = draft.edited_body or draft.body, draft.send_idempotency_key
        message_id = draft.provider_message_id

    if message_id is None:  # no transaction open during the HTTP call
        try:
            message_id = email.send_email(to, subject, body, key)
        except email.SendError as exc:
            log.warning("send: lead %s try %s failed: %s", lid, job_try, exc)
            if not exc.permanent and job_try < MAX_TRIES:
                raise Retry(defer=30 * job_try) from exc
            return _record_failure(factory, lid, tenant_id, str(exc))

    with factory() as session:  # tx 2
        lead, draft = _load(session, lid, tenant_id, lock=True)
        if not _sendable(lead, draft):
            return None
        draft.provider_message_id = message_id
        try:
            transition(session, lead, LeadStatus.SENT, ACTOR, {"provider_message_id": message_id})
        except ConcurrentTransition:
            return None
        lead.last_error = None
        session.commit()
    return "lead_sent"


def _record_failure(
    factory: sessionmaker[Session], lead_id: uuid.UUID, tenant_id: uuid.UUID, reason: str
) -> str | None:
    with factory() as session:
        lead, draft = _load(session, lead_id, tenant_id, lock=True)
        if not _sendable(lead, draft):
            return None
        lead.last_error = f"send: {reason}"[:500]
        session.commit()
    return "send_failed"
