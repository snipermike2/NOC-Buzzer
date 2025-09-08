# decisions.py
from typing import Mapping

SEVERITY_HIGH = {"high", "critical"}
CATEGORY_ALARM = "alarm"

def _norm(v: str) -> str:
    return (v or "").strip().lower()

def should_sound_buzzer(pred: Mapping[str, str]) -> bool:
    severity = _norm(pred.get("severity", ""))
    category = _norm(pred.get("category", ""))
    return (category == CATEGORY_ALARM) and (severity in SEVERITY_HIGH)
