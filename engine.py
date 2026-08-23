#!/usr/bin/env python3
"""
Alpha Engine Pro Max
- short-term equity/ETF directional scanner
- paper memory
- instrument recommendation layer
- cost/benefit filters
- no exact derivative ISIN without a live derivative feed
"""
import json, math
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

# Cost assumptions are intentionally configurable and conservative.
BROKER = {
    "fixed_cost_per_order_eur": 1.0,
    "estimated_roundtrip_orders": 2,
    "default_derivative_spread_pct": 0.9,
    "default_equity_spread_pct": 0.15,
    "min_expected_net_edge_eur": 1.0,
}

def get(d,t,f="Close"):
    if isinstance(d.columns,pd.MultiIndex):
        if (f,t) in d.columns: return d[(f,t)].dropna()
        if (t,f) in d.columns: return d[(t,f)].dropna()
    return pd.Series(dtype=float)

def rsi(s,n=14):
    d=s.diff()
    up=d.clip(lower=0).rolling(n).mean()
    dn=(-d.clip(upper=0)).rolling(n).mean()
    rs=up/dn.replace(0,np.nan)
    return 100-100/(1+rs)

def zlast(s,n=60):
    x=s.tail(n).dropna()
    if len(x)<20:return 0.0
    sd=x.std(ddof=0)
    return 0.0 if not sd or pd.isna(sd) else float((x.iloc[-1]-x.mean())/sd)

def load_memory():
    if MEM.exists():
        try:return json.loads(MEM.read_text())
        except: pass
    return {"predictions":[],"stats":{}}

def resolve_memory(mem,data):
    for p in mem["predictions"]:
        if p.get("outcome") in ("HIT","MISS"): continue
        s=get(data,p["ticker"])
        if s.empty: continue
        future=s[s.index>pd.Timestamp(p["date"])]
        h=int(p["horizon"])
        if len(future)<h: continue
        exitp=float(future.iloc[h-1])
        raw=exitp/float(p["entry"])-1
        pnl=raw if p["direction"]=="LONG" else -raw
        p["outcome"]="HIT" if pnl>0 else "MISS"
        p["return_pct"]=round(pnl*100,3)
        p["resolved_date"]=str(future.index[h-1].date())
    resolved=[p for p in mem["predictions"] if p.get("outcome") in ("HIT","MISS")]
    hits=sum(p["outcome"]=="HIT" for p in resolved)
    mem["stats"]={
        "total":len(mem["predictions"]),
        "resolved":len(resolved),
        "hits":hits,
        "misses":len(resolved)-hits,
        "hit_rate":round(hits/len(resolved),4) if resolved else None,
        "sample_quality":"LOW" if len(resolved)<30 else "MEDIUM" if len(resolved)<100 else "HIGH"
    }
    return mem

def reasons(direction,ret5,ret20,rel20,r,vz,sector):
    a=[]
    if direction=="LONG":
        if ret20>0.08:a.append("trend di breve periodo forte")
        if rel20>0.04:a.append("forza relativa positiva")
        if 55<=r<=75:a.append("momentum favorevole senza eccesso estremo")
    else:
        if ret20<-0.08:a.append("trend di breve periodo debole")
        if rel20<-0.04:a.append("forza relativa negativa")
        if 25<=r<=45:a.append("momentum ribassista ancora attivo")
    if vz>1.2:a.append("volumi sopra la norma")
    if sector in ("ENERGY","NATURAL_GAS"):a.append("settore molto sensibile a commodity e geopolitica")
    return (a or ["più indicatori concordano sulla stessa direzione"])[:4]

def risk_label(vol,score,r,vix):
    pts=0
    if vol>0.50:pts+=2
    elif vol>0.30:pts+=1
    if score<78:pts+=1
    if r>82 or r<18:pts+=1
    if vix>=30:pts+=1
    return "MOLTO ALTO" if pts>=3 else "ALTO" if pts>=1 else "MEDIO"

def leverage_policy(score,vol,vix,risk_level):
    # capped, deliberately moderate leverage policy
    lev=2.0
    if score>=84 and vol<0.40 and vix<28: lev=3.0
    if score>=88 and vol<0.30 and vix<24: lev=3.5
    if risk_level=="MOLTO ALTO": lev=min(lev,2.0)
    return round(lev,1)

def derivative_plan(direction,score,vol,vix,risk_level,stop_pct,reward1_pct,reward2_pct):
    lev=leverage_policy(score,vol,vix,risk_level)
    # KO buffer beyond underlying stop; more volatile markets require more room.
    ko_buffer=max(stop_pct*1.8, min(15.0, 4.0 + vol*10))
    if risk_level=="MOLTO ALTO":
        ko_buffer=max(ko_buffer,10.0)
    # instrument policy
    if direction=="LONG":
        if score<76:
            inst="NO TRADE"
            why="Segnale troppo debole per giustificare leva e costi."
        elif risk_level=="MOLTO ALTO":
            inst="AZIONE / ETF"
            why="Meglio evitare leva con volatilità molto elevata."
        elif score>=82:
            inst="KNOCK-OUT LONG"
            why="Coerente con orizzonte breve e direzione rialzista; leva moderata."
        else:
            inst="AZIONE / ETF"
            why="Il vantaggio non giustifica ancora un derivato."
    else:
        if score<76:
            inst="NO TRADE"
            why="Segnale ribassista troppo debole per sostenere costi e rischio."
        elif risk_level=="MOLTO ALTO":
            inst="NO TRADE / SHORT SOLO ESPERTO"
            why="Rischio di whipsaw elevato: meglio evitare KO troppo vicino."
        else:
            inst="KNOCK-OUT SHORT"
            why="Metodo più diretto per esprimere un ribasso a breve, con leva moderata."

    return {
        "instrument":inst,
        "preferred_leverage":lev,
        "ko_distance_min_pct":round(ko_buffer,1),
        "holding_days_max":5,
        "why":why,
        "avoid_if":[
            "spread elevato",
            "barriera KO più vicina dello stop tecnico",
            "costo totale troppo alto rispetto al target",
            "liquidità scarsa",
            "emittente/prodotto non disponibile o non comprensibile"
        ],
        "target_underlying_reward1_pct":reward1_pct,
        "target_underlying_reward2_pct":reward2_pct,
    }

def score_asset(ticker,sector,d):
    s=get(d,ticker); v=get(d,ticker,"Volume"); spy=get(d,"SPY"); vixs=get(d,"^VIX")
    if len(s)<80:return None

    ret5=s.iloc[-1]/s.iloc[-6]-1
    ret20=s.iloc[-1]/s.iloc[-21]-1
    spy20=spy.iloc[-1]/spy.iloc[-21]-1 if len(spy)>=21 else 0
    rel20=ret20-spy20
    rr=float(rsi(s).iloc[-1]) if pd.notna(rsi(s).iloc[-1]) else 50.0
    vol=float(s.pct_change().tail(20).std(ddof=0)*math.sqrt(252))
    vz=zlast(v) if not v.empty else 0.0
    trend=float(s.iloc[-1]/s.rolling(20).mean().iloc[-1]-1)
    vix=float(vixs.iloc[-1]) if not vixs.empty else 20.0

    mom=np.clip(50+650*(.45*ret5+.35*ret20+.20*trend),0,100)
    rel=np.clip(50+750*rel20,0,100)
    vol_score=np.clip(35+vol*90,20,90)
    volume_score=np.clip(50+14*vz,0,100)
    rsi_long=85 if 55<=rr<=75 else 60 if 45<=rr<55 or 75<rr<=82 else 35
    rsi_short=85 if 25<=rr<=45 else 60 if 18<=rr<25 or 45<rr<=55 else 35

    long=.31*mom+.26*rel+.14*vol_score+.14*volume_score+.15*rsi_long
    short=.31*(100-mom)+.26*(100-rel)+.14*vol_score+.14*volume_score+.15*rsi_short
    if vix>=30:
        long-=4
        short+=3

    direction,score=("LONG",float(long)) if long>=short else ("SHORT",float(short))
    price=float(s.iloc[-1])
    daily_abs=float(s.pct_change().abs().tail(14).mean())
    stopdist=max(price*daily_abs*1.35,price*0.018)

    if direction=="LONG":
        stop=price-stopdist
        t1=price+1.4*stopdist
        t2=price+2.2*stopdist
    else:
        stop=price+stopdist
        t1=price-1.4*stopdist
        t2=price-2.2*stopdist

    stop_pct=abs(price-stop)/price*100
    rw1=abs(t1-price)/price*100
    rw2=abs(t2-price)/price*100
    risk=risk_label(vol,score,rr,vix)
    dplan=derivative_plan(direction,score,vol,vix,risk,stop_pct,rw1,rw2)

    return {
        "ticker":ticker,"sector":sector,"direction":direction,
        "score":round(score,2),"score_label":f"{score:.0f}/100","horizon":5,
        "entry_price":round(price,4),"stop_price":round(stop,4),
        "target1_price":round(t1,4),"target2_price":round(t2,4),
        "risk_pct":round(stop_pct,2),
        "reward1_pct":round(rw1,2),"reward2_pct":round(rw2,2),
        "risk_reward_1":1.4,"risk_reward_2":2.2,
        "ret5_pct":round(ret5*100,2),"ret20_pct":round(ret20*100,2),"rel20_pct":round(rel20*100,2),
        "rsi":round(rr,1),"volatility_pct":round(vol*100,1),"volume_z":round(vz,2),
        "risk_level":risk,"reasons":reasons(direction,ret5,ret20,rel20,rr,vz,sector),
        "derivative_plan":dplan,
    }

def main():
    tickers=sorted(set(CONTEXT+[t for xs in UNIVERSE.values() for t in xs]))
    data=yf.download(tickers,period="2y",interval="1d",auto_adjust=True,progress=False,group_by="column",threads=True)
    if data is None or data.empty: raise RuntimeError("No market data")

    mem=resolve_memory(load_memory(),data)
    rows=[]
    for sector,ts in UNIVERSE.items():
        for t in ts:
            x=score_asset(t,sector,data)
            if x:rows.append(x)

    rows.sort(key=lambda x:x["score"],reverse=True)
    signals=[x for x in rows if x["score"]>=72][:8]

    today=str(pd.Timestamp.utcnow().date())
    known={p["id"] for p in mem["predictions"]}
    for s in signals:
        pid=f'{today}|{s["ticker"]}|{s["direction"]}|5'
        if pid not in known:
            mem["predictions"].append({
                "id":pid,"date":today,"ticker":s["ticker"],"sector":s["sector"],
                "direction":s["direction"],"horizon":5,"entry":s["entry_price"],
                "score":s["score"],"outcome":"PENDING"
            })

    MEM.write_text(json.dumps(mem,indent=2),encoding="utf-8")
    vix=get(data,"^VIX"); spy=get(data,"SPY")
    out={
        "updated_at":datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "broker_assumptions":BROKER,
        "market":{
            "vix":round(float(vix.iloc[-1]),2) if not vix.empty else None,
            "spy_20d_pct":round(float((spy.iloc[-1]/spy.iloc[-21]-1)*100),2) if len(spy)>=21 else None
        },
        "memory":mem["stats"],
        "signals":signals,
        "watchlist":rows[:15],
        "disclaimer":"Research/paper-trading tool. Score is not a guaranteed probability. Exact derivative selection requires live product data."
    }
    OUT.write_text(json.dumps(out,indent=2),encoding="utf-8")
    print(json.dumps(out,indent=2))

if __name__=="__main__":
    main()
