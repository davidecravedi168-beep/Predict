#!/usr/bin/env python3
import json
from datetime import datetime, timezone
from pathlib import Path

SRC = Path('data/latest.json')
OUT = Path('data/model-health.json')


def pct(x):
    return None if x is None else round(float(x) * 100, 2)


def main():
    d = json.loads(SRC.read_text(encoding='utf-8'))
    mem = d.get('memory') or {}
    bt = d.get('backtest') or {}
    dq = d.get('data_quality') or {}
    resolved = int(mem.get('resolved') or 0)
    active = int(mem.get('active') or 0)
    signals = len(d.get('signals') or [])

    if resolved >= 100:
        maturity = 'MATURE'
    elif resolved >= 30:
        maturity = 'CALIBRATING'
    elif resolved >= 10:
        maturity = 'EARLY'
    else:
        maturity = 'COLD_START'

    flags = []
    if resolved < 30:
        flags.append('FORWARD_SAMPLE_BELOW_CALIBRATION_THRESHOLD')
    if bt.get('state') == 'AVAILABLE' and float(bt.get('avg_return_pct') or 0) <= 0:
        flags.append('BACKTEST_NET_EXPECTANCY_NON_POSITIVE')
    if bt.get('state') == 'AVAILABLE' and float(bt.get('hit_rate') or 0) < 0.52:
        flags.append('BACKTEST_HIT_RATE_BELOW_52_PERCENT')
    if float(dq.get('availability_ratio') or 0) < 0.95:
        flags.append('DATA_AVAILABILITY_BELOW_95_PERCENT')
    if resolved == 0 and active >= 20:
        flags.append('MANY_ACTIVE_EPISODES_WITHOUT_RESOLVED_FORWARD_SAMPLE')

    probability_published = any(x.get('forecast_probability') is not None for x in (d.get('signals') or []))
    if resolved < 30 and probability_published:
        flags.append('FAIL_CLOSED_BREACH_PROBABILITY_PUBLISHED_TOO_EARLY')

    health = {
        'schema_version': '1.0',
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'source_updated_at': d.get('engine_updated_at') or d.get('updated_at'),
        'model_version': d.get('model_version'),
        'operational': True,
        'forward': {
            'maturity': maturity,
            'independent_episodes': int(mem.get('independent_episodes') or mem.get('total') or 0),
            'active': active,
            'resolved': resolved,
            'hits': int(mem.get('hits') or 0),
            'misses': int(mem.get('misses') or 0),
            'hit_rate_pct': pct(mem.get('hit_rate')),
            'brier': mem.get('brier'),
            'brier_n': int(mem.get('brier_n') or 0),
            'calibration_threshold': 30,
            'probability_publication_allowed': resolved >= 30,
        },
        'backtest': {
            'state': bt.get('state'),
            'signals': int(bt.get('signals') or 0),
            'hit_rate_pct': pct(bt.get('hit_rate')),
            'avg_return_pct_after_costs': bt.get('avg_return_pct'),
            'gross_avg_return_pct': bt.get('gross_avg_return_pct'),
            'best_supported_horizon': max(
                (
                    {'days': int(k), **v}
                    for k, v in (bt.get('by_horizon') or {}).items()
                    if isinstance(v, dict) and int(v.get('n') or 0) >= 30
                ),
                key=lambda x: float(x.get('avg_return_pct') or -1e9),
                default=None,
            ),
        },
        'data': {
            'availability_ratio': dq.get('availability_ratio'),
            'unavailable_tickers': dq.get('unavailable_tickers') or [],
            'signals': signals,
            'watchlist': len(d.get('watchlist') or []),
        },
        'flags': flags,
        'status': 'BLOCKED' if any(f.startswith('FAIL_CLOSED_BREACH') for f in flags) else ('CAUTION' if flags else 'HEALTHY'),
        'note': 'Research/paper only. Forward evidence takes precedence over legacy/lightweight backtest for promotion decisions.'
    }
    OUT.write_text(json.dumps(health, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f"model-health: {health['status']} · maturity={maturity} · resolved={resolved} · flags={len(flags)}")


if __name__ == '__main__':
    main()
