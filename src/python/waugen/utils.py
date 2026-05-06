# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# See LICENSE at the repository root.

from __future__ import annotations

import re


def macro_name(name: str) -> str:
    """Convert a free-form label to an uppercase Verilog-macro-friendly symbol."""
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_")
    if not cleaned:
        return "UNNAMED"
    if cleaned[0].isdigit():
        cleaned = f"N_{cleaned}"
    return cleaned.upper()


def validate_range(value: int, *, minimum: int, name: str) -> int:
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
    return value


def clamp(value: int, lower: int, upper: int) -> int:
    return max(lower, min(upper, value))
