# Scalping Speed Results Follow-up - 2026-05-06

## Scope

Same-day follow-up to `SCALPING_SPEED_RESULTS_2026-05-06.md` on branch `codex/scalping-speed-followup`.

Baseline files were read first, including:

- `docs/SCALPING_SPEED_RESULTS_2026-05-06.md`
- `docs/SCALPING_SPEED_VALIDATION_PLAN.md`
- `docs/SCALPING_PERFORMANCE_OPTIMIZATION_PLAN.md`
- `docs/SCALPING_FAST_LANES_FOLLOWUP_PLAN.md`
- `docs/ORDER_ENTRY_RAIL_SCALPING_REPAIR_PLAN.md`

## Implementation Summary

- Added server-side cache refresh attribution for options snapshot refreshes.
- Added fast stale-cache return for same-key `/trade_chain` consumers when a slow refresh is already in flight.
- Added pre-lock and post-lock cache-hit attribution to distinguish fast cache hits from lock waits.
- Added browser perf attribution for route JSON parse, route apply, chart payload parse, chart rendering, realtime candle apply, and selected quote handling.
- Kept `/update_price` payload unchanged because captured parse timings were small and chart apply/render work was the larger browser cost.
- Kept `/update` isolated. It remains slow, but the focused run did not show it breaking Active Trader responsiveness.
- Kept `/trade/orders` polling conditional and added slow-call visibility plus client backoff.

No live order, preview, place, or live-order endpoints were used. Auto Send stayed off.

## Verification

Commands run:

```bash
python3 -m py_compile ezoptionsschwab.py
git diff --check
python3 -m unittest tests.test_session_levels tests.test_trade_preview
python3 -c "import re, pathlib, ezoptionsschwab as m; html=m.app.test_client().get('/').get_data(as_text=True); scripts=re.findall(r'<script[^>]*>(.*?)</script>', html, re.S|re.I); pathlib.Path('/tmp/gex-inline-scripts.js').write_text('\n;\n'.join(scripts)); print('scripts', len(scripts))"
node --check /tmp/gex-inline-scripts.js
```

Results:

- `py_compile` passed with the pre-existing invalid escape sequence warning near the chart HTML.
- `git diff --check` passed.
- Focused unit tests passed: 37 tests.
- Embedded browser JavaScript extracted as 5 inline scripts and passed `node --check`.

## Live Retest Setup

Server:

```bash
PORT=5017 GEX_PERF_TRACE=1 FLASK_DEBUG=0 FLASK_USE_RELOADER=0 python3 ezoptionsschwab.py 2>&1 | tee /tmp/gex-speed-5017.log
```

Browser:

- Opened `http://127.0.0.1:5017/` in the in-app browser.
- Browser perf logging was active through the server-provided `GEX_PERF_TRACE_FROM_SERVER` flag.
- The direct `javascript:` localStorage reload attempt was not accepted by the browser automation API, but `[perf]` logs were emitted.

Test state:

- Ticker: SPY.
- Expiration: nearest 0DTE, `2026-05-06`.
- Active Trader open.
- Auto Send off.
- 1 min run with near-ATM call selected.
- 1 min run with near-ATM put selected.
- 5 min run with near-ATM put selected.

Artifacts:

- Server log: `/tmp/gex-speed-5017.log`
- Browser perf log: `/tmp/gex-browser-perf-20260506-followup.log`
- Browser perf JSONL: `/tmp/gex-browser-perf-20260506-followup.jsonl`

## Server Route Summary

| Route | n | p50 ms | p95 ms | max ms | median bytes |
| --- | ---: | ---: | ---: | ---: | ---: |
| `/trade/account_details` | 4 | 804.8 | 1278.8 | 1322.6 | 545 |
| `/trade/orders` | 4 | 1369.2 | 1933.8 | 2006.0 | 251 |
| `/trade_chain` | 53 | 662.9 | 2568.3 | 3348.3 | 43232 |
| `/update` | 9 | 2913.1 | 5881.3 | 5920.3 | 251202 |
| `/update_price` | 9 | 946.9 | 3505.6 | 3824.2 | 5037386 |

## Trade Chain Attribution

| Outcome | n | p50 ms | p95 ms | max ms |
| --- | ---: | ---: | ---: | ---: |
| `cache_hit_prelock` | 17 | 16.0 | 32.5 | 45.5 |
| `cache_only` | 2 | 3.1 | 5.8 | 6.1 |
| `fetched_stored` | 29 | 897.7 | 3207.7 | 3348.3 |
| `stale_inflight` | 5 | 16.9 | 27.8 | 28.6 |

Findings:

- The original refresh-hit outlier hypothesis is confirmed. Attribution captured a `/update` cache refresh with `cache_refresh_outcome=cache_hit_after_wait` and `cache_refresh_lock_wait_ms=3221.207`, which means a nominal cache hit still waited behind another refresh lock holder.
- The new same-key stale-in-flight path prevented fast `/trade_chain` consumers from waiting behind the slow lock holder when usable stale data existed. The stale responses returned in under 30 ms p95 and reported zero lock wait/held time.
- Fetch-backed `/trade_chain` remains dominated by Schwab chain/current-price work. The `fetched_stored` p95 was 3207.7 ms, so the stale path improves fast-lane consumers but does not make the upstream fetch itself faster.
- Retest logs include some stale-in-flight rows with `cache_refresh_error=Cache_refresh_did_not_return_usable_option_data.` because that classification was still too broad during the run. The code was corrected afterward so stale snapshots are not marked as refresh errors.

## Browser Perf Summary

The in-app browser dev log was capped at the latest 500 entries, so this is a focused sample rather than a complete long-run capture.

| Span | n | p50 ms | p95 ms | max ms |
| --- | ---: | ---: | ---: | ---: |
| `browser_long_task` | 7 | 360.0 | 515.3 | 581.0 |
| `renderTVPriceChart` | 1 | 557.6 | 557.6 | 557.6 |
| `applyPriceData` | 1 | 575.9 | 575.9 | 575.9 |
| `applyRealtimeCandle` | 1 | 360.3 | 360.3 | 360.3 |
| `apply:/update_price` | 1 | 576.3 | 576.3 | 576.3 |
| `parse:/update_price` | 1 | 5.2 | 5.2 | 5.2 |
| `parse:price_chart_payload` | 1 | 7.0 | 7.0 | 7.0 |
| `apply:/update` | 1 | 53.8 | 53.8 | 53.8 |
| `parse:/update` | 1 | 0.5 | 0.5 | 0.5 |
| `apply:/trade_chain` | 7 | 12.8 | 15.1 | 15.3 |
| `parse:/trade_chain` | 7 | 0.1 | 0.3 | 0.3 |
| `applyTradeSelectedQuoteMessage` | 36 | 0.4 | 0.7 | 0.8 |
| `renderTradeActiveTrader` | 83 | 2.4 | 6.4 | 7.4 |

Findings:

- Route JSON parse was not a measured bottleneck in this sample. `/update_price` JSON parse was 5.2 ms and nested chart payload parse was 7.0 ms.
- The largest attributed browser cost was chart application/rendering: `apply:/update_price`, `applyPriceData`, and `renderTVPriceChart` were all around 558-576 ms in the captured sample.
- Realtime candle handling also produced a 360.3 ms span, matching nearby long-task duration.
- Selected quote handling was not the long-task source in the captured data. Parse, merge, signature, state update, and Active Trader render were all small.
- Active Trader remained visually responsive while slow `/trade_chain`, `/update`, and `/update_price` requests were in flight. Selected quote ticks and ladder renders continued.

## Before/After Highlights

Compared to `SCALPING_SPEED_RESULTS_2026-05-06.md`:

- `/trade_chain` cache-only p95 stayed fast: 8.8 ms baseline to 5.8 ms follow-up.
- The old refresh-hit/no-fetch outlier lane is now split into fast `cache_hit_prelock` and `stale_inflight` outcomes. These were 32.5 ms p95 and 27.8 ms p95 respectively.
- Fetch-backed `/trade_chain` is still slow: 2414.4 ms baseline p95 to 3207.7 ms follow-up p95.
- `/update_price` payload size stayed about the same: 5028168 baseline median bytes to 5037386 follow-up median bytes.
- `/update` remained slow: 5428.4 ms baseline p95 to 5881.3 ms follow-up p95.
- `renderTradeActiveTrader` stayed fast: 6.5 ms baseline p95 to 6.4 ms follow-up p95.
- Browser long tasks are still present: 577.1 ms baseline p95 to 515.3 ms follow-up p95 in the capped sample.

## Bottlenecks

1. Fast-chain lock waits were real and are now mitigated for same-key fast consumers when stale cache exists.
2. Slow Schwab chain/current-price fetches still dominate fetch-backed `/trade_chain`.
3. `/update` can still wait behind the options cache refresh lock, but it is not currently the fast-lane blocker.
4. Browser long tasks are more strongly tied to price chart apply/render and realtime candle processing than to route JSON parse or selected quote handling.
5. `/trade/orders` remained conditional. One Schwab order call crossed the 2 second slow-call threshold and is now visible as slow with client backoff available.

## Recommended Next Actions

1. Keep the stale-in-flight `/trade_chain` path.
2. Focus the next optimization on chart work: inspect `applyRealtimeCandle`, cumulative volume/RVOL work, and `renderTVPriceChart` full-series application.
3. Do not reduce `/update_price` payload blindly. Parse was small in this run; application/rendering was the measured cost.
4. Add a small in-app perf ring buffer or downloadable trace endpoint so browser logs are not capped at the latest 500 entries.
5. Leave `/update` analytics formulas untouched and optimize only if future traces show it correlates with fast-lane staleness or long tasks.
6. Keep order polling conditional and retain the slow-call backoff.

## Detailed Findings

### Cache Lock Behavior

The original outlier pattern looked like `/trade_chain` refresh-hit calls were sometimes waiting even when they did not need a Schwab fetch. The new attribution confirms the underlying issue: a caller can enter refresh handling while another request owns `_options_cache_refresh_lock`, then wait behind the lock before discovering the cache is already usable. The clearest captured example was a `/update` request with:

- `cache_refresh_outcome=cache_hit_after_wait`
- `cache_refresh_lock_wait_ms=3221.207`

That proves the slow path was not only network fetch time. Some nominal cache hits were really lock wait time.

The follow-up implementation changes `/trade_chain` behavior when the refresh lock is already held. If the caller is requesting the same ticker/date/price-range key and the existing snapshot is stale but usable, the endpoint returns that stale snapshot immediately instead of waiting for the slow refresh holder. In the retest, that path produced:

- `stale_inflight` count: 5
- p50: 16.9 ms
- p95: 27.8 ms
- max: 28.6 ms
- lock wait: 0 ms
- lock held: 0 ms

This is the right fast-lane behavior for Active Trader and quote consumers because a slightly stale chain is better than blocking behind a multi-second Schwab refresh when a fresh request is already in flight.

### Remaining Server Bottleneck

Fetch-backed `/trade_chain` is still slow when the app genuinely needs fresh chain data:

- `fetched_stored` count: 29
- p50: 897.7 ms
- p95: 3207.7 ms
- max: 3348.3 ms

This is upstream-bound work: Schwab option chain fetch, current-price fetch, and downstream snapshot construction. The stale-in-flight path protects fast consumers from this cost, but it does not reduce the cost of the refresh itself.

`/update` and `/update_price` remain heavier background routes:

- `/update` p95: 5881.3 ms
- `/update_price` p95: 3505.6 ms
- `/update_price` median bytes: 5037386

The retest did not show these routes breaking Active Trader responsiveness, but they should remain under observation because they can compete for browser main-thread work.

### Browser Main Thread Behavior

The added browser attribution separates route fetch, JSON parse, route apply, selected quote work, and chart work. In the captured sample, JSON parse was not the main problem:

- `parse:/update_price`: 5.2 ms
- `parse:price_chart_payload`: 7.0 ms
- `parse:/trade_chain` p95: 0.3 ms
- `parse:/update`: 0.5 ms

The expensive attributed work was chart application/rendering:

- `apply:/update_price`: 576.3 ms
- `applyPriceData`: 575.9 ms
- `renderTVPriceChart`: 557.6 ms
- `applyRealtimeCandle`: 360.3 ms

Selected quote and Active Trader work stayed small:

- `applyTradeSelectedQuoteMessage` p95: 0.7 ms
- `renderTradeActiveTrader` p95: 6.4 ms
- `selectedQuote:parse`, `selectedQuote:merge`, `selectedQuote:signature`, and `selectedQuote:state` were all sub-millisecond in the captured sample.

The practical reading is that Active Trader is not the source of the long tasks. The next optimization should target chart update paths, especially steady-state work that still rebuilds or reapplies more series data than needed.

### Order Polling

`/trade/orders` remained conditional and did not become a high-frequency background poll. The route now emits Schwab call timing and marks calls over the slow threshold:

- `/trade/orders` p95: 1933.8 ms
- max: 2006.0 ms
- one captured call crossed the 2 second slow-call threshold

The client-side backoff path is present so slow/failing order calls do not stack up. Continue keeping Auto Send off during normal testing.

## Next Implementation Plan

### 1. Add A Browser Perf Ring Buffer

Problem:

The in-app browser dev log only preserved the latest 500 messages. That is not enough to correlate route events, long tasks, selected quote updates, and chart spans across a full 10-minute validation.

Implementation approach:

- Extend the existing `gexPerfStart` / `gexPerfEnd` path in `ezoptionsschwab.py`.
- Add a global ring buffer such as `window.gexPerfTraceEvents = []` with a fixed cap, for example 5000 events.
- Push normalized event objects into the buffer whenever a `[perf]` span is emitted:
  - `ts`
  - `name`
  - `duration_ms`
  - `detail`
  - `url` or route when available
- Also push `browser_long_task` entries from the PerformanceObserver path.
- Add a browser helper such as `window.dumpGexPerfTrace()` that returns a JSON string or triggers a download.
- Keep console logging unchanged so current trace workflows still work.

Correctness checks:

- With `localStorage.gexPerfTrace=1` or server trace enabled, call `window.gexPerfTraceEvents.length` in the browser after activity and confirm it grows.
- Confirm the buffer caps at the intended size and drops oldest entries, not newest entries.
- Confirm event objects include the same span names seen in console logs.
- Confirm no buffer writes happen when perf tracing is disabled, or keep only a very small disabled-mode buffer if that is intentionally useful.

Testing:

- Run `node --check` on extracted embedded scripts.
- Load the app with `GEX_PERF_TRACE=1`.
- Run a 2-3 minute SPY test and export/dump the buffer.
- Verify exported event counts exceed the old 500-console-message limit.

### 2. Optimize Steady-State Chart Application

Problem:

`/update_price` parse is small, but `apply:/update_price`, `applyPriceData`, and `renderTVPriceChart` produced 550+ ms spans. This points to chart application/rendering, not JSON parse, as the immediate browser bottleneck.

Implementation approach:

- Inspect `applyPriceData`, `updateCharts`, and `renderTVPriceChart`.
- Separate initial full chart setup from steady-state updates.
- Avoid full `setData` calls on every steady `/update_price` refresh if only the latest candle or a small tail changed.
- Prefer incremental Lightweight Charts updates for the active candle when safe:
  - use `series.update(latestBar)` for the active candle
  - only call `setData(fullSeries)` when the symbol, timeframe, trading day, or backfill boundary changes
- Track a lightweight chart data signature:
  - ticker
  - timeframe
  - first timestamp
  - last timestamp
  - count
  - last OHLCV values
- Use the signature to decide whether a full reapply is necessary.
- Keep Plotly chart updates isolated from TradingView-Lightweight-Charts updates so one does not force the other.

Correctness checks:

- Changing ticker still forces a full chart reset.
- Changing timeframe from 1 min to 5 min still forces a full chart reset.
- A new candle appends cleanly and does not reorder bars.
- An in-progress candle updates OHLCV without duplicating bars.
- Extended candle history remains available after an initial full load.
- HVL, EM, and secondary wall overlays remain intact after incremental updates.

Testing:

- Add or keep focused browser perf spans around:
  - `applyPriceData`
  - `renderTVPriceChart`
  - `applyRealtimeCandle`
  - full reset vs incremental update decision
- Run extracted JS through `node --check`.
- In the in-app browser, start with 1 min SPY 0DTE and watch at least two candle transitions.
- Expected result after optimization:
  - steady `apply:/update_price` p95 should be far below the current 576 ms sample
  - steady `renderTVPriceChart` should not appear on every normal refresh
  - `applyRealtimeCandle` p95 should stay below 50-100 ms in normal conditions
  - chart remains visually correct through timeframe changes

### 3. Inspect Realtime Candle Work

Problem:

`applyRealtimeCandle` produced a 360.3 ms span. That is too high for a steady tick/candle path.

Implementation approach:

- Inspect `applyRealtimeCandle` for full-array scans, repeated JSON parsing, volume aggregation, cumulative volume, RVOL, or repeated sort/filter work.
- Cache per-timeframe derived volume state when possible.
- Update only the active candle bucket when a quote tick lands inside the current timeframe bucket.
- Recompute cumulative volume/RVOL across the full day only when the chart session changes, timeframe changes, or history is reloaded.

Correctness checks:

- Last candle close/high/low updates correctly as price moves.
- Candle volume increments correctly.
- Cumulative volume and RVOL remain consistent after a hard refresh and after incremental updates.
- Switching 1 min to 5 min rebuilds the aggregation from source data rather than carrying over stale 1 min state.

Testing:

- Add span detail fields such as `mode=incremental|full`, `bars`, `timeframe`, `new_bar`, and `reason`.
- Use browser trace to verify normal quote ticks take the incremental mode.
- Compare visible candle values before and after a hard reload during the same minute.

### 4. Keep `/update_price` Payload Reduction As A Second-Step Optimization

Problem:

The `/update_price` payload is large at about 5 MB, but parse attribution was small in this focused sample. Reducing the payload may still help bandwidth and memory pressure, but it should not be the first change unless chart application is already incremental.

Implementation approach:

- First optimize chart application.
- Then measure whether the large payload still correlates with long tasks, GC, or slow applies.
- If it still matters, add an explicit steady-state response mode rather than changing the full response contract globally.
- Possible response split:
  - initial/full load: full historical payload
  - steady refresh: latest candle tail, quote, latest price metadata, and only changed overlays
- Gate the mode behind a request parameter or server-side capability flag so fallback to full payload remains easy.

Correctness checks:

- First load remains identical.
- Hard reload restores the full chart.
- Steady mode can recover from missed ticks by requesting a full refresh when signatures diverge.
- Existing chart controls and overlays remain correct.

Testing:

- Compare response sizes before/after.
- Compare chart signatures before/after full reload.
- Verify no missing candles after 5-10 minutes of 1 min updates.
- Confirm `parse:/update_price` and `apply:/update_price` both improve or remain stable.

### 5. Leave `/update` Formula Work Untouched

Problem:

`/update` remains slow, but it carries analytics and formula-heavy work. The standing repo rule is no analytical formula changes.

Implementation approach:

- Do not change GEX/DEX/Vanna/Charm/Flow math.
- Keep `/update` optimization focused on cache behavior, response handling, or scheduling.
- If future traces show `/update` waiting behind `_options_cache_refresh_lock` in a way that hurts fast-lane consumers, consider a stale snapshot path for non-critical UI updates, but keep that separate from `/trade_chain`.

Correctness checks:

- Existing analytical outputs match before/after for the same cached chain.
- Dealer impact, scenarios, alerts, and key levels continue rendering.
- No formula helper behavior changes.

Testing:

- Existing focused unit tests.
- Any available route tests for session levels, side panel, order preview, and chart data.
- Live visual comparison of the KPI strip, dealer impact, scenarios, and alerts rail after `/update`.

### 6. Keep Order Polling Conditional

Problem:

Order calls can be slow, but they should not become a constant source of latency during scalping unless the user is actively using order state.

Implementation approach:

- Keep `shouldPollTradeOrders()` as the gate.
- Keep slow-call backoff and failure backoff.
- If future traces show slow order calls overlapping with UI jank, increase backoff for slow calls or pause polling while the document is hidden.

Correctness checks:

- Orders refresh when Active Trader needs them.
- Polling stops when the order panel is collapsed or conditions no longer require it.
- Slow/failing calls do not overlap or stack.

Testing:

- With live trading disabled and Auto Send off, open Active Trader and observe polling spans.
- Collapse order-related UI and confirm polling quiets.
- If a mocked slow Schwab call is available, verify the client schedules the next poll after backoff instead of immediately.

## Focused Verification Plan For Next Changes

Run these after each implementation pass:

```bash
python3 -m py_compile ezoptionsschwab.py
git diff --check
python3 -m unittest tests.test_session_levels tests.test_trade_preview
python3 -c "import re, pathlib, ezoptionsschwab as m; html=m.app.test_client().get('/').get_data(as_text=True); scripts=re.findall(r'<script[^>]*>(.*?)</script>', html, re.S|re.I); pathlib.Path('/tmp/gex-inline-scripts.js').write_text('\n;\n'.join(scripts)); print('scripts', len(scripts))"
node --check /tmp/gex-inline-scripts.js
```

Add targeted tests when practical:

- Python unit test for any new server cache mode.
- Browser trace smoke test using the in-app browser.
- Manual chart correctness pass for ticker changes, timeframe changes, hard reload, and at least one live candle transition.

## Live Retest Plan After Next Optimization

Use the same shape as this follow-up so results are comparable:

1. Start a traced server on a non-conflicting port:

   ```bash
   PORT=5017 GEX_PERF_TRACE=1 FLASK_DEBUG=0 FLASK_USE_RELOADER=0 python3 ezoptionsschwab.py 2>&1 | tee /tmp/gex-speed-5017-next.log
   ```

2. Open `http://127.0.0.1:5017/` in the in-app browser.
3. Enable browser tracing with either the server flag or `localStorage.setItem('gexPerfTrace', '1')`, then hard reload.
4. Confirm token health through `/token_health`.
5. Use SPY nearest 0DTE if available, otherwise nearest 1DTE.
6. Keep Auto Send off.
7. Open Active Trader with a near-ATM call.
8. Run 1 min for at least 5 minutes.
9. Switch to a near-ATM put and run 1 min for at least 5 minutes.
10. Switch to 5 min and run at least 5 minutes.
11. Export server perf logs and browser perf ring buffer.
12. Compare against this note and `SCALPING_SPEED_RESULTS_2026-05-06.md`.

Success criteria for the next pass:

- `/trade_chain` cache/stale lanes stay under 50 ms p95.
- No fast `/trade_chain` consumer waits behind a slow Schwab fetch when same-key stale data exists.
- `renderTradeActiveTrader` stays under 10 ms p95.
- `applyTradeSelectedQuoteMessage` stays under 5 ms p95.
- Steady `apply:/update_price` and `renderTVPriceChart` p95 materially improve from the 550-576 ms sample.
- `applyRealtimeCandle` steady p95 improves from the 360 ms sample, ideally under 100 ms.
- Browser long-task count and p95 decline during the 1 min and 5 min samples.
- Candles, selected quote, and Active Trader remain visually responsive while slow server requests are in flight.

## Prompt For Next Session

```text
We are in /Users/scottmunger/Desktop/Trading/Dashboards/GEX-Dashboard on branch codex/scalping-speed-followup.

Read AGENTS.md and these docs first:
- docs/SCALPING_SPEED_RESULTS_2026-05-06.md
- docs/SCALPING_SPEED_RESULTS_2026-05-06_FOLLOWUP.md
- docs/SCALPING_SPEED_VALIDATION_PLAN.md
- docs/SCALPING_PERFORMANCE_OPTIMIZATION_PLAN.md
- docs/SCALPING_FAST_LANES_FOLLOWUP_PLAN.md
- docs/ORDER_ENTRY_RAIL_SCALPING_REPAIR_PLAN.md

Confirm branch/worktree with:
- git branch -a
- git log --oneline main..HEAD
- git status --short

Do not place live orders. Keep Auto Send off for normal testing. Do not use preview/place/live-order endpoints unless safe preview-only behavior is explicitly needed. If testing Auto Send behavior, only do it with live trading disabled and confirm rejection behavior.

Goal: implement the next optimization pass described in docs/SCALPING_SPEED_RESULTS_2026-05-06_FOLLOWUP.md.

Priorities:
1. Add a browser perf ring buffer/export path so traces are not capped by the in-app browser's latest 500 console messages.
2. Use the new trace capture to optimize chart main-thread work, especially applyPriceData, renderTVPriceChart, and applyRealtimeCandle.
3. Prefer incremental steady-state chart/candle updates over full-series reapplication when ticker, timeframe, session, and history signature have not changed.
4. Do not reduce /update_price payload until attribution shows payload size is still contributing after chart application is optimized.
5. Leave analytical formulas unchanged.
6. Keep /update isolated unless traces show it correlates with fast-lane staleness or browser long tasks.
7. Keep /trade/orders polling conditional and preserve slow-call/failure backoff.

Respect repo constraints:
- No analytical formula changes.
- No JS framework introduction.
- Keep the single-file ezoptionsschwab.py structure.
- Use existing vanilla JS/CSS patterns.
- Do not revert unrelated user changes.

After implementation, run:
- python3 -m py_compile ezoptionsschwab.py
- git diff --check
- python3 -m unittest tests.test_session_levels tests.test_trade_preview
- extract embedded scripts from the Flask page and run node --check on them

Then run a live scalping speed retest:
- Start traced server on a non-conflicting port, preferably:
  PORT=5017 GEX_PERF_TRACE=1 FLASK_DEBUG=0 FLASK_USE_RELOADER=0 python3 ezoptionsschwab.py 2>&1 | tee /tmp/gex-speed-5017-next.log
- Open http://127.0.0.1:5017/ in the in-app browser.
- Enable browser tracing, hard reload, and verify the perf ring buffer collects events.
- Use SPY nearest 0DTE if available, otherwise nearest 1DTE.
- Test 1 min near-ATM call, 1 min near-ATM put, then 5 min near-ATM put.
- Keep Active Trader open and Auto Send off.
- Capture server perf logs, browser trace export, route cadence, p50/p95/max timings, response sizes, visible lag/jank, and stale quote behavior.

At the end, update or create a dated results note under docs/ with:
- Code changes
- Tests run
- Route/browser before-after summaries
- Whether candles and Active Trader stayed responsive while slow requests were in flight
- Bottlenecks found with evidence
- Recommended next actions
```
