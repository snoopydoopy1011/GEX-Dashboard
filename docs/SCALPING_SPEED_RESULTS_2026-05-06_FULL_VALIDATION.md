# Scalping Speed Results - Full Validation - 2026-05-06

## Scope

Fuller market-hours validation after the short chart-pass retest in `docs/SCALPING_SPEED_RESULTS_2026-05-06_CHART_PASS.md`.

No live orders were placed. Auto Send stayed off. No preview, place, replace, cancel, or live-order endpoints were used.

## Code Changes

None.

## Tests Run

No unit tests were run because this was a live validation pass only. Validation was performed through the traced Flask server, in-app browser, server route logs, and exported browser traces.

## Live Retest Setup

Server:

```bash
PORT=5017 GEX_PERF_TRACE=1 FLASK_DEBUG=0 FLASK_USE_RELOADER=0 python3 ezoptionsschwab.py 2>&1 | tee /tmp/gex-speed-5017-full-validation.log
```

Token health before the run:

- `access_token_valid=true`
- `refresh_token_valid=true`
- `api_ok=true`

Browser state:

- URL: `http://127.0.0.1:5017/`
- Ticker: SPY
- Expiry: nearest 0DTE, `2026-05-06`; 1DTE `2026-05-07` was also available
- Auto Send: off
- Active Trader: open
- Primary samples:
  - 1 min chart with near-ATM call, `SPY 733C · 0DTE`, about 5 minutes
  - 5 min chart with near-ATM put, `SPY 733P · 0DTE`, about 5 minutes
  - Order-poll containment and context-switch stress after the timed samples

Artifacts:

- Server perf log: `/tmp/gex-speed-5017-full-validation.log`
- Final browser trace JSON: `/tmp/gex-browser-perf-20260506T191131Z-1778094691306.json`
- Final browser trace JSONL: `/tmp/gex-browser-perf-20260506T191131Z-1778094691306.jsonl`
- Earlier browser trace JSONL, before the delayed 1DTE reconnect completed: `/tmp/gex-browser-perf-20260506T190921Z-1778094561683.jsonl`

The final browser export hit the current 10,000-event ring-buffer cap. It covers `2026-05-06T18:57:55Z` to `2026-05-06T19:11:30Z` (about 13.6 minutes).

## Server Route Summary

Final browser-trace-aligned server window, from about `13:57:55` to `14:11:30` CT:

| Route | n | p50 ms | p95 ms | max ms | median bytes |
| --- | ---: | ---: | ---: | ---: | ---: |
| `/trade_chain` | 119 | 651.3 | 1425.0 | 2669.7 | 44613 |
| `/update_price` | 16 | 1042.5 | 1680.3 | 1776.6 | 5065774 |
| `/update` | 18 | 3199.2 | 4424.7 | 4683.5 | 256278 |
| `/trade/orders` | 8 | 629.8 | 824.7 | 833.6 | 251 |
| `/trade/account_details` | 2 | 382.4 | 473.3 | 483.4 | 545 |

Full server log:

| Route | n | p50 ms | p95 ms | max ms | median bytes |
| --- | ---: | ---: | ---: | ---: | ---: |
| `/trade_chain` | 231 | 652.0 | 1620.3 | 3451.7 | 43202 |
| `/update_price` | 30 | 1033.2 | 2757.8 | 3312.9 | 5064395 |
| `/update` | 32 | 3205.6 | 8390.9 | 9683.6 | 255060 |
| `/trade/orders` | 11 | 658.9 | 1648.8 | 2272.9 | 251 |
| `/trade/account_details` | 5 | 486.7 | 1546.0 | 1773.0 | 545 |

## Trade Chain Cache Outcomes

Final browser-trace-aligned server window:

| Outcome | n | p50 ms | p95 ms | max ms | median bytes |
| --- | ---: | ---: | ---: | ---: | ---: |
| `cache_hit` | 3 | 20.9 | 23.0 | 23.2 | 52581 |
| `cache_hit_prelock` | 39 | 16.0 | 36.1 | 43.2 | 43202 |
| `fetched_stored` | 70 | 802.5 | 1622.4 | 2669.7 | 44621 |
| `stale_inflight` | 7 | 15.8 | 19.8 | 21.1 | 44644 |

Fast-lane cache and stale paths stayed fast. The slower `/trade_chain` samples were fresh Schwab fetches, dominated by upstream chain/quote work, not by payload build:

| Server subspan | n | p50 ms | p95 ms | max ms |
| --- | ---: | ---: | ---: | ---: |
| `/trade_chain fetch_chain_ms` | 132 | 511.4 | 1474.3 | 2420.5 |
| `/trade_chain get_current_price_ms` | 132 | 282.4 | 695.6 | 1599.4 |
| `/trade_chain build_trading_chain_payload_ms` | 230 | 10.5 | 19.7 | 26.3 |
| `/trade_chain create_fast_strike_profile_payload_ms` | 224 | 5.8 | 10.4 | 25.1 |

## Browser Summary

Final browser trace:

| Span | n | p50 ms | p95 ms | max ms |
| --- | ---: | ---: | ---: | ---: |
| `applyRealtimeQuote` | 587 | 0.2 | 0.4 | 0.8 |
| `applyRealtimeCandle` | 14 | 0.9 | 2.2 | 2.4 |
| `applyTradeSelectedQuoteMessage` | 804 | 0.5 | 0.9 | 4.5 |
| `renderTradeActiveTrader` | 1593 | 2.0 | 4.8 | 9.1 |
| `buildTradeActiveLadderHtml` | 370 | 0.2 | 0.3 | 0.4 |
| `fetch:/trade_chain` | 114 | 652.2 | 1465.7 | 2674.8 |
| `apply:/trade_chain` | 115 | 12.6 | 16.9 | 18.5 |
| `fetch:/update_price` | 15 | 1233.9 | 1692.8 | 1782.9 |
| `parse:/update_price` | 15 | 5.5 | 6.9 | 7.2 |
| `apply:/update_price` | 15 | 35.3 | 394.1 | 426.4 |
| `fetch:/update` | 17 | 3227.8 | 4444.0 | 4689.6 |
| `apply:/update` | 17 | 60.8 | 80.2 | 80.4 |
| `updateCharts` | 17 | 49.2 | 55.0 | 55.2 |
| `browser_long_task` | 147 | 358.0 | 390.1 | 434.0 |

Chart apply modes:

| Span | Mode | n | p50 ms | p95 ms | max ms |
| --- | --- | ---: | ---: | ---: | ---: |
| `applyPriceData` | `incremental` | 13 | 34.4 | 37.6 | 38.1 |
| `renderTVPriceChart` | `incremental` | 13 | 14.9 | 16.0 | 16.2 |
| `applyTVSeriesData` | `incremental` | 13 | 13.2 | 14.3 | 14.4 |
| `applyPriceData` | `full_context` | 2 | 402.9 | 423.7 | 426.0 |
| `renderTVPriceChart` | `full_context` | 2 | 380.0 | 399.3 | 401.4 |
| `applyTVSeriesData` | `full_context` | 2 | 27.4 | 30.7 | 31.1 |

No `full_fallback` chart modes appeared in the final trace.

## Live Behavior Observed

- 1 min call sample: selected quote age stayed `now` or `1s`; last price moved from about `$732.81` to `$733.00`; B/M/A moved from about `0.42 / 0.425 / 0.43` to `0.50 / 0.505 / 0.51`.
- 5 min put sample: selected timeframe was confirmed as `5 min`; quote age stayed `now` or `1s`; B/M/A moved while `/trade_chain`, `/update_price`, and `/update` were in flight.
- Underlying price stream stayed connected through the run. `/price_stream/SPY` connected once at page load.
- Selected option quote streams connected for the call, the put, the rail collapse/expand reconnect, and eventually the 1DTE put:
  - `SPY 260506C00733000`
  - `SPY 260506P00733000`
  - `SPY 260507P00733000`
- Active Trader remained usable. The ladder and B/M/A continued updating between `/trade_chain` polls.
- No stale selected quote mutation was observed after the call-to-put switch or rail collapse/expand.
- Rail collapse made quote age rise briefly to `9s`; after expand it returned to `now` and continued updating.

## Order Polling Containment

- Auto Send was explicitly set unchecked.
- A ladder price was staged at `0.41`; the UI showed `STAGED X1`; no preview/place endpoint was called.
- With Orders collapsed, the staged marker did not start continuous `/trade/orders` polling.
- Opening Orders changed the toggle to `HIDE` and started `/trade/orders` polling; quote age remained current.
- Closing Orders and clearing the staged marker stopped polling after the already-scheduled requests drained.
- Server log check found no preview/place/live-order endpoint usage.

## Bottlenecks And Issues

1. Browser long tasks remain above the target.
   - Final trace: `browser_long_task` n=147, p50 `358.0 ms`, p95 `390.1 ms`, max `434.0 ms`; 127 events were over 100 ms.
   - Some long tasks align with expected full-context chart work. Example: a 434 ms long task occurred next to `renderTVPriceChart full_context=401.4 ms` and `applyPriceData full_context=426.0 ms`.
   - Many long tasks also occur near quote ticks even when measured quote/Active Trader spans are tiny, so the remaining source needs better attribution before optimizing.

2. `/update` is still slow, but it stayed isolated during this run.
   - Final aligned window: `/update` p50 `3199.2 ms`, p95 `4424.7 ms`.
   - Full log p95 reached `8594.3 ms`.
   - Server subspans point mainly to upstream and slow analytics work: `fetch_chain_ms` p50 `1311.5 ms` / p95 `4043.3 ms`, `analytics_price_history_ms` p50 `725.4 ms` / p95 `1971.0 ms`, and `quote_expected_move_ms` p50 `384.8 ms` / p95 `2216.9 ms`.
   - Despite that, `applyRealtimeQuote`, `applyRealtimeCandle`, and Active Trader spans remained small.

3. Fresh `/trade_chain` refreshes are upstream-bound; cache/stale fast lanes are healthy.
   - `fetched_stored` p95 was `1622.4 ms` in the final aligned window.
   - `cache_hit_prelock` p95 was `36.1 ms`; `stale_inflight` p95 was `19.8 ms`.
   - Payload build stayed low, with `build_trading_chain_payload_ms` p95 `19.6 ms`.

4. `/update_price` payload is still large, but parsing is not the bottleneck.
   - Median bytes were about `5.1 MB`.
   - Browser parse p95 was `6.9 ms`; nested `parse:price_chart_payload` p95 was `8.4 ms`.
   - Incremental chart work stayed good. Full-context chart work is still about 400 ms on context changes.

5. Dashboard expiry to trade-rail expiry sync is delayed.
   - The dashboard expiry checkbox changed to `2026-05-07`, but the trade rail initially stayed on `2026-05-06` and kept `SPY 733P · 0DTE`.
   - The 1DTE quote stream eventually connected and the rail showed `SPY 733P · 1DTE`, but not immediately.
   - This is a context-switch UX/state issue rather than a route-latency bottleneck.

## Interpretation

The core scalping lanes passed the longer validation:

- Underlying quote and candle SSE stayed live while `/update`, `/update_price`, and `/trade_chain` were in flight.
- Selected option quote SSE stayed live for the near-ATM call and put, and the Active Trader ladder updated between `/trade_chain` polls.
- Active Trader render work was well below target. `renderTradeActiveTrader` p95 was `4.8 ms`, and `buildTradeActiveLadderHtml` p95 was `0.3 ms`.
- `/trade_chain` fast cache paths were healthy. `cache_hit_prelock` p95 was `36.1 ms`; `stale_inflight` p95 was `19.8 ms`.
- Order polling behaved correctly. Polling did not start for a staged local ladder marker, started when the Orders panel was open, and stopped after Orders was closed and the marker was cleared.

The remaining work is narrower:

- Browser long tasks still exceed the target and need better attribution before changing behavior.
- Full-context chart refresh is still expensive, but steady-state incremental chart refresh is fast.
- `/update` remains slow, but it did not block fast quote, candle, or Active Trader work in this run.
- Context switching from dashboard expiry to trade-rail expiry eventually worked, but not immediately enough for a scalping workflow.

## Next Implementation Plan

### 1. Attribute Browser Long Tasks Before Optimizing

Problem:

- Final trace: `browser_long_task` n=147, p50 `358.0 ms`, p95 `390.1 ms`, max `434.0 ms`.
- Some long tasks align with full-context chart work, but many occur near quote ticks where measured app spans are tiny.
- The current trace tells us long tasks exist, but not which app operation or browser phase caused each one.

Implementation approach:

1. Add a lightweight long-task correlation buffer in the frontend.
   - Track the last 20-50 `gexPerfStart`/`gexPerfEnd` spans with `name`, `duration_ms`, `start_ms`, `end_ms`, and key `detail`.
   - When `PerformanceObserver` reports a `browser_long_task`, attach nearby completed spans in a small time window, for example `start_ms - 250 ms` through `end_ms + 250 ms`.
   - Include `document.visibilityState`, selected timeframe, selected contract, trade rail collapsed/open, active right-rail tab, and whether a full-context or incremental chart apply just occurred.

2. Add a tiny event-loop delay sampler while perf trace is enabled.
   - Use a repeated timer, for example every `250 ms`, that records when actual delay is materially higher than expected.
   - Emit a `browser_event_loop_delay` trace event only when delay exceeds a threshold, for example `100 ms`.
   - This can catch stalls that do not line up cleanly with named spans.

3. Add explicit browser spans around likely unmeasured work.
   - Candidate areas: chart resize/layout handlers, rail collapse/expand handling, `renderChartContextStrip`, `renderRailAlerts`, `renderFlowPulse`, `renderTradeJournal`, and any repeated scroll-preservation wrapper.
   - Do not optimize these first. Add spans only where current trace lacks visibility.

Focused verification:

- Run `node --check` on extracted inline scripts after instrumentation.
- Start traced server and export a short browser trace.
- Confirm every `browser_long_task` event includes correlation detail or explicitly says no nearby app span was found.
- Confirm normal quote ticks still have low overhead: `applyTradeSelectedQuoteMessage` p95 under `5 ms`, `renderTradeActiveTrader` p95 under `20 ms`.

Success criteria:

- At least 80% of long tasks over `100 ms` have a nearby app span, route apply, chart mode, or event-loop delay context.
- If long tasks remain unattributed, the trace should prove they are not caused by the named app hot paths.

### 2. Test Chart History Window Size Before Payload Work

Hypothesis:

- Reducing candle history for scalping timeframes may improve `/update_price` server time, response bytes, full-context chart apply time, and maybe browser long-task frequency.
- Current validation still showed about `5.1 MB` median `/update_price` responses, while parse stayed cheap. This means reducing payload is not currently the first proven fix, but it is a low-risk experiment if measured in isolation.

Specific experiment:

1. Use SPY with the same expiry and visible layout.
2. Test 5 min chart with current/default lookback first. During validation, the drawer showed 5 min history configured at `60` days.
3. Reduce only the 5 min history window to smaller values:
   - `30` days
   - `20` days
   - `10` days
4. For each value, force a 5 min `/update_price` refresh and export a trace.
5. Repeat the same experiment for 1 min only if 5 min results show a meaningful improvement.

Metrics to compare:

- `/update_price` bytes
- `/update_price get_price_history_ms`
- `/update_price prepare_price_chart_data_ms`
- `fetch:/update_price`
- `parse:/update_price`
- `parse:price_chart_payload`
- `applyPriceData` by mode
- `renderTVPriceChart` by mode
- `browser_long_task` count, p50, p95, max
- Visible chart completeness: current session, prior context, levels, RVOL/VP/TPO if enabled

Implementation options if the experiment wins:

- Add a scalp-mode preset that lowers only intraday chart history defaults, probably 5 min first.
- Add per-timeframe defaults tuned for scalping, keeping the existing user override controls.
- Consider lowering forced refresh lookback only for steady-state refreshes, while allowing larger history on first load or explicit user request.

Focused verification:

- Unit tests are probably unnecessary unless lookback resolver logic changes.
- If `resolve_lookback_days` defaults or caps change, add a focused test for timeframe defaults/caps.
- Run `python3 -m py_compile ezoptionsschwab.py`.
- Run inline JS extraction plus `node --check`.
- Live verify that 5 min chart still renders enough context and does not lose expected levels or overlays.

Success criteria:

- A reduced 5 min window lowers `/update_price` bytes and `get_price_history_ms` materially, ideally by 25% or more.
- Incremental chart apply remains around current levels: `applyPriceData incremental` p95 under `50 ms`.
- Full-context chart apply improves from about `400 ms`, or long-task count drops during timeframe/history switches.
- If the benefit is small, keep current defaults and do not change payload behavior.

### 3. Fix Dashboard Expiry To Trade-Rail Sync

Problem:

- During the stress test, dashboard expiry changed from `2026-05-06` to `2026-05-07`, but the trade rail initially stayed on `2026-05-06`.
- The selected quote stream eventually reconnected to `SPY 733P · 1DTE`, but the delay is not acceptable for a deliberate 0DTE-to-1DTE context switch.

Implementation approach:

1. Find the ownership path for dashboard expiry selection:
   - `loadExpirations`
   - `updateExpiryDisplay`
   - expiry checkbox `change` listeners
   - `getTradeRailSelectedDashboardExpiries`
   - `requestTradeChain`
   - `renderTradeExpiryOptions`
2. When dashboard expiry changes, invalidate trade rail manual expiry if it is no longer included in the dashboard-selected expiries.
3. Clear selected contract, reset selected quote override, disconnect selected quote stream, clear limit price, and force a trade-chain refresh for the new expiry context.
4. Avoid double-fetching if `updateData` and `requestTradeChain` fire together. Prefer one explicit forced trade-chain refresh after the expiry state is settled.

Focused verification:

- With SPY on 0DTE put, switch dashboard expiry to 1DTE.
- Expected immediate behavior:
  - trade expiry select changes to `2026-05-07`
  - selected contract updates to a 1DTE contract
  - old 0DTE quote stream disconnects
  - new `/trade/quote_stream/...260507...` connects
  - quote age returns to `now` or `1s`
  - stale 0DTE quote messages do not mutate the 1DTE selected state
- Repeat the reverse switch from 1DTE back to 0DTE.

Success criteria:

- Expiry switch completes within one forced `/trade_chain` cycle.
- No stale selected contract remains visible after expiry changes.
- Browser trace shows small apply costs and no unusual long-task spike beyond expected full-context chart work.

### 4. Keep Trade Chain Fast-Lane Behavior, But Consider Refresh Freshness Later

Current evidence:

- Fast cache paths are good enough.
- Fresh `/trade_chain` refreshes are mostly upstream-bound.
- Lowering cadence would not make Schwab faster and could make the chain context stale.

Possible future experiments:

- Increase reuse window only if fresh fetches begin causing visible quote/candle lag.
- Add a user-visible chain age indicator if stale reuse becomes more common.
- Keep stale-inflight behavior; it was effective.

Do not implement this in the next pass unless trace evidence changes.

### 5. Leave `/update` Isolated Unless It Starts Affecting Fast Lanes

Current evidence:

- `/update` can be multi-second, especially when it fetches fresh chain data.
- It did not freeze candles, selected quote stream, or Active Trader.

Possible future optimizations if needed:

- Defer or split analytics UI application after `/update`.
- Lazily update inactive secondary charts.
- Reduce Plotly work when the user is focused on the price chart and Active Trader.
- Cache expected-move or analytics price-history work if it repeatedly dominates.

Do not change formulas. Any implementation should preserve GEX/DEX/Vanna/Charm/Flow semantics.

## Suggested Next-Pass Test Plan

### Phase A - Instrumentation-Only Pass

1. Add long-task attribution and optional event-loop delay trace events.
2. Run static checks:
   - `python3 -m py_compile ezoptionsschwab.py`
   - inline JS extraction and `node --check`
   - `git diff --check`
3. Start traced server on port `5017`.
4. Export a 3-5 minute trace with SPY 0DTE Active Trader open.
5. Confirm long-task attribution works before changing performance behavior.

### Phase B - History-Window Experiment

1. Keep Auto Send off.
2. Use SPY nearest 0DTE if available.
3. Use 5 min chart and near-ATM selected option.
4. Test 5 min history windows: current/default, 30 days, 20 days, 10 days.
5. For each window:
   - warm up 1 minute
   - collect at least 3 `/update_price` samples
   - switch away and back to force one full-context chart apply
   - export browser trace
6. Compare route bytes, server time, chart apply modes, and long-task p95.

### Phase C - Context-Sync Fix

1. Implement dashboard expiry to trade-rail sync if Phase A/B did not reveal a more urgent blocker.
2. Test 0DTE to 1DTE and 1DTE to 0DTE.
3. Confirm old quote stream cannot mutate new selection.
4. Confirm order polling remains conditional and no preview/place endpoints are touched.

### Phase D - Full Retest

Run the full validation again:

1. 1 min chart for at least 5 minutes.
2. 5 min chart for at least 5 minutes.
3. Near-ATM call Active Trader for 3-5 minutes.
4. Near-ATM put Active Trader for 3-5 minutes.
5. 10-minute route cadence sample for `/trade_chain`, `/update_price`, and `/update`.
6. Order polling containment with Auto Send off.
7. Context-switch stress:
   - call to put
   - 0DTE to 1DTE
   - strike range change
   - exposure metric change
   - timeframe switch
   - trade rail collapse/expand
8. Export browser trace with cap high enough for the whole run.
9. Update this results note or create a new dated note with before/after tables.

## Open Questions For The Next Pass

- Are the recurring `browser_long_task` events caused by app code, browser rendering/paint, the in-app browser environment, or uninstrumented layout work?
- Does reducing the 5 min history window from 60 days to 30/20/10 days materially reduce `/update_price` time or full-context chart long tasks?
- Does long-task p95 improve when heavy lower panels, journal workspace, or right-rail sections are collapsed?
- Can full-context chart work be reduced without sacrificing chart context, levels, or user-selected history?
- Should long-validation traces raise `gexPerfTraceCap` to 50,000 by default when `GEX_PERF_TRACE=1`?
- Should dashboard expiry changes always override trade rail manual expiry, or only when the manual expiry is no longer included in the selected dashboard expiries?

## Recommended Next Actions

1. Start with instrumentation for long-task attribution. Do not optimize the 300-400 ms long tasks blindly.
2. Run the 5 min history-window experiment before changing `/update_price` payload or lookback defaults.
3. Fix dashboard expiry to trade-rail sync once the trace attribution pass is complete.
4. Preserve `/trade_chain` cache/stale behavior and order-polling containment.
5. Keep `/update` isolated and leave analytical formulas unchanged.

## Prompt For Next Session

Use this prompt to start the next implementation session:

```text
We are in /Users/scottmunger/Desktop/Trading/Dashboards/GEX-Dashboard on main.

Read first:
- AGENTS.md if present locally
- docs/speed/README.md
- docs/speed/AGENTS.md
- docs/SCALPING_SPEED_VALIDATION_PLAN.md
- docs/SCALPING_SPEED_RESULTS_2026-05-06_FULL_VALIDATION.md
- docs/SCALPING_SPEED_RESULTS_2026-05-06_CHART_PASS.md

Confirm branch/worktree with:
- git branch -a
- git log --oneline main..HEAD
- git status --short

Do not place live orders. Keep Auto Send off. Do not use preview/place/live-order endpoints unless I explicitly ask for safe preview-only behavior.

Goal: start the next scalping-speed follow-up based on docs/SCALPING_SPEED_RESULTS_2026-05-06_FULL_VALIDATION.md.

Please do this in order:

1. Review the full-validation findings and form a short implementation plan.
2. Start with instrumentation, not optimization:
   - Improve browser_long_task attribution so each long task includes nearby app spans, chart apply mode, route/apply context, selected timeframe, trade rail state, selected contract, and visible panel state when possible.
   - Add a lightweight event-loop delay trace event if useful.
   - Raise or make configurable the browser trace cap for long validations, preferably 50,000 events while GEX_PERF_TRACE=1.
3. Run focused static checks after instrumentation:
   - python3 -m py_compile ezoptionsschwab.py
   - extract inline JS and run node --check
   - git diff --check
4. Run a short traced validation on PORT=5017 with GEX_PERF_TRACE=1 and export a browser trace. Confirm long tasks now have useful attribution.
5. Run the 5 min chart history-window experiment before changing payload behavior:
   - SPY nearest 0DTE if available, otherwise nearest 1DTE
   - Compare current/default 5 min history against 30 days, 20 days, and 10 days
   - For each: collect /update_price route timing, bytes, get_price_history_ms, prepare_price_chart_data_ms, parse:/update_price, parse:price_chart_payload, applyPriceData modes, renderTVPriceChart modes, and browser_long_task p50/p95/max
   - Decide from evidence whether reducing 5 min history is worth implementing as a scalp-mode/default change
6. Fix dashboard expiry to trade-rail expiry sync if the instrumentation/history-window work does not identify a more urgent blocker:
   - 0DTE to 1DTE should immediately clear stale selected contract state, force trade chain refresh, reconnect selected quote stream, and prevent old quote stream messages from mutating the new selection
7. Preserve:
   - no analytical formula changes
   - single-file ezoptionsschwab.py
   - vanilla JS only
   - /trade_chain cache/stale behavior
   - conditional /trade/orders polling and slow-call/failure backoff
8. After any implementation, update or create a dated docs/SCALPING_SPEED_RESULTS_YYYY-MM-DD_<LABEL>.md note with:
   - code changes
   - tests run
   - route summaries
   - browser span summaries
   - long-task attribution findings
   - history-window experiment results
   - remaining bottlenecks
   - recommended next actions

Do not optimize blindly. If browser_long_task p95 is still around 300-400 ms, first prove whether it is full-context chart work, uninstrumented app layout/render work, browser paint/compositor work, or the in-app browser environment.
```
