#!/usr/bin/env python3
import json, math, os
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import yfinance as yf

OUT = Path("data/latest.json")
MEM = Path("data/memory.json")

UNIVERSE = {
    "ENERGY": ["XLE","XOP","OIH","XOM","CVX","COP","SLB","MPC","VLO"],
    "NATURAL_GAS": ["UNG","EQT","AR","RRC","CTRA"],
    "WATER": ["PHO","FIW","AWK","WTRG","XYL","PNR"],
    "UTILITIES": ["XLU","NEE","DUK","SO","AEP","SRE"],
}
CONTEXT = ["SPY","^VIX","CL=F","NG=F","DX-Y.NYB","^TNX"]

def safe_num(x, default=None):
    try:
        if pd.isna(x): return default
        return float(x)
    except: return default

def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).rolling(n).mean()
    dn = (-d.clip(upper=0)).rolling(n).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - (100/(1+rs))

def zlast(s, n=60):
    x = s.tail(n).dropna()
    if len(x) < 20: return 0.0
    sd = x.std(ddof=0)
    if not sd or pd.isna(sd): return 0.0
    return float((x.iloc[-1]-x.mean())/sd)

def get(data, ticker, field="Close"):
    if isinstance(data.columns, pd.MultiIndex):
        if (field,ticker) in data.columns: return data[(field,ticker)].dropna()
        if (ticker,field) in data.columns: return data[(ticker,field)].dropna()
    return pd.Series(dtype=float)

def load_memory():
    if MEM.exists():
        try: return json.loads(MEM.read_text())
        except: pass
    return {"predictions": [], "stats": {}}

def save_memory(mem):
    MEM.parent.mkdir(exist_ok=True)
    MEM.write_text(json.dumps(mem, indent=2), encoding="utf-8")

def resolve_memory(mem, data):
    for p in mem["predictions"]:
        if p.get("outcome") in ("HIT","MISS"): continue
        ticker = p["ticker"]
        s = get(data, ticker)
        if s.empty: continue
        dt = pd.Timestamp(p["date"])
        future = s[s.index > dt]
        h = int(p["horizon"])
        if len(future) < h: continue
        exit_price = float(future.iloc[h-1])
        raw = exit_price / float(p["entry"]) - 1
        pnl = raw if p["direction"]=="LONG" else -raw
        p["outcome"] = "HIT" if pnl > 0 else "MISS"
        p["return_pct"] = round(pnl*100,3)
        p["resolved"] = str(future.index[h-1].date())

    resolved = [p for p in mem["predictions"] if p.get("outcome") in ("HIT","MISS")]
    hits = sum(p["outcome"]=="HIT" for p in resolved)
    mem["stats"] = {
        "total": len(mem["predictions"]),
        "resolved": len(resolved),
        "hits": hits,
        "misses": len(resolved)-hits,
        "hit_rate": round(hits/len(resolved),4) if resolved else None,
    }
    return mem

def add_prediction(mem, sig):
    pid = f'{sig["date"]}|{sig["ticker"]}|{sig["direction"]}|{sig["horizon"]}'
    if any(p.get("id")==pid for p in mem["predictions"]): return
    mem["predictions"].append({
        "id": pid,
        "date": sig["date"],
        "ticker": sig["ticker"],
        "sector": sig["sector"],
        "direction": sig["direction"],
        "horizon": sig["horizon"],
        "probability": sig["confidence"],
        "entry": sig["price"],
        "outcome": "PENDING"
    })

def score_asset(ticker, sector, data):
    s = get(data,ticker)
    if len(s) < 80: return None
    v = get(data,ticker,"Volume")
    spy = get(data,"SPY")
    vix = get(data,"^VIX")

    ret5 = s.iloc[-1]/s.iloc[-6]-1
    ret20 = s.iloc[-1]/s.iloc[-21]-1
    ret60 = s.iloc[-1]/s.iloc[-61]-1
    spy20 = spy.iloc[-1]/spy.iloc[-21]-1 if len(spy)>=21 else 0
    rel20 = ret20-spy20
    r = safe_num(rsi(s).iloc[-1],50)
    vol = safe_num(s.pct_change().tail(20).std(ddof=0)*math.sqrt(252),0)
    vz = zlast(v) if not v.empty else 0
    trend = s.iloc[-1]/s.rolling(20).mean().iloc[-1]-1

    mom = np.clip(50 + 650*(0.45*ret5+0.35*ret20+0.20*trend),0,100)
    rel = np.clip(50 + 750*rel20,0,100)
    vol_score = np.clip(35 + vol*90,20,90)
    volume_score = np.clip(50 + 14*vz,0,100)
    rsi_long = 85 if 55<=r<=75 else 60 if 45<=r<55 or 75<r<=82 else 35
    rsi_short = 85 if 25<=r<=45 else 60 if 18<=r<25 or 45<r<=55 else 35

    long_score = .31*mom+.26*rel+.14*vol_score+.14*volume_score+.15*rsi_long
    short_score = .31*(100-mom)+.26*(100-rel)+.14*vol_score+.14*volume_score+.15*rsi_short

    vix_now = safe_num(vix.iloc[-1],20) if not vix.empty else 20
    if vix_now>=30:
        long_score -= 4
        short_score += 3

    if long_score >= short_score:
        direction, score = "LONG", long_score
    else:
        direction, score = "SHORT", short_score

    # Conservative mapping: score is not probability.
    confidence = 0.50 + max(0, score-50)/100
    confidence = float(np.clip(confidence,0.50,0.92))

    return {
        "ticker": ticker, "sector": sector, "direction": direction,
        "score": round(float(score),2), "confidence": round(confidence,4),
        "horizon": 5, "price": round(float(s.iloc[-1]),4),
        "ret5_pct": round(float(ret5*100),2),
        "ret20_pct": round(float(ret20*100),2),
        "rel20_pct": round(float(rel20*100),2),
        "rsi": round(float(r),1),
        "volatility_pct": round(float(vol*100),1),
        "volume_z": round(float(vz),2),
    }

def main():
    tickers = sorted(set(CONTEXT + [t for xs in UNIVERSE.values() for t in xs]))
    data = yf.download(tickers, period="2y", interval="1d", auto_adjust=True,
                       progress=False, group_by="column", threads=True)
    if data is None or data.empty:
        raise RuntimeError("No market data")

    mem = resolve_memory(load_memory(), data)

    rows=[]
    for sector, tickers in UNIVERSE.items():
        for t in tickers:
            x=score_asset(t,sector,data)
            if x: rows.append(x)

    rows.sort(key=lambda x:x["confidence"], reverse=True)
    signals=[x for x in rows if x["confidence"]>=0.72][:8]

    today = str(pd.Timestamp.utcnow().date())
    for s in signals:
        s["date"]=today
        add_prediction(mem,s)

    save_memory(mem)

    vix = get(data,"^VIX")
    spy = get(data,"SPY")
    result = {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "market": {
            "vix": round(float(vix.iloc[-1]),2) if not vix.empty else None,
            "spy_20d_pct": round(float((spy.iloc[-1]/spy.iloc[-21]-1)*100),2) if len(spy)>=21 else None,
        },
        "memory": mem["stats"],
        "signals": signals,
        "watchlist": rows[:15],
        "note": "Paper trading / research only. Confidence is model-calibrated heuristic, not a guarantee."
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))

if __name__=="__main__":
    main()
