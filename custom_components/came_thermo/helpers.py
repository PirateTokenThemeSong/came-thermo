"""Small helpers shared by the CAME thermostat platforms."""
from __future__ import annotations

from typing import Any


def as_float(value: Any, digits: int = 1) -> float | None:
    """Return ``value`` as a rounded float, or ``None`` when unusable."""
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def normalize_levels(cfg: dict[str, Any]) -> dict[str, Any]:
    """Keep T1 <= T2 <= T3 so the thermostat firmware accepts the level set.

    The device silently ignores a payload whose comfort levels are out of order,
    so neighbouring levels are pushed instead of dropping the write.
    """
    if cfg.get("T1") is not None and cfg.get("T2") is not None and cfg["T1"] > cfg["T2"]:
        cfg["T2"] = cfg["T1"]
    if cfg.get("T2") is not None and cfg.get("T3") is not None and cfg["T3"] < cfg["T2"]:
        cfg["T2"] = cfg["T3"]
    return cfg
