from __future__ import annotations

import math
from statistics import mean


def haversine_m(lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> float:
    radius = 6_371_000.0
    phi_a, phi_b = math.radians(lat_a), math.radians(lat_b)
    d_phi = phi_b - phi_a
    d_lambda = math.radians(lon_b - lon_a)
    value = (
        math.sin(d_phi / 2) ** 2 + math.cos(phi_a) * math.cos(phi_b) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * radius * math.asin(math.sqrt(value))


def pearson(values_a: list[float], values_b: list[float]) -> float:
    count = min(len(values_a), len(values_b))
    if count < 3:
        return 0.0
    a, b = values_a[-count:], values_b[-count:]
    mean_a, mean_b = mean(a), mean(b)
    numerator = sum((left - mean_a) * (right - mean_b) for left, right in zip(a, b, strict=True))
    denominator = math.sqrt(
        sum((value - mean_a) ** 2 for value in a) * sum((value - mean_b) ** 2 for value in b)
    )
    return numerator / denominator if denominator else 0.0
