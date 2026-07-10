from __future__ import annotations

import re
from re import Match

from num2words import num2words

_UNIT_WORDS = {
    "km/u": "kilometer per uur",
    "km/h": "kilometer per uur",
    "km": "kilometer",
    "m/s": "meter per seconde",
    "ug/m3": "microgram per kubieke meter",
    "µg/m³": "microgram per kubieke meter",
    "cm": "centimeter",
    "mm": "millimeter",
    "%": "procent",
}


def _number(value: str) -> str:
    try:
        return str(num2words(int(value), lang="nl"))
    except ValueError:
        return value


def normalize_dutch_speech(text: str) -> str:
    """Format display text for speech without changing the underlying source value."""
    protected: list[str] = []

    def protect(match: Match[str]) -> str:
        protected.append(match.group(0))
        return f"__PROTECTED_{len(protected) - 1}__"

    output = re.sub(r"https?://\S+|\b[a-f0-9]{16,}\b", protect, text, flags=re.IGNORECASE)
    output = re.sub(
        r"\b([01]?\d|2[0-3]):([0-5]\d)\b",
        lambda match: f"{_number(match.group(1))} uur {_number(match.group(2))}",
        output,
    )
    output = re.sub(
        r"\b([A-Z])\s?(\d{1,3})\b",
        lambda match: f"de {match.group(1)} {_number(match.group(2))}",
        output,
    )
    output = re.sub(
        r"(?<!\w)(-?\d+),([0-9]+)",
        lambda match: (
            f"{_number(match.group(1))} komma {' '.join(_number(digit) for digit in match.group(2))}"
        ),
        output,
    )
    output = re.sub(r"(?<![\w_])-?\d+(?![\w_])", lambda match: _number(match.group(0)), output)
    output = output.replace("%", " procent")
    for unit, spoken in sorted(_UNIT_WORDS.items(), key=lambda item: len(item[0]), reverse=True):
        if unit == "%":
            continue
        output = re.sub(rf"(?i)(?<=\s){re.escape(unit)}(?=\s|[.,;:!?]|$)", spoken, output)
    output = re.sub(r"\bPM10\b", "P M tien", output, flags=re.IGNORECASE)
    output = re.sub(r"\bde de ([A-Z])\b", r"de \1", output)
    for index, value in enumerate(protected):
        output = output.replace(f"__PROTECTED_{index}__", value)
    return re.sub(r"\s+", " ", output).strip()
