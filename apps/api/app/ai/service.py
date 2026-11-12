import re
from importlib import resources

from app.ai.client import call_structured
from app.ai.schemas import LeadExtraction, ReplyDraft
from app.models import Lead, Tenant

DEFAULT_FACTS = "No details provided yet."
DEFAULT_TONE = "friendly and professional"

_CLOSING_TAG = re.compile(r"<\s*/\s*(lead|agency_facts)\s*>", re.IGNORECASE)


def _prompt(name: str) -> str:
    return resources.files("app.ai.prompts").joinpath(name).read_text(encoding="utf-8")


def _clean(text: str | None) -> str:
    """Neutralise closing tags so user text cannot break out of its data block."""
    return _CLOSING_TAG.sub(lambda m: f"<​/{m.group(1)}>", text or "")


def _lead_block(lead: Lead) -> str:
    return (
        "<lead>\n"
        f"name: {_clean(lead.name)}\n"
        f"email: {_clean(lead.email)}\n"
        f"company: {_clean(lead.company) or '(not given)'}\n"
        f"source: {_clean(lead.source)}\n"
        f"message:\n{_clean(lead.message)}\n"
        "</lead>"
    )


def extract_lead(lead: Lead) -> tuple[LeadExtraction, str, dict]:
    return call_structured(
        _prompt("extract.md"), _lead_block(lead), "extract_lead", LeadExtraction
    )


def draft_reply(
    lead: Lead, extraction: LeadExtraction, tenant: Tenant
) -> tuple[ReplyDraft, str, dict]:
    settings = tenant.settings or {}
    facts = settings.get("agency_facts") or DEFAULT_FACTS
    tone = settings.get("tone") or DEFAULT_TONE
    system = _prompt("draft.md").replace("{tone}", _clean(tone)).replace(
        "{agency_name}", _clean(tenant.name)
    )
    user = (
        f"<agency_facts>\n{_clean(facts)}\n</agency_facts>\n\n"
        f"{_lead_block(lead)}\n\n"
        "Analysis of the lead (from our own system):\n"
        f"intent: {extraction.intent}\n"
        f"service: {extraction.service or 'unknown'}\n"
        f"budget: {extraction.budget or 'not stated'}\n"
        f"urgency: {extraction.urgency}"
    )
    return call_structured(system, user, "write_reply", ReplyDraft)
