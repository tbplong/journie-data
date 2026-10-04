"""Source-independent missing-field derivation and read-only consistency checks."""

from datetime import date

from schema import Extraction, canonical

PROCESSING_VERSION = "missing-fields-v1"
DIAGNOSTICS_VERSION = "consistency-v1"


def derive_missing_fields(extraction: Extraction) -> list[str]:
    missing = []
    if extraction.destination is None:
        missing.append("destination")
    if all(
        value is None
        for value in (extraction.start_date, extraction.end_date, extraction.duration_days)
    ):
        missing.append("travel_dates_or_duration")
    if extraction.budget_vnd is None:
        missing.append("budget")
    if extraction.available_start is None or extraction.available_end is None:
        missing.append("available_time")
    return missing


def process_extraction(extraction: Extraction) -> Extraction:
    return extraction.model_copy(
        deep=True, update={"missing_fields": derive_missing_fields(extraction)}
    )


def consistency_issues(extraction: Extraction) -> list[dict[str, str]]:
    """Check representation consistency, not grounding, feasibility, or user intent."""
    issues: list[dict[str, str]] = []

    def report(code: str, field: str, detail: str, severity: str = "error") -> None:
        issues.append({"code": code, "field": field, "severity": severity, "detail": detail})

    lists = {
        "interests": ("soft_constraints", "prefer"),
        "preferred_places": ("soft_constraints", "prefer"),
        "must_visit": ("hard_constraints", "include"),
        "avoid": ("hard_constraints", "exclude"),
    }
    seen: set[str] = set()
    for kind in ("hard_constraints", "soft_constraints"):
        for atom in getattr(extraction, kind):
            key = canonical(atom.model_dump())
            if key in seen:
                report("duplicate_atom", atom.field, "Duplicate atom across constraint lists.")
            seen.add(key)
            valid = True
            if atom.field in lists:
                expected_kind, operator = lists[atom.field]
                valid = kind == expected_kind and atom.operator == operator
            elif kind == "soft_constraints":
                valid = atom.operator == "prefer"
            elif atom.field == "destination":
                valid = atom.operator == "eq"
            elif atom.field != "other":
                valid = atom.operator in ("eq", "min", "max")
            else:
                valid = atom.operator != "prefer"
            if not valid:
                report(
                    "atom_classification_mismatch",
                    atom.field,
                    f"Unexpected {kind} operator {atom.operator!r} for this field.",
                )

    for field in (
        "destination",
        "start_date",
        "end_date",
        "duration_days",
        "budget_vnd",
        "available_start",
        "available_end",
    ):
        value = getattr(extraction, field)
        if value is None:
            continue
        operators = ("eq", "max") if field == "budget_vnd" else ("eq",)
        values = {
            atom.value
            for atom in extraction.hard_constraints
            if atom.field == field and atom.operator in operators
        }
        if not values:
            report("missing_atom", field, "Populated scalar lacks a corresponding factual atom.")
        elif str(value) not in values:
            report("scalar_atom_mismatch", field, "Scalar differs from corresponding atom values.")

    for field, (kind, operator) in lists.items():
        values = set(getattr(extraction, field))
        atom_values = {
            atom.value
            for atom in getattr(extraction, kind)
            if atom.field == field and atom.operator == operator
        }
        if values - atom_values:
            report("missing_atom", field, "List entries lack corresponding atoms.")
        if atom_values - values:
            report(
                "list_atom_mismatch", field, "Atom values are absent from the corresponding list."
            )

    if extraction.missing_fields != derive_missing_fields(extraction):
        report(
            "missing_fields_mismatch", "missing_fields", "Missing fields differ from null logic."
        )

    # Only these conflicts have mechanically checkable evidence in the schema.
    conflicts: dict[str, bool | None] = {
        "date_order": None,
        "duration_disagreement": None,
        "budget_disagreement": False,
        "must_visit_avoid": bool(set(extraction.must_visit) & set(extraction.avoid)),
    }
    if extraction.start_date is not None and extraction.end_date is not None:
        days = (
            date.fromisoformat(extraction.end_date) - date.fromisoformat(extraction.start_date)
        ).days + 1
        conflicts["date_order"] = days < 1
        if extraction.duration_days is not None:
            conflicts["duration_disagreement"] = extraction.duration_days != days
    bounds: dict[str, list[int]] = {"min": [], "max": []}
    uncertain_bound = False
    for atom in extraction.hard_constraints:
        if atom.field != "budget_vnd" or atom.operator not in bounds:
            continue
        try:
            value = int(atom.value)
            if str(value) != atom.value:
                raise ValueError("Not a canonical integer string")
        except ValueError:
            uncertain_bound = True
            report(
                "budget_bound_requires_review",
                "budget_vnd",
                "Budget bound cannot be checked as a canonical integer.",
                "review",
            )
        else:
            bounds[atom.operator].append(value)
    observed_budget_conflict = any(
        minimum > maximum for minimum in bounds["min"] for maximum in bounds["max"]
    )
    conflicts["budget_disagreement"] = (
        True if observed_budget_conflict else None if uncertain_bound else False
    )
    for conflict, present in conflicts.items():
        declared = conflict in extraction.possible_conflicts
        if present is not None and present != declared:
            report(
                "conflict_metadata_mismatch",
                "possible_conflicts",
                f"{conflict}: declared={declared}, mechanically observed={present}.",
            )
        elif present is None and declared:
            report(
                "conflict_metadata_unverifiable",
                "possible_conflicts",
                f"{conflict}: required explicit values are absent.",
                "review",
            )

    if "destination_disagreement" in extraction.possible_conflicts:
        destinations = {
            atom.value
            for atom in extraction.hard_constraints
            if atom.field == "destination" and atom.operator == "eq"
        }
        if extraction.destination is not None or len(destinations) < 2:
            report(
                "conflict_metadata_mismatch",
                "possible_conflicts",
                "Declared destination disagreement requires a null scalar and distinct eq atoms.",
            )

    start, end = extraction.available_start, extraction.available_end
    if start is not None and end is not None:
        if end < start:
            report(
                "overnight_relation_requires_review",
                "available_time",
                "End precedes start on the clock; the schema lacks a day offset, so the day "
                "relationship and time_order cannot be independently verified.",
                "review",
            )
        elif "time_order" in extraction.possible_conflicts:
            report(
                "conflict_metadata_mismatch",
                "possible_conflicts",
                "Declared time_order is not supported by the clock endpoints.",
            )
    elif "time_order" in extraction.possible_conflicts:
        report(
            "conflict_metadata_unverifiable",
            "possible_conflicts",
            "time_order: exact endpoints are absent; day relationship cannot be verified.",
            "review",
        )
    if extraction.possible_conflicts and not extraction.needs_clarification:
        report(
            "conflict_requires_clarification",
            "needs_clarification",
            "Declared conflicts require clarification; flags were not changed.",
        )
    return issues
