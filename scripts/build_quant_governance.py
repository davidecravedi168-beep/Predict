#!/usr/bin/env python3
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

LATEST = Path('data/latest.json')
HEALTH = Path('data/model-health.json')
MEMORY = Path('data/memory.json')
OUT = Path('data/quant-governance.json')


def _f(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _i(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _ts(raw):
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
    except ValueError:
        return None


def _concentration(rows, key):
    vals = [str(x.get(key) or 'UNKNOWN') for x in rows]
    c = Counter(vals)
    n = len(vals)
    top = c.most_common(1)[0] if c else (None, 0)
    return {
        'counts': dict(c),
        'top': top[0],
        'top_share': round(top[1] / n, 4) if n else None,
    }


def _wilson_lower(hits, n, z=1.96):
    if n <= 0:
        return None
    p = hits / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    adj = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return round((centre - adj) / denom, 4)


def _confidence_band(row):
    c = _f(row.get('confidence_pct'))
    if c is None:
        c = _f(row.get('latest_confidence_pct'))
    if c is None:
        p = _f(row.get('forecast_probability') or row.get('probability'))
        c = p * 100 if p is not None else None
    if c is None:
        return 'UNKNOWN'
    if c < 60:
        return '<60'
    if c < 70:
        return '60-70'
    if c < 80:
        return '70-80'
    return '80+'


def _clean_forward_rows(memory_data):
    predictions = list((memory_data or {}).get('predictions') or [])
    out = []
    for row in predictions:
        outcome = str(row.get('outcome') or '').upper()
        if outcome not in {'HIT', 'MISS'}:
            continue
        if not row.get('resolved'):
            continue
        resolution_state = str(row.get('resolution_state') or '').upper()
        if any(x in resolution_state for x in ('EXCLUDED', 'SUPERSEDED', 'DUPLICATE', 'AMBIGUOUS')):
            continue
        # Forward segmentation intentionally excludes pre-versioned legacy rows.
        if not row.get('model_version'):
            continue
        out.append(row)
    return out


def _max_drawdown_pct(rows):
    equity = 0.0
    peak = 0.0
    worst = 0.0
    ordered = sorted(rows, key=lambda x: str(x.get('resolved') or x.get('date') or ''))
    for row in ordered:
        r = _f(row.get('return_pct'))
        if r is None:
            continue
        equity += r
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return round(worst, 4)


def _segment_metrics(name, rows):
    n = len(rows)
    hits = sum(1 for r in rows if str(r.get('outcome') or '').upper() == 'HIT')
    returns = [_f(r.get('return_pct')) for r in rows]
    returns = [x for x in returns if x is not None]
    probs = []
    for r in rows:
        p = _f(r.get('forecast_probability'))
        if p is None:
            continue
        y = 1.0 if str(r.get('outcome') or '').upper() == 'HIT' else 0.0
        probs.append((p, y))
    hit_rate = hits / n if n else None
    avg_return = sum(returns) / len(returns) if returns else None
    med_return = median(returns) if returns else None
    brier = sum((p - y) ** 2 for p, y in probs) / len(probs) if probs else None
    if n < 5:
        evidence = 'INSUFFICIENT'
    elif n < 20:
        evidence = 'OBSERVE'
    elif avg_return is not None and avg_return > 0 and hit_rate is not None and hit_rate >= 0.52:
        evidence = 'PROMISING'
    elif avg_return is not None and avg_return <= 0:
        evidence = 'WEAK'
    else:
        evidence = 'MIXED'
    return {
        'segment': str(name),
        'n': n,
        'hits': hits,
        'misses': n - hits,
        'hit_rate': round(hit_rate, 4) if hit_rate is not None else None,
        'wilson_lower_95': _wilson_lower(hits, n),
        'avg_return_pct_after_costs': round(avg_return, 4) if avg_return is not None else None,
        'median_return_pct_after_costs': round(med_return, 4) if med_return is not None else None,
        'max_drawdown_pct_flat_sequence': _max_drawdown_pct(rows),
        'brier': round(brier, 6) if brier is not None else None,
        'brier_n': len(probs),
        'evidence': evidence,
    }


def _segment_analysis(memory_data):
    rows = _clean_forward_rows(memory_data)
    dimensions = {
        'asset_class': lambda r: r.get('asset_class') or r.get('sector') or 'UNKNOWN',
        'regime': lambda r: f"{r.get('risk_regime') or 'UNKNOWN'} | {r.get('rates_regime') or 'UNKNOWN'}",
        'horizon': lambda r: f"{_i(r.get('horizon'))}d" if _i(r.get('horizon')) else 'UNKNOWN',
        'confidence_band': _confidence_band,
        'setup_family': lambda r: r.get('horizon_setup_family') or 'UNKNOWN',
    }
    result = {}
    ranked = []
    for dimension, key_fn in dimensions.items():
        groups = defaultdict(list)
        for row in rows:
            groups[str(key_fn(row))].append(row)
        metrics = [_segment_metrics(k, v) for k, v in groups.items()]
        metrics.sort(key=lambda x: (-x['n'], x['segment']))
        result[dimension] = metrics
        for m in metrics:
            if m['n'] >= 5:
                ranked.append({'dimension': dimension, **m})
    ranked.sort(
        key=lambda x: (
            x['evidence'] == 'PROMISING',
            x['wilson_lower_95'] if x['wilson_lower_95'] is not None else -1,
            x['avg_return_pct_after_costs'] if x['avg_return_pct_after_costs'] is not None else -1e9,
            x['n'],
        ),
        reverse=True,
    )
    return {
        'eligible_forward_resolved': len(rows),
        'minimum_n_to_display': 5,
        'minimum_n_for_directional_evidence': 20,
        'dimensions': result,
        'ranked_evidence': ranked[:12],
        'policy': 'Diagnostic only. Segment evidence must never auto-retune the main model; changes require separate out-of-sample validation.',
    }


def build_governance(d, health=None, now=None, memory_data=None):
    health = health or {}
    now = now or datetime.now(timezone.utc)
    mem = d.get('memory') or {}
    dq = d.get('data_quality') or {}
    bt = d.get('backtest') or {}
    signals = list(d.get('signals') or [])
    watch = list(d.get('watchlist') or [])
    rows = signals + watch

    resolved = _i(mem.get('resolved'))
    active = _i(mem.get('active'))
    independent = _i(mem.get('independent_episodes') or mem.get('total'))
    brier = _f(mem.get('brier'))
    brier_n = _i(mem.get('brier_n'))
    hit_rate = _f(mem.get('hit_rate'))

    source_raw = d.get('engine_updated_at') or d.get('updated_at')
    source_ts = _ts(source_raw)
    freshness_minutes = None
    if source_ts:
        freshness_minutes = round((now - source_ts).total_seconds() / 60, 1)

    completeness = [
        _f(x.get('model_completeness_score')) for x in rows
        if _f(x.get('model_completeness_score')) is not None
    ]
    avg_completeness = round(sum(completeness) / len(completeness), 4) if completeness else None
    min_completeness = round(min(completeness), 4) if completeness else None

    availability = _f(dq.get('availability_ratio'))
    bt_state = bt.get('state')
    bt_avg = _f(bt.get('avg_return_pct'))
    bt_hit = _f(bt.get('hit_rate'))

    if resolved >= 100 and brier_n >= 100:
        maturity = 'MATURE_FORWARD'
    elif resolved >= 30 and brier_n >= 30:
        maturity = 'CALIBRATING_FORWARD'
    elif resolved >= 10:
        maturity = 'EARLY_FORWARD'
    else:
        maturity = 'COLD_START'

    flags = []
    blockers = []
    if freshness_minutes is None:
        blockers.append('SOURCE_TIMESTAMP_MISSING_OR_INVALID')
    elif freshness_minutes < -5:
        blockers.append('SOURCE_TIMESTAMP_IN_FUTURE')
    elif freshness_minutes > 90:
        flags.append('SOURCE_DATA_OLDER_THAN_90_MINUTES')
    if availability is not None and availability < 0.95:
        flags.append('DATA_AVAILABILITY_BELOW_95_PERCENT')
    if avg_completeness is not None and avg_completeness < 0.80:
        flags.append('AVERAGE_MODEL_COMPLETENESS_BELOW_80_PERCENT')
    if min_completeness is not None and min_completeness < 0.60:
        flags.append('LOW_COMPLETENESS_ASSET_PRESENT')
    if resolved < 30:
        flags.append('FORWARD_SAMPLE_NOT_CALIBRATION_READY')
    if brier_n < 30:
        flags.append('BRIER_SAMPLE_NOT_CALIBRATION_READY')
    if bt_state == 'AVAILABLE' and bt_avg is not None and bt_avg <= 0:
        flags.append('LEGACY_BACKTEST_EXPECTANCY_NON_POSITIVE')
    if bt_state == 'AVAILABLE' and bt_hit is not None and bt_hit < 0.52:
        flags.append('LEGACY_BACKTEST_HIT_RATE_BELOW_52_PERCENT')
    if resolved == 0 and active >= 20:
        flags.append('ACTIVE_EPISODE_BUILDUP_WITHOUT_RESOLUTIONS')

    cluster = _concentration(signals, 'cluster')
    asset_class = _concentration(signals, 'asset_class')
    direction = _concentration(signals, 'direction')
    if cluster['top_share'] is not None and cluster['top_share'] > 0.50 and len(signals) >= 4:
        flags.append('SIGNAL_CLUSTER_CONCENTRATION_ABOVE_50_PERCENT')
    if direction['top_share'] is not None and direction['top_share'] > 0.80 and len(signals) >= 5:
        flags.append('SIGNAL_DIRECTION_CONCENTRATION_ABOVE_80_PERCENT')

    probabilities = [x for x in signals if x.get('forecast_probability') is not None]
    if resolved < 30 and probabilities:
        blockers.append('EMPIRICAL_PROBABILITY_PUBLISHED_BEFORE_MINIMUM_FORWARD_SAMPLE')

    segments = _segment_analysis(memory_data or {})
    if segments['eligible_forward_resolved'] != resolved:
        flags.append('SEGMENT_LEDGER_RESOLVED_COUNT_DIFFERS_FROM_SUMMARY')

    if blockers:
        status = 'BLOCKED'
        promotion = 'RESEARCH_ONLY'
        paper_risk_unit_cap = 0.0
    elif resolved < 30 or brier_n < 30:
        status = 'CAUTION'
        promotion = 'RESEARCH_ONLY'
        paper_risk_unit_cap = 0.25
    elif resolved < 100:
        status = 'CAUTION' if flags else 'HEALTHY'
        promotion = 'PAPER_ONLY'
        paper_risk_unit_cap = 0.50
    else:
        status = 'CAUTION' if flags else 'HEALTHY'
        promotion = 'PAPER_ONLY'
        paper_risk_unit_cap = 1.0

    return {
        'schema_version': '2.1',
        'generated_at': now.isoformat(),
        'source_updated_at': source_raw,
        'model_version': d.get('model_version'),
        'status': status,
        'promotion_state': promotion,
        'policy': {
            'minimum_forward_resolved_for_calibration': 30,
            'mature_forward_threshold': 100,
            'live_capital_auto_promotion': False,
            'paper_risk_unit_cap': paper_risk_unit_cap,
            'principle': 'Forward evidence outranks in-sample or legacy backtest evidence.',
        },
        'forward': {
            'maturity': maturity,
            'independent_episodes': independent,
            'active': active,
            'resolved': resolved,
            'hit_rate': hit_rate,
            'brier': brier,
            'brier_n': brier_n,
        },
        'forward_segments': segments,
        'data_quality': {
            'freshness_minutes': freshness_minutes,
            'availability_ratio': availability,
            'average_model_completeness': avg_completeness,
            'minimum_model_completeness': min_completeness,
            'unavailable_tickers': dq.get('unavailable_tickers') or [],
        },
        'signal_book': {
            'signals': len(signals),
            'watchlist': len(watch),
            'cluster_concentration': cluster,
            'asset_class_concentration': asset_class,
            'direction_concentration': direction,
        },
        'legacy_backtest': {
            'state': bt_state,
            'signals': _i(bt.get('signals')),
            'hit_rate': bt_hit,
            'avg_return_pct_after_costs': bt_avg,
        },
        'calibration': {
            'empirical_probabilities_published': len(probabilities),
            'allowed': resolved >= 30 and brier_n >= 30,
        },
        'flags': flags,
        'blockers': blockers,
        'expert_note': 'Research/paper governance only: no automatic promotion to real-money execution.',
    }


def main():
    d = json.loads(LATEST.read_text(encoding='utf-8'))
    h = json.loads(HEALTH.read_text(encoding='utf-8')) if HEALTH.exists() else {}
    m = json.loads(MEMORY.read_text(encoding='utf-8')) if MEMORY.exists() else {}
    out = build_governance(d, h, memory_data=m)
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(
        f"quant-governance: {out['status']} · {out['promotion_state']} · "
        f"forward={out['forward']['resolved']} resolved · segments={out['forward_segments']['eligible_forward_resolved']} · "
        f"flags={len(out['flags'])} · blockers={len(out['blockers'])}"
    )


if __name__ == '__main__':
    main()
