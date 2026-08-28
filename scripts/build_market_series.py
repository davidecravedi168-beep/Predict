from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
LATEST = ROOT / "data" / "latest.json"
OUT = ROOT / "data" / "market-series.json"

MACRO = {
    "SPY": {"display": "S&P 500", "asset_class": "INDEX", "currency": "USD"},
    "QQQ": {"display": "Nasdaq 100", "asset_class": "INDEX", "currency": "USD"},
    "^VIX": {"display": "VIX", "asset_class": "VOLATILITY", "currency": "INDEX"},
    "^TNX": {"display": "US 10Y", "asset_class": "RATES", "currency": "%"},
    "EURUSD=X": {"display": "EUR/USD", "asset_class": "FX", "currency": "USD"},
    "GC=F": {"display": "Gold", "asset_class": "COMMODITY", "currency": "USD"},
    "CL=F": {"display": "WTI Oil", "asset_class": "COMMODITY", "currency": "USD"},
    "BTC-USD": {"display": "Bitcoin", "asset_class": "CRYPTO", "currency": "USD"},
}


def clean_number(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(value):
        return None
    return round(value, 8)


def close_series(frame: pd.DataFrame, symbol: str):
    if frame is None or frame.empty:
        return None
    try:
        if isinstance(frame.columns, pd.MultiIndex):
            if symbol in frame.columns.get_level_values(0):
                part = frame[symbol]
                if "Close" in part.columns:
                    return part["Close"]
            if symbol in frame.columns.get_level_values(-1):
                part = frame.xs(symbol, axis=1, level=-1)
                if "Close" in part.columns:
                    return part["Close"]
        if "Close" in frame.columns:
            return frame["Close"]
    except (KeyError, TypeError, ValueError):
        return None
    return None


def rows(series, limit: int):
    if series is None:
        return []
    out = []
    for ts, value in series.dropna().tail(limit).items():
        number = clean_number(value)
        if number is None:
            continue
        stamp = pd.Timestamp(ts)
        out.append([stamp.isoformat(), number])
    return out


def main():
    latest = json.loads(LATEST.read_text(encoding="utf-8"))
    metadata = dict(MACRO)

    ranked = []
    for source in (latest.get("signals") or [], latest.get("watchlist") or []):
        for item in source:
            ticker = item.get("ticker")
            if not ticker or ticker in metadata:
                continue
            metadata[ticker] = {
                "display": item.get("display_ticker") or item.get("name") or ticker,
                "asset_class": item.get("asset_class") or "UNKNOWN",
                "currency": item.get("currency") or latest.get("market", {}).get("default_quote_currency") or "",
            }
            ranked.append(ticker)
            if len(ranked) >= 16:
                break
        if len(ranked) >= 16:
            break

    symbols = list(MACRO) + [x for x in ranked if x not in MACRO]
    symbols = list(dict.fromkeys(symbols))[:24]

    errors = []
    daily = None
    intraday = None
    try:
        daily = yf.download(
            tickers=symbols,
            period="1y",
            interval="1d",
            auto_adjust=True,
            progress=False,
            group_by="ticker",
            threads=True,
        )
    except Exception as exc:  # upstream failure is represented, not fabricated
        errors.append(f"daily:{type(exc).__name__}")

    try:
        intraday = yf.download(
            tickers=symbols,
            period="5d",
            interval="15m",
            auto_adjust=True,
            progress=False,
            group_by="ticker",
            threads=True,
        )
    except Exception as exc:
        errors.append(f"intraday:{type(exc).__name__}")

    series = {}
    for symbol in symbols:
        d = rows(close_series(daily, symbol), 270)
        i = rows(close_series(intraday, symbol), 220)
        if not d and not i:
            continue
        meta = metadata.get(symbol, {})
        series[symbol] = {
            "ticker": symbol,
            "display": meta.get("display") or symbol,
            "asset_class": meta.get("asset_class") or "UNKNOWN",
            "currency": meta.get("currency") or "",
            "daily": d,
            "intraday": i,
        }

    payload = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "market_data_at": latest.get("market_data_at"),
        "source": "Yahoo Finance via yfinance",
        "provenance": "OBSERVED_OR_AUTO_ADJUSTED_MARKET_CLOSES_FROM_UPSTREAM_SOURCE",
        "strict_no_fabrication": True,
        "status": "OK" if series and not errors else ("DEGRADED" if series else "UNAVAILABLE"),
        "requested_symbols": len(symbols),
        "available_symbols": len(series),
        "errors": errors,
        "symbols": series,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"market-series: {payload['status']} {len(series)}/{len(symbols)} symbols")


if __name__ == "__main__":
    main()
