"""Prompt experiments preserve the authoritative contract and output schema."""

from __future__ import annotations

import json
from typing import Literal

from schema import ANNOTATION_RULES, Extraction

PromptVariant = Literal["baseline", "reinforced"]
RESPONSE_SCHEMA = Extraction.model_json_schema()
PROMPT_VERSIONS = {"baseline": "baseline-v1", "reinforced": "reinforced-v1"}

REINFORCEMENT = """
FINAL EXTRACTION CHECKLIST (perform silently, do not print it):
1. Cover every explicit actionable travel clause exactly once in the required atoms.
   Check scalar/list facts against their atoms; budget_scope alone is scalar-only.
2. Copy only stated facts. Never calculate an unstated end date or trip duration.
   Never populate budget_vnd from a minimum-only budget, foreign currency, or a bare number.
3. Use hard eq for exact facts, hard min/max for stated bounds, hard include/exclude
   for mandatory visits/avoidance, and soft prefer for interests and flexible targets.
   An approximate numerical visit target is soft prefer, not an invented exact obligation
   or tolerance. A visit duration does not itself imply must_visit or an interest.
4. Preserve unmodeled explicit requirements, foreign budgets and next-day availability
   using other atoms and the original clause wording. Preserve unmapped place spelling.
5. missing_fields follows null scalars, not general itinerary completeness. A start date
   alone supplies travel-date information. Zero VND is a usable explicit budget.
6. Missing information alone does not require clarification. Preserve ambiguity and
   contradictions; do not repair them or infer feasibility. Return only complete JSON.

Independently authored atom illustrations ONLY, not complete output objects:
- "At Gallery Cedar stay at least 75 minutes": hard visit_duration_min min "Gallery Cedar:75".
  Do not add Gallery Cedar to must_visit just because a duration was stated.
- "Prefer roughly 35 minutes at Garden Willow": soft visit_duration_min prefer "Garden Willow:35".
- "budget exactly 60 CHF": hard other eq "budget exactly 60 CHF"; budget_vnd remains null.
  Foreign currency alone does not require clarification. Do not convert or add budget scope.
- "Start 2027-04-18, duration 4 days": hard start_date eq "2027-04-18" and duration_days eq "4";
  end_date stays null. No calculation supplies an additional stated fact.
- "available from 21:15 to 00:45 the next day": hard available_start eq "21:15",
  hard available_end eq "00:45", hard other eq "available from 21:15 to 00:45 the next day".
  No time_order conflict, no derived trip dates/duration. Unstated overnight is not assumed.
Do not copy illustration facts into the user's extraction. Do not print reasoning,
the checklist, or partial example objects; return all required schema keys.
"""


def build_prompt(variant: PromptVariant = "baseline") -> str:
    if variant not in PROMPT_VERSIONS:
        raise ValueError(f"Unknown prompt variant: {variant}")
    baseline = (
        ANNOTATION_RULES + "\nJSON Schema:\n" + json.dumps(RESPONSE_SCHEMA, ensure_ascii=False)
    )
    return baseline + (REINFORCEMENT if variant == "reinforced" else "")
