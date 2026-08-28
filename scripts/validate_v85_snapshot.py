from __future__ import annotations

import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LATEST = ROOT / "data" / "latest.json"
SERIES = ROOT / "data" / "market-series.json"


def fail(message: str):
    raise SystemExit(message)


def finite_or_none(value):
    return value is None or (isinstance(value, (int, float)) and math.isfinite(value))


def main():
    if not LATEST.exists():
        fail("data/latest.json missing")
    d = json.loads(LATEST.read_text(encoding="utf-8"))

    if d.get("schema_version") != "8.5":
        fail(f"unexpected schema_version: {d.get('schema_version')}")
    if not str(d.get("model_version", "")).startswith("8.5."):
        fail("V8.5 model_version missing")
    if d.get("data_quality", {}).get("strict_no_fabrication") is not True:
        fail("strict_no_fabrication gate missing")
    if d.get("edge_core", {}).get("version") != "1.0.0":
        fail("Edge Core metadata missing")
    if d.get("edge_core", {}).get("domain_profile") != "FINANCE":
        fail("FINANCE Edge Core profile missing")

    engine_ts = d.get("engine_updated_at") or d.get("updated_at")
    if not engine_ts:
        fail("engine_updated_at/updated_at missing")
    ts = datetime.fromisoformat(engine_ts.replace("Z", "+00:00"))
    age = (datetime.now(timezone.utc) - ts).total_seconds()
    if age > 600:
        fail(f"latest.json too old: {age:.0f}s")

    signals = d.get("signals")
    watch = d.get("watchlist")
    if not isinstance(signals, list):
        fail("signals missing/not list")
    if not isinstance(watch, list):
        fail("watchlist missing/not list")
    if len({x.get("ticker") for x in signals}) != len(signals):
        fail("duplicate ticker in signals")

    def validate_row(x, where):
        req = ("ticker", "asset_class", "cluster", "direction", "score", "confidence_pct", "data_quality_score", "provenance")
        miss = [k for k in req if x.get(k) is None]
        if miss:
            fail(f"{where} {x.get('ticker')} missing {miss}")
        if x.get("direction") not in ("LONG", "SHORT"):
            fail(f"bad direction {x.get('ticker')}")
        for key in ("score", "confidence_pct", "data_quality_score", "price", "entry_price", "stop_price", "target1_price", "target2_price"):
            if not finite_or_none(x.get(key)):
                fail(f"non-finite {key} for {x.get('ticker')}")

        p0, st, t1, t2 = x.get("entry_price"), x.get("stop_price"), x.get("target1_price"), x.get("target2_price")
        if p0 is not None and p0 <= 0:
            fail(f"non-positive price {x.get('ticker')}")
        if None not in (p0, st, t1):
            if x["direction"] == "LONG" and not (st < p0 < t1):
                fail(f"LONG geometry invalid {x.get('ticker')}")
            if x["direction"] == "SHORT" and not (t1 < p0 < st):
                fail(f"SHORT geometry invalid {x.get('ticker')}")
            if t2 is not None:
                if x["direction"] == "LONG" and t2 < t1:
                    fail(f"LONG T2 invalid {x.get('ticker')}")
                if x["direction"] == "SHORT" and t2 > t1:
                    fail(f"SHORT T2 invalid {x.get('ticker')}")

        fp = x.get("forecast_probability")
        if fp is not None:
            ps = x.get("probability_state") or {}
            if not isinstance(fp, (int, float)) or not math.isfinite(fp) or not 0 <= fp <= 1:
                fail(f"invalid forecast_probability {x.get('ticker')}")
            if ps.get("state") != "EMPIRICALLY_CALIBRATED" or int(ps.get("n", 0) or 0) < 30:
                fail(f"probability published without enough empirical sample {x.get('ticker')}")

        hp = x.get("horizon_profile") or {}
        horizon = x.get("horizon")
        if not isinstance(horizon, int) or horizon < 1 or int(hp.get("selected", -1) or -1) != horizon:
            fail(f"adaptive horizon/profile invalid {x.get('ticker')}")
        if x.get("horizon_policy_version") != "AH-1.0":
            fail(f"horizon policy mismatch {x.get('ticker')}")
        if (x.get("provenance") or {}).get("horizon") not in (
            "MODEL_DERIVED_EMPIRICAL_PRE_FORECAST",
            "RULE_BASED_ASSET_CLASS_SETUP_FALLBACK",
        ):
            fail(f"horizon provenance invalid {x.get('ticker')}")
        if d.get("data_source", {}).get("official_bond_terms_feed") is not True and x.get("yield_to_maturity_pct") is not None:
            fail(f"YTM must remain null without official bond terms feed: {x.get('ticker')}")
        for field in ("price", "entry_price", "stop_price", "target1_price"):
            if field not in (x.get("provenance") or {}):
                fail(f"provenance missing {field} for {x.get('ticker')}")

    for row in signals:
        validate_row(row, "signal")
    for row in watch:
        validate_row(row, "watch")

    div = d.get("diversification", {})
    if signals and not isinstance(div.get("by_asset_class"), dict):
        fail("diversification metadata missing")
    cc = Counter(x.get("asset_class") for x in signals)
    kc = Counter(x.get("cluster") for x in signals)
    if any(v > 2 for v in cc.values()):
        fail(f"asset-class concentration cap breached: {dict(cc)}")
    if any(v > 2 for v in kc.values()):
        fail(f"cluster concentration cap breached: {dict(kc)}")
    if sum(x.get("role") == "SATELLITE" for x in signals) > 1:
        fail("satellite concentration cap breached")

    learning = d.get("model_learning", {})
    if learning.get("no_lookahead") is not True or learning.get("learning_lineage") != "ALPHA_V85_COST_AWARE_1":
        fail("learning governance mismatch")
    if float(learning.get("max_positive_adjustment_points", 99)) > 1.0:
        fail("positive learning cap too high")
    if float(learning.get("max_negative_adjustment_points", 0)) > -4.0:
        fail("negative learning guard too weak")

    gov = d.get("model_governance", {})
    horizon_policy = gov.get("horizon_policy", {})
    if gov.get("outcome_target") != "ADAPTIVE_HORIZON_DIRECTIONAL_RETURN":
        fail("adaptive outcome target missing")
    if horizon_policy.get("version") != "AH-1.0" or horizon_policy.get("no_lookahead") is not True:
        fail("adaptive horizon governance missing")

    ext = d.get("external_models", {})
    if ext.get("positive_boost_enabled") is not False or ext.get("independent_verification_claimed") is not False:
        fail("external model governance mismatch")
    if int(ext.get("min_resolved_before_downside_guard", 20) or 0) < 20:
        fail("external track-record gate too weak")

    cov = d.get("coverage", {})
    if "DISABLED" not in str(cov.get("btp_direct", "")):
        fail("direct BTP feed guard missing")
    expected_btp = {"IITB.MI", "IITA.MI", "BTP10.MI", "BT27.MI"}
    if not expected_btp.issubset(set(cov.get("btp_proxies_verified", []))):
        fail("verified BTP proxy registry incomplete")
    if d.get("execution_assumptions", {}).get("cost_model") != "CONSERVATIVE_ASSET_CLASS_ROUND_TRIP_BPS":
        fail("cost model missing")

    if not SERIES.exists():
        fail("data/market-series.json missing")
    s = json.loads(SERIES.read_text(encoding="utf-8"))
    if s.get("schema_version") != "1.0" or s.get("strict_no_fabrication") is not True:
        fail("market-series contract invalid")
    if not isinstance(s.get("symbols"), dict):
        fail("market-series symbols missing")
    for ticker, row in s.get("symbols", {}).items():
        for bucket in ("daily", "intraday"):
            points = row.get(bucket) or []
            if not isinstance(points, list):
                fail(f"{ticker} {bucket} not list")
            for point in points:
                if not isinstance(point, list) or len(point) != 2 or not isinstance(point[1], (int, float)) or not math.isfinite(point[1]):
                    fail(f"invalid chart point {ticker}/{bucket}")

    print("Alpha V8.5 + V9 chart feed validation OK")
    print("engine_updated_at:", engine_ts)
    print("market_data_at:", d.get("market_data_at"))
    print("signals:", len(signals), "watchlist:", len(watch))
    print("chart_symbols:", s.get("available_symbols"), "status:", s.get("status"))


if __name__ == "__main__":
    main()
