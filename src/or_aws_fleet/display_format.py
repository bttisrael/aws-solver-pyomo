from __future__ import annotations

import math
from numbers import Real


def compact_whole_number(value: Real, unit: str = "") -> str:
    """Format dashboard metrics as whole numbers, using k at 1,000 and above."""
    number = float(value)
    if not math.isfinite(number):
        return "0"

    absolute = abs(number)
    if absolute >= 1_000:
        rounded = math.floor(absolute / 1_000 + 0.5)
        display = f"{'-' if number < 0 else ''}{rounded}k"
    else:
        rounded = math.floor(absolute + 0.5)
        display = f"{'-' if number < 0 else ''}{rounded}"
    return f"{display} {unit}".rstrip()
