"""Shared Edge Core controls adapted to the finance domain."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

EDGE_CORE_VERSION = "1.0.0"
_SECRET_KEY = re.compile(r"api[_-]?key|token|secret|password|authorization", re.I)
_SECRET_VALUE = re.compile(r"(?:sk-|gh[oprsu]_|bearer\s+)[A-Za-z0-9._-]{12,}", re.I)

_ROUND_TRIP_COST_BPS = {
    "EQUITY": 12.0,
    "ETF_EQUITY": 10.0,
    "ETF_BOND_GOV": 9.0,
    "ETF_BOND_CREDIT": 11.0,
    "BTP": 14.0,
    "ETF_COMMODITY": 14.0,
    "FX": 8.0,
    "CASH_EQUIVALENT": 6.0,
    "CRYPTO": 35.0,
    "INDEX_FUTURE": 8.0,
}


def estimated_round_trip_cost_bps(asset_class: str | None) -> float:
    return float(_ROUND_TRIP_COST_BPS.get(str(asset_class or "").upper(), 15.0))


def cost_adjusted_return_pct(gross_return_pct: float, asset_class: str | None) -> tuple[float, float]:
    cost_bps = estimated_round_trip_cost_bps(asset_class)
    return round(float(gross_return_pct) - cost_bps / 100.0, 4), cost_bps


def assess_freshness(updated_at: str | None, warn_minutes: int = 45, fail_minutes: int = 90) -> dict:
    if not updated_at:
        return {"state": "NO_TIMESTAMP", "age_minutes": None, "operational": False}
    try:
        ts = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        age = max(0.0, (datetime.now(timezone.utc) - ts).total_seconds() / 60.0)
    except Exception:
        return {"state": "INVALID_TIMESTAMP", "age_minutes": None, "operational": False}
    return {"state": "STALE" if age > fail_minutes else "DEGRADED" if age > warn_minutes else "FRESH", "age_minutes": round(age, 1), "operational": age <= fail_minutes}


def assert_public_snapshot(snapshot: dict) -> bool:
    if snapshot.get("data_quality", {}).get("strict_no_fabrication") is not True:
        raise ValueError("strict_no_fabrication missing")
    if not isinstance(snapshot.get("signals"), list) or not isinstance(snapshot.get("watchlist"), list):
        raise ValueError("public collections invalid")
    raw = json.dumps(snapshot, ensure_ascii=False)
    if _SECRET_VALUE.search(raw):
        raise ValueError("secret-like value in public snapshot")
    if any(_SECRET_KEY.search(str(key)) for key in snapshot):
        raise ValueError("sensitive root key in public snapshot")
    return True


def automation_receipt(snapshot: dict, event: str = "unknown", schedule: str | None = None) -> dict:
    assert_public_snapshot(snapshot)
    summary = {"model_version": snapshot.get("model_version"), "updated_at": snapshot.get("updated_at"), "signals": len(snapshot["signals"]), "watchlist": len(snapshot["watchlist"])}
    digest = hashlib.sha256(json.dumps(summary, sort_keys=True).encode()).hexdigest()[:16]
    return {"ok": True, "app": "ALPHA_ENGINE", "edge_core_version": EDGE_CORE_VERSION, "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": event, "schedule": schedule, "artifact_digest": digest, "freshness": assess_freshness(snapshot.get("engine_updated_at") or snapshot.get("updated_at")), "summary": summary, "security": {"secrets_server_side": True, "public_artifact_scan": "PASS", "strict_no_fabrication": True}}
