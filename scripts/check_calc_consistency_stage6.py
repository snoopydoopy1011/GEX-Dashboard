#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
import tempfile
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
import sys

import pandas as pd
import pytz

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import ezoptionsschwab as app


def _assert(condition, message):
    if not condition:
        raise AssertionError(message)


def _assert_close(actual, expected, message, tol=1e-9):
    if actual is None or abs(float(actual) - float(expected)) > tol:
        raise AssertionError(f"{message}: expected {expected}, got {actual}")


def _reset_module_state():
    app._SESSION_BASELINE.clear()
    app._SESSION_LEVEL_BASELINE.clear()
    app._SESSION_IV_BASELINE.clear()
    app._ALERT_COOLDOWNS.clear()
    app._IV_BUFFER.clear()
    app._LAST_WALLS.clear()
    app._VOL_SPIKE_CACHE.clear()


def _option_row(
    strike,
    expiry,
    bid,
    ask,
    *,
    gex=0.0,
    dex=0.0,
    vex=0.0,
    charm=0.0,
    oi=0.0,
    volume=0.0,
    iv=0.2,
):
    return {
        'strike': float(strike),
        'expiration': expiry,
        'expiration_date': expiry,
        'bid': float(bid),
        'ask': float(ask),
        'GEX': float(gex),
        'DEX': float(dex),
        'VEX': float(vex),
        'Charm': float(charm),
        'openInterest': float(oi),
        'volume': float(volume),
        'impliedVolatility': float(iv),
    }


@contextmanager
def _patched_attr(obj, name, value):
    original = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, original)


def test_expected_move_is_deterministic():
    calls = pd.DataFrame([
        _option_row(100, '2026-04-25', 3.0, 3.0, gex=15, oi=200, volume=90),
        _option_row(105, '2026-04-25', 1.0, 1.2, gex=8, oi=50, volume=20),
        _option_row(100, '2026-04-24', 1.0, 1.0, gex=12, oi=120, volume=70),
        _option_row(95, '2026-04-24', 0.8, 0.9, gex=6, oi=40, volume=15),
    ])
    puts = pd.DataFrame([
        _option_row(100, '2026-04-25', 3.0, 3.0, gex=10, oi=180, volume=80),
        _option_row(95, '2026-04-25', 1.1, 1.3, gex=7, oi=60, volume=25),
        _option_row(100, '2026-04-24', 1.0, 1.0, gex=9, oi=110, volume=65),
        _option_row(105, '2026-04-24', 0.9, 1.0, gex=5, oi=35, volume=18),
    ])

    selected_expiries = ['2026-04-25', '2026-04-24']
    snapshot = app.calculate_expected_move_snapshot(
        calls, puts, 100.0, selected_expiries=selected_expiries
    )
    shuffled_snapshot = app.calculate_expected_move_snapshot(
        calls.sample(frac=1.0, random_state=7),
        puts.sample(frac=1.0, random_state=11),
        100.0,
        selected_expiries=selected_expiries,
    )

    _assert(snapshot is not None, 'Expected move snapshot should exist')
    _assert(snapshot == shuffled_snapshot, 'Expected move should not depend on row order')
    _assert(snapshot['expiry'] == '2026-04-24', 'Expected move should use nearest selected expiry')
    _assert_close(snapshot['move'], 2.0, 'Expected move should use the chosen ATM straddle')

    later_expiry = ['2026-04-25']
    levels = app.compute_key_levels(
        calls, puts, 100.0, selected_expiries=later_expiry, strike_range=0.10
    )
    later_snapshot = app.calculate_expected_move_snapshot(
        calls, puts, 100.0, selected_expiries=later_expiry
    )
    _assert(later_snapshot is not None, 'Later-expiry snapshot should exist')
    _assert(levels['em_upper'] is not None and levels['em_lower'] is not None,
            'Key levels should expose expected-move bands')
    _assert_close(levels['em_upper']['price'], later_snapshot['upper'],
                  'Key levels EM upper should match shared selector output')
    _assert_close(levels['em_lower']['price'], later_snapshot['lower'],
                  'Key levels EM lower should match shared selector output')


def test_scope_aware_baselines_are_isolated():
    _reset_module_state()
    first_all = app._compute_session_deltas('SPY', 100.0, 50.0, scope_id='all')
    second_all = app._compute_session_deltas('SPY', 130.0, 55.0, scope_id='all')
    first_0dte = app._compute_session_deltas('SPY', 220.0, 80.0, scope_id='expiry:2026-04-24')
    second_0dte = app._compute_session_deltas('SPY', 260.0, 95.0, scope_id='expiry:2026-04-24')

    _assert_close(first_all['net_gex_vs_open'], 0.0, 'First scope baseline should start at zero')
    _assert_close(second_all['net_gex_vs_open'], 30.0, 'All-scope delta should keep its own baseline')
    _assert_close(first_0dte['net_gex_vs_open'], 0.0, '0DTE scope should start from its own baseline')
    _assert_close(second_0dte['net_gex_vs_open'], 40.0, '0DTE scope should not inherit all-scope baseline')

    level_seed = {
        'call_wall': 105.0,
        'put_wall': 95.0,
        'gamma_flip': 100.0,
        'em_upper': 102.0,
        'em_lower': 98.0,
    }
    level_shift = {
        'call_wall': 107.0,
        'put_wall': 94.0,
        'gamma_flip': 101.5,
        'em_upper': 103.0,
        'em_lower': 97.0,
    }
    base_levels = app._compute_level_session_deltas('SPY', level_seed, scope_id='all')
    shifted_levels = app._compute_level_session_deltas('SPY', level_shift, scope_id='all')
    isolated_levels = app._compute_level_session_deltas(
        'SPY', level_shift, scope_id='expiry:2026-04-24'
    )

    _assert_close(base_levels['call_wall'], 0.0, 'Initial level baseline should be zeroed')
    _assert_close(shifted_levels['call_wall'], 2.0, 'Level deltas should track within the same scope')
    _assert_close(isolated_levels['call_wall'], 0.0, 'Different scopes should get separate level baselines')


def test_chain_activity_respects_window_and_expiry_scope():
    _reset_module_state()
    calls = pd.DataFrame([
        _option_row(100, '2026-04-24', 1.0, 1.2, gex=12, dex=7, vex=1.2, charm=0.4, oi=100, volume=40),
        _option_row(130, '2026-04-24', 0.5, 0.7, gex=30, dex=12, vex=0.9, charm=0.2, oi=900, volume=500),
        _option_row(100, '2026-05-01', 2.0, 2.2, gex=20, dex=11, vex=1.0, charm=0.3, oi=700, volume=300),
    ])
    puts = pd.DataFrame([
        _option_row(100, '2026-04-24', 1.1, 1.3, gex=8, dex=-5, vex=-0.8, charm=-0.2, oi=60, volume=20),
        _option_row(70, '2026-04-24', 0.4, 0.5, gex=18, dex=-7, vex=-0.5, charm=-0.1, oi=800, volume=450),
        _option_row(100, '2026-05-01', 2.1, 2.3, gex=16, dex=-9, vex=-0.7, charm=-0.3, oi=500, volume=250),
    ])

    with ExitStack() as stack:
        stack.enter_context(_patched_attr(app, 'build_centroid_panel_payload', lambda ticker: None))
        stack.enter_context(_patched_attr(app, 'compute_iv_context', lambda *args, **kwargs: None))
        stack.enter_context(_patched_attr(app, 'build_flow_pulse_snapshot', lambda *args, **kwargs: []))
        stack.enter_context(_patched_attr(app, 'compute_flow_alerts', lambda *args, **kwargs: []))
        stack.enter_context(_patched_attr(
            app,
            'compute_scenario_gex',
            lambda *args, **kwargs: {'net_gex': 0.0, 'regime': 'Long Gamma'},
        ))
        stats = app.compute_trader_stats(
            calls,
            puts,
            100.0,
            strike_range=0.05,
            selected_expiries=['2026-04-24'],
            ticker='SPY',
            scope_id=app._build_stats_scope_id(
                0.05,
                selected_expiries=['2026-04-24'],
                scope_label='all',
            ),
        )

    chain_activity = stats['chain_activity']
    _assert(chain_activity is not None, 'Chain activity payload should exist')
    _assert_close(chain_activity['call_oi'], 100.0, 'Chain activity should exclude outside-window calls')
    _assert_close(chain_activity['put_oi'], 60.0, 'Chain activity should exclude outside-window puts')
    _assert_close(chain_activity['call_vol'], 40.0, 'Chain activity should exclude non-selected expiries')
    _assert_close(chain_activity['put_vol'], 20.0, 'Chain activity should stay on the active expiry/window scope')


def test_iv_surge_buffers_are_split_by_side_and_expiry():
    _reset_module_state()
    original_fetch = app._fetch_vol_spike_data
    app._fetch_vol_spike_data = lambda *args, **kwargs: {}
    try:
        now_iso = '2026-04-22T14:30:00Z'
        for _ in range(5):
            calls = pd.DataFrame([
                _option_row(100, '2026-04-24', 1.0, 1.2, iv=0.20),
                _option_row(100, '2026-04-25', 1.1, 1.3, iv=0.20),
            ])
            puts = pd.DataFrame([
                _option_row(100, '2026-04-24', 1.0, 1.2, iv=0.20, dex=-1.0),
            ])
            alerts = app.compute_flow_alerts(
                'SPY', calls, puts, now_iso, 100.0, strike_range=0.02, gate_strike_alerts=False
            )
            _assert(not alerts, 'Warm-up ticks should not emit IV alerts')

        spike_calls = pd.DataFrame([
            _option_row(100, '2026-04-24', 1.0, 1.2, iv=0.80),
            _option_row(100, '2026-04-25', 1.1, 1.3, iv=0.85),
        ])
        spike_puts = pd.DataFrame([
            _option_row(100, '2026-04-24', 1.0, 1.2, iv=0.75, dex=-1.0),
        ])
        alerts = app.compute_flow_alerts(
            'SPY', spike_calls, spike_puts, now_iso, 100.0, strike_range=0.02, gate_strike_alerts=False
        )
    finally:
        app._fetch_vol_spike_data = original_fetch

    alert_ids = sorted(alert['id'] for alert in alerts if alert['id'].startswith('iv_surge:'))
    _assert('iv_surge:call:2026-04-24:100' in alert_ids,
            'Call IV surge should emit its own alert id')
    _assert('iv_surge:put:2026-04-24:100' in alert_ids,
            'Put IV surge should not be blocked by the call at the same strike')
    _assert('iv_surge:call:2026-04-25:100' in alert_ids,
            'Different expiries at the same strike should keep separate buffers')


def test_vol_spike_uses_interval_deltas():
    _reset_module_state()
    est = pytz.timezone('US/Eastern')
    today = pd.Timestamp.now(tz=est).date().isoformat()
    now_ts = int(time.time())

    with tempfile.NamedTemporaryFile(suffix='.db') as tmp:
        conn = sqlite3.connect(tmp.name)
        try:
            conn.execute(
                '''
                CREATE TABLE interval_data (
                    ticker TEXT,
                    date TEXT,
                    strike REAL,
                    timestamp INTEGER,
                    net_volume REAL
                )
                '''
            )
            rows = [
                ('SPY', today, 100.0, now_ts - 180, 5000.0),
                ('SPY', today, 100.0, now_ts - 120, 5010.0),
                ('SPY', today, 100.0, now_ts - 60, 5020.0),
                ('SPY', today, 101.0, now_ts - 180, 0.0),
                ('SPY', today, 101.0, now_ts - 120, 100.0),
                ('SPY', today, 101.0, now_ts - 60, 1100.0),
            ]
            conn.executemany(
                'INSERT INTO interval_data (ticker, date, strike, timestamp, net_volume) VALUES (?, ?, ?, ?, ?)',
                rows,
            )
            conn.commit()
        finally:
            conn.close()

        original_connect = app.sqlite3.connect
        app.sqlite3.connect = lambda _path: original_connect(tmp.name)
        try:
            result = app._fetch_vol_spike_data('SPY', 100.5, 0.02)
        finally:
            app.sqlite3.connect = original_connect

    _assert(100.0 in result and 101.0 in result, 'Vol spike data should include both seeded strikes')
    _assert_close(result[100.0]['curr'], 10.0, 'Quiet cumulative series should resolve to a 10-contract delta')
    _assert_close(result[100.0]['avg20'], 10.0, 'Quiet cumulative series should average prior deltas, not totals')
    _assert_close(result[101.0]['curr'], 1000.0, 'Burst series should reflect the latest interval delta')
    _assert_close(result[101.0]['avg20'], 100.0, 'Burst series average should use prior interval deltas')


TESTS = [
    ('expected move determinism', test_expected_move_is_deterministic),
    ('scope-aware baselines', test_scope_aware_baselines_are_isolated),
    ('chain activity scope', test_chain_activity_respects_window_and_expiry_scope),
    ('iv-surge separation', test_iv_surge_buffers_are_split_by_side_and_expiry),
    ('vol-spike deltas', test_vol_spike_uses_interval_deltas),
]


def main():
    for label, fn in TESTS:
        _reset_module_state()
        fn()
        print(f'PASS {label}')
    print(f'PASS stage6 regression sweep ({len(TESTS)} checks)')


if __name__ == '__main__':
    main()
