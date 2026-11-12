"""Live eval: run extract + draft on the sample leads against OmniRoute (no DB).

Usage: cd apps/api && uv run python -m scripts.eval_ai
"""
import json
from pathlib import Path

from app.ai.service import draft_reply, extract_lead
from app.config import settings
from app.models import Lead, Tenant

SAMPLES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "sample_leads.json"

FACTS = """Brightline Digital is a small digital marketing agency based in Chennai.
- SEO: from Rs 25,000/month
- Google and Meta ads management: from Rs 20,000/month plus ad spend
- Website redesign: from Rs 80,000
- Onboarding takes about 2 weeks."""


def main() -> None:
    tenant = Tenant(name="Brightline Digital", settings={"agency_facts": FACTS})
    failures: list[str] = []
    print(f"models: {[settings.ai_model, *settings.ai_fallback_models]}")
    for i, sample in enumerate(json.loads(SAMPLES.read_text()), 1):
        lead = Lead(**sample)
        print(f"\n=== Lead {i}: {lead.name} ===")
        try:
            ext, model, _ = extract_lead(lead)
            print(f"model: {model}\nfit_score: {ext.fit_score}  urgency: {ext.urgency}")
            print(f"intent: {ext.intent}\nservice: {ext.service}  budget: {ext.budget}")
            draft, model, _ = draft_reply(lead, ext, tenant)
            print(f"\n[draft via {model}]\nSubject: {draft.subject}\n\n{draft.body}")
        except Exception as e:  # report and continue with the next lead
            failures.append(f"lead {i} ({lead.name}): {type(e).__name__}: {e}")
            print(f"FAILED: {e}")
    print(f"\n=== Summary: {5 - len(failures)}/5 ok ===")
    for f in failures:
        print(" -", f)


if __name__ == "__main__":
    main()
