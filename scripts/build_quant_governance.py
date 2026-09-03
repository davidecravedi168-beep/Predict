#!/usr/bin/env python3
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

LATEST = Path('data/latest.json')
HEALTH = Path('data/model-health.json')
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


def build_governance(d, health=None, now=None):
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
        'schema_version': '2.0',
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
    out = build_governance(d, h)
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f"quant-governance: {out['status']} · {out['promotion_state']} · forward={out['forward']['resolved']} resolved · flags={len(out['flags'])} · blockers={len(out['blockers'])}")


if __name__ == '__main__':
    main()
