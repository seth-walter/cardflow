from typing import Any


def clean_value(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    if isinstance(value, str):
        value = value.strip()
        return value if value else fallback
    return str(value)
