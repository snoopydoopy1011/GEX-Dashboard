# GEX Dashboard - Scalping Speed Results - 2026-05-06

**Run window:** 2026-05-06 11:46-12:05 ET  
**Branch/worktree:** `main`; no commits ahead of `main`; untracked `Trading_from_dashboard.txt` and `docs/SCALPING_SPEED_VALIDATION_PLAN.md` were present before this run.  
**Server:** `PORT=5017 GEX_PERF_TRACE=1 FLASK_DEBUG=0 FLASK_USE_RELOADER=0 python3 ezoptionsschwab.py 2>&1 | tee /tmp/gex-speed-5017.log`  
**Browser trace:** `/tmp/gex-browser-perf-normalized.log` from in-app browser console `[perf]` logs.  
**State:** SPY, 0DTE `2026-05-06`, 1 min then 5 min, Active Trader open, Auto Send off, call then put selected. No preview/place/live-order endpoint was used.

## Completed

- First load/cache warm-up completed with valid Schwab token health.
- Underlying price SSE stayed connected and `applyRealtimeQuote` stayed sub-millisecond.
- Selected Active Trader quote stream connected for `SPY 260506C00732000`, then reconnected for `SPY 260506P00731000`.
- Active Trader ladder render churn, local staged marker, clear marker, and scroll stability were tested with Auto Send off.
- `/trade_chain`, `/update_price`, `/update`, and `/trade/orders` were sampled during steady state and context switches.
- Options Vol -> GEX overlay switch, 5 min timeframe switch, and `2026-05-07` expiry switch/restore were tested.
- Orders panel open/close used guarded polling only.

## Not Fully Tested

- No live orders and no Auto Send rejection flow were tested.
- No HAR or full DevTools Performance recording was exported through the in-app browser tooling; console spans and screenshots were used instead.
- No `applyRealtimeCandle` span was captured in the saved browser console logs.
- No dedicated selected-quote apply-path span was emitted; quote stream health was verified through `/trade/quote_stream` connections and live Active Trader header changes.

## Route Summary

| Route | n | p50 ms | p95 ms | max ms | median bytes | Notes |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `/trade_chain` all OK | 163 | 738.2 | 2156.8 | 8603.7 | 42986 | Slow samples are refresh-cache Schwab fetch/current-price spikes. |
| `/trade_chain` cache-only | 2 | 8.3 | 8.8 | 8.8 | 35919 | Cache-only path is healthy. |
| `/trade_chain` refresh hit, no fetch | 68 | 18.4 | 1702.4 | 2736.8 | 44374 | Some no-fetch calls likely waited behind cache-refresh lock. |
| `/trade_chain` refresh fetched | 93 | 904.9 | 2414.4 | 8603.7 | 42982 | Worst sample: `get_current_price_ms=6831.5`, `fetch_chain_ms=1747.6`. |
| `/update_price` | 25 | 1031.2 | 1876.6 | 2157.0 | 5028168 | No `compute_trader_stats_*` or `key_levels*` spans appeared here. |
| `/update` | 23 | 2870.1 | 5428.4 | 5741.8 | 255779 | Slow lane exceeded target but did not visibly block fast lanes. |
| `/trade/orders` | 9 | 748.4 | 4336.3 | 5357.2 | 251 | Schwab call latency, contained to account/order checks. |

## Browser Summary

| Span | n | p50 ms | p95 ms | max ms | Notes |
| --- | ---: | ---: | ---: | ---: | --- |
| `applyRealtimeQuote` | 312 | 0.3 | 0.5 | 1.1 | Healthy; quote ticks did not create render cost. |
| `applyRealtimeCandle` | 0 | | | | Not captured in saved logs. |
| selected quote apply path | 0 | | | | No dedicated span emitted. |
| `renderTradeActiveTrader` | 833 | 2.5 | 6.5 | 8.5 | Under target. |
| `buildTradeActiveLadderHtml` | 245 | 0.2 | 0.4 | 3.9 | Under target. |
| `updateCharts` | 8 | 51.5 | 68.2 | 75.3 | Aligns with slow analytics responses. |
| `browser_long_task` | 92 | 367.0 | 577.1 | 664.0 | Repeated issue; mostly around route completions/minute boundaries, not every quote tick. |

## Detailed Findings

### Responsiveness During Slow Requests

Candles/price and Active Trader remained usable while slow `/update` and slow `/trade_chain` refreshes were in flight. The strongest evidence is that `applyRealtimeQuote` stayed sub-millisecond across 312 spans and `renderTradeActiveTrader` stayed under 10 ms max while `/update` samples reached 5.7 seconds and `/trade_chain` reached 8.6 seconds. The selected option quote stream also reconnected cleanly when switching from the near-ATM call to the near-ATM put.

The fast UI lane therefore appears structurally separated from the slow analytics lane. The remaining risk is not that `/update` blocks every tick directly, but that large response handling or route completion work may still contribute to the 300-600 ms browser long tasks seen near route completions and minute boundaries.

### Active Trader And Ladder Behavior

The ladder did not recenter on every quote tick. The local staged marker appeared immediately and cleared cleanly with Auto Send off. Trading rail scroll stayed at Order Ticket / Contract Picker for 50 seconds while live updates continued; it did not jump back to Active Trader. No preview, place, or live-order endpoint was touched.

The measured render costs were healthy:

- `renderTradeActiveTrader`: p50 2.5 ms, p95 6.5 ms, max 8.5 ms.
- `buildTradeActiveLadderHtml`: p50 0.2 ms, p95 0.4 ms, max 3.9 ms.

The missing piece is a dedicated span for the selected quote apply path. The selected stream was visibly live and the SSE connections were confirmed, but the browser logs do not yet isolate parse/merge/signature/schedule cost for the selected option quote message.

### `/trade_chain` Fast Lane

The cache-only path is healthy: p50 8.3 ms and p95 8.8 ms. This means the payload builder and response path can be fast enough for scalping when the data is already usable.

The refresh path has two distinct issues:

- Fetch-backed refreshes were slow when Schwab/current-price calls were slow. The worst sample reached 8603.7 ms, mostly from `get_current_price_ms=6831.5` plus `fetch_chain_ms=1747.6`.
- Refresh-hit/no-fetch requests had a p95 of 1702.4 ms even though they should behave close to cache-only responses when no external fetch is needed. That points to lock wait, request scheduling, or contention around `refresh_options_cache_snapshot`, not payload construction.

The next implementation should instrument before changing behavior. If the no-fetch outliers are confirmed as lock waits, the fix should be a stale-cache or in-flight coalescing path so a fast refresh request can return existing usable data rather than sitting behind a slow external fetch.

### `/update_price` Price-History Lane

`/update_price` met the rough timing target in this run: p50 1031.2 ms, p95 1876.6 ms, max 2157.0 ms. No `compute_trader_stats_*` or `key_levels*` spans appeared here, so the previous lane diet appears to be doing its job.

The concern is payload size. Median response size was about 5.0 MB, which can still pressure the browser through network transfer, JSON parse, allocation, and chart application. This is a candidate for payload diet only after attribution confirms whether `/update_price` parse/apply work contributes to `browser_long_task`.

### `/update` Analytics Lane

`/update` remains slow: p50 2870.1 ms, p95 5428.4 ms, max 5741.8 ms. During the live test it did not visibly block candles, selected quotes, or the ladder. Its cadence settled near 60 seconds after warm-up, with forced updates on context changes.

The right next action is containment first, optimization second. As long as fast lanes remain responsive, `/update` should not be changed aggressively until browser attribution shows it is responsible for main-thread pressure or route overlap.

### Browser Main Thread

The largest unresolved issue is browser long-task pressure: 92 long tasks, p50 367.0 ms, p95 577.1 ms, max 664.0 ms. The current spans do not explain those durations because quote and ladder spans are small, and `updateCharts` only reached 75.3 ms max.

Likely candidates are uninstrumented JSON parsing, post-fetch object normalization, Plotly work outside `updateCharts`, chart payload application, browser/runtime overhead, or large DOM/layout work not covered by current spans. The next implementation should add attribution around fetch parse and apply phases before trying UI rewrites.

### SQLite And Flow Alerts

SQLite hot-path spans stayed low in normal samples, and no SQLite lock errors appeared. This validates the Phase 3 index/hot-path work for this scalping run. Keep watching this during longer retests, but it is not the immediate bottleneck.

### Order And Account Polling

`/trade/orders` is isolated but can be slow: p95 4336.3 ms, max 5357.2 ms. Because this is an account/orders Schwab API path, it should remain conditional and should not be made more frequent during scalping. The useful improvement is visibility and backoff when orders are open or an active intent is present, not tighter polling.

## Bottlenecks

| Bottleneck | Evidence | Likely lane | Next action |
| --- | --- | --- | --- |
| Fast-chain refresh spikes | `/trade_chain refresh fetched` p95 2414.4 ms, max 8603.7 ms; worst sample mostly `get_current_price_ms=6831.5`. | Schwab chain/current price fetch | Instrument lock/fetch/in-flight behavior, then add stale-cache or backoff behavior if confirmed. |
| No-fetch `/trade_chain` waits | Refresh-hit/no-fetch p95 1702.4 ms despite cache-only p95 8.8 ms. | Cache lock / request scheduling | Measure lock wait and lock hold time inside `refresh_options_cache_snapshot`. |
| Browser long tasks | 92 long tasks; p50 367 ms, p95 577 ms, max 664 ms. | Browser main thread | Add parse/apply/render attribution around route completion and chart work. |
| `/update_price` payload size | Median bytes 5028168 while route timing is acceptable. | Price-history payload and browser parse/apply | Reduce payload only after confirming consumers and long-task attribution. |
| Slow analytics | `/update` p50 2870.1 ms, p95 5428.4 ms. | Slow analytics lane | Keep isolated; optimize if it correlates with long tasks or stale UI during retest. |
| Slow order polling | `/trade/orders` p95 4336.3 ms, max 5357.2 ms. | Schwab orders API | Keep polling conditional; add timeout/backoff visibility. |

## Implementation Plan For Next Session

Use `docs/SCALPING_SPEED_VALIDATION_PLAN.md` as the authoritative test matrix. The implementation work below should be done in stages so each change can be measured against this baseline.

### Stage 0 - Branch, Safety, And Baseline Hygiene

Implementation:

1. Confirm the worktree with `git branch -a`, `git log --oneline main..HEAD`, and `git status --short`.
2. Create or switch to a focused branch such as `codex/scalping-speed-followup`.
3. Keep Auto Send off during normal testing.
4. Do not use preview/place/live-order endpoints unless a test explicitly requires safe preview-only behavior.
5. If Auto Send rejection behavior is tested, do it only with live trading disabled and confirm the expected rejection.

Verification:

1. Run `python3 -m py_compile ezoptionsschwab.py`.
2. Run `git diff --check`.
3. Run the existing focused tests that cover session levels and order preview behavior, if present: `python3 -m unittest tests.test_session_levels tests.test_trade_preview`.
4. If browser JS is changed inside the Python template, extract/check the affected script or run the existing local syntax-check pattern if one exists in the repo.

### Stage 1 - Add Missing Perf Attribution

Implementation:

1. Add server trace fields around `_options_cache_refresh_lock` in `refresh_options_cache_snapshot`.
2. Record at least:
   - `cache_refresh_lock_wait_ms`
   - `cache_refresh_lock_held_ms`
   - cache key match/miss state
   - cache age or minimum refresh age when available
   - whether the request fetched, reused cache, or returned stale cache
3. Add browser spans for selected quote stream handling in `syncTradeSelectedQuoteStream`, around parse/merge/signature/state write/render scheduling.
4. Add browser spans around route response parse and apply work:
   - `/trade_chain`: fetch, JSON parse, payload apply, rail render scheduling.
   - `/update_price`: fetch, JSON parse, price payload apply, chart update.
   - `/update`: fetch, JSON parse, data apply, chart/rail update.
5. Keep trace output gated behind `GEX_PERF_TRACE` and/or `localStorage.gexPerfTrace` so normal use is not noisy.

Likely code anchors:

- `_PerfTrace` near the top of `ezoptionsschwab.py`.
- `_options_cache_refresh_lock`.
- `refresh_options_cache_snapshot`.
- `/trade_chain` route.
- `gexPerfStart` / `gexPerfEnd`.
- `syncTradeSelectedQuoteStream`.
- `requestTradeChain`.
- `fetchPriceHistory`.
- `updateData`.
- `renderTradeActiveTrader`.
- `buildTradeActiveLadderHtml`.

Verification:

1. Start the traced app on a non-conflicting port.
2. Hit `/trade_chain` once cache-only and once with refresh enabled.
3. Confirm server logs include lock wait/held fields and cache/fetch outcome fields.
4. Open the app with browser tracing and confirm console `[perf]` lines include selected quote apply and route parse/apply spans.
5. Confirm no new trace noise appears when tracing is disabled.

Expected success:

- The next run can explain whether refresh-hit `/trade_chain` outliers are lock wait, browser parse/apply work, or another cause.
- Selected quote stream now has a measurable browser span.
- `applyRealtimeCandle` either appears in the active path or the code clearly shows why the candle update path is not reaching the traced span.

### Stage 2 - Fix `/trade_chain` Lock Contention If Confirmed

Implementation:

1. If no-fetch refresh outliers show high `cache_refresh_lock_wait_ms`, add a pre-lock fresh-cache check for the current ticker/expiry/strike-count context.
2. If a fetch is already in flight and a usable stale cache exists, return the stale cache quickly with trace metadata instead of blocking the fast lane.
3. Keep only the external fetch/update section protected by the minimum necessary lock scope.
4. Add a conservative backoff if Schwab fetch/current-price calls repeatedly exceed 2 seconds. During backoff, reuse the best available cache for fast-chain payloads while selected quote SSE remains the live quote lane.
5. Do not alter GEX/DEX/Vanna/Charm/Flow calculations.

Verification:

1. Monkeypatch or temporarily simulate a slow chain/current-price fetch in a controlled local test.
2. Fire two `/trade_chain?refresh_cache=1` requests close together.
3. Confirm the second request either returns quickly from usable cache/stale cache or logs the wait explicitly.
4. Confirm the trading rail does not blank when the fetch is slow or fails.
5. Confirm route metadata distinguishes fetched, reused, stale, and error fallback outcomes.

Expected success:

- Cache-only `/trade_chain` remains p95 below 100 ms.
- Refresh-hit/no-fetch `/trade_chain` p95 moves close to cache-only behavior, target below 150 ms.
- Fetch-backed refreshes can still be slow when Schwab is slow, but they no longer block every fast-chain consumer behind them.

### Stage 3 - Attribute And Reduce Browser Long Tasks

Implementation:

1. Use the Stage 1 browser spans to line up `browser_long_task` events with nearby route parse/apply/render spans.
2. If long tasks align with JSON parse, reduce payload or split parse/apply work.
3. If long tasks align with Plotly or chart updates, defer lower-priority chart work with a short timer or idle callback where safe.
4. If long tasks align with trade rail rendering, keep the render incremental and avoid rebuilding stable sections.
5. Do not introduce a JS framework.

Verification:

1. Capture at least 3-5 minutes of browser console `[perf]` logs with SPY 0DTE, Active Trader open, and a near-ATM call/put selected.
2. Parse long tasks and compare timestamps against route parse/apply spans.
3. Confirm quote tick spans are not sitting next to 300-600 ms long tasks.
4. Confirm the ladder viewport remains stable during the same run.

Expected success:

- `browser_long_task` count drops materially, or each remaining long task has a known source.
- `renderTradeActiveTrader` remains p95 below 20 ms.
- Selected quote apply path remains p95 below 20 ms.

### Stage 4 - Reduce `/update_price` Payload If Evidence Supports It

Implementation:

1. Map every active consumer of `/update_price` in `fetchPriceHistory` and downstream chart/update functions.
2. Separate initial/full-history loads from steady-state refreshes.
3. For steady-state, send only the newest candles/deltas needed by the current timeframe and visible overlays.
4. Gate heavy optional payloads behind the relevant overlay/toggle state when possible.
5. Keep timeframe switches, ticker switches, and overlay changes as explicit full-refresh triggers.

Verification:

1. Compare median response bytes before and after. Baseline median was 5028168 bytes.
2. Confirm 1 min and 5 min charts render correctly on first load.
3. Confirm timeframe switches force full refresh and do not leave gaps.
4. Confirm Options Vol and GEX overlay switches still render.
5. Confirm `/update_price` p95 remains below baseline and browser long tasks do not increase.

Expected success:

- Steady-state `/update_price` median bytes drop materially.
- No chart blanking, flicker, stale candles, or missing overlays.
- `applyRealtimeQuote` and Active Trader remain responsive during price refreshes.

### Stage 5 - Optimize `/update` Only After Isolation Is Proven

Implementation:

1. If `/update` correlates with long tasks or stale UI, inspect slow spans first.
2. Reuse recent fast-chain/cache results where safe.
3. Skip or defer unchanged secondary chart payload application.
4. Keep all analytical formulas unchanged.

Verification:

1. Confirm `/update` cadence remains near 60 seconds after warm-up.
2. Confirm GEX/DEX/key levels/dealer-impact/alerts do not blank.
3. Confirm formula output shape remains compatible with existing tests and UI consumers.
4. Confirm fast-lane route timings do not regress.

Expected success:

- Slow analytics no longer create visible jank or long tasks.
- Any timing improvement is secondary to preserving isolation.

### Stage 6 - Keep Order Polling Contained

Implementation:

1. Keep `/trade/orders` polling conditional on an open orders panel or active order intent.
2. Add trace metadata for Schwab call latency and backoff state.
3. Add conservative backoff if repeated account/order calls exceed 2 seconds.
4. Do not increase polling cadence.

Verification:

1. With no active intent and Orders closed, confirm `/trade/orders` is not called.
2. Open Orders and confirm polling starts.
3. Close Orders and confirm polling stops.
4. Stage an order with Auto Send off and confirm no preview/place/live-order endpoint is called.
5. Only if explicitly required, test Auto Send rejection with live trading disabled and confirm rejection behavior.

Expected success:

- Slow order API calls remain isolated from scalping quote/candle/ladder responsiveness.
- Account/order polling never becomes a background load source during normal scalping.

## Retest Plan After Implementation

Retest with the full matrix in `docs/SCALPING_SPEED_VALIDATION_PLAN.md`. Use this result note as the before-change baseline.

Recommended run:

1. Start traced server:

   ```bash
   PORT=5017 GEX_PERF_TRACE=1 FLASK_DEBUG=0 FLASK_USE_RELOADER=0 python3 ezoptionsschwab.py 2>&1 | tee /tmp/gex-speed-5017.log
   ```

2. Open `http://127.0.0.1:5017/` in the in-app browser.
3. Enable browser tracing:

   ```js
   localStorage.setItem('gexPerfTrace', '1')
   ```

4. Hard reload.
5. Use SPY nearest 0DTE if available, otherwise nearest 1DTE.
6. Test 1 min first, then 5 min.
7. Keep Active Trader open with a near-ATM call and put.
8. Keep Auto Send off.
9. Run at least:
   - First load/cache warm-up.
   - Underlying candle stream responsiveness.
   - Selected Active Trader quote stream for near-ATM call and put.
   - Active Trader ladder render churn and viewport stability.
   - Fast `/trade_chain` context lane.
   - Slow `/update` analytics isolation.
   - `/update_price` price-history lane diet.
   - Browser main-thread/long-task pressure.
   - SQLite/flow-alert hot path.
   - Order/account polling containment.
   - Context switch stress.
10. Capture at least 10 minutes if possible so `/update` has enough samples.
11. Parse server and browser logs using the parser commands from `docs/SCALPING_SPEED_VALIDATION_PLAN.md`.
12. Write a new result note, for example `docs/SCALPING_SPEED_RESULTS_2026-05-07.md`, or append a dated retest section here.

Compare against these baseline thresholds:

| Metric | 2026-05-06 baseline | Retest target |
| --- | ---: | --- |
| `/trade_chain` cache-only p95 | 8.8 ms | Stay below 100 ms. |
| `/trade_chain` refresh-hit/no-fetch p95 | 1702.4 ms | Move near cache-only behavior; target below 150 ms if no fetch is needed. |
| `/trade_chain` refresh-fetched p95 | 2414.4 ms | Improve if possible; more importantly, do not block stale/cache fallback. |
| `/update_price` median bytes | 5028168 | Drop materially if payload diet is implemented. |
| `/update` p95 | 5428.4 ms | Do not regress fast-lane responsiveness; optimize only if evidence supports it. |
| `renderTradeActiveTrader` p95 | 6.5 ms | Stay below 20 ms. |
| `buildTradeActiveLadderHtml` p95 | 0.4 ms | Stay below 10 ms. |
| selected quote apply path p95 | not captured | Add span; target below 20 ms. |
| `applyRealtimeCandle` | not captured | Add or explain span; verify live candle path. |
| `browser_long_task` p95 | 577.1 ms | Reduce materially or attribute every remaining long task. |

Retest pass criteria:

1. Candles and Active Trader remain responsive while `/update`, `/update_price`, and fetch-backed `/trade_chain` requests are in flight.
2. Active Trader selected quote updates continue without waiting for chain polls.
3. Ladder viewport does not jump during live quote/render churn.
4. No preview/place/live-order endpoints are used during normal tests.
5. No SQLite lock errors appear.
6. Route/browser summaries are captured with p50, p95, max, response sizes, and visible behavior notes.

## New Session Prompt

Use this prompt to start the next implementation session:

```text
We are in /Users/scottmunger/Desktop/Trading/Dashboards/GEX-Dashboard.

Read AGENTS.md and these docs first:
- docs/SCALPING_SPEED_RESULTS_2026-05-06.md
- docs/SCALPING_SPEED_VALIDATION_PLAN.md
- docs/SCALPING_PERFORMANCE_OPTIMIZATION_PLAN.md
- docs/SCALPING_FAST_LANES_FOLLOWUP_PLAN.md
- docs/ORDER_ENTRY_RAIL_SCALPING_REPAIR_PLAN.md

Confirm branch/worktree with:
- git branch -a
- git log --oneline main..HEAD
- git status --short

Create or switch to a focused branch such as codex/scalping-speed-followup if needed.

Do not place live orders. Keep Auto Send off for normal testing. Do not use preview/place/live-order endpoints unless the validation plan explicitly requires safe preview-only behavior. If testing Auto Send behavior, only do it with live trading disabled and confirm rejection behavior.

Implement the follow-up plan from docs/SCALPING_SPEED_RESULTS_2026-05-06.md:
1. Add missing server/browser perf attribution first.
2. Use that attribution to confirm whether /trade_chain refresh-hit outliers are cache-lock waits.
3. If confirmed, add a stale-cache or in-flight coalescing path so fast /trade_chain consumers do not wait behind slow Schwab fetches.
4. Attribute browser_long_task events around route JSON parse, route apply, chart work, and selected quote handling.
5. Reduce /update_price steady-state payload only if attribution shows it is contributing to parse/apply pressure.
6. Keep /update isolated and optimize it only if it correlates with browser long tasks or fast-lane staleness.
7. Keep /trade/orders polling conditional and add visibility/backoff for slow Schwab order calls if needed.

Respect repo constraints:
- No analytical formula changes.
- No JS framework introduction.
- Keep the single-file ezoptionsschwab.py structure.
- Use existing vanilla JS/CSS patterns.
- Do not revert unrelated user changes.

After implementation, run focused verification:
- python3 -m py_compile ezoptionsschwab.py
- git diff --check
- focused unit tests that exist for session levels/order preview
- JS syntax checks for changed embedded browser code if available

Then run the live scalping speed retest using docs/SCALPING_SPEED_VALIDATION_PLAN.md and compare against docs/SCALPING_SPEED_RESULTS_2026-05-06.md. Start the traced server on a non-conflicting port, preferably:
PORT=5017 GEX_PERF_TRACE=1 FLASK_DEBUG=0 FLASK_USE_RELOADER=0 python3 ezoptionsschwab.py 2>&1 | tee /tmp/gex-speed-5017.log

Open http://127.0.0.1:5017/ in the in-app browser, enable:
localStorage.setItem('gexPerfTrace', '1')
Then hard reload.

Use SPY nearest 0DTE if available, otherwise nearest 1DTE. Test 1 min first, then 5 min. Keep Active Trader open with a near-ATM call and put. Capture server perf logs, browser [perf] console logs, route cadence, p50/p95/max timings, response sizes, and visible lag/jank/stale quote behavior.

At the end, create a new dated results note under docs/ named like SCALPING_SPEED_RESULTS_YYYY-MM-DD.md. Final output should include code changes, tests run, route/browser before-after summaries, whether candles and Active Trader stayed responsive while slow requests were in flight, bottlenecks found with evidence, and recommended next actions.
```
