#!/usr/bin/env python3
"""Stable evidence identities shared by packages, libraries, and agents."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def stable_evidence_key(item: dict[str, Any]) -> str:
    """Return a package-independent identity for one evidence excerpt."""
    evidence = item.get("evidence") or {}
    record = item.get("record") or {}
    locator = item.get("locator") or {}
    context = str(evidence.get("german_context") or "")
    quote_hash = evidence.get("quote_sha256") or hashlib.sha256(
        context.encode("utf-8")
    ).hexdigest()
    identity = {
        "page_id": record.get("page_id"),
        "passage_id": record.get("passage_id"),
        "record_id": record.get("record_id"),
        "quote_sha256": quote_hash,
        "abteilung": locator.get("abteilung"),
        "band": locator.get("band"),
        "text_type": locator.get("text_type"),
        "page": locator.get("physical_or_source_page"),
        "char_start": locator.get("char_start"),
        "char_end": locator.get("char_end"),
    }
    stable = json.dumps(identity, ensure_ascii=False, sort_keys=True)
    return "ev_" + hashlib.sha256(stable.encode("utf-8")).hexdigest()


def package_evidence_ref(package_id: str, evidence_id: str) -> str:
    """Return an unambiguous package-local evidence reference."""
    return f"{str(package_id).strip()}:{str(evidence_id).strip()}"
