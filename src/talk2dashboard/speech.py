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
    "hPa": "hectopascal",
    "m³/s": "kubieke meter per seconde",
    "µg/m3": "microgram per kubieke meter",
    "°C": "graden Celsius",
}

_ABBREVIATIONS = {
    "KNMI": "K N M I",
    "RWS": "Rijkswaterstaat",
    "NDW": "N D W",
    "P2000": "P tweeduizend",
    "NOS": "N O S",
    "NS": "N S",
    "LKI": "L K I",
    "NO2": "N O twee",
    "O3": "O drie",
}

_MONTHS = {
    1: "januari",
    2: "februari",
    3: "maart",
    4: "april",
    5: "mei",
    6: "juni",
    7: "juli",
    8: "augustus",
    9: "september",
    10: "oktober",
    11: "november",
    12: "december",
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
        r"\b([0-3]?\d)[-/.]([01]?\d)[-/.](\d{4})\b",
        lambda match: (
            f"{_number(match.group(1))} "
            f"{_MONTHS.get(int(match.group(2)), match.group(2))} {_number(match.group(3))}"
        ),
        output,
    )
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
    for abbreviation, spoken in _ABBREVIATIONS.items():
        output = re.sub(rf"\b{re.escape(abbreviation)}\b", spoken, output, flags=re.IGNORECASE)
    output = re.sub(r"\bde de ([A-Z])\b", r"de \1", output)
    for index, value in enumerate(protected):
        output = output.replace(f"__PROTECTED_{index}__", value)
    return re.sub(r"\s+", " ", output).strip()
