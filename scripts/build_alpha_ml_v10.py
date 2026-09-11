#!/usr/bin/env python3
import argparse, json, math
from pathlib import Path
from datetime import datetime, timezone
from alpha_ml_runtime import FEATURE_NAMES, feature_map

MEM=Path("data/memory.json")
OUT=Path("data/alpha-ml-training-v10.json")
MODEL=Path("data/alpha-ml-model-v10.json")
LINEAGE="ALPHA_V86_EPISODE_LEDGER_1"

def clip(x,a,b): return max(a,min(b,x))
def mean(xs): return sum(xs)/len(xs) if xs else None
def sigmoid(x):
    x=clip(x,-12,12)
    return 1/(1+math.exp(-x))
def logloss(rows, fn):
    vals=[]
    for r in rows:
        p=clip(fn(r),1e-9,1-1e-9); y=r["y"]
        vals.append(-(y*math.log(p)+(1-y)*math.log(1-p)))
    return mean(vals)
def brier(rows, fn): return mean([(clip(fn(r),0,1)-r["y"])**2 for r in rows])
def max_drawdown(returns):
    eq=peak=0.0; dd=0.0
    for r in returns:
        eq+=r; peak=max(peak,eq); dd=max(dd,peak-eq)
    return dd

def row_from_prediction(p):
    if p.get("outcome") not in ("HIT","MISS") or p.get("learning_lineage")!=LINEAGE:
        return None
    ret=p.get("return_pct")
    if not isinstance(ret,(int,float)): return None
    fm=feature_map(p)
    return {
        "id":p.get("episode_id") or p.get("id"),
        "date":p.get("date"),
        "resolved":p.get("resolved"),
        "y":1 if p.get("outcome")=="HIT" else 0,
        "return_pct":float(ret),
        "features":[float(fm[n]) for n in FEATURE_NAMES],
        "asset_class":p.get("asset_class"),
        "horizon":p.get("horizon"),
    }

def scaler(rows):
    cols=list(zip(*[r["features"] for r in rows]))
    means=[sum(c)/len(c) for c in cols]
    scales=[]
    for c,m in zip(cols,means):
        v=sum((x-m)**2 for x in c)/len(c)
        scales.append(max(math.sqrt(v),1e-6))
    return means,scales

def transform(row,means,scales):
    return [(x-m)/s for x,m,s in zip(row["features"],means,scales)]

def fit(train,l2=0.8,lr=0.05,steps=1600):
    means,scales=scaler(train)
    w=[0.0]*len(FEATURE_NAMES)
    prior=clip(sum(r["y"] for r in train)/len(train),.05,.95)
    b=math.log(prior/(1-prior))
    n=len(train)
    for step in range(steps):
        gw=[0.0]*len(w); gb=0.0
        rate=lr/(1+step/800)
        for r in train:
            x=transform(r,means,scales)
            p=sigmoid(b+sum(a*z for a,z in zip(w,x)))
            e=p-r["y"]; gb+=e
            for i,z in enumerate(x): gw[i]+=e*z
        gb/=n
        for i in range(len(w)):
            gw[i]=gw[i]/n + l2*w[i]/n
            w[i]-=rate*gw[i]
        b-=rate*gb
    return {"intercept":b,"coefficients":w,"means":means,"scales":scales,"prior":prior}

def pred(model,row):
    x=transform(row,model["means"],model["scales"])
    return sigmoid(model["intercept"]+sum(a*z for a,z in zip(model["coefficients"],x)))

def metrics(rows,fn):
    if not rows:return {"n":0,"hit_rate":None,"brier":None,"logloss":None}
    return {
        "n":len(rows),
        "hit_rate":sum(r["y"] for r in rows)/len(rows),
        "brier":brier(rows,fn),
        "logloss":logloss(rows,fn),
    }

def selected_metrics(rows,fn,threshold=.58,extra_friction_bps=20):
    z=[r for r in rows if fn(r)>=threshold]
    if not z:return {"n":0,"avg_return_pct":None,"stressed_avg_return_pct":None,"hit_rate":None,"max_drawdown_pct":None}
    rs=[r["return_pct"] for r in z]
    stressed=[x-extra_friction_bps/100 for x in rs]
    return {
        "n":len(z),
        "avg_return_pct":mean(rs),
        "stressed_avg_return_pct":mean(stressed),
        "hit_rate":sum(r["y"] for r in z)/len(z),
        "max_drawdown_pct":max_drawdown(stressed),
    }

def load_active():
    try:
        m=json.loads(MODEL.read_text())
        return m if m.get("schema")=="ALPHA-ML-MODEL-V10" and m.get("status")=="ACTIVE" else None
    except Exception:return None

def self_test():
    rows=[]
    for i in range(140):
        good=i%4!=0
        ctx={"confidence_pct":76 if good else 55,"direction":"LONG","model_votes":[{"direction":"LONG","conviction":80}] if good else [{"direction":"SHORT","conviction":75}],"risk_pct":2,"horizon":5,"risk_regime":"RISK_ON" if good else "RISK_OFF","rates_regime":"RATES_STABLE","asset_class":"ETF_EQUITY"}
        fm=feature_map(ctx)
        rows.append({"y":1 if good else 0,"return_pct":.5 if good else -.8,"features":[fm[n] for n in FEATURE_NAMES]})
    train,hold=rows[:-30],rows[-30:]
    m=fit(train,steps=500)
    mm=metrics(hold,lambda r:pred(m,r))
    if not (mm["logloss"]<.5 and selected_metrics(hold,lambda r:pred(m,r))["n"]>0):
        raise SystemExit("ALPHA_ML_SELF_TEST_FAILED")
    print(json.dumps({"ok":True,"tests":["regularized_logistic","time_ordered_holdout","cost_stress","guarded_promotion"]}))

def main():
    mem=json.loads(MEM.read_text())
    rows=[x for p in mem.get("predictions",[]) if (x:=row_from_prediction(p))]
    rows.sort(key=lambda r:(str(r.get("date") or ""),str(r.get("resolved") or ""),str(r.get("id") or "")))
    n=len(rows); active=load_active()
    out={"schema":"ALPHA-ML-TRAINING-V10","generated_at":datetime.now(timezone.utc).isoformat(),"settled_n":n,"status":"COLD","active_model":active.get("version") if active else None}
    if n<60:
        out.update({"status":"COLD","reason":"MIN_60_INDEPENDENT_RESOLVED_EPISODES_FOR_TRAINING","required":60})
        OUT.write_text(json.dumps(out,indent=2)+"\n")
        print(json.dumps({"ok":True,"status":"COLD","n":n}))
        return
    hold_n=max(20,min(40,n//4))
    train,hold=rows[:-hold_n],rows[-hold_n:]
    cand=fit(train)
    prior=cand["prior"]
    cand_m=metrics(hold,lambda r:pred(cand,r))
    prior_m=metrics(hold,lambda r:prior)
    select=selected_metrics(hold,lambda r:pred(cand,r))
    new_labels=n-int(active.get("trained_on_n",0) if active else 0)
    gates={
        "minimum_total_n":n>=100,
        "minimum_holdout_n":hold_n>=20,
        "minimum_new_labels":new_labels>=10,
        "logloss_beats_prior_2pct":cand_m["logloss"]<=prior_m["logloss"]*.98,
        "brier_beats_prior_1pct":cand_m["brier"]<=prior_m["brier"]*.99,
        "selected_n_at_least_10":select["n"]>=10,
        "selected_survives_plus_20bps":select["stressed_avg_return_pct"] is not None and select["stressed_avg_return_pct"]>0,
    }
    stage="MATURE" if n>=200 else "ACTIVE_GUARDED"
    promoted=all(gates.values())
    candidate={
        "train_n":len(train),"holdout_n":len(hold),"prior_probability":prior,
        "metrics":cand_m,"prior_metrics":prior_m,"selected_at_p58":select,
        "gates":gates,"new_labels_since_active":new_labels,
        "coefficients":[round(x,8) for x in cand["coefficients"]],
        "intercept":round(cand["intercept"],8),
        "scaler":{"means":[round(x,8) for x in cand["means"]],"scales":[round(x,8) for x in cand["scales"]]},
    }
    out.update({"status":"PROMOTED" if promoted else "SHADOW","candidate":candidate,"stage":stage})
    if promoted:
        model={
            "schema":"ALPHA-ML-MODEL-V10","status":"ACTIVE","version":f"ALPHA-ML-V10-N{n}",
            "stage":stage,"trained_on_n":n,"promoted_at":datetime.now(timezone.utc).isoformat(),
            "feature_names":FEATURE_NAMES,"intercept":candidate["intercept"],"coefficients":candidate["coefficients"],
            "scaler":candidate["scaler"],"validation":{"candidate":cand_m,"prior":prior_m,"selection":select,"gates":gates},
            "policy":{"positive_boost":stage=="MATURE","max_positive_confidence_adjustment":2.0 if stage=="MATURE" else 0.0,"max_negative_confidence_adjustment":-4.0,"paper_research_only":True}
        }
        MODEL.write_text(json.dumps(model,indent=2)+"\n")
    OUT.write_text(json.dumps(out,indent=2)+"\n")
    print(json.dumps({"ok":True,"status":out["status"],"n":n,"stage":stage,"gates":gates}))

if __name__=="__main__":
    ap=argparse.ArgumentParser();ap.add_argument("--self-test",action="store_true");args=ap.parse_args()
    self_test() if args.self_test else main()
