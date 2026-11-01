"""Small shared helpers (time, hashing, rounding, JSON safety)."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, timezone
from typing import Any

from .config import GH_TZ


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return now_utc().isoformat(timespec="seconds")


def ghana_now() -> datetime:
    return datetime.now(GH_TZ)


def ghana_time_label() -> dict[str, str]:
    now = ghana_now()
    return {
        "iso": now.isoformat(timespec="seconds"),
        "time": now.strftime("%H:%M"),
        "date": now.strftime("%d %b %Y"),
        "weekday": now.strftime("%A"),
        "timezone": "Africa/Accra",
        "utc_offset": now.strftime("%z"),
    }


def parse_date(value: str | date | None) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def stable_hash(*parts: Any) -> str:
    """Stable short hash used for cache keys."""
    raw = json.dumps(parts, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def geometry_hash(geometry: dict) -> str:
    coordinates = geometry.get("coordinates")
    return stable_hash(geometry.get("type"), coordinates)


def round_sig(value: float | None, digits: int = 3) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, digits)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def json_safe(value: Any) -> Any:
    """Recursively replace NaN/Infinity (invalid JSON) and numpy scalars."""
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    # numpy / pandas scalars expose .item()
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return json_safe(item())
        except (ValueError, TypeError):
            return None
    try:
        if value != value:  # NaN
            return None
    except (TypeError, ValueError):
        pass
    return str(value)


def human_bytes(num: int | float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num) < 1024.0:
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} TB"
