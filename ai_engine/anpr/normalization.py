"""
Conservative Indian Vehicle License Plate Normalization and Format Validation.

Handles:
- Whitespace and punctuation stripping
- Canonical uppercase transformation
- Defensible context-aware OCR character disambiguation (e.g. O->0, I->1 in numeric slots)
- Generalized Indian RTO format validation (e.g. GJ05AB1234, DL01A1234)
- Bharat Series (BH) validation (e.g. 22BH1234AA)
- Strict preservation of raw OCR output
"""

from __future__ import annotations

import re
from typing import Final

# Comprehensive set of recognized 2-letter State & Union Territory codes in India
INDIAN_STATE_CODES: Final[frozenset[str]] = frozenset({
    "AN", "AP", "AR", "AS", "BR", "CH", "CG", "DD", "DL", "DN",
    "GA", "GJ", "HR", "HP", "JH", "JK", "KA", "KL", "LA", "LD",
    "MP", "MH", "MN", "ML", "MZ", "NL", "OD", "PB", "PY", "RJ",
    "SK", "TN", "TR", "TS", "UK", "UP", "WB"
})

# Standard Indian RTO format: [State 2L][District 1-2D][Series 1-3L][Number 1-4D]
STANDARD_PLATE_REGEX: Final[re.Pattern] = re.compile(
    r"^([A-Z]{2})([0-9]{1,2})([A-Z]{1,3})([0-9]{1,4})$"
)

# Bharat Series format: [Year 2D] BH [Number 4D] [Series 1-2L]
BH_PLATE_REGEX: Final[re.Pattern] = re.compile(
    r"^([0-9]{2})(BH)([0-9]{4})([A-Z]{1,2})$"
)

# Defensible character substitutions for numeric positions
_NUMERIC_SUBSTITUTIONS: Final[dict[str, str]] = {
    "O": "0",
    "I": "1",
    "L": "1",
    "Z": "2",
    "S": "5",
    "B": "8",
}

# Defensible character substitutions for state-code positions (e.g., 0D -> OD for Odisha, 6J -> GJ for Gujarat)
_STATE_SUBSTITUTIONS: Final[dict[str, str]] = {
    "0": "O",
    "1": "I",
    "6": "G",
}


def sanitize_plate_string(raw_text: str) -> str:
    """
    Perform initial sanitization: uppercase, strip whitespace and punctuation.
    """
    if not raw_text:
        return ""
    # Strip whitespace, hyphens, colons, periods, underscores, slashes, quotes, brackets
    return re.sub(r"[\s\-:._,/\\|'\"\[\](){}]+", "", raw_text.strip().upper())


def disambiguate_numeric_slots(text: str) -> str:
    """
    Apply conservative character substitutions in positions that are structurally numeric.

    Pattern: [State 2L][RTO 1-2D][Series 1-3L][Number 1-4D]
    """
    if len(text) < 6:
        return text

    chars = list(text)

    # Check if first 2 characters resemble a state code
    # Check state-code disambiguation (e.g. 0D -> OD)
    state_candidate = chars[0] + chars[1]
    if state_candidate not in INDIAN_STATE_CODES:
        sub_c0 = _STATE_SUBSTITUTIONS.get(chars[0], chars[0])
        sub_c1 = _STATE_SUBSTITUTIONS.get(chars[1], chars[1])
        if (sub_c0 + sub_c1) in INDIAN_STATE_CODES:
            chars[0] = sub_c0
            chars[1] = sub_c1
            state_candidate = sub_c0 + sub_c1

    if state_candidate in INDIAN_STATE_CODES:
        # Positions 2 and 3 should be RTO district digits (e.g., '05' in 'GJ05')
        for idx in (2, 3):
            if idx < len(chars) and chars[idx] in _NUMERIC_SUBSTITUTIONS:
                chars[idx] = _NUMERIC_SUBSTITUTIONS[chars[idx]]

        # Trailing characters: the numeric sequence (1-4 digits) at the end of the plate
        # Scan backward from the end while characters are digits or numeric confusables
        rev_indices = []
        for i in range(len(chars) - 1, 3, -1):
            if chars[i].isdigit() or chars[i] in _NUMERIC_SUBSTITUTIONS:
                rev_indices.append(i)
                if len(rev_indices) == 4:
                    break
            else:
                break

        for idx in rev_indices:
            if chars[idx] in _NUMERIC_SUBSTITUTIONS:
                chars[idx] = _NUMERIC_SUBSTITUTIONS[chars[idx]]

    # Check BH-Series candidate (e.g. 22BH1234AA)
    elif len(chars) >= 9 and "".join(chars[2:4]) == "BH":
        # First 2 positions are year digits
        for idx in (0, 1):
            if chars[idx] in _NUMERIC_SUBSTITUTIONS:
                chars[idx] = _NUMERIC_SUBSTITUTIONS[chars[idx]]
        # Next 4 positions after BH (indices 4, 5, 6, 7) are number digits
        for idx in range(4, min(8, len(chars))):
            if chars[idx] in _NUMERIC_SUBSTITUTIONS:
                chars[idx] = _NUMERIC_SUBSTITUTIONS[chars[idx]]

    return "".join(chars)


def validate_indian_plate_format(candidate: str) -> bool:
    """
    Check whether a normalized string matches standard Indian RTO or BH registration syntax.
    """
    if not candidate:
        return False

    # Standard format: [State 2L][District 1-2D][Series 1-3L][Number 1-4D]
    match = STANDARD_PLATE_REGEX.match(candidate)
    if match:
        state = match.group(1)
        # Check against recognized Indian State/UT codes
        if state in INDIAN_STATE_CODES:
            return True

    # BH format: [Year 2D][BH][Number 4D][Series 1-2L]
    if BH_PLATE_REGEX.match(candidate):
        return True

    return False


def normalize_plate(raw_text: str) -> tuple[str, bool]:
    """
    Normalize an OCR string and determine whether it represents a valid Indian plate.

    Parameters:
        raw_text: Literal OCR output.

    Returns:
        tuple of (normalized_plate: str, is_valid_format: bool)
    """
    sanitized = sanitize_plate_string(raw_text)
    if not sanitized:
        return "", False

    # Check direct match before disambiguation
    if validate_indian_plate_format(sanitized):
        return sanitized, True

    # Apply conservative context-aware disambiguation
    disambiguated = disambiguate_numeric_slots(sanitized)
    is_valid = validate_indian_plate_format(disambiguated)

    return disambiguated, is_valid
