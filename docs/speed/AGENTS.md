# Codex Speed Testing Instructions

Use this file whenever the task is about scalping speed, fast lanes, chart responsiveness, Active Trader quote lag, browser long tasks, or market-hours validation.

## Read Order

1. Root `AGENTS.md`.
2. `docs/speed/README.md`.
3. Latest dated `SCALPING_SPEED_RESULTS_*.md`.
4. `docs/SCALPING_SPEED_VALIDATION_PLAN.md` when running live validation.
5. The implementation plan most relevant to the suspected lane.

## Safety Rules

- Do not place live orders.
- Keep Auto Send off during speed validation unless the user explicitly asks for a disabled/rejection-flow test.
- Do not use preview/place/live-order endpoints unless the user explicitly asks and the behavior is preview-only or safely disabled.
- Leave analytical formulas unchanged: GEX, DEX, Vanna, Charm, Flow, key-level math, and alert semantics are not performance knobs.
- Keep the single-file `ezoptionsschwab.py` structure and vanilla JS approach.

## Standard Validation Setup

Use a non-conflicting traced port:

```bash
PORT=5017 GEX_PERF_TRACE=1 FLASK_DEBUG=0 FLASK_USE_RELOADER=0 python3 ezoptionsschwab.py 2>&1 | tee /tmp/gex-speed-5017.log
```

Open `http://127.0.0.1:5017/`, use SPY nearest 0DTE if available, otherwise nearest 1DTE, keep Active Trader open, and keep Auto Send off.

For browser traces, use the in-page Trace button when `GEX_PERF_TRACE=1`. It posts to `/perf/browser_trace` and writes `/tmp/gex-browser-perf-*.jsonl`.

## Current Speed Baseline

As of `docs/SCALPING_SPEED_RESULTS_2026-05-06_ATTRIBUTION_HISTORY_SYNC.md`:

- `GEX_PERF_TRACE=1` raises the browser trace cap to 50,000 events.
- `browser_long_task` events include attribution detail: nearby app spans, route/apply context, chart apply mode, selected timeframe, selected contract, trade rail state, and visible panel state.
- Trace-only `browser_event_loop_delay` events are emitted when the event loop delay exceeds 100 ms.
- The 5 min chart-history default is 10 days. Do not restore the previous 60-day default without a new measured reason.
- Dashboard expiry changes that remove the current trade-rail expiry should immediately clear stale selected contract state, force `/trade_chain`, reconnect the selected quote stream, and ignore stale stream messages.

## Protocol Tiers

Use the smallest protocol that answers the question.

| Tier | Use it when | Minimum coverage |
| --- | --- | --- |
| Static check | After code edits | `py_compile`, extracted inline JS `node --check`, `git diff --check` |
| Smoke trace | Verifying instrumentation or a narrow UI fix | 2-3 minutes with Active Trader open, one browser trace export |
| Focused experiment | Testing one variable such as chart history, overlay state, or rail visibility | Change one variable at a time; capture route bytes/timing, chart modes, long tasks, and selected quote spans |
| Full market-hours validation | Establishing a new speed baseline | 5 min call, 5 min put, 1 min sample, expiry switch, rail collapse/expand, order polling containment |
| Stress matrix | Reproducing intermittent jank/stale state | Repeated expiry switches, overlay matrix, right/trade rail states, volatile market window |

## Post-Change Full Validation Matrix

After the 5 min 10-day default and expiry-sync fix, the next full validation should cover:

1. SPY nearest 0DTE 5 min call, about 5 minutes.
2. SPY nearest 0DTE 5 min put, about 5 minutes.
3. SPY 1 min selected-contract sample, 3-5 minutes.
4. Dashboard expiry switch from 0DTE to 1DTE and back if both are available.
5. Trading rail collapse/expand and right rail collapse/expand while selected quote stream is live.
6. Orders open/closed with Auto Send off and no preview/place/live-order calls.

Required checks:

- 5 min Chart Data Window is `10` unless the test explicitly changes it.
- Selected option quote age stays `now` or `1s` while stream status is `Live`.
- `applyTradeSelectedQuoteMessage` p95 stays under `5 ms`.
- `renderTradeActiveTrader` p95 stays under `20 ms`.
- `/trade_chain` cache/stale paths stay under the validation-plan target.
- Browser long tasks over `100 ms` are attributed; normal quote ticks should not produce recurring long tasks.
- Server logs contain no preview/place/live-order endpoints.

## Lane Ownership

- `/price_stream/<ticker>` and `applyRealtimeQuote` / `applyRealtimeCandle`: live candle and last-price lane.
- `/trade/quote_stream/<contract_symbol>` and Active Trader render spans: selected option quote lane.
- `/trade_chain`: fast chain/context lane. Cache/stale fast paths should stay fast and non-overlapping.
- `/update_price`: price history/chart lane. Do not reduce payload before proving payload/parse is still the bottleneck.
- `/update`: slow analytics lane. Keep isolated unless traces prove it blocks fast lanes.
- `/trade/orders` and `/trade/account_details`: conditional order/account lane. Preserve slow-call/failure backoff.

## Required Evidence Before Optimizing

Capture server route logs, exported browser JSONL traces, response sizes, chart apply modes, quote/candle visible behavior, and whether Active Trader remains responsive.

Prefer fixing the lane that evidence identifies:

- Quote stale while stream is live: inspect selected option stream merge/reconnect first.
- Candles lag but routes are fine: inspect realtime candle/quote main-thread work.
- `/trade_chain` cache/stale paths slow: inspect cache lock, payload build, and DOM apply.
- `/update_price` parse or payload dominates after chart apply is optimized: then consider payload reduction.
- `/update` aligns with long tasks: split/defer analytics rendering before touching formulas.

## Result Notes

Every speed pass should end with a dated note under `docs/` that includes:

- Code changes.
- Tests run.
- Live retest setup.
- Server route p50/p95/max and response sizes.
- Browser span p50/p95/max, including chart modes.
- Visible lag/jank/stale quote behavior.
- Bottlenecks with evidence.
- Recommended next actions.
