#!/usr/bin/env python3
"""Normalize lightweight agent hints into the existing QueryPlan refinement."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Iterable


def _items(values: str | Iterable[str] | None) -> list[str]:
    if values is None:
        return []
    raw = [values] if isinstance(values, str) else list(values)
    output = []
    seen = set()
    for item in raw:
        for value in re.split(r"[,;\uFF0C\uFF1B]", str(item or "")):
            cleaned = value.strip()
            key = cleaned.casefold()
            if len(cleaned) > 1 and key not in seen:
                seen.add(key)
                output.append(cleaned)
    return output


def merge_query_hints(
    refinement: dict | None = None,
    *,
    focus_terms: str | Iterable[str] | None = None,
    context_terms: str | Iterable[str] | None = None,
    target_volumes: str | Iterable[str] | None = None,
    intent: str | None = None,
) -> dict | None:
    """Merge low-friction hints without creating a second planning protocol."""
    output = deepcopy(refinement) if refinement else {}
    mappings = (
        ("focus_terms", _items(focus_terms)),
        ("context_terms", _items(context_terms)),
        ("target_volumes", _items(target_volumes)),
    )
    for field, supplied in mappings:
        if not supplied:
            continue
        existing = output.get(field) or []
        if not isinstance(existing, list):
            raise ValueError(f"query refinement field {field} must be a list")
        merged = []
        seen = set()
        for value in list(existing) + supplied:
            key = str(value).strip().casefold()
            if key and key not in seen:
                seen.add(key)
                merged.append(value)
        output[field] = merged
    if intent:
        output["intent"] = str(intent).strip()
    return output or None
