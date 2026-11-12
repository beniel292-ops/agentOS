from typing import Literal

from pydantic import BaseModel, Field


class LeadExtraction(BaseModel):
    intent: str = Field(min_length=1, max_length=300, description="What the lead wants.")
    service: str | None = Field(
        default=None,
        max_length=100,
        description='Which agency service fits, e.g. "SEO", "Paid ads", "Web design".',
    )
    budget: str | None = Field(
        default=None, max_length=100, description="Budget as stated by the lead. Never invent one."
    )
    urgency: Literal["low", "medium", "high"]
    fit_score: int = Field(ge=0, le=100)
    reasoning: str = Field(min_length=1, max_length=500, description="Why that score.")


class ReplyDraft(BaseModel):
    subject: str = Field(min_length=1, max_length=150)
    body: str = Field(min_length=1, max_length=4000)
