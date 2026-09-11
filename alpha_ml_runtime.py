import json, math
from pathlib import Path

MODEL_PATH = Path("data/alpha-ml-model-v10.json")
FEATURE_NAMES = [
    "confidence",
    "vote_agreement",
    "aligned_conviction",
    "opposed_conviction",
    "risk_scaled",
    "horizon_scaled",
    "risk_on",
    "risk_off_or_stress",
    "rates_up",
    "rates_down",
    "equity_like",
    "bond_like",
    "satellite",
]

def _clip(x, lo, hi):
    return max(lo, min(hi, x))

def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0

def load_model():
    try:
        obj=json.loads(MODEL_PATH.read_text(encoding="utf-8"))
        if obj.get("schema")!="ALPHA-ML-MODEL-V10" or obj.get("status")!="ACTIVE":
            return None
        if obj.get("feature_names")!=FEATURE_NAMES:
            return None
        if len(obj.get("coefficients",[]))!=len(FEATURE_NAMES):
            return None
        return obj
    except Exception:
        return None

def feature_map(ctx):
    direction=str(ctx.get("direction") or "").upper()
    votes=list(ctx.get("model_votes") or [])
    aligned=[v for v in votes if str(v.get("direction") or "").upper()==direction]
    opposed=[v for v in votes if str(v.get("direction") or "").upper() in ("LONG","SHORT") and str(v.get("direction") or "").upper()!=direction]
    agreement=(len(aligned)/len(votes)) if votes else 0.5
    aligned_conv=_mean([float(v.get("conviction") or 0)/100 for v in aligned])
    opposed_conv=_mean([float(v.get("conviction") or 0)/100 for v in opposed])
    cls=str(ctx.get("asset_class") or "").upper()
    risk_regime=str(ctx.get("risk_regime") or "").upper()
    rates=str(ctx.get("rates_regime") or "").upper()
    confidence=float(ctx.get("confidence_pct") or 50)/100
    risk_pct=float(ctx.get("risk_pct") or 0)
    horizon=float(ctx.get("horizon") or 5)
    return {
        "confidence":_clip(confidence,0,1),
        "vote_agreement":_clip(agreement,0,1),
        "aligned_conviction":_clip(aligned_conv,0,1),
        "opposed_conviction":_clip(opposed_conv,0,1),
        "risk_scaled":_clip(risk_pct/10,0,1),
        "horizon_scaled":_clip(horizon/40,0,1.5),
        "risk_on":1.0 if risk_regime=="RISK_ON" else 0.0,
        "risk_off_or_stress":1.0 if risk_regime in ("RISK_OFF","STRESS") else 0.0,
        "rates_up":1.0 if rates=="RATES_UP" else 0.0,
        "rates_down":1.0 if rates=="RATES_DOWN" else 0.0,
        "equity_like":1.0 if cls in ("EQUITY","ETF_EQUITY","INDEX_FUTURE") else 0.0,
        "bond_like":1.0 if cls in ("ETF_BOND_GOV","ETF_BOND_CREDIT","BTP","CASH_EQUIVALENT") else 0.0,
        "satellite":1.0 if cls in ("CRYPTO","FX","ETF_COMMODITY","INDEX_FUTURE") else 0.0,
    }

def predict(ctx, model=None):
    model=model or load_model()
    if not model:
        return {"state":"NO_ACTIVE_MODEL","probability":None,"confidence_adjustment":0.0,"stage":None}
    fm=feature_map(ctx)
    means=model["scaler"]["means"]
    scales=model["scaler"]["scales"]
    z=float(model.get("intercept",0.0))
    for i,name in enumerate(FEATURE_NAMES):
        x=(fm[name]-float(means[i]))/max(float(scales[i]),1e-9)
        z+=float(model["coefficients"][i])*x
    z=_clip(z,-12,12)
    p=1/(1+math.exp(-z))
    stage=str(model.get("stage") or "ACTIVE_GUARDED")
    # Guarded use: before maturity ML can veto/penalize but never positively boost.
    if stage=="MATURE":
        adj=_clip((p-.50)*20,-4.0,2.0)
    else:
        adj=_clip((p-.50)*20,-4.0,0.0)
    return {
        "state":"ACTIVE",
        "probability":round(p,6),
        "confidence_adjustment":round(adj,3),
        "stage":stage,
        "version":model.get("version"),
        "features":fm,
    }
