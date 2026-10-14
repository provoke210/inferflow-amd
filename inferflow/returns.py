"""Deterministic demo-shop policy. This is a precheck, never a refund approval."""
from typing import List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt


class ReturnFacts(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    days_since_delivery: Optional[StrictInt] = Field(default=None, ge=0)
    unused: Optional[StrictBool] = None
    packaging_intact: Optional[StrictBool] = None


class ConditionCheck(BaseModel):
    field: Literal["days_since_delivery", "unused", "packaging_intact"]
    status: Literal["met", "not_met", "unknown"]
    explanation: str


class ReturnCheck(BaseModel):
    policy_version: Literal["demo-return-v1"] = "demo-return-v1"
    window_days: Literal[7] = 7
    status: Literal["needs_information", "conditions_not_met", "ready_for_human_review"]
    facts: ReturnFacts
    checks: List[ConditionCheck]
    missing_fields: List[str]
    unmet_conditions: List[str]
    questions: List[str]
    explanation: str
    human_review_required: Literal[True] = True
    automatic_action: Literal[False] = False
    source: Literal["local_rules"] = "local_rules"


def check_return(facts: ReturnFacts) -> ReturnCheck:
    """Day 0 through day 7 inclusive; all three facts must be explicitly known.

    Unknown facts take precedence in the overall status. Any already-known
    failing conditions remain visible and never become a positive decision.
    Packaging only needs to be intact, not unopened. No LLM output is an input.
    """
    values = [
        ("days_since_delivery", facts.days_since_delivery,
         facts.days_since_delivery is not None and facts.days_since_delivery <= 7,
         f"Delivered {facts.days_since_delivery} days ago. Demo policy allows days 0 through 7 inclusive.",
         "How many days ago was the item delivered?"),
        ("unused", facts.unused, facts.unused is True,
         "The item is unused." if facts.unused is True else "The item has been used and does not meet the unused condition.",
         "Is the item unused?"),
        ("packaging_intact", facts.packaging_intact, facts.packaging_intact is True,
         "Packaging is intact. Unopened packaging is not an additional requirement." if facts.packaging_intact is True else "Packaging is not intact and does not meet the packaging condition.",
         "Is the packaging intact?"),
    ]
    checks, missing, unmet, questions = [], [], [], []
    for field, value, met, explanation, question in values:
        if value is None:
            missing.append(field)
            questions.append(question)
            state, explanation = "unknown", "Not confirmed. " + question
        else:
            state = "met" if met else "not_met"
            if not met:
                unmet.append(field)
        checks.append(ConditionCheck(field=field, status=state, explanation=explanation))
    if missing:
        status = "needs_information"
        explanation = "More information is needed. Eligibility cannot be established."
        if unmet:
            explanation += " Known facts also fail one or more conditions; see the checks below."
    elif unmet:
        status = "conditions_not_met"
        explanation = "The supplied facts do not meet this demo shop policy. Refer to a person for verification or exceptions."
    else:
        status = "ready_for_human_review"
        explanation = "The supplied facts meet the demo policy conditions and can go to human review. No return or refund is approved."
    return ReturnCheck(status=status, facts=facts, checks=checks,
                       missing_fields=missing, unmet_conditions=unmet,
                       questions=questions, explanation=explanation)
