#!/usr/bin/env python3
"""Conservative text-layer classification for MEGA page records."""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Iterable

CLASSIFIER_VERSION = "text-layer-v3"


@dataclass(frozen=True)
class LayerDecision:
    layer: str
    confidence: float
    provenance: str
    header: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    value = value.replace("ſ", "s")
    return re.sub(r"\s+", " ", value).strip().lower()


def extract_running_header(text: str) -> str:
    """Return a known running header, then fall back to the first plausible line."""
    lines = [
        unicodedata.normalize("NFKC", raw_line).strip()
        for raw_line in (text or "").splitlines()[:12]
    ]
    known_pattern = re.compile(
        r"\b("
        r"editorische\s+einleitung|editorische\s+hinweise|"
        r"hinweise\s+zur\s+edition|inhaltsverzeichnis|inhalt|"
        r"einleitung|vorwort|sachregister|namenregister|"
        r"literaturregister|personenregister|quellenregister"
        r")\b",
        flags=re.IGNORECASE,
    )
    for line in lines:
        known_heading = known_pattern.search(line)
        if known_heading:
            return re.sub(r"\s+", " ", known_heading.group(1))[:180]

    for line in lines:
        line = re.sub(r"^[\W_\d]+", "", line, flags=re.UNICODE).strip()
        if len(line) < 3:
            continue
        letters = sum(char.isalpha() for char in line)
        if letters < 3 or letters / max(len(line), 1) < 0.45:
            continue
        return re.sub(r"\s+", " ", line)[:180]
    return ""

def _heading_matches(header: str, patterns: Iterable[str]) -> bool:
    normalized = normalize_text(header)
    return any(re.match(pattern, normalized, flags=re.IGNORECASE) for pattern in patterns)


def _strong_editorial_anchor(text: str) -> bool:
    normalized = normalize_text(text[:3000])
    patterns = (
        r"\bder vorliegende band\b",
        r"\b(?:das|der|die) vorliegende(?:n|r|s)?\b",
        r"\bim vorliegenden band\b",
        r"\bdie herausgeber\b",
        r"\bherausgegeben von\b",
        r"\beditorische hinweise\b",
        r"\bedierten text\b",
        r"\bwerkstellenapparat\b",
        r"\btextgrundlage\b",
        r"\bentstehung und überlieferung\b",
    )
    return any(re.search(pattern, normalized) for pattern in patterns)


def _editorial_narration_score(text: str) -> int:
    """Detect third-person editorial narration inside an introduction."""
    normalized = normalize_text(text[:5000])
    patterns = (
        r"\bmarx bezeichnete\b",
        r"\bmarx(?:'|\u2019|s)? kritik und analyse\b",
        r"\bf\u00fcr marx war\b",
        r"\bmarx ging von\b",
        r"\bmarx wies nach\b",
        r"\bmarx formulierte\b",
        r"\bim manuskript von\b",
        r"\bim vorliegenden band\b",
        r"\bheft [ivxlcdm0-9]+\b",
    )
    return sum(bool(re.search(pattern, normalized)) for pattern in patterns)


def _looks_like_volume_contents(text: str, page: int, max_page: int) -> bool:
    if page > max(40, int(max_page * 0.15)):
        return False
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()[:120]]
    page_reference_lines = sum(
        bool(re.search(r"(?:\s|^)[0-9]{1,4}(?:\s+[0-9]{1,4})?\s*$", line))
        for line in lines if line
    )
    normalized = normalize_text(text[:5000])
    has_text_apparat_columns = "text apparat" in normalized
    return page_reference_lines >= 4 or (
        has_text_apparat_columns and page_reference_lines >= 2
    )

def _looks_like_front_matter(text: str, page: int, max_page: int) -> bool:
    if max_page <= 0 or page > max(12, int(max_page * 0.025)):
        return False
    normalized = normalize_text(text[:2500])
    markers = (
        "marx engels gesamtausgabe",
        "marx-engels-gesamtausgabe",
        "internationale marx-engels-stiftung",
        "akademie verlag",
        "dietz verlag",
        "isbn ",
        "copyright",
        "cip-einheitsaufnahme",
    )
    marker_count = sum(marker in normalized for marker in markers)
    return marker_count >= 1 and len(normalized) < 2200


def classify_records(records: list[dict]) -> dict[str, LayerDecision]:
    """Classify one source file as a sequence; returns decisions keyed by chunk id."""
    if not records:
        return {}

    decisions: dict[str, LayerDecision] = {}
    source_type = str(records[0].get("source_type") or "").upper()
    source_collection = str(records[0].get("source_collection") or "ocr").lower()

    if source_type == "APPARAT":
        return {
            record["id"]: LayerDecision(
                "apparatus", 0.99, f"{CLASSIFIER_VERSION}:source_type=APPARAT"
            )
            for record in records
        }

    if source_collection == "megadigital" and source_type == "TEXT":
        return {
            record["id"]: LayerDecision(
                "author_text", 0.99, f"{CLASSIFIER_VERSION}:structured_megadigital_text"
            )
            for record in records
        }

    max_page = max(int(record.get("page") or 0) for record in records)
    headers: dict[str, str] = {}
    header_groups: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        header = extract_running_header(record.get("chunk_text") or "")
        headers[record["id"]] = header
        header_groups[normalize_text(header)].append(record)

    editorial_headers: set[str] = set()
    for normalized_header, group in header_groups.items():
        if not normalized_header:
            continue
        is_intro_heading = re.match(
            r"^(einleitung|vorwort|editorische einleitung)\b", normalized_header
        )
        in_front_region = min(int(item.get("page") or 0) for item in group) <= max(
            80, int(max_page * 0.20)
        )
        strong_anchor = any(
            _strong_editorial_anchor(item.get("chunk_text") or "") for item in group
        )
        narrated_intro = any(
            _editorial_narration_score(item.get("chunk_text") or "") >= 2
            for item in group
        )
        if is_intro_heading and in_front_region and (
            strong_anchor or narrated_intro
        ):
            editorial_headers.add(normalized_header)

    for record in records:
        record_id = record["id"]
        text = record.get("chunk_text") or ""
        page = int(record.get("page") or 0)
        header = headers[record_id]
        normalized_header = normalize_text(header)
        progress = page / max(max_page, 1)

        if (
            _heading_matches(header, (r"^inhalt\b", r"^inhaltsverzeichnis\b"))
            and _looks_like_volume_contents(text, page, max_page)
        ):
            decision = LayerDecision(
                "table_of_contents", 0.98,
                f"{CLASSIFIER_VERSION}:running_header=contents", header,
            )
        elif _heading_matches(
            header,
            (r"^editorische hinweise\b", r"^hinweise zur edition\b", r"^zur edition\b"),
        ):
            decision = LayerDecision(
                "editorial_note", 0.98,
                f"{CLASSIFIER_VERSION}:running_header=editorial_note", header,
            )
        elif normalized_header in editorial_headers:
            decision = LayerDecision(
                "editorial_intro", 0.96,
                f"{CLASSIFIER_VERSION}:sequence_header+editorial_anchor", header,
            )
        elif progress >= 0.70 and _heading_matches(
            header,
            (
                r"^namenregister\b", r"^sachregister\b", r"^literaturregister\b",
                r"^personenregister\b", r"^quellenregister\b",
                r"^bibliographisches register\b", r"^register\b",
            ),
        ):
            decision = LayerDecision(
                "register", 0.97,
                f"{CLASSIFIER_VERSION}:late_running_header=register", header,
            )
        elif _heading_matches(header, (r"^verzeichnis der abbildungen\b",)):
            decision = LayerDecision(
                "illustration_list", 0.96,
                f"{CLASSIFIER_VERSION}:running_header=illustration_list", header,
            )
        elif _looks_like_front_matter(text, page, max_page):
            decision = LayerDecision(
                "front_matter", 0.90,
                f"{CLASSIFIER_VERSION}:front_position+publication_marker", header,
            )
        else:
            decision = LayerDecision(
                "textband_unclassified", 0.0,
                f"{CLASSIFIER_VERSION}:no_high_confidence_rule", header,
            )
        decisions[record_id] = decision

    return decisions