import json
from pathlib import Path
from datetime import datetime, timezone

DATA=Path("data")
health=json.loads((DATA/"model-health.json").read_text())
attr=json.loads((DATA/"decision-attribution.json").read_text())
gov=json.loads((DATA/"quant-governance.json").read_text())

fr=attr.get("friction_stress") or {}
tiers={int(x.get("extra_friction_bps",0)):x for x in fr.get("tiers") or []}
base_n=int(fr.get("baseline_n") or 0)
base_avg=fr.get("baseline_avg_net_return_pct")
t10=tiers.get(10) or {}
t20=tiers.get(20) or {}
t35=tiers.get(35) or {}

status="COLD"
reasons=[]
if base_n < 20:
    status="COLD"; reasons.append("FORWARD_SAMPLE_TOO_SMALL")
elif base_n < 30:
    status="FRAGILE"; reasons.append("FORWARD_SAMPLE_BELOW_30")
else:
    status="ROBUST"

if t20.get("avg_return_pct") is not None and t20["avg_return_pct"] <= 0:
    status="FRAGILE"; reasons.append("EDGE_DOES_NOT_SURVIVE_20BPS_EXTRA_FRICTION")
if t10.get("positive_survival_ratio") is not None and t10["positive_survival_ratio"] < .9:
    status="FRAGILE"; reasons.append("POSITIVE_SURVIVAL_BELOW_90PCT_AT_10BPS")
if health.get("status") in ("CAUTION","BLOCKED"):
    reasons.append("MODEL_HEALTH_"+str(health.get("status")))

out={
  "schema_version":"1.0",
  "generated_at":datetime.now(timezone.utc).isoformat(),
  "model_version":health.get("model_version"),
  "status":status,
  "forward_sample":base_n,
  "baseline_avg_net_return_pct":base_avg,
  "stress":{
    "10bps":t10,
    "20bps":t20,
    "35bps":t35
  },
  "policy":{
    "capital_mode":"PAPER_RESEARCH_ONLY",
    "real_money_promotion_allowed":False,
    "minimum_forward_n_for_robustness":30,
    "required_20bps_avg_return_gt_zero":True,
    "auto_retune":False
  },
  "reasons":reasons,
  "recommendation":"Keep paper-only and require cost-robust forward evidence before any promotion." if status!="ROBUST" else "Cost robustness passed; still requires separate governance review before any promotion."
}
(DATA/"execution-robustness.json").write_text(json.dumps(out,indent=2)+"\n")
print(json.dumps({"ok":True,"status":status,"n":base_n,"reasons":reasons}))
