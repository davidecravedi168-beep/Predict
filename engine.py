#!/usr/bin/env python3
"""Alpha Engine V8 · Cross-Asset Evidence Engine

Principi:
- nessun dato di mercato viene inventato: i dati mancanti restano mancanti;
- ogni valore calcolato è marcato come MODEL_DERIVED con formula/versione;
- la memoria usa solo esiti già osservati (prequential / no look-ahead);
- l'apprendimento può penalizzare molto più di quanto possa premiare;
- la selezione diversifica solo tra segnali che superano le soglie: non forza mai
  un asset debole solo per riempire una classe.
"""
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import yfinance as yf
from edge_core import EDGE_CORE_VERSION, assert_public_snapshot, cost_adjusted_return_pct, estimated_round_trip_cost_bps

MODEL_VERSION = "8.6.1-math-risk-layer"
# Migration compatibility baseline: 8.6.0-episode-ledger-cost-aware
LEARNING_LINEAGE = "ALPHA_V86_EPISODE_LEDGER_1"
OUT = Path("data/latest.json")
MEM = Path("data/memory.json")
EXTERNAL_MODELS = Path("data/external_models.json")
BACKTEST_OUT = Path("data/backtest-v8.json")

HORIZON_POLICY_VERSION = "AH-1.0"
HORIZON_MIN_OBS = 18
HORIZON_MIN_HALF_OBS = 8
HORIZON_MIN_HIT_RATE = 0.52
HORIZON_MIN_HALF_HIT_RATE = 0.50
HORIZON_SWITCH_MARGIN = 0.02

# Candidate windows are deliberately bounded to the information horizon of the
# daily features used by the engine. The selector may override the deterministic
# fallback only with stable evidence available before the forecast timestamp.
HORIZON_CONFIG = {
    "EQUITY": {"candidates": [3,5,10,20], "fallback": {"TREND":10,"RELATIVE":10,"BREAKOUT":10,"MEAN_REVERSION":3,"OSCILLATOR":5,"FLOW":5,"MIXED":5}},
    "ETF_EQUITY": {"candidates": [3,5,10,20], "fallback": {"TREND":10,"RELATIVE":10,"BREAKOUT":10,"MEAN_REVERSION":3,"OSCILLATOR":5,"FLOW":5,"MIXED":5}},
    "ETF_COMMODITY": {"candidates": [3,5,10,20], "fallback": {"TREND":10,"RELATIVE":10,"BREAKOUT":5,"MEAN_REVERSION":3,"OSCILLATOR":5,"FLOW":5,"MIXED":5}},
    "INDEX_FUTURE": {"candidates": [1,3,5,10], "fallback": {"TREND":5,"RELATIVE":5,"BREAKOUT":3,"MEAN_REVERSION":1,"OSCILLATOR":3,"FLOW":3,"MIXED":3}},
    "FX": {"candidates": [3,5,10,20], "fallback": {"TREND":10,"RELATIVE":10,"BREAKOUT":5,"MEAN_REVERSION":3,"OSCILLATOR":5,"FLOW":5,"MIXED":5}},
    "CRYPTO": {"candidates": [3,5,10,20], "fallback": {"TREND":10,"RELATIVE":10,"BREAKOUT":5,"MEAN_REVERSION":3,"OSCILLATOR":5,"FLOW":5,"MIXED":5}},
    "ETF_BOND_GOV": {"candidates": [5,10,20,40], "fallback": {"TREND":20,"RELATIVE":20,"BREAKOUT":10,"MEAN_REVERSION":5,"OSCILLATOR":10,"FLOW":10,"MIXED":10}},
    "BTP": {"candidates": [5,10,20,40], "fallback": {"TREND":20,"RELATIVE":20,"BREAKOUT":10,"MEAN_REVERSION":5,"OSCILLATOR":10,"FLOW":10,"MIXED":10}},
    "ETF_BOND_CREDIT": {"candidates": [5,10,20,40], "fallback": {"TREND":20,"RELATIVE":20,"BREAKOUT":10,"MEAN_REVERSION":5,"OSCILLATOR":10,"FLOW":10,"MIXED":10}},
    "CASH_EQUIVALENT": {"candidates": [10,20,40,60], "fallback": {"TREND":20,"RELATIVE":20,"BREAKOUT":20,"MEAN_REVERSION":10,"OSCILLATOR":20,"FLOW":20,"MIXED":20}},
}

# Universo multi-asset. Gli strumenti italiani qui inclusi sono quotati su Borsa
# Italiana; se Yahoo/yfinance non restituisce dati, l'asset viene segnato come
# unavailable e NON sostituito con valori sintetici.
GROUPS = {
    "US_BROAD_ETF": {
        "asset_class": "ETF_EQUITY", "cluster": "US_BROAD", "benchmark": "SPY", "role": "CORE",
        "tickers": ["SPY", "QQQ", "IWM", "DIA", "USMV"],
    },
    "GLOBAL_EQUITY_ETF": {
        "asset_class": "ETF_EQUITY", "cluster": "GLOBAL_EQUITY", "benchmark": "SPY", "role": "CORE",
        "tickers": ["ACWI", "VT", "VGK", "EEM"],
    },
    "US_SECTOR_ETF": {
        "asset_class": "ETF_EQUITY", "cluster": "US_SECTORS", "benchmark": "SPY", "role": "CORE",
        "tickers": ["XLK", "XLF", "XLV", "XLI", "XLP", "XLY", "XLU"],
    },
    "US_QUALITY_GROWTH": {
        "asset_class": "EQUITY", "cluster": "US_MEGA_CAP", "benchmark": "SPY", "role": "CORE",
        "tickers": ["MSFT", "AAPL", "NVDA", "AMZN", "GOOGL", "META", "AVGO"],
    },
    "US_FINANCIALS": {
        "asset_class": "EQUITY", "cluster": "FINANCIALS", "benchmark": "XLF", "role": "CORE",
        "tickers": ["JPM", "BAC", "GS"],
    },
    "ITALY_EQUITY": {
        "asset_class": "EQUITY", "cluster": "ITALY_EQUITY", "benchmark": "EWI", "role": "CORE",
        "tickers": ["ENI.MI", "ENEL.MI", "ISP.MI", "LDO.MI", "STM.MI"],
    },
    "EUROPE_EQUITY": {
        "asset_class": "EQUITY", "cluster": "EUROPE_EQUITY", "benchmark": "VGK", "role": "CORE",
        "tickers": ["ASML.AS", "SAP.DE", "SIE.DE", "AIR.PA"],
    },
    "ENERGY_ETF": {
        "asset_class": "ETF_EQUITY", "cluster": "ENERGY", "benchmark": "SPY", "role": "CORE",
        "tickers": ["XLE", "XOP", "OIH"],
    },
    "ENERGY_EQUITY": {
        "asset_class": "EQUITY", "cluster": "ENERGY", "benchmark": "XLE", "role": "CORE",
        "tickers": ["XOM", "CVX", "COP", "SLB", "MPC", "VLO"],
    },
    "NATURAL_GAS_ETF": {
        "asset_class": "ETF_COMMODITY", "cluster": "NATURAL_GAS", "benchmark": "NG=F", "role": "SATELLITE",
        "tickers": ["UNG"],
    },
    "NATURAL_GAS_EQUITY": {
        "asset_class": "EQUITY", "cluster": "NATURAL_GAS", "benchmark": "XOP", "role": "SATELLITE",
        "tickers": ["EQT", "AR", "RRC", "CTRA"],
    },
    "WATER_ETF": {
        "asset_class": "ETF_EQUITY", "cluster": "WATER", "benchmark": "SPY", "role": "DIVERSIFIER",
        "tickers": ["PHO", "FIW"],
    },
    "WATER_UTILITIES_EQUITY": {
        "asset_class": "EQUITY", "cluster": "DEFENSIVES", "benchmark": "XLU", "role": "DIVERSIFIER",
        "tickers": ["AWK", "WTRG", "XYL", "NEE", "DUK"],
    },
    "REAL_ASSETS": {
        "asset_class": "ETF_EQUITY", "cluster": "REAL_ASSETS", "benchmark": "SPY", "role": "DIVERSIFIER",
        "tickers": ["VNQ", "IGF"],
    },
    "US_TREASURY": {
        "asset_class": "ETF_BOND_GOV", "cluster": "US_RATES", "benchmark": "IEF", "role": "DIVERSIFIER",
        "tickers": ["SHY", "IEF", "TLT"],
    },
    "US_AGGREGATE_BOND": {
        "asset_class": "ETF_BOND_GOV", "cluster": "BROAD_BONDS", "benchmark": "AGG", "role": "DIVERSIFIER",
        "tickers": ["AGG", "BND"],
    },
    "CREDIT": {
        "asset_class": "ETF_BOND_CREDIT", "cluster": "CREDIT", "benchmark": "LQD", "role": "DIVERSIFIER",
        "tickers": ["LQD", "HYG"],
    },
    "INTERNATIONAL_BONDS": {
        "asset_class": "ETF_BOND_GOV", "cluster": "GLOBAL_RATES", "benchmark": "BNDX", "role": "DIVERSIFIER",
        "tickers": ["BNDX"],
    },
    "EMERGING_MARKET_DEBT": {
        "asset_class": "ETF_BOND_CREDIT", "cluster": "EM_DEBT", "benchmark": "EMB", "role": "DIVERSIFIER",
        "tickers": ["EMB"],
    },
    "ITALY_GOVT_BOND_ETF": {
        "asset_class": "BTP", "cluster": "ITALY_RATES", "benchmark": "IITB.MI", "role": "DIVERSIFIER",
        "tickers": ["IITB.MI", "IITA.MI", "BTP10.MI", "BT27.MI"],
    },
    "INFLATION_LINKED": {
        "asset_class": "ETF_BOND_GOV", "cluster": "INFLATION_LINKED", "benchmark": "IEF", "role": "DIVERSIFIER",
        "tickers": ["TIP", "IBCI.MI"],
    },
    "EURO_SHORT_CREDIT": {
        "asset_class": "ETF_BOND_CREDIT", "cluster": "EURO_CREDIT", "benchmark": None, "role": "DIVERSIFIER",
        "tickers": ["EUES.MI", "SUSE.MI"],
    },
    "COMMODITIES": {
        "asset_class": "ETF_COMMODITY", "cluster": "COMMODITIES", "benchmark": "DBC", "role": "DIVERSIFIER",
        "tickers": ["DBC", "GLD", "SLV", "USO"],
    },
    "FX": {
        "asset_class": "FX", "cluster": "FX", "benchmark": None, "role": "SATELLITE",
        "tickers": ["EURUSD=X", "GBPUSD=X", "JPY=X"],
    },
    "CASH_EQUIVALENT": {
        "asset_class": "CASH_EQUIVALENT", "cluster": "CASH", "benchmark": "BIL", "role": "DEFENSIVE",
        "tickers": ["BIL", "SGOV"],
    },
    "CRYPTO_SATELLITE": {
        "asset_class": "CRYPTO", "cluster": "CRYPTO", "benchmark": "BTC-USD", "role": "SATELLITE",
        "tickers": ["BTC-USD", "ETH-USD"],
    },
    "INDEX_FUTURES": {
        "asset_class": "INDEX_FUTURE", "cluster": "INDEX_FUTURES", "benchmark": "SPY", "role": "SATELLITE",
        "tickers": ["ES=F", "NQ=F"],
    },
}
DISPLAY_TICKER = {
    "ES=F": "ES", "NQ=F": "NQ",
    "IITB.MI": "IITB · BTP Italia Dist",
    "IITA.MI": "IITA · BTP Italia Acc",
    "BT27.MI": "BT27 · BTP 2027",
    "BTP10.MI": "BTP10 · Italy BTP 10Y",
    "IBCI.MI": "IBCI · Euro Inflation Linked",
    "EUES.MI": "EUES · Euro Ultrashort Bond",
    "SUSE.MI": "SUSE · Euro Corp 0-3Y",
}

# Fonti di riferimento statiche sugli strumenti (NON quotazioni). Servono a
# distinguere un simbolo verificato da un ticker ipotetico.
INSTRUMENT_REFERENCES = {
    "BNDX": {"status": "VERIFIED_LISTING", "issuer": "Vanguard", "market": "US ETF", "verified_at": "2026-08-25", "source": "VANGUARD_OFFICIAL"},
    "EMB": {"status": "VERIFIED_LISTING", "issuer": "iShares/BlackRock", "market": "NASDAQ", "cusip": "464288281", "verified_at": "2026-08-25", "source": "ISHARES_OFFICIAL"},
    "IITB.MI": {"status": "VERIFIED_LISTING", "issuer": "iShares/BlackRock", "market": "Borsa Italiana", "isin": "IE00B7LW6Y90", "verified_at": "2026-08-25", "source": "BORSA_ITALIANA_BLACKROCK"},
    "IITA.MI": {"status": "VERIFIED_LISTING", "issuer": "iShares/BlackRock", "market": "Borsa Italiana", "isin": "IE000589MF42", "verified_at": "2026-08-25", "source": "BORSA_ITALIANA_BLACKROCK"},
    "BTP10.MI": {"status": "VERIFIED_LISTING", "issuer": "Amundi", "market": "Borsa Italiana", "isin": "LU1598691217", "verified_at": "2026-08-25", "source": "AMUNDI_OFFICIAL_FACTSHEET"},
    "BT27.MI": {"status": "VERIFIED_LISTING", "issuer": "Amundi", "market": "Borsa Italiana", "isin": "LU2780872128", "verified_at": "2026-08-25", "source": "BORSA_ITALIANA"},
    "EUES.MI": {"status": "VERIFIED_LISTING", "issuer": "iShares/BlackRock", "market": "Borsa Italiana", "isin": "IE000NBRE3P7", "verified_at": "2026-08-25", "source": "BORSA_ITALIANA"},
    "SUSE.MI": {"status": "VERIFIED_LISTING", "issuer": "iShares/BlackRock", "market": "Borsa Italiana", "isin": "IE00BYZTVV78", "verified_at": "2026-08-25", "source": "BORSA_ITALIANA"},
}

CONTEXT = ["SPY", "^VIX", "CL=F", "NG=F", "GC=F", "DX-Y.NYB", "^TNX", "EURUSD=X", "IITB.MI", "BTP10.MI"]


ASSET_META = {}
for group_name, cfg in GROUPS.items():
    for ticker in cfg["tickers"]:
        ASSET_META[ticker] = {
            "group": group_name,
            "asset_class": cfg["asset_class"],
            "cluster": cfg["cluster"],
            "benchmark": cfg.get("benchmark"),
            "role": cfg.get("role", "CORE"),
        }


def safe_num(x, default=None):
    try:
        if pd.isna(x):
            return default
        return float(x)
    except Exception:
        return default


def clip(x, lo, hi):
    return float(np.clip(x, lo, hi))


def get(data, ticker, field="Close"):
    if data is None or getattr(data, "empty", True):
        return pd.Series(dtype=float)
    if isinstance(data.columns, pd.MultiIndex):
        if (field, ticker) in data.columns:
            return data[(field, ticker)].dropna()
        if (ticker, field) in data.columns:
            return data[(ticker, field)].dropna()
    # fallback per eventuali download a singolo ticker
    if field in data.columns and len(ASSET_META) == 1:
        return data[field].dropna()
    return pd.Series(dtype=float)


def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).rolling(n).mean()
    dn = (-d.clip(upper=0)).rolling(n).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def rolling_z_last(series, window=252, min_obs=60):
    x = series.dropna()
    if len(x) < min_obs:
        return None
    hist = x.tail(window)
    sd = hist.std(ddof=0)
    if not np.isfinite(sd) or sd == 0:
        return None
    return float((hist.iloc[-1] - hist.mean()) / sd)


def zlast(s, n=60):
    x = s.tail(n).dropna()
    if len(x) < 20:
        return None
    sd = x.std(ddof=0)
    if not sd or pd.isna(sd):
        return None
    return float((x.iloc[-1] - x.mean()) / sd)


def atr14(data, ticker):
    h, l, c = get(data, ticker, "High"), get(data, ticker, "Low"), get(data, ticker, "Close")
    if min(len(h), len(l), len(c)) < 20:
        return None
    df = pd.concat([h.rename("h"), l.rename("l"), c.rename("c")], axis=1).dropna()
    if len(df) < 20:
        return None
    prev = df["c"].shift(1)
    tr = pd.concat([(df["h"] - df["l"]), (df["h"] - prev).abs(), (df["l"] - prev).abs()], axis=1).max(axis=1)
    v = tr.rolling(14).mean().iloc[-1]
    return safe_num(v)


def infer_currency(ticker):
    if ticker.endswith((".MI", ".DE", ".PA", ".AS")):
        return "EUR"
    if ticker == "EURUSD=X":
        return "USD"
    if ticker == "GBPUSD=X":
        return "USD"
    if ticker == "JPY=X":
        return "JPY"
    return "USD"


def classify_regimes(data):
    spy = get(data, "SPY")
    vix = get(data, "^VIX")
    tnx = get(data, "^TNX")
    spy20 = safe_num((spy.iloc[-1] / spy.iloc[-21] - 1) * 100) if len(spy) >= 21 else None
    vix_last = safe_num(vix.iloc[-1]) if not vix.empty else None
    tnx_bps = safe_num((tnx.iloc[-1] - tnx.iloc[-21]) * 100) if len(tnx) >= 21 else None

    # Italy government-bond context uses the observed exchange-traded BTP proxy,
    # never a synthetic Italian yield. Fallback to BTP10 only if IITB is absent.
    it = get(data, "IITB.MI")
    if len(it) < 21:
        it = get(data, "BTP10.MI")
    italy_bond20 = safe_num((it.iloc[-1] / it.iloc[-21] - 1) * 100) if len(it) >= 21 else None

    if vix_last is not None and vix_last >= 30:
        risk = "STRESS"
    elif spy20 is not None and spy20 <= -5:
        risk = "RISK_OFF"
    elif vix_last is not None and vix_last >= 24:
        risk = "RISK_OFF"
    elif spy20 is not None and spy20 > 0 and (vix_last is None or vix_last < 22):
        risk = "RISK_ON"
    else:
        risk = "NEUTRAL"

    if tnx_bps is None:
        rates = "UNKNOWN"
    elif tnx_bps >= 25:
        rates = "RATES_UP"
    elif tnx_bps <= -25:
        rates = "RATES_DOWN"
    else:
        rates = "RATES_STABLE"

    if italy_bond20 is None:
        italy_bond_regime = "UNKNOWN"
    elif italy_bond20 >= 1.0:
        italy_bond_regime = "ITALY_BOND_RALLY"
    elif italy_bond20 <= -1.0:
        italy_bond_regime = "ITALY_BOND_SELL_OFF"
    else:
        italy_bond_regime = "ITALY_BOND_STABLE"

    return {
        "risk_regime": risk,
        "rates_regime": rates,
        "italy_bond_regime": italy_bond_regime,
        "italy_gov_bond_20d_pct": italy_bond20,
        "spy20_pct": spy20,
        "vix": vix_last,
        "us10y_change_20d_bps": tnx_bps,
    }


def load_external_model_votes(now=None):
    """Optional gateway. Nothing is synthesized if the file/source is absent or invalid."""
    now = now or datetime.now(timezone.utc)
    if not EXTERNAL_MODELS.exists():
        return {}, {"status": "NOT_CONNECTED", "accepted": 0, "rejected": 0, "positive_boost_enabled": False}
    try:
        raw = json.loads(EXTERNAL_MODELS.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, {"status": "INVALID_FILE", "accepted": 0, "rejected": 0, "positive_boost_enabled": False, "error": str(exc)[:120]}
    rows = raw.get("predictions", raw) if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        return {}, {"status": "INVALID_SCHEMA", "accepted": 0, "rejected": 0, "positive_boost_enabled": False}
    out = defaultdict(list)
    rejected = 0
    for row in rows:
        if not isinstance(row, dict):
            rejected += 1; continue
        model_id = str(row.get("model_id") or "").strip()
        ticker = str(row.get("ticker") or "").strip()
        direction = str(row.get("direction") or "").upper().strip()
        source = str(row.get("source_url") or row.get("source") or "").strip()
        ts_raw = row.get("timestamp") or row.get("as_of")
        conf = safe_num(row.get("confidence"))
        horizon = safe_num(row.get("horizon_days"), safe_num(row.get("horizon")))
        try:
            ts = pd.Timestamp(ts_raw)
            ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
            age_h = (pd.Timestamp(now) - ts).total_seconds() / 3600
        except Exception:
            rejected += 1; continue
        if not model_id or ticker not in ASSET_META or direction not in ("LONG", "SHORT") or not source or conf is None or not (0 <= conf <= 1) or horizon is None or int(horizon) != horizon or not (1 <= int(horizon) <= 60) or age_h < -1 or age_h > 48:
            rejected += 1; continue
        out[ticker].append({
            "model_id": model_id, "ticker": ticker, "direction": direction,
            "confidence": round(conf, 4), "timestamp": ts.isoformat(),
            "horizon": int(horizon),
            "source": source, "verification_status": str(row.get("verification_status") or "SOURCE_DECLARED"), "origin": "EXTERNAL_DECLARED_FEED",
        })
    accepted = sum(len(v) for v in out.values())
    return dict(out), {"status": "CONNECTED" if accepted else "NO_VALID_PREDICTIONS", "accepted": accepted, "rejected": rejected, "positive_boost_enabled": False}


def load_memory():
    if MEM.exists():
        try:
            obj = json.loads(MEM.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                obj.setdefault("predictions", [])
                obj.setdefault("stats", {})
                obj.setdefault("model_stats", {})
                obj.setdefault("model_stats_by_asset_class", {})
                obj.setdefault("external_model_stats", {})
                obj.setdefault("external_predictions", [])
                return obj
        except Exception:
            pass
    return {"predictions": [], "external_predictions": [], "stats": {}, "model_stats": {}, "model_stats_by_asset_class": {}, "external_model_stats": {}, "version": MODEL_VERSION}


def save_memory(mem):
    MEM.parent.mkdir(exist_ok=True)
    mem["version"] = MODEL_VERSION
    # limita crescita infinita senza alterare gli esiti più recenti
    mem["predictions"] = mem.get("predictions", [])[-3000:]
    mem["external_predictions"] = mem.get("external_predictions", [])[-5000:]
    MEM.write_text(json.dumps(mem, indent=2, ensure_ascii=False), encoding="utf-8")


def _vote_hit(vote_direction, raw_return):
    if raw_return == 0:
        return None
    actual = "LONG" if raw_return > 0 else "SHORT"
    return vote_direction == actual


def resolve_memory(mem, data):
    """Resolve only predictions whose entire horizon is now observable."""
    for p in mem.get("predictions", []):
        if p.get("outcome") in ("HIT", "MISS", "SUPERSEDED", "EXPIRED", "CANCELLED", "EXCLUDED_DUPLICATE"):
            continue
        ticker = p.get("ticker")
        s = get(data, ticker)
        if s.empty:
            continue
        try:
            dt = pd.Timestamp(p["date"])
        except Exception:
            continue
        future_close = s[s.index > dt]
        h_raw = safe_num(p.get("horizon"))
        if h_raw is None or int(h_raw) != h_raw or int(h_raw) < 1:
            # Legacy records without an explicit horizon are not silently assumed to be 5 days.
            p.setdefault("resolution_state", "PENDING_MISSING_DECLARED_HORIZON")
            continue
        h = int(h_raw)
        if len(future_close) < h:
            continue
        entry = safe_num(p.get("entry"))
        if entry is None or entry <= 0:
            continue

        exit_price = float(future_close.iloc[h - 1])
        raw = exit_price / entry - 1
        direction = p.get("direction")
        pnl = raw if direction == "LONG" else -raw
        gross_pct = round(pnl * 100, 4)
        net_pct, cost_bps = cost_adjusted_return_pct(gross_pct, p.get("asset_class"))
        p["outcome"] = "HIT" if net_pct > 0 else "MISS"
        p["gross_return_pct"] = gross_pct
        p["estimated_round_trip_cost_bps"] = cost_bps
        p["return_pct"] = net_pct
        p["underlying_return_pct"] = round(raw * 100, 4)
        p["exit"] = round(exit_price, 6)
        p["resolved"] = str(future_close.index[h - 1].date())

        # Excursions use observed High/Low when present. If missing, fields remain null.
        hi, lo = get(data, ticker, "High"), get(data, ticker, "Low")
        if not hi.empty and not lo.empty:
            window_idx = future_close.index[:h]
            wh = hi.reindex(window_idx).dropna()
            wl = lo.reindex(window_idx).dropna()
            if not wh.empty and not wl.empty:
                high, low = float(wh.max()), float(wl.min())
                favorable = (high / entry - 1) if direction == "LONG" else (entry / low - 1)
                adverse = (low / entry - 1) if direction == "LONG" else (entry / high - 1)
                p["mfe_pct"] = round(max(0.0, favorable) * 100, 4)
                p["mae_pct"] = round(min(0.0, adverse) * 100, 4)
                stop = safe_num(p.get("stop"))
                t1 = safe_num(p.get("target1"))
                t2 = safe_num(p.get("target2"))
                if stop is not None:
                    p["stop_touched"] = bool(low <= stop) if direction == "LONG" else bool(high >= stop)
                if t1 is not None:
                    p["target1_touched"] = bool(high >= t1) if direction == "LONG" else bool(low <= t1)
                if t2 is not None:
                    p["target2_touched"] = bool(high >= t2) if direction == "LONG" else bool(low <= t2)
                # Daily bars cannot reveal intraday ordering if both stop and target were touched.
                if p.get("stop_touched") and (p.get("target1_touched") or p.get("target2_touched")):
                    p["path_order"] = "AMBIGUOUS_DAILY_BAR"
                else:
                    p["path_order"] = "NOT_AMBIGUOUS_AT_DAILY_RESOLUTION"

        for vote in p.get("model_votes", []) or []:
            hit = _vote_hit(vote.get("direction"), raw)
            if hit is not None:
                vote["outcome"] = "HIT" if hit else "MISS"
        for vote in p.get("external_model_votes", []) or []:
            hit = _vote_hit(vote.get("direction"), raw)
            if hit is not None:
                vote["outcome"] = "HIT" if hit else "MISS"

    # Independent external-model ledger: evaluated on each model's declared horizon,
    # not conditioned on Alpha Engine selecting the same ticker.
    for p in mem.get("external_predictions", []):
        if p.get("outcome") in ("HIT", "MISS"):
            continue
        ticker = p.get("ticker")
        s = get(data, ticker)
        if s.empty:
            continue
        try:
            dt = pd.Timestamp(p["date"])
        except Exception:
            continue
        h = int(p.get("horizon", 0) or 0)
        entry = safe_num(p.get("entry"))
        if h < 1 or entry is None or entry <= 0:
            continue
        future = s[s.index > dt]
        if len(future) < h:
            continue
        exit_price = float(future.iloc[h - 1])
        raw = exit_price / entry - 1
        pnl = raw if p.get("direction") == "LONG" else -raw
        gross_pct = round(pnl * 100, 4)
        net_pct, cost_bps = cost_adjusted_return_pct(gross_pct, p.get("asset_class"))
        p["outcome"] = "HIT" if net_pct > 0 else "MISS"
        p["gross_return_pct"] = gross_pct
        p["estimated_round_trip_cost_bps"] = cost_bps
        p["return_pct"] = net_pct
        p["exit"] = round(exit_price, 6)
        p["resolved"] = str(future.index[h - 1].date())

    resolved = [
        p for p in mem.get("predictions", [])
        if p.get("outcome") in ("HIT", "MISS")
        and p.get("learning_lineage") == LEARNING_LINEAGE
    ]
    legacy_resolved = [
        p for p in mem.get("predictions", [])
        if p.get("outcome") in ("HIT", "MISS")
        and p.get("learning_lineage") != LEARNING_LINEAGE
    ]
    hits = sum(p["outcome"] == "HIT" for p in resolved)
    brier_vals = []
    for p in resolved:
        prob = safe_num(p.get("forecast_probability"))
        if prob is not None and 0 <= prob <= 1:
            y = 1 if p["outcome"] == "HIT" else 0
            brier_vals.append((prob - y) ** 2)

    by_class = {}
    for cls in sorted({p.get("asset_class", "UNKNOWN") for p in resolved}):
        z = [p for p in resolved if p.get("asset_class", "UNKNOWN") == cls]
        zh = sum(p["outcome"] == "HIT" for p in z)
        rets = [safe_num(p.get("return_pct")) for p in z]
        rets = [x for x in rets if x is not None]
        by_class[cls] = {
            "resolved": len(z), "hits": zh,
            "hit_rate": round(zh / len(z), 4) if z else None,
            "avg_return_pct": round(float(np.mean(rets)), 4) if rets else None,
        }

    by_horizon = {}
    for h in sorted({int(p.get("horizon", 0) or 0) for p in resolved if int(p.get("horizon", 0) or 0) > 0}):
        z = [p for p in resolved if int(p.get("horizon", 0) or 0) == h]
        zh = sum(p["outcome"] == "HIT" for p in z)
        rets = [safe_num(p.get("return_pct")) for p in z]
        rets = [x for x in rets if x is not None]
        by_horizon[str(h)] = {
            "resolved": len(z), "hits": zh,
            "hit_rate": round(zh / len(z), 4) if z else None,
            "avg_return_pct": round(float(np.mean(rets)), 4) if rets else None,
        }
    by_class_horizon = {}
    for p in resolved:
        cls = p.get("asset_class", "UNKNOWN")
        h = int(p.get("horizon", 0) or 0)
        if h <= 0:
            continue
        key = f"{cls}|{h}"
        by_class_horizon.setdefault(key, {"resolved": 0, "hits": 0, "returns": []})
        by_class_horizon[key]["resolved"] += 1
        by_class_horizon[key]["hits"] += int(p.get("outcome") == "HIT")
        r = safe_num(p.get("return_pct"))
        if r is not None:
            by_class_horizon[key]["returns"].append(r)
    for key, z in list(by_class_horizon.items()):
        n = z["resolved"]
        rs = z.pop("returns")
        z["hit_rate"] = round(z["hits"] / n, 4) if n else None
        z["avg_return_pct"] = round(float(np.mean(rs)), 4) if rs else None

    model_acc = defaultdict(lambda: {"resolved": 0, "hits": 0})
    model_by_class = defaultdict(lambda: {"resolved": 0, "hits": 0})
    external_acc = defaultdict(lambda: {"resolved": 0, "hits": 0})
    learning_resolved = [p for p in resolved if p.get("learning_lineage") == LEARNING_LINEAGE]
    for p in learning_resolved:
        cls = p.get("asset_class", "UNKNOWN")
        for v in p.get("model_votes", []) or []:
            if v.get("outcome") in ("HIT", "MISS") and v.get("model_id"):
                mid = v["model_id"]
                model_acc[mid]["resolved"] += 1
                model_acc[mid]["hits"] += int(v["outcome"] == "HIT")
                key = f"{mid}|{cls}"
                model_by_class[key]["resolved"] += 1
                model_by_class[key]["hits"] += int(v["outcome"] == "HIT")

    external_resolved = [p for p in mem.get("external_predictions", []) if p.get("outcome") in ("HIT", "MISS") and p.get("learning_lineage") == LEARNING_LINEAGE]
    for p in external_resolved:
        if p.get("model_id") and p.get("source") and p.get("horizon"):
            key = f'{p["model_id"]}|{p["source"]}|{int(p["horizon"])}'
            external_acc[key]["resolved"] += 1
            external_acc[key]["hits"] += int(p["outcome"] == "HIT")

    def finalize_stats(src):
        out = {}
        for k, m in src.items():
            n = m["resolved"]
            out[k] = {**m, "hit_rate": round(m["hits"] / n, 4) if n else None}
        return out

    recent = resolved[-50:]
    recent_hits = sum(p["outcome"] == "HIT" for p in recent)
    high_conf_misses = sum(
        1 for p in resolved
        if p["outcome"] == "MISS" and safe_num(p.get("confidence_pct"), 0) >= 75
    )

    lineage_rows = [p for p in mem.get("predictions", []) if p.get("learning_lineage") == LEARNING_LINEAGE]
    active_rows = [p for p in lineage_rows if p.get("outcome") == "PENDING"]
    superseded_rows = [p for p in mem.get("predictions", []) if p.get("outcome") in ("SUPERSEDED", "EXCLUDED_DUPLICATE")]
    mem["stats"] = {
        "total": len(lineage_rows),
        "independent_episodes": len(lineage_rows),
        "active": len(active_rows),
        "superseded_or_excluded": len(superseded_rows),
        "legacy_resolved_excluded": len(legacy_resolved),
        "resolved": len(resolved),
        "hits": hits,
        "misses": len(resolved) - hits,
        "hit_rate": round(hits / len(resolved), 4) if resolved else None,
        "recent_50_hit_rate": round(recent_hits / len(recent), 4) if recent else None,
        "brier": round(float(np.mean(brier_vals)), 5) if len(brier_vals) >= 20 else None,
        "brier_n": len(brier_vals),
        "high_confidence_misses": high_conf_misses,
        "learning_lineage": LEARNING_LINEAGE,
        "learning_resolved": len(learning_resolved),
        "external_predictions_resolved": len(external_resolved),
        "by_asset_class": by_class,
        "by_horizon": by_horizon,
        "by_asset_class_horizon": by_class_horizon,
    }
    mem["model_stats"] = finalize_stats(model_acc)
    mem["model_stats_by_asset_class"] = finalize_stats(model_by_class)
    mem["external_model_stats"] = finalize_stats(external_acc)
    return mem


def append_external_predictions(mem, votes_by_ticker, data):
    """Persist each declared external forecast independently; no Alpha selection filter."""
    today = str(pd.Timestamp.utcnow().date())
    ledger = mem.setdefault("external_predictions", [])
    existing = {p.get("id") for p in ledger}
    for ticker, votes in (votes_by_ticker or {}).items():
        s = get(data, ticker)
        if s.empty:
            continue
        entry = safe_num(s.iloc[-1])
        if entry is None or entry <= 0:
            continue
        for v in votes:
            h = int(v.get("horizon", 0) or 0)
            if h < 1:
                continue
            pid = f'EXT|{today}|{v.get("model_id")}|{v.get("source")}|{ticker}|{v.get("direction")}|{h}'
            if pid in existing:
                continue
            ledger.append({
                "id": pid, "learning_lineage": LEARNING_LINEAGE, "date": today,
                "model_id": v.get("model_id"), "source": v.get("source"),
                "ticker": ticker, "direction": v.get("direction"), "horizon": h,
                "confidence": v.get("confidence"), "forecast_timestamp": v.get("timestamp"),
                "entry": round(entry, 6), "entry_source": "YFINANCE_DAILY_ADJUSTED_CLOSE_OBSERVED",
                "outcome": "PENDING",
            })
            existing.add(pid)


def _episode_key_from_parts(ticker, direction, horizon, setup_family):
    return f"{ticker}|{direction}|{int(horizon)}|{setup_family or 'MIXED'}"


def _episode_key_from_prediction(p):
    h = safe_num(p.get("horizon"))
    if h is None or int(h) != h or int(h) < 1:
        return None
    ticker = str(p.get("ticker") or "")
    direction = str(p.get("direction") or "")
    if not ticker or direction not in ("LONG", "SHORT"):
        return None
    return _episode_key_from_parts(ticker, direction, int(h), p.get("horizon_setup_family") or "MIXED")


def migrate_prediction_episodes(mem):
    """Quarantine overlapping legacy PENDING rows before any learning/statistics."""
    rows = mem.setdefault("predictions", [])
    pending = []
    for p in rows:
        if p.get("outcome") != "PENDING":
            continue
        key = _episode_key_from_prediction(p)
        if key:
            p.setdefault("episode_key", key)
            p.setdefault("episode_id", f"EP|{p.get('date')}|{key}")
        p.setdefault("observations", 1)
        p.setdefault("state", "ACTIVE")
        pending.append(p)

    # Exact active duplicates: keep the first immutable forecast, quarantine copies.
    by_key = defaultdict(list)
    for p in pending:
        if p.get("episode_key"):
            by_key[p["episode_key"]].append(p)
    for group in by_key.values():
        if len(group) < 2:
            continue
        ordered = sorted(group, key=lambda x: (str(x.get("date") or ""), str(x.get("id") or "")))
        canonical = ordered[0]
        for dup in ordered[1:]:
            if dup.get("outcome") != "PENDING":
                continue
            canonical["observations"] = max(int(canonical.get("observations", 1) or 1), int(dup.get("observations", 1) or 1))
            canonical["last_seen"] = max(str(canonical.get("last_seen") or canonical.get("date") or ""), str(dup.get("date") or ""))
            dup["outcome"] = "EXCLUDED_DUPLICATE"
            dup["state"] = "SUPERSEDED"
            dup["superseded_by"] = canonical.get("id")
            dup["resolution_state"] = "LEGACY_DUPLICATE_EXCLUDED_FROM_STATS"

    # One active thesis per ticker. If setup/direction/horizon changed, the newest
    # survives as ACTIVE and older unresolved theses are audit-preserved as SUPERSEDED.
    by_ticker = defaultdict(list)
    for p in rows:
        if p.get("outcome") == "PENDING" and p.get("ticker"):
            by_ticker[p["ticker"]].append(p)
    for group in by_ticker.values():
        if len(group) < 2:
            continue
        ordered = sorted(group, key=lambda x: (str(x.get("date") or ""), str(x.get("id") or "")))
        newest = ordered[-1]
        for old in ordered[:-1]:
            old["outcome"] = "SUPERSEDED"
            old["state"] = "SUPERSEDED"
            old["superseded_by"] = newest.get("id")
            old["resolution_state"] = "LEGACY_OVERLAP_SUPERSEDED"
        newest["state"] = "ACTIVE"
    return mem


def add_prediction(mem, sig):
    """Create or refresh exactly one independent active forecast episode per ticker."""
    ledger = mem.setdefault("predictions", [])
    h = int(sig["horizon"])
    setup = sig.get("horizon_setup_family") or "MIXED"
    episode_key = _episode_key_from_parts(sig["ticker"], sig["direction"], h, setup)
    active = [p for p in ledger if p.get("outcome") == "PENDING" and p.get("ticker") == sig["ticker"]]

    for p in active:
        existing_key = p.get("episode_key") or _episode_key_from_prediction(p)
        if existing_key == episode_key:
            p["episode_key"] = episode_key
            p.setdefault("episode_id", f"EP|{p.get('date')}|{episode_key}")
            p["state"] = "ACTIVE"
            p["last_seen"] = sig["date"]
            p["observations"] = int(p.get("observations", 1) or 1) + 1
            p["latest_confidence_pct"] = sig.get("confidence_pct")
            p["latest_learning_adjustment"] = sig.get("learning_adjustment", 0)
            p["latest_model_version"] = MODEL_VERSION
            return {"action": "UPDATED_ACTIVE_EPISODE", "episode_id": p["episode_id"]}

    for p in active:
        p["outcome"] = "SUPERSEDED"
        p["state"] = "SUPERSEDED"
        p["superseded_on"] = sig["date"]
        p["resolution_state"] = "SUPERSEDED_BY_NEW_ACTIVE_THESIS"

    pid = f'EP|{sig["date"]}|{episode_key}|{MODEL_VERSION}'
    ledger.append({
        "id": pid, "episode_id": pid, "episode_key": episode_key,
        "state": "ACTIVE", "observations": 1, "first_seen": sig["date"], "last_seen": sig["date"],
        "model_version": MODEL_VERSION, "learning_lineage": LEARNING_LINEAGE,
        "date": sig["date"], "ticker": sig["ticker"], "sector": sig.get("sector"),
        "asset_class": sig.get("asset_class"), "cluster": sig.get("cluster"), "role": sig.get("role"),
        "direction": sig["direction"], "horizon": sig["horizon"],
        "horizon_state": sig.get("horizon_state"), "horizon_setup_family": sig.get("horizon_setup_family"),
        "horizon_policy_version": sig.get("horizon_policy_version"), "confidence_pct": sig.get("confidence_pct"),
        "forecast_probability": sig.get("forecast_probability"), "probability_state": sig.get("probability_state"),
        "entry": sig.get("entry_price", sig.get("price")), "entry_source": (sig.get("provenance") or {}).get("entry_price"),
        "risk_pct": sig.get("risk_pct"), "stop": sig.get("stop_price"), "target1": sig.get("target1_price"),
        "target2": sig.get("target2_price"), "data_quality_score": sig.get("data_quality_score"),
        "model_completeness_score": sig.get("model_completeness_score"), "model_votes": sig.get("model_votes", []),
        "external_model_votes": sig.get("external_model_votes", []), "learning_adjustment": sig.get("learning_adjustment", 0),
        "risk_regime": sig.get("risk_regime"), "rates_regime": sig.get("rates_regime"), "outcome": "PENDING",
    })
    return {"action": "CREATED_NEW_EPISODE", "episode_id": pid}


def _segment(mem, asset_class, direction, horizon=None):
    return [
        p for p in mem.get("predictions", [])
        if p.get("outcome") in ("HIT", "MISS")
        and p.get("learning_lineage") == LEARNING_LINEAGE
        and p.get("asset_class") == asset_class
        and p.get("direction") == direction
        and (horizon is None or int(p.get("horizon", -1) or -1) == int(horizon))
    ]


def empirical_probability(mem, asset_class, direction, horizon, confidence_pct):
    """Return an empirical/shrunk probability only after enough resolved examples."""
    seg = _segment(mem, asset_class, direction, horizon)
    c = safe_num(confidence_pct)
    if c is None:
        return None, {"state": "NO_CONFIDENCE_INDEX", "n": 0}
    # Local calibration window around the current confidence index.
    local = [p for p in seg if safe_num(p.get("confidence_pct")) is not None and abs(float(p["confidence_pct"]) - c) <= 7.5]
    sample = local if len(local) >= 30 else seg if len(seg) >= 60 else []
    if not sample:
        return None, {"state": "INSUFFICIENT_CALIBRATION_SAMPLE", "n": max(len(local), len(seg)), "required": 30}
    hits = sum(p["outcome"] == "HIT" for p in sample)
    n = len(sample)
    # Jeffreys prior shrinkage; explicitly model-derived from observed outcomes.
    prob = (hits + 0.5) / (n + 1.0)
    return round(prob, 4), {
        "state": "EMPIRICALLY_CALIBRATED", "n": n, "hits": hits,
        "method": "JEFFREYS_BETA_BINOMIAL", "scope": "LOCAL_CONFIDENCE_SAME_HORIZON" if sample is local else "ASSET_CLASS_DIRECTION_SAME_HORIZON",
        "horizon": int(horizon),
    }


def learning_adjustment(mem, asset_class, direction, horizon):
    """Error-first prequential overlay. Early learning can only penalize, not boost."""
    seg = _segment(mem, asset_class, direction, horizon)
    n = len(seg)
    if n < 20:
        return 0.0, {"state": "INSUFFICIENT_SAMPLE", "n": n, "required": 20}
    hit = sum(p["outcome"] == "HIT" for p in seg) / n
    # Benchmark 50% is deliberately simple and observable; no hidden target.
    delta = hit - 0.50
    if delta < 0:
        points = clip(delta * 20.0, -5.0, 0.0)
        state = "ERROR_PENALTY_ACTIVE"
    elif n >= 100:
        points = clip(delta * 8.0, 0.0, 1.0)
        state = "MATURE_SMALL_BONUS"
    else:
        points = 0.0
        state = "POSITIVE_EDGE_SHADOW_ONLY"
    return points, {"state": state, "n": n, "observed_hit_rate": round(hit, 4), "raw_delta_vs_50": round(delta, 4)}


def model_weight(mem, model_id, asset_class):
    seg = mem.get("model_stats_by_asset_class", {}).get(f"{model_id}|{asset_class}", {})
    glob = mem.get("model_stats", {}).get(model_id, {})
    n = int(seg.get("resolved", 0) or 0)
    hr = safe_num(seg.get("hit_rate"))
    if n < 20 or hr is None:
        n = int(glob.get("resolved", 0) or 0)
        hr = safe_num(glob.get("hit_rate"))
    if n < 30 or hr is None:
        return 1.0
    # Bounded. Bad models lose weight faster than good models gain it.
    if hr < 0.50:
        return clip(1.0 + (hr - 0.50) * 0.60, 0.82, 1.0)
    if n < 100:
        return 1.0
    return clip(1.0 + (hr - 0.50) * 0.20, 1.0, 1.06)


def external_model_reliability(mem, vote):
    """Track record for one declared external model/source. No sample => shadow only."""
    model_id = str(vote.get("model_id") or "")
    source = str(vote.get("source") or "")
    horizon = int(vote.get("horizon", 0) or 0)
    key = f"{model_id}|{source}|{horizon}"
    st = mem.get("external_model_stats", {}).get(key, {})
    n = int(st.get("resolved", 0) or 0)
    hr = safe_num(st.get("hit_rate"))
    return {
        "resolved": n,
        "hit_rate": round(hr, 4) if hr is not None else None,
        "mature_for_guard": bool(n >= 20 and hr is not None),
        "positive_boost_eligible": False,
    }


def historical_edge(mem, asset_class, direction, horizon):
    seg = _segment(mem, asset_class, direction, horizon)
    if len(seg) < 30:
        return None, [f"Campione insufficiente ({len(seg)}/30): nessun edge storico dichiarato"]
    hit = sum(p["outcome"] == "HIT" for p in seg) / len(seg)
    return round(hit * 100, 1), [f"Hit-rate empirico su {len(seg)} esiti risolti della stessa classe/direzione/orizzonte ({int(horizon)} sedute)"]


def score_from_z(z):
    if z is None or not np.isfinite(z):
        return None
    return clip(50.0 + 50.0 * math.tanh(z / 2.0), 0, 100)


def make_vote(model_id, score, as_of, weight=1.0):
    if score is None:
        return None
    direction = "LONG" if score >= 50 else "SHORT"
    conviction = abs(score - 50) * 2
    return {
        "model_id": model_id,
        "direction": direction,
        "score": round(score, 2),
        "conviction": round(conviction, 2),
        "weight": round(weight, 4),
        "as_of": as_of,
        "origin": "INTERNAL_SUBMODEL",
    }

def horizon_setup_family(votes, direction):
    """Classify the dominant current setup without inventing a forecast horizon."""
    family_map = {
        "TREND_Z": "TREND", "RELATIVE_Z": "RELATIVE", "BREAKOUT_63D": "BREAKOUT",
        "MEAN_REVERSION_20D": "MEAN_REVERSION", "RSI_MOMENTUM": "OSCILLATOR",
        "VOLUME_CONFIRM": "FLOW",
    }
    aligned = [v for v in (votes or []) if v.get("direction") == direction and safe_num(v.get("conviction")) is not None]
    if not aligned:
        return "MIXED"
    dominant = max(aligned, key=lambda v: safe_num(v.get("conviction"), 0) * safe_num(v.get("weight"), 1))
    return family_map.get(dominant.get("model_id"), "MIXED")


def _rolling_z_series(series, window=252, min_periods=80):
    m = series.rolling(window, min_periods=min_periods).mean()
    sd = series.rolling(window, min_periods=min_periods).std(ddof=0).replace(0, np.nan)
    return (series - m) / sd


def _historical_setup_mask(s, family, direction, volume=None, benchmark=None):
    """Historical occurrences of the current setup family using only contemporaneous data."""
    ret5 = s.pct_change(5)
    ret20 = s.pct_change(20)
    ma20 = s.rolling(20).mean()
    trend = s / ma20 - 1
    long_side = direction == "LONG"

    if family == "MEAN_REVERSION":
        z = _rolling_z_series(trend)
        mask = z <= -0.8 if long_side else z >= 0.8
    elif family == "BREAKOUT":
        lo = s.rolling(63, min_periods=63).min()
        hi = s.rolling(63, min_periods=63).max()
        pos = (s - lo) / (hi - lo).replace(0, np.nan)
        mask = (pos >= 0.70) & (ret20 > 0) if long_side else (pos <= 0.30) & (ret20 < 0)
    elif family == "OSCILLATOR":
        rv = rsi(s)
        mask = rv >= 55 if long_side else rv <= 45
    elif family == "FLOW" and volume is not None and not volume.empty:
        vz = _rolling_z_series(volume.astype(float), window=60, min_periods=20)
        mask = (vz >= 0.5) & (ret5 > 0) if long_side else (vz >= 0.5) & (ret5 < 0)
    elif family == "RELATIVE" and benchmark is not None and not benchmark.empty:
        aligned = pd.concat([s.rename("a"), benchmark.rename("b")], axis=1).dropna()
        rel = aligned["a"].pct_change(20) - aligned["b"].pct_change(20)
        rel = rel.reindex(s.index)
        mask = (rel > 0) & (s > ma20) if long_side else (rel < 0) & (s < ma20)
    else:
        mask = (ret20 > 0) & (s > ma20) if long_side else (ret20 < 0) & (s < ma20)
    return mask.reindex(s.index).fillna(False).astype(bool)


def _non_overlapping_positions(mask, horizon, total_len):
    positions = [int(i) for i in np.flatnonzero(mask.to_numpy()) if int(i) + int(horizon) < total_len]
    out, last = [], -10**9
    # no overlapping outcome windows: more conservative sample size and less pseudo-replication
    for i in positions:
        if i - last >= int(horizon):
            out.append(i)
            last = i
    return out


def _horizon_evidence(s, mask, direction, horizon):
    positions = _non_overlapping_positions(mask, horizon, len(s))
    if not positions:
        return {"horizon": int(horizon), "n": 0, "stable": False}
    vals = []
    for i in positions:
        entry = safe_num(s.iloc[i])
        exit_price = safe_num(s.iloc[i + horizon])
        if entry is None or exit_price is None or entry <= 0:
            continue
        raw = exit_price / entry - 1
        vals.append(raw if direction == "LONG" else -raw)
    n = len(vals)
    if not n:
        return {"horizon": int(horizon), "n": 0, "stable": False}
    a = np.asarray(vals, dtype=float)
    half = n // 2
    first = a[:half] if half else np.array([], dtype=float)
    second = a[half:] if half else np.array([], dtype=float)
    hr = float(np.mean(a > 0))
    h1 = float(np.mean(first > 0)) if len(first) else None
    h2 = float(np.mean(second > 0)) if len(second) else None
    avg = float(np.mean(a))
    med = float(np.median(a))
    stable = bool(
        n >= HORIZON_MIN_OBS and len(first) >= HORIZON_MIN_HALF_OBS and len(second) >= HORIZON_MIN_HALF_OBS
        and hr >= HORIZON_MIN_HIT_RATE and h1 is not None and h2 is not None
        and h1 >= HORIZON_MIN_HALF_HIT_RATE and h2 >= HORIZON_MIN_HALF_HIT_RATE
        and avg > 0 and med >= 0
    )
    return {
        "horizon": int(horizon), "n": n, "hit_rate": round(hr, 4),
        "avg_directional_return_pct": round(avg * 100, 4),
        "median_directional_return_pct": round(med * 100, 4),
        "first_half_hit_rate": round(h1, 4) if h1 is not None else None,
        "second_half_hit_rate": round(h2, 4) if h2 is not None else None,
        "min_half_hit_rate": round(min(h1, h2), 4) if h1 is not None and h2 is not None else None,
        "stable": stable,
    }


def select_adaptive_horizon(ticker, data, direction, votes):
    """Choose a horizon using setup-aware fallback plus pre-forecast historical evidence.

    The current forecast never uses future data. In historical backtests this function
    receives a truncated dataframe, so the same no-lookahead rule is preserved.
    """
    meta = ASSET_META[ticker]
    asset_class = meta["asset_class"]
    cfg = HORIZON_CONFIG.get(asset_class, HORIZON_CONFIG["EQUITY"])
    family = horizon_setup_family(votes, direction)
    candidates = [int(h) for h in cfg["candidates"]]
    fallback = int(cfg["fallback"].get(family, cfg["fallback"].get("MIXED", candidates[0])))
    if fallback not in candidates:
        fallback = min(candidates, key=lambda h: abs(h - fallback))

    s = get(data, ticker)
    if len(s) < 140:
        return fallback, {
            "policy_version": HORIZON_POLICY_VERSION, "state": "RULE_BASED_COLD_START",
            "setup_family": family, "fallback": fallback, "selected": fallback,
            "candidates": candidates, "reason": "insufficient_price_history_for_empirical_horizon_test",
            "evidence": [],
        }
    v = get(data, ticker, "Volume")
    b = get(data, meta.get("benchmark")) if meta.get("benchmark") else pd.Series(dtype=float)
    mask = _historical_setup_mask(s, family, direction, v, b)
    evidence = [_horizon_evidence(s, mask, direction, h) for h in candidates]
    stable = [e for e in evidence if e.get("stable")]
    fallback_ev = next((e for e in evidence if e["horizon"] == fallback), None)

    selected = fallback
    state = "RULE_BASED_FALLBACK_NO_STABLE_EDGE"
    reason = "no_candidate_passed_stability_gate"
    if stable:
        # Primary rank is worst-half directional accuracy; this resists one-regime winners.
        ranked = sorted(
            stable,
            key=lambda e: (safe_num(e.get("min_half_hit_rate"), 0), safe_num(e.get("hit_rate"), 0), -int(e["horizon"])),
            reverse=True,
        )
        top = ranked[0]
        fb_floor = safe_num((fallback_ev or {}).get("min_half_hit_rate"), None)
        top_floor = safe_num(top.get("min_half_hit_rate"), None)
        if top["horizon"] == fallback:
            selected, state, reason = fallback, "EMPIRICAL_SUPPORTS_FALLBACK", "fallback_passed_stability_gate"
        elif fallback_ev is None or not fallback_ev.get("stable"):
            selected, state, reason = int(top["horizon"]), "EMPIRICAL_STABLE_OVERRIDE", "fallback_failed_stability_gate"
        elif top_floor is not None and fb_floor is not None and top_floor >= fb_floor + HORIZON_SWITCH_MARGIN:
            selected, state, reason = int(top["horizon"]), "EMPIRICAL_STABLE_OVERRIDE", "stable_candidate_exceeded_fallback_margin"
        else:
            selected, state, reason = fallback, "EMPIRICAL_DIFFERENCE_TOO_SMALL", "kept_shorter_or_setup_consistent_fallback"

    return int(selected), {
        "policy_version": HORIZON_POLICY_VERSION, "state": state, "setup_family": family,
        "fallback": fallback, "selected": int(selected), "candidates": candidates,
        "reason": reason, "min_observations": HORIZON_MIN_OBS,
        "min_half_observations": HORIZON_MIN_HALF_OBS,
        "switch_margin": HORIZON_SWITCH_MARGIN, "evidence": evidence,
        "provenance": "MODEL_DERIVED_FROM_PRE_FORECAST_OBSERVED_DAILY_CLOSES",
    }



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

def score_asset(ticker, data, mem, regimes, external_votes_by_ticker):
    meta = ASSET_META[ticker]
    asset_class, cluster, benchmark = meta["asset_class"], meta["cluster"], meta.get("benchmark")
    role = meta.get("role", "CORE")
    s = get(data, ticker)
    if len(s) < 100:
        return None

    # -------------------- observed series --------------------
    ret5_series = s.pct_change(5)
    ret20_series = s.pct_change(20)
    ma20 = s.rolling(20).mean()
    trend_series = s / ma20 - 1
    ret5 = safe_num(ret5_series.iloc[-1])
    ret20 = safe_num(ret20_series.iloc[-1])
    ret60 = safe_num(s.pct_change(60).iloc[-1]) if len(s) >= 61 else None
    trend = safe_num(trend_series.iloc[-1])
    rv = safe_num(rsi(s).iloc[-1])
    vol_series = s.pct_change().rolling(20).std(ddof=0) * math.sqrt(252)
    vol = safe_num(vol_series.iloc[-1])
    vol_z = rolling_z_last(vol_series, window=252, min_obs=80)

    v = get(data, ticker, "Volume")
    vz = zlast(v) if not v.empty else None
    avg_volume20 = safe_num(v.tail(20).mean()) if len(v) >= 20 else None

    # Relative strength is omitted—not zero-filled—when benchmark data is absent.
    rel20 = rel_z = None
    if benchmark and benchmark != ticker:
        b = get(data, benchmark)
        if len(b) >= 100:
            aligned = pd.concat([s.rename("a"), b.rename("b")], axis=1).dropna()
            if len(aligned) >= 100:
                rel_series = aligned["a"].pct_change(20) - aligned["b"].pct_change(20)
                rel20 = safe_num(rel_series.iloc[-1])
                rel_z = rolling_z_last(rel_series, window=252, min_obs=80)

    # -------------------- heterogeneous internal models --------------------
    z5 = rolling_z_last(ret5_series, window=252, min_obs=80)
    z20 = rolling_z_last(ret20_series, window=252, min_obs=80)
    ztrend = rolling_z_last(trend_series, window=252, min_obs=80)
    available_trend = [(z, w) for z, w in [(z5, 0.35), (z20, 0.45), (ztrend, 0.20)] if z is not None]
    trend_z = None
    if available_trend:
        sw = sum(w for _, w in available_trend)
        trend_z = sum(z * w for z, w in available_trend) / sw
    trend_score = score_from_z(trend_z)
    relative_score = score_from_z(rel_z)

    oscillator_score = None
    if rv is not None:
        oscillator_score = clip(50 + (rv - 50) * 1.25, 0, 100)
        if rv > 82:
            oscillator_score = min(oscillator_score, 67)
        elif rv < 18:
            oscillator_score = max(oscillator_score, 33)

    flow_score = None
    if vz is not None and ret5 is not None and ret5 != 0:
        flow_score = clip(50 + math.copysign(min(abs(vz), 3.0) * 10.0, ret5), 0, 100)

    # Breakout model: current position inside the observed 63-session range.
    breakout_score = None
    if len(s) >= 63:
        lo63, hi63 = safe_num(s.tail(63).min()), safe_num(s.tail(63).max())
        if lo63 is not None and hi63 is not None and hi63 > lo63:
            breakout_score = clip((float(s.iloc[-1]) - lo63) / (hi63 - lo63) * 100, 0, 100)

    # Mean-reversion challenger deliberately disagrees with stretched trends.
    meanrev_score = None
    mz = rolling_z_last(trend_series, window=252, min_obs=80)
    if mz is not None:
        meanrev_score = clip(50 - 18 * mz, 0, 100)

    as_of = str(pd.Timestamp(s.index[-1]).date())
    vote_defs = [
        ("TREND_Z", trend_score),
        ("RELATIVE_Z", relative_score),
        ("RSI_MOMENTUM", oscillator_score),
        ("BREAKOUT_63D", breakout_score),
        ("MEAN_REVERSION_20D", meanrev_score),
    ]
    if asset_class in ("EQUITY", "ETF_EQUITY", "ETF_COMMODITY"):
        vote_defs.append(("VOLUME_CONFIRM", flow_score))

    votes = []
    for model_id, sc in vote_defs:
        if sc is None:
            continue
        w = model_weight(mem, model_id, asset_class)
        vote = make_vote(model_id, sc, as_of, w)
        if vote:
            votes.append(vote)
    if len(votes) < 3:
        return None

    signed = 0.0
    weight_conv = 0.0
    for vote in votes:
        sign = 1 if vote["direction"] == "LONG" else -1
        w = vote["weight"] * max(0.15, vote["conviction"] / 100)
        signed += sign * vote["conviction"] * w
        weight_conv += w
    if weight_conv <= 0:
        return None
    ensemble_signed = signed / weight_conv
    direction = "LONG" if ensemble_signed >= 0 else "SHORT"
    score = clip(50 + abs(ensemble_signed) / 2, 50, 100)

    # Adaptive target horizon. This sees only data available in `data`; during the
    # walk-forward `data` is truncated at the forecast date.
    horizon, horizon_meta = select_adaptive_horizon(ticker, data, direction, votes)

    same = sum(v["direction"] == direction for v in votes)
    consensus = same / len(votes)
    agreement_adj = 1.0 if consensus == 1 and len(votes) >= 4 else 0.0
    if consensus < 0.55:
        agreement_adj = -10.0
    elif consensus < 0.70:
        agreement_adj = -5.0

    # Volatility is treated as risk, not automatic opportunity.
    vol_adj = 0.0
    if vol_z is not None:
        if vol_z >= 2.0:
            vol_adj = -7.0
        elif vol_z >= 1.25:
            vol_adj = -4.0
        elif vol_z >= 0.60:
            vol_adj = -2.0

    # Macro regime overlay is penalty-first and uses only observed context series.
    macro_adj = 0.0
    risk_regime, rates_regime = regimes.get("risk_regime"), regimes.get("rates_regime")
    if asset_class in ("EQUITY", "ETF_EQUITY", "CRYPTO", "INDEX_FUTURE"):
        if direction == "LONG" and risk_regime == "STRESS": macro_adj = -5.0
        elif direction == "LONG" and risk_regime == "RISK_OFF": macro_adj = -2.5
        elif direction == "SHORT" and risk_regime == "RISK_ON": macro_adj = -2.0
    elif asset_class == "BTP":
        italy_bond_regime = regimes.get("italy_bond_regime")
        if direction == "LONG" and italy_bond_regime == "ITALY_BOND_SELL_OFF": macro_adj = -3.0
        elif direction == "SHORT" and italy_bond_regime == "ITALY_BOND_RALLY": macro_adj = -3.0
    elif asset_class == "ETF_BOND_GOV":
        if direction == "LONG" and rates_regime == "RATES_UP": macro_adj = -3.0
        elif direction == "SHORT" and rates_regime == "RATES_DOWN": macro_adj = -3.0
    elif asset_class == "ETF_BOND_CREDIT":
        if direction == "LONG" and risk_regime == "STRESS": macro_adj = -5.0
        elif direction == "LONG" and risk_regime == "RISK_OFF": macro_adj = -3.0

    expected = {
        "price_history": len(s) >= 100,
        "ret5": ret5 is not None,
        "ret20": ret20 is not None,
        "trend": trend is not None,
        "rsi": rv is not None,
        "volatility": vol is not None,
        "breakout_range": breakout_score is not None,
    }
    if benchmark and benchmark != ticker:
        expected["relative_strength"] = rel20 is not None
    if asset_class in ("EQUITY", "ETF_EQUITY", "ETF_COMMODITY"):
        expected["volume"] = vz is not None
    data_quality = round(100 * sum(expected.values()) / len(expected))

    base_conf = clip(50 + (score - 50) * 0.85, 50, 92)
    # Learning and calibration are horizon-specific: a good 3-day forecast is not
    # silently treated as evidence for a 20-day forecast.
    learn_adj, learn_meta = learning_adjustment(mem, asset_class, direction, horizon)
    math_adj, math_guard = mathematical_confidence_guard(direction, rv, vol, ret20, horizon)
    final_conf = clip(base_conf + agreement_adj + vol_adj + macro_adj + learn_adj + math_adj, 50, 92)
    if data_quality < 70:
        final_conf = min(final_conf, 64)

    # Optional external model gateway: audit-first. A declared model cannot move the
    # decision until its OWN forecasts have accumulated a resolved track record.
    ext_votes = []
    for raw_vote in external_votes_by_ticker.get(ticker, []):
        vote = dict(raw_vote)
        vote["track_record"] = external_model_reliability(mem, vote)
        ext_votes.append(vote)
    mature_ext = [v for v in ext_votes if int(v.get("horizon", 0) or 0) == int(horizon) and v.get("track_record", {}).get("mature_for_guard")]
    external_adj = 0.0
    if len(mature_ext) >= 2:
        sources = {v.get("source") for v in mature_ext}
        if len(sources) >= 2:
            same_ext = sum(v.get("direction") == direction for v in mature_ext) / len(mature_ext)
            if same_ext < 0.50:
                external_adj = -3.0
            # Agreement is recorded but never rewarded automatically: even a mature
            # historical track record is not proof of future independent alpha.
    final_conf = clip(final_conf + external_adj, 50, 92)

    # Probability is declared ONLY after empirical calibration has enough history
    # for the same class, direction and selected horizon, using the final confidence
    # after any allowed downside-only external guard.
    forecast_p, prob_meta = empirical_probability(mem, asset_class, direction, horizon, final_conf)

    # -------------------- risk envelope --------------------
    atr = atr14(data, ticker)
    price = float(s.iloc[-1])
    stop = target1 = target2 = risk_pct = None
    if atr is not None and price > 0:
        # Lower-volatility bond ETFs use the same observed ATR rule; no fake fixed %.
        dist = 1.5 * atr
        if direction == "LONG":
            stop, target1, target2 = price - dist, price + 1.4 * dist, price + 2.2 * dist
        else:
            stop, target1, target2 = price + dist, price - 1.4 * dist, price - 2.2 * dist
        risk_pct = abs(price - stop) / price * 100

    reward1_pct = abs(target1 - price) / price * 100 if target1 is not None else None
    reward2_pct = abs(target2 - price) / price * 100 if target2 is not None else None
    math_metrics = mathematical_trade_metrics(direction, vol, ret20, horizon, risk_pct, reward1_pct, reward2_pct)

    if vol_z is None:
        risk_level = "DATI PARZIALI"
    elif vol_z >= 2.0:
        risk_level = "MOLTO ALTO"
    elif vol_z >= 0.75:
        risk_level = "ALTO"
    elif vol_z <= -0.75:
        risk_level = "BASSO RELATIVO"
    else:
        risk_level = "MEDIO"

    model_completeness_fields = {
        "market_feature_set": data_quality >= 80,
        "risk_envelope": stop is not None and target1 is not None and target2 is not None,
        "empirical_probability": forecast_p is not None,
        "learning_segment_mature": learn_meta.get("state") not in ("INSUFFICIENT_SAMPLE", "INSUFFICIENT_CALIBRATION_SAMPLE", "NO_CONFIDENCE_INDEX"),
        "external_model_evidence": bool(ext_votes),
    }
    model_completeness_weights = {"market_feature_set": 40, "risk_envelope": 20, "empirical_probability": 15, "learning_segment_mature": 15, "external_model_evidence": 10}
    model_completeness_score = sum(model_completeness_weights[k] for k, ok in model_completeness_fields.items() if ok)
    decision_reliability_state = "CALIBRATION_LIMITED" if forecast_p is None else ("MODEL_COMPLETE" if model_completeness_score >= 80 else "PARTIAL_MODEL_EVIDENCE")

    edge, edge_reasons = historical_edge(mem, asset_class, direction, horizon)
    reasons = []
    if trend_score is not None:
        reasons.append(f"Trend standardizzato {trend_score:.0f}/100")
    if relative_score is not None:
        reasons.append(f"Forza relativa standardizzata {relative_score:.0f}/100")
    if breakout_score is not None:
        reasons.append(f"Posizione nel range 63g {breakout_score:.0f}/100")
    if consensus == 1:
        reasons.append(f"{len(votes)}/{len(votes)} sotto-modelli interni concordi")
    elif consensus < 0.70:
        reasons.append("Contro-modello in disaccordo: confidenza penalizzata")
    if macro_adj < 0:
        reasons.append(f"Regime macro: penalità prudenziale {macro_adj:.1f} pt")
    if learn_adj < 0:
        reasons.append("Memoria errori: penalità empirica attiva")
    if math_adj < 0:
        reasons.append(f"Math guard: penalità prudenziale {math_adj:.1f} pt")
    elif learn_meta.get("state") == "POSITIVE_EDGE_SHADOW_ONLY":
        reasons.append("Edge positivo osservato ma ancora in shadow: nessun bonus")
    if forecast_p is None:
        reasons.append("Probabilità non pubblicata: campione di calibrazione insufficiente")
    if external_adj < 0:
        reasons.append("Feed di modelli esterni con track record sufficiente e stesso orizzonte discordi: penalità prudenziale")
    hstate = horizon_meta.get("state")
    if hstate == "EMPIRICAL_STABLE_OVERRIDE":
        reasons.append(f"Orizzonte {horizon} sedute: override empirico stabile su storico pre-forecast")
    elif hstate == "EMPIRICAL_SUPPORTS_FALLBACK":
        reasons.append(f"Orizzonte {horizon} sedute: fallback confermato dallo storico")
    else:
        reasons.append(f"Orizzonte {horizon} sedute: fallback {horizon_meta.get('setup_family','MIXED')} · nessun override abbastanza robusto")

    return {
        "model_version": MODEL_VERSION,
        "ticker": ticker,
        "display_ticker": DISPLAY_TICKER.get(ticker, ticker),
        "name": DISPLAY_TICKER.get(ticker, ticker),
        "sector": meta["group"],
        "asset_class": asset_class,
        "instrument_type": asset_class,
        "cluster": cluster,
        "role": role,
        "benchmark": benchmark,
        "currency": infer_currency(ticker),
        "direction": direction,
        "score": round(score, 2),
        "confidence": round(final_conf / 100.0, 4),
        "confidence_pct": round(final_conf, 2),
        "raw_confidence_pct": round(base_conf, 2),
        "forecast_probability": forecast_p,
        "probability_state": prob_meta,
        "historical_edge": edge,
        "historical_edge_reasons": edge_reasons,
        "horizon": int(horizon),
        "horizon_state": horizon_meta.get("state"),
        "horizon_setup_family": horizon_meta.get("setup_family"),
        "horizon_policy_version": HORIZON_POLICY_VERSION,
        "horizon_profile": horizon_meta,
        "risk_regime": regimes.get("risk_regime"),
        "rates_regime": regimes.get("rates_regime"),
        "italy_bond_regime": regimes.get("italy_bond_regime"),
        "price": round(price, 6),
        "model_price": round(price, 6),
        "entry_price": round(price, 6),
        "ret5_pct": round(ret5 * 100, 3) if ret5 is not None else None,
        "ret20_pct": round(ret20 * 100, 3) if ret20 is not None else None,
        "ret60_pct": round(ret60 * 100, 3) if ret60 is not None else None,
        "rel20_pct": round(rel20 * 100, 3) if rel20 is not None else None,
        "rsi": round(rv, 2) if rv is not None else None,
        "volatility_pct": round(vol * 100, 3) if vol is not None else None,
        "volatility_z": round(vol_z, 3) if vol_z is not None else None,
        "volume_z": round(vz, 3) if vz is not None else None,
        "avg_volume20": round(avg_volume20, 2) if avg_volume20 is not None else None,
        "atr14": round(atr, 6) if atr is not None else None,
        "risk_level": risk_level,
        "risk_pct": round(risk_pct, 3) if risk_pct is not None else None,
        "stop_price": round(stop, 6) if stop is not None else None,
        "target1_price": round(target1, 6) if target1 is not None else None,
        "target2_price": round(target2, 6) if target2 is not None else None,
        "risk_reward_1": 1.4 if stop is not None else None,
        "risk_reward_2": 2.2 if stop is not None else None,
        "reward1_pct": round(reward1_pct, 3) if reward1_pct is not None else None,
        "reward2_pct": round(reward2_pct, 3) if reward2_pct is not None else None,
        "break_even_probability_t1": math_metrics.get("break_even_probability_t1"),
        "break_even_probability_t2": math_metrics.get("break_even_probability_t2"),
        "terminal_directional_probability_proxy": math_metrics.get("terminal_directional_probability_proxy"),
        "terminal_target1_probability_proxy": math_metrics.get("terminal_target1_probability_proxy"),
        "terminal_target2_probability_proxy": math_metrics.get("terminal_target2_probability_proxy"),
        "terminal_stop_probability_proxy": math_metrics.get("terminal_stop_probability_proxy"),
        "math_confidence_adjustment": math_adj,
        "math_guard": math_guard,
        "math_metrics": math_metrics,
        "yield_to_maturity_pct": None,
        "data_quality_score": data_quality,
        "data_quality_fields": expected,
        "model_completeness_score": model_completeness_score,
        "model_completeness_fields": model_completeness_fields,
        "decision_reliability_state": decision_reliability_state,
        "model_consensus": round(consensus, 3),
        "model_votes": votes,
        "external_model_votes": ext_votes,
        "external_model_adjustment": external_adj,
        "agreement_adjustment": agreement_adj,
        "volatility_adjustment": vol_adj,
        "macro_regime_adjustment": macro_adj,
        "learning_adjustment": round(learn_adj, 3),
        "learning_state": learn_meta,
        "instrument_reference": INSTRUMENT_REFERENCES.get(ticker),
        "reasons": reasons,
        "provenance": {
            "price": "YFINANCE_DAILY_ADJUSTED_CLOSE_OBSERVED",
            "entry_price": "YFINANCE_DAILY_ADJUSTED_CLOSE_OBSERVED",
            "quote_price": "MISSING_UNTIL_INTRADAY_OVERLAY",
            "ret5_pct": "MODEL_DERIVED_FROM_OBSERVED_CLOSES",
            "ret20_pct": "MODEL_DERIVED_FROM_OBSERVED_CLOSES",
            "rel20_pct": "MODEL_DERIVED_FROM_OBSERVED_BENCHMARK" if rel20 is not None else "MISSING",
            "rsi": "MODEL_DERIVED_RSI14" if rv is not None else "MISSING",
            "volatility_pct": "MODEL_DERIVED_REALIZED_VOL20" if vol is not None else "MISSING",
            "stop_price": "MODEL_DERIVED_ATR14_1.5X" if stop is not None else "MISSING",
            "target1_price": "MODEL_DERIVED_1.4R" if target1 is not None else "MISSING",
            "target2_price": "MODEL_DERIVED_2.2R" if target2 is not None else "MISSING",
            "forecast_probability": "EMPIRICALLY_CALIBRATED_FROM_RESOLVED_MEMORY_SAME_HORIZON" if forecast_p is not None else "MISSING_INSUFFICIENT_SAME_HORIZON_SAMPLE",
            "horizon": "MODEL_DERIVED_EMPIRICAL_PRE_FORECAST" if str(horizon_meta.get("state","")).startswith("EMPIRICAL") else "RULE_BASED_ASSET_CLASS_SETUP_FALLBACK",
            "yield_to_maturity_pct": "MISSING_REQUIRES_OFFICIAL_BOND_OR_ISSUER_FEED",
            "macro_regime_adjustment": "MODEL_DERIVED_FROM_OBSERVED_MARKET_CONTEXT" if macro_adj != 0 else "NO_ADJUSTMENT",
            "learning_adjustment": "RESOLVED_MEMORY_ONLY" if learn_adj != 0 else "NO_ADJUSTMENT",
            "math_confidence_adjustment": "MODEL_DERIVED_PENALTY_ONLY_RSI_VOLATILITY_EXTENSION",
            "math_metrics": "MODEL_DERIVED_GAUSSIAN_TERMINAL_PROXY_AND_BREAK_EVEN_ARITHMETIC",
            "external_model_votes": "DECLARED_TIMESTAMPED_EXTERNAL_FEED_SHADOW_ONLY" if ext_votes else "MISSING_NOT_CONNECTED_OR_NO_VALID_SIGNAL",
        },
    }

def latest_quotes(tickers):
    tickers = sorted(set(tickers))
    if not tickers:
        return {}, None
    try:
        q = yf.download(
            tickers, period="5d", interval="15m", auto_adjust=True,
            progress=False, group_by="column", threads=True, prepost=False,
        )
        if q is None or q.empty:
            return {}, None
        prices, stamps = {}, []
        for t in tickers:
            s = get(q, t, "Close")
            if not s.empty:
                prices[t] = float(s.iloc[-1])
                try:
                    ts = pd.Timestamp(s.index[-1])
                    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
                    stamps.append(ts)
                except Exception:
                    pass
        return prices, (max(stamps).isoformat() if stamps else None)
    except Exception:
        return {}, None


def select_diversified(rows, n=10, watch=False):
    if watch:
        candidates = [r for r in rows if r.get("data_quality_score", 0) >= 55]
    else:
        candidates = [
            r for r in rows
            if r.get("asset_class") != "CASH_EQUIVALENT"
            and r.get("data_quality_score", 0) >= 70
            and safe_num(r.get("confidence_pct"), 0) >= 68
        ]
    candidates = sorted(
        candidates,
        key=lambda r: (safe_num(r.get("confidence_pct"), 0), safe_num(r.get("score"), 0)),
        reverse=True,
    )
    if not candidates:
        return []

    chosen, seen = [], set()
    class_count, cluster_count, role_count = Counter(), Counter(), Counter()

    # Pass 1: strongest valid candidate per asset class. No weak filler is created.
    for r in candidates:
        cls = r["asset_class"]
        if cls in seen or len(chosen) >= n:
            continue
        if not watch and r.get("role") == "SATELLITE" and role_count["SATELLITE"] >= 1:
            continue
        chosen.append(r)
        seen.add(cls)
        class_count[cls] += 1
        cluster_count[r["cluster"]] += 1
        role_count[r.get("role", "CORE")] += 1

    # Pass 2: concentration caps at class, cluster, and satellite levels.
    for r in candidates:
        if r in chosen or len(chosen) >= n:
            continue
        cls, cl, role = r["asset_class"], r["cluster"], r.get("role", "CORE")
        if class_count[cls] >= (4 if watch else 2):
            continue
        if cluster_count[cl] >= (3 if watch else 2):
            continue
        if role == "SATELLITE" and role_count[role] >= (3 if watch else 1):
            continue
        chosen.append(r)
        class_count[cls] += 1
        cluster_count[cl] += 1
        role_count[role] += 1
    return sorted(chosen, key=lambda r: safe_num(r.get("confidence_pct"), 0), reverse=True)

def breadth_metrics(rows):
    if not rows:
        return {"score": 0, "asset_classes": 0, "clusters": 0, "hhi": None}
    counts = Counter(r.get("asset_class", "UNKNOWN") for r in rows)
    shares = [c / len(rows) for c in counts.values()]
    hhi = sum(s * s for s in shares)
    k = len(counts)
    # normalizzato 0-100: 100 = distribuzione uniforme tra le classi presenti
    min_hhi = 1 / k if k else 1
    score = 100 if k == 1 else clip((1 - hhi) / (1 - min_hhi) * 100, 0, 100)
    return {
        "score": round(score, 1), "asset_classes": k,
        "clusters": len(set(r.get("cluster") for r in rows)), "hhi": round(hhi, 4),
        "by_asset_class": dict(counts),
    }


def market_context(data):
    def last(t):
        ss = get(data, t)
        return safe_num(ss.iloc[-1]) if not ss.empty else None
    def ret20(t):
        ss = get(data, t)
        return safe_num((ss.iloc[-1] / ss.iloc[-21] - 1) * 100) if len(ss) >= 21 else None

    regimes = classify_regimes(data)
    return {
        "vix": round(regimes["vix"], 3) if regimes.get("vix") is not None else None,
        "spy_20d_pct": round(regimes["spy20_pct"], 3) if regimes.get("spy20_pct") is not None else None,
        "us10y_yield_pct": round(last("^TNX"), 3) if last("^TNX") is not None else None,
        "us10y_change_20d_bps": round(regimes["us10y_change_20d_bps"], 2) if regimes.get("us10y_change_20d_bps") is not None else None,
        "risk_regime": regimes.get("risk_regime"),
        "rates_regime": regimes.get("rates_regime"),
        "dxy": round(last("DX-Y.NYB"), 3) if last("DX-Y.NYB") is not None else None,
        "oil_20d_pct": round(ret20("CL=F"), 3) if ret20("CL=F") is not None else None,
        "gold_20d_pct": round(ret20("GC=F"), 3) if ret20("GC=F") is not None else None,
        "eurusd": round(last("EURUSD=X"), 6) if last("EURUSD=X") is not None else None,
        "italy_gov_bond_20d_pct": round(regimes["italy_gov_bond_20d_pct"], 3) if regimes.get("italy_gov_bond_20d_pct") is not None else None,
        "italy_bond_regime": regimes.get("italy_bond_regime"),
        "default_quote_currency": "USD",
        "provenance": "YAHOO_FINANCE_VIA_YFINANCE_OBSERVED_OR_DERIVED_FROM_OBSERVED_CLOSES",
    }



def load_backtest_summary():
    if not BACKTEST_OUT.exists():
        return None
    try:
        obj = json.loads(BACKTEST_OUT.read_text(encoding="utf-8"))
        if obj.get("model_version") != MODEL_VERSION:
            return {"state": "STALE_VERSION", "model_version": obj.get("model_version")}
        return {"state": "AVAILABLE", **obj.get("summary", {}), "generated_at": obj.get("generated_at")}
    except Exception:
        return {"state": "INVALID_FILE"}


def run_backtest():
    """Walk-forward non ottimizzato: ogni previsione vede solo dati disponibili fino a quel giorno."""
    tickers = sorted(set(CONTEXT + list(ASSET_META.keys()) + [m["benchmark"] for m in ASSET_META.values() if m.get("benchmark")]))
    data = yf.download(
        tickers, period="5y", interval="1d", auto_adjust=True,
        progress=False, group_by="column", threads=True,
    )
    if data is None or data.empty:
        raise RuntimeError("No market data for backtest")
    anchor = get(data, "SPY")
    if len(anchor) < 400:
        raise RuntimeError("Insufficient history for walk-forward")

    # Lightweight research budget: ~48 monthly walk-forward points across multiple years.
    # Each forecast uses the production adaptive-horizon selector; at each date it sees only truncated history.
    dates = list(anchor.index[300::20])[-48:]
    records = []
    bt_mem = {"predictions": [], "stats": {}, "model_stats": {}, "model_stats_by_asset_class": {}, "version": MODEL_VERSION}
    for dt in dates:
        hist = data.loc[:dt]
        # At every forecast date only already-observable prior outcomes may affect the adaptive overlay.
        bt_mem = resolve_memory(bt_mem, hist)
        rows = []
        regimes = classify_regimes(hist)
        for ticker in ASSET_META:
            x = score_asset(ticker, hist, bt_mem, regimes, {})
            if x:
                rows.append(x)
        signals = select_diversified(rows, n=10, watch=False)
        forecast_date = str(pd.Timestamp(dt).date())
        for sig in signals:
            sig["date"] = forecast_date
            add_prediction(bt_mem, sig)
            series = get(data, sig["ticker"])
            future = series[series.index > dt]
            h_raw = safe_num(sig.get("horizon"))
            if h_raw is None or int(h_raw) != h_raw or int(h_raw) < 1:
                continue
            h = int(h_raw)
            if len(future) < h:
                continue
            entry = safe_num(sig.get("price"))
            if entry is None or entry == 0:
                continue
            exit_price = float(future.iloc[h - 1])
            raw = exit_price / entry - 1
            pnl = raw if sig["direction"] == "LONG" else -raw
            gross_pct = round(pnl * 100, 4)
            net_pct, cost_bps = cost_adjusted_return_pct(gross_pct, sig.get("asset_class"))
            y = 1 if net_pct > 0 else 0
            p = safe_num(sig.get("forecast_probability"))
            vote_results = []
            for v in sig.get("model_votes", []):
                hit = _vote_hit(v.get("direction"), raw)
                vote_results.append({"model_id": v.get("model_id"), "direction": v.get("direction"), "hit": hit})
            records.append({
                "date": str(pd.Timestamp(dt).date()), "ticker": sig["ticker"],
                "asset_class": sig.get("asset_class"), "cluster": sig.get("cluster"),
                "direction": sig["direction"], "confidence_pct": sig.get("confidence_pct"),
                "horizon": h, "horizon_state": sig.get("horizon_state"),
                "horizon_setup_family": sig.get("horizon_setup_family"),
                "forecast_probability": p, "entry": round(entry, 6), "exit": round(exit_price, 6),
                "gross_return_pct": gross_pct, "estimated_round_trip_cost_bps": cost_bps,
                "return_pct": net_pct, "hit": bool(y), "model_votes": vote_results,
            })

    if not records:
        raise RuntimeError("Backtest produced no resolved signals")
    returns = np.array([r["return_pct"] for r in records], dtype=float)
    gross_returns = np.array([r["gross_return_pct"] for r in records], dtype=float)
    hits = np.array([1 if r["hit"] else 0 for r in records], dtype=float)
    probs = [(r["forecast_probability"], 1 if r["hit"] else 0) for r in records if r.get("forecast_probability") is not None]
    by_class = {}
    for cls in sorted({r["asset_class"] for r in records}):
        z = [r for r in records if r["asset_class"] == cls]
        by_class[cls] = {
            "n": len(z),
            "hit_rate": round(sum(r["hit"] for r in z) / len(z), 4),
            "avg_return_pct": round(float(np.mean([r["return_pct"] for r in z])), 4),
        }
    by_horizon = {}
    for h in sorted({int(r.get("horizon", 0) or 0) for r in records if int(r.get("horizon", 0) or 0) > 0}):
        z = [r for r in records if int(r.get("horizon", 0) or 0) == h]
        by_horizon[str(h)] = {
            "n": len(z),
            "hit_rate": round(sum(r["hit"] for r in z) / len(z), 4),
            "avg_return_pct": round(float(np.mean([r["return_pct"] for r in z])), 4),
        }
    horizon_states = Counter(r.get("horizon_state") or "UNKNOWN" for r in records)

    vote_acc = defaultdict(lambda: {"n": 0, "hits": 0})
    for r in records:
        for v in r.get("model_votes", []):
            if v.get("hit") is not None and v.get("model_id"):
                a = vote_acc[v["model_id"]]
                a["n"] += 1
                a["hits"] += int(v["hit"])
    submodels = {k: {"n": v["n"], "hit_rate": round(v["hits"] / v["n"], 4) if v["n"] else None} for k, v in vote_acc.items()}

    result = {
        "model_version": MODEL_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "method": "WALK_FORWARD_PREQUENTIAL_ADAPTIVE_HORIZON",
        "sampling": "APPROX_MONTHLY_20_TRADING_SESSIONS",
        "lookahead": False,
        "optimization_on_test": False,
        "transaction_costs_included": True,
        "transaction_cost_model": "EDGE_CORE_ASSET_CLASS_CONSERVATIVE_ROUND_TRIP_BPS",
        "summary": {
            "signals": len(records),
            "test_dates": len(set(r["date"] for r in records)),
            "hit_rate": round(float(hits.mean()), 4),
            "gross_avg_return_pct": round(float(gross_returns.mean()), 4),
            "avg_return_pct": round(float(returns.mean()), 4),
            "median_return_pct": round(float(np.median(returns)), 4),
            "brier": round(float(np.mean([(p - y) ** 2 for p, y in probs])), 5) if len(probs) >= 20 else None,
            "by_asset_class": by_class,
            "by_horizon": by_horizon,
            "horizon_selection_states": dict(horizon_states),
            "submodels": submodels,
        },
        "limitations": [
            "Current-universe / survivorship bias is not removed.",
            "Yahoo/yfinance adjusted market data are used; no institutional tick history is claimed.",
            "A conservative asset-class round-trip cost estimate is included; user-specific broker fees, taxes and market impact remain excluded.",
            "BTP exposure is represented by verified exchange-traded Italy government-bond ETFs, not individual MOT/ISIN bonds.",
            "Adaptive memory and horizon selection at each test date use only information already observable by that date.",
            "Horizon candidates are fixed ex ante by asset class; the selector cannot introduce new horizons from test outcomes.",
            "This workflow does not optimize parameters on the test window.",
        ],
        "records": records,
    }
    BACKTEST_OUT.parent.mkdir(exist_ok=True)
    BACKTEST_OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result["summary"], indent=2, ensure_ascii=False))


def validate_result(result):
    required = ["model_version", "updated_at", "data_quality", "market", "signals", "watchlist"]
    missing = [k for k in required if k not in result]
    if missing:
        raise ValueError(f"Missing required output keys: {missing}")
    if result.get("data_quality", {}).get("strict_no_fabrication") is not True:
        raise ValueError("strict_no_fabrication must be true")
    learning = result.get("model_learning", {})
    if learning.get("no_lookahead") is not True or learning.get("learning_lineage") != LEARNING_LINEAGE:
        raise ValueError("learning governance/lineage invalid")
    if safe_num(learning.get("max_positive_adjustment_points"), 99) > 1.0:
        raise ValueError("positive learning cap too permissive")
    external = result.get("external_models", {})
    if external.get("positive_boost_enabled") is not False:
        raise ValueError("external positive boost must remain disabled")
    for bucket in ("signals", "watchlist"):
        if not isinstance(result.get(bucket), list):
            raise ValueError(f"{bucket} must be a list")
        for row in result[bucket]:
            if row.get("ticker") not in ASSET_META:
                raise ValueError(f"Unknown ticker in {bucket}: {row.get('ticker')}")
            if row.get("direction") not in ("LONG", "SHORT"):
                raise ValueError(f"Invalid direction for {row.get('ticker')}")
            dq = safe_num(row.get("data_quality_score"))
            if dq is None or not (0 <= dq <= 100):
                raise ValueError(f"Invalid data quality for {row.get('ticker')}")
            p = safe_num(row.get("forecast_probability"))
            if p is not None and not (0 <= p <= 1):
                raise ValueError(f"Invalid probability for {row.get('ticker')}")
            h = safe_num(row.get("horizon"))
            cfg = HORIZON_CONFIG.get(row.get("asset_class"), HORIZON_CONFIG["EQUITY"])
            if h is None or int(h) != h or int(h) not in cfg["candidates"]:
                raise ValueError(f"Invalid adaptive horizon for {row.get('ticker')}: {row.get('horizon')}")
            if row.get("horizon_policy_version") != HORIZON_POLICY_VERSION:
                raise ValueError(f"Horizon policy mismatch for {row.get('ticker')}")
            hp = row.get("horizon_profile") or {}
            if int(hp.get("selected", -1) or -1) != int(h):
                raise ValueError(f"Horizon profile/selected mismatch for {row.get('ticker')}")
    sigs = result.get("signals", [])
    cc = Counter(r.get("asset_class") for r in sigs)
    kc = Counter(r.get("cluster") for r in sigs)
    if any(v > 2 for v in cc.values()):
        raise ValueError(f"asset-class concentration cap breached: {dict(cc)}")
    if any(v > 2 for v in kc.values()):
        raise ValueError(f"cluster concentration cap breached: {dict(kc)}")
    if sum(r.get("role") == "SATELLITE" for r in sigs) > 1:
        raise ValueError("satellite concentration cap breached")
    if result.get("edge_core", {}).get("version") != EDGE_CORE_VERSION:
        raise ValueError("edge core metadata missing")
    assert_public_snapshot(result)
    return True


def self_test():
    tickers = list(ASSET_META)
    assert len(tickers) == len(set(tickers)), "Duplicate tickers in universe"
    verified_btp = ("IITB.MI", "IITA.MI", "BTP10.MI", "BT27.MI")
    assert all(t in tickers for t in verified_btp), "Verified Italy government bond ETF coverage missing"
    assert all(INSTRUMENT_REFERENCES[t]["status"] == "VERIFIED_LISTING" for t in verified_btp)
    assert clip(-1, 0, 100) == 0 and clip(101, 0, 100) == 100
    assert _vote_hit("LONG", 0.01) is True and _vote_hit("SHORT", 0.01) is False
    fake = {
        "predictions": [
            {"outcome": "HIT", "learning_lineage": LEARNING_LINEAGE, "asset_class": "EQUITY", "direction": "LONG", "horizon": 5, "confidence_pct": 72},
            {"outcome": "MISS", "learning_lineage": LEARNING_LINEAGE, "asset_class": "EQUITY", "direction": "LONG", "horizon": 5, "confidence_pct": 72},
        ] * 15,
        "stats": {}, "model_stats": {}, "model_stats_by_asset_class": {}, "external_model_stats": {}, "external_predictions": [],
    }
    prob, meta = empirical_probability(fake, "EQUITY", "LONG", 5, 72)
    assert prob is not None and meta["state"] == "EMPIRICALLY_CALIBRATED"
    # Horizon policy invariants: every fallback is a declared candidate and the
    # selector cannot emit an undeclared horizon. Synthetic data are test-only.
    for cls, cfg in HORIZON_CONFIG.items():
        assert cfg["candidates"] == sorted(set(cfg["candidates"])), f"bad candidates {cls}"
        assert all(int(h) > 0 for h in cfg["candidates"]), f"non-positive horizon {cls}"
        assert all(int(h) in cfg["candidates"] for h in cfg["fallback"].values()), f"fallback outside candidates {cls}"
    idx = pd.bdate_range("2020-01-01", periods=700)
    px = pd.Series(100 * np.exp(np.linspace(0, 0.70, len(idx))) * (1 + 0.015*np.sin(np.arange(len(idx))/8)), index=idx)
    vol = pd.Series(1_000_000 + 100_000*np.sin(np.arange(len(idx))/9), index=idx)
    cols = pd.MultiIndex.from_product([["Close","High","Low","Volume"],["SPY"]])
    syn = pd.DataFrame(index=idx, columns=cols, dtype=float)
    syn[("Close","SPY")] = px
    syn[("High","SPY")] = px * 1.005
    syn[("Low","SPY")] = px * 0.995
    syn[("Volume","SPY")] = vol
    hv, hm = select_adaptive_horizon("SPY", syn, "LONG", [{"model_id":"TREND_Z","direction":"LONG","conviction":80,"weight":1}])
    assert hv in HORIZON_CONFIG["ETF_EQUITY"]["candidates"]
    assert hm["selected"] == hv and hm["policy_version"] == HORIZON_POLICY_VERSION
    ext, status = load_external_model_votes()
    assert isinstance(ext, dict) and isinstance(status, dict)
    print(json.dumps({
        "status": "PASS",
        "model_version": MODEL_VERSION,
        "universe": len(ASSET_META),
        "asset_classes": sorted({m["asset_class"] for m in ASSET_META.values()}),
        "btp_verified_proxies": list(verified_btp),
        "external_gateway": status.get("status"),
        "horizon_policy": HORIZON_POLICY_VERSION,
        "horizon_test_selected": hv,
        "horizon_test_state": hm.get("state"),
    }, indent=2, ensure_ascii=False))


def main():
    tickers = sorted(set(CONTEXT + list(ASSET_META.keys()) + [m["benchmark"] for m in ASSET_META.values() if m.get("benchmark")]))
    data = yf.download(
        tickers, period="5y", interval="1d", auto_adjust=True,
        progress=False, group_by="column", threads=True,
    )
    if data is None or data.empty:
        raise RuntimeError("No market data")

    mem = resolve_memory(migrate_prediction_episodes(load_memory()), data)
    regimes = classify_regimes(data)
    external_votes, external_status = load_external_model_votes()
    append_external_predictions(mem, external_votes, data)
    mem = resolve_memory(mem, data)
    ext_stats = mem.get("external_model_stats", {})
    external_status = {
        **external_status,
        "mode": "TRACK_RECORD_GATED_SHADOW_PLUS_DOWNSIDE_GUARD",
        "positive_boost_enabled": False,
        "independent_verification_claimed": False,
        "tracked_models": len(ext_stats),
        "mature_tracked_models": sum(1 for z in ext_stats.values() if int(z.get("resolved", 0) or 0) >= 20),
        "min_resolved_before_downside_guard": 20,
    }

    rows, unavailable = [], []
    for ticker in ASSET_META:
        x = score_asset(ticker, data, mem, regimes, external_votes)
        if x:
            rows.append(x)
        else:
            unavailable.append(ticker)

    # Intraday overlay only on strongest candidates to limit rate-limit fragility.
    overlay_candidates = sorted(rows, key=lambda x: safe_num(x.get("confidence_pct"), 0), reverse=True)[:35]
    quote_map, market_data_at = latest_quotes([x["ticker"] for x in overlay_candidates])
    for x in rows:
        qp = quote_map.get(x["ticker"])
        if qp is not None:
            old_entry = x["entry_price"]
            x["quote_price"] = round(float(qp), 6)
            x["entry_price"] = round(float(qp), 6)
            x["provenance"]["quote_price"] = "YFINANCE_15M_OBSERVED"
            x["provenance"]["entry_price"] = "YFINANCE_15M_OBSERVED"
            if x.get("stop_price") is not None and old_entry is not None:
                dist = abs(old_entry - x["stop_price"])
                if x["direction"] == "LONG":
                    x["stop_price"] = round(qp - dist, 6)
                    x["target1_price"] = round(qp + 1.4 * dist, 6)
                    x["target2_price"] = round(qp + 2.2 * dist, 6)
                else:
                    x["stop_price"] = round(qp + dist, 6)
                    x["target1_price"] = round(qp - 1.4 * dist, 6)
                    x["target2_price"] = round(qp - 2.2 * dist, 6)
                x["provenance"]["stop_price"] = "MODEL_DERIVED_ATR_DISTANCE_TRANSLATED_TO_OBSERVED_15M_ENTRY"
                x["provenance"]["target1_price"] = "MODEL_DERIVED_1.4R_FROM_TRANSLATED_STOP"
                x["provenance"]["target2_price"] = "MODEL_DERIVED_2.2R_FROM_TRANSLATED_STOP"
        else:
            x["quote_price"] = None

    signals = select_diversified(rows, n=10, watch=False)
    watchlist = select_diversified(rows, n=30, watch=True)

    today = str(pd.Timestamp.utcnow().date())
    for sig in signals:
        sig["date"] = today
        add_prediction(mem, sig)
    # Recompute counters after adding pending predictions; no future outcome is used.
    mem = resolve_memory(mem, data)
    save_memory(mem)

    engine_updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    class_counts = Counter(r["asset_class"] for r in rows)
    available_ratio = round(len(rows) / len(ASSET_META), 4) if ASSET_META else None
    btp_available = [t for t in ("IITB.MI", "IITA.MI", "BTP10.MI", "BT27.MI") if t not in unavailable]

    result = {
        "schema_version": "8.6",
        "model_version": MODEL_VERSION,
        "edge_core": {
            "version": EDGE_CORE_VERSION,
            "domain_profile": "FINANCE",
            "signal_lock": "ONE_ACTIVE_EPISODE_PER_TICKER_WITH_STABLE_SETUP_KEY",
            "fail_closed": True,
            "secrets_server_side": True,
            "watchdog_recovery": True,
        },
        "updated_at": engine_updated_at,
        "engine_updated_at": engine_updated_at,
        "market_data_at": market_data_at,
        "data_source": {
            "market_prices": "Yahoo Finance via yfinance",
            "type": "free public market-data aggregation",
            "institutional_feed": False,
            "official_bond_terms_feed": False,
            "warning": "Availability and delays depend on the upstream source; missing values are never imputed as observed facts.",
        },
        "execution_assumptions": {
            "cost_model": "CONSERVATIVE_ASSET_CLASS_ROUND_TRIP_BPS",
            "cost_bps_by_asset_class": {k: estimated_round_trip_cost_bps(k) for k in sorted({m['asset_class'] for m in ASSET_META.values()})},
            "taxes_included": False,
            "market_impact_included": False,
            "rule": "A signal is resolved HIT only when its directional return remains positive after the estimated round-trip cost.",
        },
        "data_quality": {
            "daily_model": True,
            "quote_overlay_15m": bool(quote_map),
            "quote_count": len(quote_map),
            "universe_count": len(rows),
            "requested_tickers": len(ASSET_META),
            "availability_ratio": available_ratio,
            "unavailable_tickers": unavailable,
            "by_asset_class": dict(class_counts),
            "strict_no_fabrication": True,
            "missing_is_null": True,
        },
        "market": market_context(data),
        "memory": mem.get("stats", {}),
        "backtest": load_backtest_summary(),
        "model_learning": {
            "mode": "PREQUENTIAL_ERROR_FIRST_GOVERNED",
            "no_lookahead": True,
            "min_penalty_segment_samples": 20,
            "min_probability_calibration_samples": 30,
            "min_positive_bonus_samples": 100,
            "learning_lineage": LEARNING_LINEAGE,
            "max_positive_adjustment_points": 1.0,
            "max_negative_adjustment_points": -5.0,
            "submodel_stats": mem.get("model_stats", {}),
            "submodel_stats_by_asset_class": mem.get("model_stats_by_asset_class", {}),
            "external_models": external_status,
            "note": "Errori verificati possono ridurre presto il peso; premi positivi richiedono campioni molto più maturi. Probabilità e learning sono segmentati anche per orizzonte: esiti a 3 giorni non vengono trattati come prova per 20 giorni.",
        },
        "model_governance": {
            "ensemble_families": ["TREND_Z", "RELATIVE_Z", "RSI_MOMENTUM", "BREAKOUT_63D", "MEAN_REVERSION_20D", "VOLUME_CONFIRM"],
            "champion_challenger": True,
            "adaptive_weight_bounds": [0.82, 1.06],
            "external_consensus_policy": "DECLARED_HORIZON_REQUIRED; SAME_SELECTED_HORIZON_ONLY; INDEPENDENT_LEDGER_TRACK_RECORD_GATED; AGREEMENT_NEVER_BOOSTS; MATURE_MULTI_SOURCE_DISAGREEMENT_MAY_ONLY_PENALIZE",
            "probability_policy": "NO_SAME_HORIZON_EMPIRICAL_SAMPLE_NO_PROBABILITY",
            "outcome_target": "ADAPTIVE_HORIZON_DIRECTIONAL_RETURN",
            "horizon_policy": {
                "version": HORIZON_POLICY_VERSION,
                "mode": "SETUP_AWARE_FALLBACK_PLUS_PRE_FORECAST_STABILITY_OVERRIDE",
                "candidate_horizons_by_asset_class": {k: v["candidates"] for k, v in HORIZON_CONFIG.items()},
                "minimum_observations_per_candidate": HORIZON_MIN_OBS,
                "minimum_observations_each_half": HORIZON_MIN_HALF_OBS,
                "minimum_hit_rate": HORIZON_MIN_HIT_RATE,
                "minimum_each_half_hit_rate": HORIZON_MIN_HALF_HIT_RATE,
                "switch_margin": HORIZON_SWITCH_MARGIN,
                "no_lookahead": True,
                "selection_claim": "FORECAST_HORIZON_IS_DERIVED_NOT_A_GUARANTEED_HOLDING_PERIOD",
            },
            "known_limitations": [
                "Current-universe survivorship bias is not removed in the lightweight backtest.",
                "No fundamentals, analyst estimates, order book, options IV surface, or official bond YTM are fabricated when absent.",
                "Daily bars cannot determine intraday event order when stop and target are both touched in the same session.",
                "Adaptive horizon selection is conditional on the current daily setup family and historical daily bars; it is not a guarantee that the selected holding window will outperform alternatives.",
            ],
        },
        "diversification": breadth_metrics(signals),
        "coverage": {
            "supported_asset_classes": [
                "EQUITY", "ETF_EQUITY", "ETF_BOND_GOV", "ETF_BOND_CREDIT", "BTP",
                "ETF_COMMODITY", "FX", "CASH_EQUIVALENT", "CRYPTO", "INDEX_FUTURE", "BTP_DIRECT"
            ],
            "btp_direct": "SUPPORTED_BY_SCHEMA_BUT_DISABLED_WITHOUT_RELIABLE_MOT_ISIN_FEED",
            "btp_proxies_verified": ["IITB.MI", "IITA.MI", "BTP10.MI", "BT27.MI"],
            "btp_proxies_available_now": btp_available,
            "bond_yield_fields": "NULL_WITH_CURRENT_FREE_FEED_UNLESS_OFFICIAL_SOURCE_IS_CONNECTED",
        },
        "external_models": external_status,
        "signals": signals,
        "watchlist": watchlist,
        "note": "Research/paper only. Observed, derived, learned, externally-declared and missing values are explicitly separated by provenance. External models are shadow-only until their own forecasts have enough resolved history; agreement never creates a positive boost.",
    }
    validate_result(result)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test()
    elif "--backtest" in sys.argv:
        run_backtest()
    else:
        main()
