from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine.py"
MARKER = "ALPHA_MATH_LAYER_V1"
OLD_VERSION = 'MODEL_VERSION = "8.6.0-episode-ledger-cost-aware"'
NEW_VERSION = 'MODEL_VERSION = "8.6.1-math-risk-layer"'

HELPERS = r'''

# ALPHA_MATH_LAYER_V1
# Conservative mathematical overlay. It only penalizes confidence; it never
# creates positive alpha from a formula and never promotes confidence by itself.
def mathematical_confidence_guard(direction, rsi_value, annualized_vol, ret20, horizon):
    adj = 0.0
    reasons = []
    h = max(1, int(horizon or 1))

    # RSI extension: penalize a LONG near/inside overbought and a SHORT near/inside oversold.
    if rsi_value is not None:
        if direction == "LONG" and rsi_value >= 68:
            p = min(6.0, max(0.0, (rsi_value - 68.0) * 0.45))
            adj -= p
            reasons.append(f"RSI extension penalty -{p:.2f}")
        elif direction == "SHORT" and rsi_value <= 32:
            p = min(6.0, max(0.0, (32.0 - rsi_value) * 0.45))
            adj -= p
            reasons.append(f"RSI extension penalty -{p:.2f}")

    horizon_sigma = None
    extension_z = None
    if annualized_vol is not None and annualized_vol > 0:
        horizon_sigma = annualized_vol * math.sqrt(h / 252.0)
        if horizon_sigma >= 0.08:
            p = min(5.0, (horizon_sigma - 0.08) * 45.0 + 1.0)
            adj -= p
            reasons.append(f"horizon volatility penalty -{p:.2f}")

        # Normalize the observed 20-session move by its own volatility scale.
        if ret20 is not None:
            sigma20 = annualized_vol * math.sqrt(20.0 / 252.0)
            if sigma20 > 0:
                extension_z = abs(ret20) / sigma20
                same_side = (direction == "LONG" and ret20 > 0) or (direction == "SHORT" and ret20 < 0)
                if same_side and extension_z >= 1.25:
                    p = min(5.0, (extension_z - 1.25) * 2.0)
                    adj -= p
                    reasons.append(f"price extension z penalty -{p:.2f}")

    return round(max(-12.0, min(0.0, adj)), 3), {
        "version": "ALPHA_MATH_LAYER_V1",
        "penalty_only": True,
        "horizon_sigma_pct": round(horizon_sigma * 100, 3) if horizon_sigma is not None else None,
        "extension_z": round(extension_z, 3) if extension_z is not None else None,
        "reasons": reasons,
    }


def mathematical_trade_metrics(direction, annualized_vol, ret20, horizon, risk_pct, reward1_pct, reward2_pct):
    def cdf(z):
        return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))

    out = {
        "version": "ALPHA_MATH_LAYER_V1",
        "distribution_assumption": "GAUSSIAN_TERMINAL_RETURN_PROXY",
        "barrier_probability_claimed": False,
        "break_even_probability_t1": None,
        "break_even_probability_t2": None,
        "terminal_directional_probability_proxy": None,
        "terminal_target1_probability_proxy": None,
        "terminal_target2_probability_proxy": None,
        "terminal_stop_probability_proxy": None,
        "horizon_sigma_pct": None,
        "directional_drift_proxy_pct": None,
    }

    if risk_pct is not None and reward1_pct is not None and risk_pct + reward1_pct > 0:
        out["break_even_probability_t1"] = round(risk_pct / (risk_pct + reward1_pct), 4)
    if risk_pct is not None and reward2_pct is not None and risk_pct + reward2_pct > 0:
        out["break_even_probability_t2"] = round(risk_pct / (risk_pct + reward2_pct), 4)

    if annualized_vol is None or annualized_vol <= 0:
        return out

    h = max(1, int(horizon or 1))
    sigma = annualized_vol * math.sqrt(h / 252.0)
    if sigma <= 0:
        return out

    raw_mu = (ret20 / 20.0) * h if ret20 is not None else 0.0
    mu = raw_mu if direction == "LONG" else -raw_mu
    out["horizon_sigma_pct"] = round(sigma * 100, 3)
    out["directional_drift_proxy_pct"] = round(mu * 100, 3)
    out["terminal_directional_probability_proxy"] = round(1.0 - cdf((0.0 - mu) / sigma), 4)

    if reward1_pct is not None:
        r1 = reward1_pct / 100.0
        out["terminal_target1_probability_proxy"] = round(1.0 - cdf((r1 - mu) / sigma), 4)
    if reward2_pct is not None:
        r2 = reward2_pct / 100.0
        out["terminal_target2_probability_proxy"] = round(1.0 - cdf((r2 - mu) / sigma), 4)
    if risk_pct is not None:
        r = risk_pct / 100.0
        out["terminal_stop_probability_proxy"] = round(cdf((-r - mu) / sigma), 4)
    return out
'''


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one source marker, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    text = ENGINE.read_text(encoding="utf-8")

    if NEW_VERSION not in text:
        text = replace_once(text, OLD_VERSION, NEW_VERSION, "model version")

    if MARKER not in text:
        text = replace_once(text, "\ndef score_asset(ticker, data, mem, regimes, external_votes_by_ticker):", HELPERS + "\ndef score_asset(ticker, data, mem, regimes, external_votes_by_ticker):", "helper insertion")

    old_conf = '''    learn_adj, learn_meta = learning_adjustment(mem, asset_class, direction, horizon)\n    final_conf = clip(base_conf + agreement_adj + vol_adj + macro_adj + learn_adj, 50, 92)'''
    new_conf = '''    learn_adj, learn_meta = learning_adjustment(mem, asset_class, direction, horizon)\n    math_adj, math_guard = mathematical_confidence_guard(direction, rv, vol, ret20, horizon)\n    final_conf = clip(base_conf + agreement_adj + vol_adj + macro_adj + learn_adj + math_adj, 50, 92)'''
    if "math_adj, math_guard = mathematical_confidence_guard" not in text:
        text = replace_once(text, old_conf, new_conf, "confidence guard")

    old_risk = '''        risk_pct = abs(price - stop) / price * 100\n\n    if vol_z is None:'''
    new_risk = '''        risk_pct = abs(price - stop) / price * 100\n\n    reward1_pct = abs(target1 - price) / price * 100 if target1 is not None else None\n    reward2_pct = abs(target2 - price) / price * 100 if target2 is not None else None\n    math_metrics = mathematical_trade_metrics(direction, vol, ret20, horizon, risk_pct, reward1_pct, reward2_pct)\n\n    if vol_z is None:'''
    if "math_metrics = mathematical_trade_metrics" not in text:
        text = replace_once(text, old_risk, new_risk, "trade metrics")

    old_reason = '''    if learn_adj < 0:\n        reasons.append("Memoria errori: penalità empirica attiva")'''
    new_reason = '''    if learn_adj < 0:\n        reasons.append("Memoria errori: penalità empirica attiva")\n    if math_adj < 0:\n        reasons.append(f"Math guard: penalità prudenziale {math_adj:.1f} pt")'''
    if "Math guard: penalità prudenziale" not in text:
        text = replace_once(text, old_reason, new_reason, "math reason")

    old_fields = '''        "reward1_pct": round(abs(target1 - price) / price * 100, 3) if target1 is not None else None,\n        "reward2_pct": round(abs(target2 - price) / price * 100, 3) if target2 is not None else None,'''
    new_fields = '''        "reward1_pct": round(reward1_pct, 3) if reward1_pct is not None else None,\n        "reward2_pct": round(reward2_pct, 3) if reward2_pct is not None else None,\n        "break_even_probability_t1": math_metrics.get("break_even_probability_t1"),\n        "break_even_probability_t2": math_metrics.get("break_even_probability_t2"),\n        "terminal_directional_probability_proxy": math_metrics.get("terminal_directional_probability_proxy"),\n        "terminal_target1_probability_proxy": math_metrics.get("terminal_target1_probability_proxy"),\n        "terminal_target2_probability_proxy": math_metrics.get("terminal_target2_probability_proxy"),\n        "terminal_stop_probability_proxy": math_metrics.get("terminal_stop_probability_proxy"),\n        "math_confidence_adjustment": math_adj,\n        "math_guard": math_guard,\n        "math_metrics": math_metrics,'''
    if '"math_metrics": math_metrics' not in text:
        text = replace_once(text, old_fields, new_fields, "output metrics")

    old_prov = '''            "learning_adjustment": "RESOLVED_MEMORY_ONLY" if learn_adj != 0 else "NO_ADJUSTMENT",'''
    new_prov = '''            "learning_adjustment": "RESOLVED_MEMORY_ONLY" if learn_adj != 0 else "NO_ADJUSTMENT",\n            "math_confidence_adjustment": "MODEL_DERIVED_PENALTY_ONLY_RSI_VOLATILITY_EXTENSION",\n            "math_metrics": "MODEL_DERIVED_GAUSSIAN_TERMINAL_PROXY_AND_BREAK_EVEN_ARITHMETIC",'''
    if '"math_metrics": "MODEL_DERIVED_GAUSSIAN_TERMINAL_PROXY' not in text:
        text = replace_once(text, old_prov, new_prov, "provenance")

    ENGINE.write_text(text, encoding="utf-8")
    print("Alpha mathematical risk layer applied/idempotent")


if __name__ == "__main__":
    main()
