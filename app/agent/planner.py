from __future__ import annotations

from typing import Iterable

ALLOWED_TOOLS = [
    "inspect_source",
    "model_system",
    "profile_source",
    "classify_sensitive_data",
    "infer_business_rules",
    "generate_dataset",
    "validate_dataset",
    "package_dataset",
]

MANDATORY_TAIL = [
    "classify_sensitive_data",
    "infer_business_rules",
    "generate_dataset",
    "validate_dataset",
    "package_dataset",
]


def deterministic_plan(has_source: bool) -> list[str]:
    return (["inspect_source", "profile_source"] if has_source else ["model_system"]) + list(MANDATORY_TAIL)


def normalize_plan(proposed: Iterable[str] | None, has_source: bool) -> list[str]:
    """Constrain an LLM plan to safe, allow-listed tools and mandatory safety gates."""
    base = deterministic_plan(has_source)
    if not proposed:
        return base
    allowed = set(ALLOWED_TOOLS)
    filtered: list[str] = []
    for step in proposed:
        step = str(step).strip()
        if step in allowed and step not in filtered:
            filtered.append(step)
    # Source/model acquisition is mutually selected from actual request context.
    filtered = [x for x in filtered if x not in {"inspect_source", "model_system", "profile_source"}]
    prefix = ["inspect_source", "profile_source"] if has_source else ["model_system"]
    # Mandatory steps are restored in fixed order. This prevents "skip validation" plans.
    return prefix + list(MANDATORY_TAIL)
