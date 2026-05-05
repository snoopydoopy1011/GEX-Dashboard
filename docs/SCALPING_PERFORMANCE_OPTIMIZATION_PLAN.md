# GEX Dashboard - Scalping Performance Optimization Plan

**Status:** Implementation in progress - Stages 0, 1, 2, 3, and 4 complete on `codex/scalping-performance-plan`
**Created:** 2026-05-05  
**Last updated:** 2026-05-05
**Primary file:** `ezoptionsschwab.py`  
**Target user workflow:** 0-1 DTE SPY option scalping with Active Trader ladder open  
**Active branch:** `codex/scalping-performance-plan`

---

## 0. Purpose

The dashboard has grown into a large single-file Flask + Plotly + Lightweight Charts app. The user still sees lag in the dashboard and especially in the Active Trader ladder while scalping 0-1 DTE SPY options. The goal of this effort is to validate the suspected bottlenecks with timing data, look for any additional bottlenecks during testing, then implement targeted speedups without cutting major features unless the data proves a feature is too expensive for the scalping workflow.

This is not an analytics redesign. Do not change GEX, DEX, Vanna, Charm, Flow, expected move, key level, contract helper, or order safety formulas. The focus is data freshness, fewer redundant calculations, less browser churn, and faster active-contract quote updates.

---

## 0.1 Implementation Log

Completed on `codex/scalping-performance-plan`:

- `e2a5b8c docs(perf): plan scalping dashboard speedup`
  - Created this implementation plan.
- `7c2ef28 perf: instrument scalping dashboard and stream option quotes`
  - Added Stage 0 performance tracing behind `GEX_PERF_TRACE=1` and `localStorage.gexPerfTrace`.
  - Server traces cover `/update`, `/update_price`, `/trade_chain`, `/trade/account_details`, and `/trade/orders`.
  - Browser traces cover `updateData`, fetches, Plotly renders, Active Trader render/ladder work, selected quote apply work, and long tasks.
  - Added the Stage 1 selected-contract option quote SSE endpoint at `/trade/quote_stream/<contract_symbol>`.
  - Extended `PriceStreamer` to subscribe/unsubscribe exact Schwab option symbols through `schwabdev.Stream.level_one_options(..., command="ADD")` and `command="UNSUBS"`.
  - Active Trader now merges live quotes only for the selected cached contract and disconnects/reconnects on selection, collapse, and ticker changes.
- `e79a276 perf: request active strike rail payload only`
  - Implemented Stage 2 request narrowing. The frontend sends `active_strike_rail_tab` and only one `show_<strike_rail_chart>` flag at a time for Strike Inspect Plotly payloads.
  - Utility chart visibility remains separate from strike-rail tab visibility.
  - Inactive Strike Inspect tabs keep using cached prior payloads and load fresh payloads on the next `/update` after tab selection.
  - Server perf traces now include `active_strike_rail_tab`.
- `7556926 perf(flow): share pulse snapshot across stats and blotter`
  - Added a short-lived, content-keyed flow pulse snapshot cache so copied DataFrames from the same chain snapshot can reuse one `build_flow_pulse_snapshot` scan.
  - `compute_trader_stats`, the 0DTE stats bundle, and `create_large_trades_table` now accept shared pulse snapshots while preserving their output shapes.
  - Alert cooldown checks still run inside `compute_trader_stats` for each scope; the shared snapshot only replaces duplicate pulse construction.
- `9a97c0d docs(perf): update scalping stage 3 progress`
  - Recorded the Stage 3 implementation details, live validation notes, and Stage 4 starting point.
- `75ceafc perf(db): throttle interval writes and cleanup`
  - Added in-process guards so `store_interval_data` skips duplicate writes for the same ticker/date/minute/strike-range/expiry-scope bucket unless forced.
  - Added a similar 5-minute guard for `store_centroid_data`.
  - Moved active-path `clear_old_data` calls behind an hourly in-process retention guard while preserving forced startup and end-of-day pruning.

Baseline findings collected before Stage 2:

- `/trade_chain` cached path is usually about 8-18 ms, with occasional 40-58 ms ticks.
- `/update_price` is about 1.1-3.3 s and is dominated by price history and trader stats work.
- `/update` is mostly about 1.0-1.8 s, with spikes from Schwab chain/quote latency.
- Active Trader render median is about 1.5 ms; ladder HTML is about 0.2 ms, so ladder rendering itself is not the main bottleneck.
- Browser long tasks still occur, with a sampled median around 392 ms and max around 1019 ms.

Live validation collected before Stage 3:

- `/update` with `active_strike_rail_tab=gex` and only `show_gamma=true` returned 150,767 bytes in 3.237 s and only ran `chart_gamma` among Strike Inspect chart builders.
- `/trade_chain` cached SPY 0DTE path returned 58 contracts in 9.7 ms.
- `/trade/quote_stream/SPY%20%20%20260505C00724000?ticker=SPY` returned 22 selected-contract quote events in 25 s with 144 ms median quote-time-to-receive latency.
- A second live `/update` attempt hit Schwab `401 Unauthorized` after token expiry, so broader p95 sampling still needs a fresh token session.

Validation already run for the implemented stages:

```bash
python3 -m py_compile ezoptionsschwab.py
git diff --check
python3 -m unittest tests.test_session_levels tests.test_trade_preview
python3 -c "import re, pathlib, ezoptionsschwab as m; html=m.app.test_client().get('/').get_data(as_text=True); scripts=re.findall(r'<script[^>]*>(.*?)</script>', html, re.S|re.I); pathlib.Path('/tmp/gex-inline-scripts.js').write_text('\n;\n'.join(scripts)); print('scripts', len(scripts))"
node --check /tmp/gex-inline-scripts.js
```

Tricky parts to preserve:

- Do not replace cached-chain contract validation. `/trade/quote_stream/<contract_symbol>` intentionally validates the selected symbol against the cached trade chain when `ticker` is supplied.
- The selected option stream is a narrow Active Trader fast lane, not a replacement for `/update` or `/trade_chain`. `/trade_chain` remains the source for contract universe, volume/OI context, helper rankings, and order preview/place validation.
- Live quote overrides must only apply when `msg.contract_symbol === tradeRailState.selectedSymbol`. Avoid stale stream events mutating a newly selected contract.
- EventSource closes are marked intentional before `close()` so selection/collapse changes do not poison reconnect cooldowns.
- Stage 2 depends on `_strikeRailLastPayloadByTab`; inactive tabs may temporarily show last-known data until the next `/update` after a tab switch.
- `show_price: false` must remain after spreading the narrowed visibility payload into `/update`; price history still comes from `/update_price` and underlying SSE.
- Stage 3's pulse cache is intentionally short-lived and keyed by chain content signatures rather than DataFrame identity so `/update` and `/update_price` can share copied snapshots without reusing stale flow.
- Stage 3 stores up to 4,000 pulse rows in the shared snapshot because `build_flow_pulse_snapshot` already scans and sorts the full in-range chain; stats consumers slice to their top 5 and the flow blotter can still enrich up to 4,000 rows.
- Stage 3 filters the shared pulse snapshot by `expiry_iso` before passing it to the 0DTE stats bundle; do not pass the unfiltered full-scope pulse into the nearest-expiry stats path.
- Stage 3 must not move `_alert_cooldown_ok` out of `compute_trader_stats`; full-scope alerts and 0DTE alerts use different `scope_id` values and need independent cooldown decisions.
- The shared pulse cache TTL is intentionally below the 8-second in-process history append threshold in `build_flow_pulse_snapshot`, so unchanged chain snapshots can still age into the history on later ticks.
- Stage 4 keeps the existing delete/insert table semantics because `interval_data`, `interval_session_data`, and `centroid_data` do not have unique constraints or expiry-scope columns.
- Stage 4 write guards track the latest physical ticker/time bucket signature, not every historical scope independently. If the user changes expiry scope inside the same minute, the new scope can still replace the current bucket.
- `store_interval_data(..., force=True)` and `store_centroid_data(..., force=True)` bypass duplicate skipping and update the guard signature for the next normal call.
- `clear_old_data(force=True)` is still used for startup and the end-of-day cleanup path; normal active writes run retention at most once per Eastern hour.
- Any trading rail markup change must still be mirrored in `buildTradeRailHtml()`.
- Any alerts/right-rail markup change must still be mirrored in `buildAlertsPanelHtml()`.

Remaining implementation starts at Stage 5 unless new live-market measurements show a different bottleneck.

---

## 1. Hard Constraints

- Do not change analytical formulas or trading signal math.
- Do not weaken any live-order safety gate.
- Do not bypass preview-token binding, cached-contract validation, `ENABLE_LIVE_TRADING=1`, or `SELL_TO_CLOSE` position caps.
- Do not reconstruct option symbols by hand. Use Schwab-returned contract symbols.
- Do not introduce a JS framework.
- Keep `ezoptionsschwab.py` as a single file.
- Use existing colors/tokens for UI changes.
- Any trading rail markup change must remain mirrored in `buildTradeRailHtml()` because the rail can rebuild.
- Any overview/right rail markup change must remain mirrored in `buildAlertsPanelHtml()`.
- Do not send a real live order during performance testing.

---

## 2. Current Audit Findings To Validate

These are findings from the read-only audit on 2026-05-05. Treat them as hypotheses until measured in a live or realistic session.

### F1 - Active Trader ladder is not fed by a fast option quote stream

Status: Addressed by Stage 1 in `7c2ef28`; still needs live-market validation.

Evidence anchors:

- `DASHBOARD_UPDATE_INTERVAL_MS`
- `TRADE_CHAIN_AUTO_REFRESH_MS`
- `requestTradeChain`
- `/trade_chain`
- `build_trading_chain_payload`
- `_options_cache`

Current behavior:

- The ladder selected contract comes from `tradeRailState.payload`.
- `tradeRailState.payload` comes from `/trade_chain`.
- `/trade_chain` builds from `_options_cache`.
- `_options_cache` is refreshed by the full `/update` option-chain path.
- The frontend forces `/trade_chain` only every `TRADE_CHAIN_AUTO_REFRESH_MS`.
- Current intervals are:
  - browser shell: dashboard update 1000 ms, trade chain 2500 ms
  - desktop shell: dashboard update 2000 ms, trade chain 5000 ms

Expected impact:

- The active contract bid/ask/last can lag behind the market by at least the trade-chain throttle plus any `/update` delay.
- This is likely the main reason the ladder feels slow for 0DTE SPY scalping.

Validation:

- Log the selected contract quote time, frontend receive time, and render time.
- Compare ladder bid/ask age against Schwab quote timestamps from chain snapshots.
- During market hours, compare chain-based ladder updates against the direct selected-contract quote stream.

### F2 - `/update_price` is heavier than the name implies

Evidence anchors:

- `/update_price`
- `get_price_history`
- `prepare_price_chart_data`
- `store_interval_data`
- `create_gex_side_panel`
- `compute_key_levels`
- `compute_top_oi_strikes`
- `compute_trader_stats`
- `stats_0dte`

Current behavior:

- `/update_price` does not only fetch price history.
- It can also rebuild the GEX side panel, key levels, top OI, full trader stats, and a second 0DTE stats bundle.
- It may call `store_interval_data` using cached chain data.
- `compute_trader_stats` itself computes key levels, max pain, level deltas, centroid payload, IV context, historical volatility, vol pressure, contract helper, 1DTE helper, flow pulse, scenarios, rule alerts, and flow alerts.

Expected impact:

- Price refresh work can compete with the full analytics update and the trading rail for CPU and browser render time.
- The endpoint name makes it easy to underestimate its cost.

Validation:

- Time each major block inside `/update_price`.
- Confirm how often it actually runs during normal use. The frontend throttles price history to 30 seconds unless forced, but it can be forced on ticker/settings changes.
- Confirm whether `store_interval_data` in `/update_price` is still needed once `/update` is healthy, or whether it can be gated.

### F3 - `/update` builds many Plotly payloads every analytics tick

Status: Addressed for Strike Inspect Plotly payloads by Stage 2 in `e79a276`; live gamma-tab smoke validated before Stage 3, but broader payload-size and p95 sampling still needs a fresh token session.

Evidence anchors:

- `/update`
- `CHART_VISIBILITY_DEFAULTS`
- `create_exposure_chart`
- `create_options_volume_chart`
- `create_open_interest_chart`
- `create_premium_chart`
- `create_large_trades_table`
- `renderStrikeRailPanel`

Current behavior:

- Default visible charts include `gamma`, `delta`, `vanna`, `charm`, `options_volume`, `open_interest`, and `premium`.
- Many of these are strike-rail charts, but the user only sees one strike-rail tab at a time.
- The server can build every enabled Plotly payload even if only one strike-rail metric is actively visible.
- The browser then parses JSON and can call `Plotly.react` for visible utility charts.

Expected impact:

- Server CPU and response payload size are likely too high for a 1 second analytics loop.
- For a scalper, active contract quotes should be fastest; secondary analytics can trail slightly.

Validation:

- Log per-chart server build time and serialized response size.
- In the browser, record time spent in `updateCharts`, `renderStrikeRailPanel`, and `Plotly.react`.
- Compare full default visibility against a scalp preset where only price, GEX, and minimal overlays are active.

### F4 - Flow pulse work appears duplicated

Status: Addressed by Stage 3 in `7556926`.

Evidence anchors:

- `build_flow_pulse_snapshot`
- `compute_trader_stats`
- `create_large_trades_table`

Previous behavior:

- `compute_trader_stats` builds `flow_pulse`.
- `create_large_trades_table` separately calls `build_flow_pulse_snapshot(... top_n=4000)` to enrich the flow blotter.
- Both can run from the same chain snapshot across `/update` and `/update_price`.

Current behavior:

- A short-lived shared pulse snapshot cache keys by ticker, session date, spot, strike range, and chain content signatures.
- `/update`, `/update_price`, `compute_trader_stats`, the 0DTE stats bundle, and `create_large_trades_table` can reuse the same-chain pulse rows.
- Flow alerts and pulse alerts still run through the existing per-scope cooldown checks.

Expected impact:

- Repeated DataFrame filtering and per-row iteration can add cost during the busiest ticks.

Validation:

- Add timing around each `build_flow_pulse_snapshot` call.
- Count rows processed and size of `_FLOW_CONTRACT_HISTORY`.
- Confirm whether the same snapshot can be built once and shared.

### F5 - SQLite interval writes do repeated cleanup and delete/insert work

Status: Addressed by Stage 4 in `75ceafc`; still needs live timing validation.

Evidence anchors:

- `store_interval_data`
- `store_centroid_data`
- `clear_old_data`
- `idx_interval_data_ticker_date_ts`

Current behavior:

- `store_interval_data` stores 1-minute data and now skips duplicate writes for the same ticker/date/minute/strike-range/expiry-scope signature unless forced.
- It still uses delete/insert for the current physical ticker/minute bucket because the table schema has no unique key or expiry-scope column.
- `store_centroid_data` stores 5-minute data and now skips duplicate writes for the same ticker/date/5-minute/expiry-scope signature unless forced.
- `/update_price` may also call `store_interval_data`, but the duplicate guard lets it share the minute bucket already written by `/update`.
- `clear_old_data` still runs on startup and forced end-of-day cleanup, but active write paths now hit retention at most once per Eastern hour.

Expected impact:

- Repeated SQLite cleanup and delete/insert cycles can add avoidable latency and lock contention.

Validation:

- Time `clear_old_data`, interval deletes, interval inserts, and centroid writes separately.
- Count how often each write path runs per minute.
- Confirm whether writes happen twice from `/update` and `/update_price` in the same minute.

### F6 - Browser-side live tick work can still be heavy

Evidence anchors:

- `connectPriceStream`
- `applyRealtimeQuote`
- `schedulePlotlyPriceLineUpdate`
- `updateAllPlotlyPriceLines`
- `PLOTLY_PRICE_LINE_CHARTS`
- `renderTradeActiveTrader`
- `renderTradeScalpTargets`

Current behavior:

- Underlying price SSE updates the Lightweight Chart and schedules current-price line updates across many Plotly chart IDs.
- Active Trader re-renders on a 1 second interval while open.
- `renderTradeActiveTrader` also calls `renderTradeScalpTargets`.
- `renderTradeScalpTargets` can recreate scalp target chart lines through `syncTradeScalpTargetLines`.

Expected impact:

- Even if backend speed improves, browser jank can remain when many Plotly charts and the trading rail are visible.

Validation:

- Use browser Performance panel or the Browser Use plugin trace while the ladder is open.
- Measure scripting time around `applyRealtimeQuote`, `updateAllPlotlyPriceLines`, `renderTradeActiveTrader`, and `syncTradeScalpTargetLines`.
- Confirm whether scalp target lines are being recreated even when inputs did not change.

---

## 3. Measurement Plan Before Implementing Speedups

Stage 0 instrumentation is implemented in `7c2ef28`. Continue to use it before and after each remaining optimization. Do not optimize blind.

### 3.1 Server timing instrumentation

Implemented behind `GEX_PERF_TRACE=1`. Keep it disabled by default.

Recommended route-level metrics:

- route name
- ticker
- selected expiries count
- strike range
- request start/end
- total duration ms
- response byte size if practical

Recommended block-level spans:

- `/update`
  - `fetch_options_for_date` or `fetch_options_for_multiple_dates`
  - `get_current_price`
  - `_options_cache` copy
  - `store_interval_data`
  - `store_centroid_data`
  - `create_strike_profile_payload`
  - each chart builder
  - `create_large_trades_table`
  - quote fetch and expected move block
- `/update_price`
  - `get_price_history`
  - `prepare_price_chart_data`
  - pinned expected move lookup
  - `store_interval_data`
  - `create_gex_side_panel`
  - `compute_key_levels`
  - `compute_top_oi_strikes`
  - `compute_trader_stats` full scope
  - `compute_trader_stats` 0DTE scope
- `/trade_chain`
  - cache lookup
  - `build_trading_chain_payload`
  - selected contract count
- `/trade/account_details`
  - Schwab call duration
  - payload normalization duration
- `/trade/orders`
  - Schwab call duration
  - payload normalization duration

Useful output format:

```text
[perf] route=/update ticker=SPY total_ms=842 fetch_chain_ms=410 charts_ms=215 flow_ms=78 db_ms=34 bytes=512338
[perf] route=/trade_chain ticker=SPY total_ms=18 contracts=80 selected=2026-05-05
```

### 3.2 Frontend timing instrumentation

Implemented behind the server flag and the local flag `localStorage.gexPerfTrace = "1"`.

Recommended frontend spans:

- `updateData` full cycle
- `fetch('/update')` network duration
- `updateCharts`
- `renderStrikeRailPanel`
- `Plotly.react` per chart
- `renderTradeRail`
- `renderTradeActiveTrader`
- `buildTradeActiveLadderHtml`
- `syncTradeScalpTargetLines`
- `applyRealtimeQuote`
- `updateAllPlotlyPriceLines`
- `requestTradeChain` network duration

Log only compact summaries so the console does not become the bottleneck.

### 3.3 Baseline test matrix

Run these in market hours if possible because option-chain freshness and streaming behavior matter most for this issue.

1. Browser shell baseline
   - `PORT=501x FLASK_DEBUG=0 FLASK_USE_RELOADER=0 python3 ezoptionsschwab.py`
   - SPY, nearest expiry selected
   - Active Trader open, selected ATM or 1 OTM contract
   - default chart visibility
   - collect 3-5 minutes of metrics

2. Desktop shell baseline
   - launch through the desktop path if that is the user workflow
   - same SPY contract setup
   - collect 3-5 minutes of metrics

3. Scalp-minimal visibility
   - price visible
   - only the active strike rail metric needed
   - minimal overlays
   - Active Trader open
   - collect 3-5 minutes of metrics

4. Order rail stress without live orders
   - Auto off
   - repeatedly stage ladder prices
   - preview only if needed
   - do not place live orders
   - verify ladder marker responsiveness

5. Account/order polling check
   - account selected
   - Orders collapsed, no active local intents
   - Orders open
   - local staged/previewed/sending intents
   - verify `/trade/orders` does not poll more often than intended

Record:

- median and p95 `/update`
- median and p95 `/update_price`
- median and p95 `/trade_chain`
- selected contract quote age shown in Active Trader
- time from selected contract quote change to ladder render
- browser long tasks over 50 ms
- visible stutter during ladder operation
- server log errors

---

## 4. Implementation Plan

Implement in small stages. After each stage, rerun the relevant metrics and compare against baseline.

### Stage 1 - Active contract option quote fast lane

Status: Implemented in `7c2ef28`; needs live-market validation.

Goal:

Feed the Active Trader ladder from a selected-contract quote stream instead of waiting for the full option-chain cache loop.

Backend plan:

- Extend `PriceStreamer` or add a sibling streamer path for selected option contracts.
- Use `schwabdev.Stream.level_one_options(keys, fields, command="ADD")`.
- Relevant local SDK field mapping lives in `schwabdev/translate.py` under `LEVELONE_OPTIONS`.
- Initial useful fields:
  - `0` Symbol
  - `2` Bid Price
  - `3` Ask Price
  - `4` Last Price
  - `8` Total Volume
  - `9` Open Interest
  - `10` Volatility
  - `20` Strike Price
  - `27` Days to Expiration
  - `28` Delta
  - `29` Gamma
  - `30` Theta
  - `31` Vega
  - `37` Mark Price
  - `38` Quote Time in Long
  - `39` Trade Time in Long
- Add an SSE endpoint such as `/trade/quote_stream/<path:contract_symbol>` or multiplex selected option quote messages through an existing stream manager.
- Use Schwab-returned `contract_symbol` from cached chain. Do not build symbols manually.
- Handle subscribe/unsubscribe when the selected contract changes.
- Keep the chain snapshot as the fallback if the option quote stream fails.

Frontend plan:

- Add selected-contract quote stream connection management:
  - connect when Active Trader is open and `tradeRailState.selectedSymbol` is set
  - reconnect on selected symbol change
  - disconnect when trade rail collapses or ticker changes
- Store live selected-contract quote override separately, for example `tradeRailState.liveSelectedQuote`.
- Merge the override into `getSelectedTradeContract()` or the Active Trader render path.
- Throttle ladder re-render with `requestAnimationFrame`, not a fixed network loop.
- Update only the Active Trader quote line and ladder rows when bid/ask/last/mark changes.
- Keep `/trade_chain` refresh for contract universe, volume/OI context, and fallback.

Validation:

- With the same selected SPY option, compare:
  - chain quote age
  - streamed option quote age
  - ladder render age
- Confirm ladder bid/ask changes without waiting for `/update` or `/trade_chain`.
- Confirm stream reconnects on contract selection.
- Confirm Auto off staging still uses the visible locked quote.
- Confirm Auto on still requires backend preview/place gates and does not skip safety.
- Confirm no live order is sent during testing.

Expected win:

- Largest perceived improvement for scalping. Active ladder should move with option quote stream cadence instead of 2.5-5 second cached-chain cadence.

Rollback:

- If stream fails, fall back to current cached `/trade_chain` behavior.

### Stage 2 - Build only active strike-rail chart payloads

Status: Implemented in `e79a276`; needs live-market before/after payload-size and p95 validation.

Goal:

Stop generating every strike-rail Plotly figure every analytics tick when only one strike-rail tab is visible.

Frontend plan:

- Include active strike-rail tab in the `/update` payload, for example `active_strike_rail_tab`.
- For strike-rail chart IDs, send `show_<id>` true only for the active strike-rail tab that needs a Plotly payload.
- Keep utility chart visibility separate from strike-rail chart visibility.
- Ensure `renderStrikeRailPanel` can show cached prior payload while the new active tab payload is loading.

Backend plan:

- Respect the narrowed `show_<id>` fields.
- Keep `create_strike_profile_payload` if needed for TradingView strike overlay, but measure it separately.
- Avoid creating `gamma`, `delta`, `vanna`, `charm`, `open_interest`, `options_volume`, and `premium` Plotly JSON unless requested.

Validation:

- Compare `/update` payload size before/after.
- Compare per-chart builder timings before/after.
- Switch strike-rail tabs and confirm each tab still loads correctly.
- Confirm disabled tabs show a loading/last-known state rather than breaking.

Expected win:

- Lower server CPU, smaller response JSON, less browser parsing.

Rollback:

- Re-enable the previous all-visible chart build path if tab-switch loading is unacceptable.

### Stage 3 - Share flow pulse and alert intermediate results

Status: Implemented in `7556926`.

Goal:

Avoid duplicate flow-pulse scans from the same chain snapshot.

Backend plan:

- Built a short-lived, content-keyed shared snapshot helper around `build_flow_pulse_snapshot`.
- `/update` now builds or reuses one shared pulse snapshot before rendering the flow blotter.
- `/update_price` now builds or reuses one shared pulse snapshot before rendering full and 0DTE stats.
- `compute_trader_stats` and `create_large_trades_table` accept a precomputed pulse snapshot and fall back to the shared helper when none is supplied.
- Output shape is unchanged.
- Alert cooldown behavior is preserved because `compute_trader_stats` still owns alert creation and `_alert_cooldown_ok` calls per scope.

Validation:

- Synthetic smoke test monkeypatched `build_flow_pulse_snapshot` and confirmed original DataFrames plus copied DataFrames reused one build: `build_calls=1`, `hit2=True`.
- `python3 -m py_compile ezoptionsschwab.py`
- `git diff --check`
- `python3 -m unittest tests.test_session_levels tests.test_trade_preview`
- Extracted inline JS from `app.test_client().get('/')` to `/tmp/gex-inline-scripts.js`.
- `node --check /tmp/gex-inline-scripts.js`

Expected win:

- Moderate CPU reduction during active flow-heavy sessions.

Rollback:

- Return to independent function calls if shared context causes stale/mismatched flow alert behavior.

### Stage 4 - Throttle SQLite interval writes and cleanup

Status: Implemented in `75ceafc`; live DB timing validation still needed.

Goal:

Keep historical data intact while avoiding repeated delete/insert and retention cleanup work.

Backend plan:

- Added a small in-process guard for interval writes:
  - key by ticker, date, interval timestamp, strike range, and selected expiry scope
  - only write once per matching minute signature unless explicitly forced
- Added a similar guard for centroid writes:
  - key by ticker, date, 5-minute timestamp, and selected expiry scope
  - only write once per matching 5-minute signature unless explicitly forced
- Moved normal active-path `clear_old_data` calls behind an hourly Eastern-time guard.
- Kept delete/insert instead of `INSERT OR REPLACE` because the tables still use autoincrement ids and have no unique constraints.
- Confirm `_fetch_vol_spike_data` still has enough fresh interval samples for flow alerts.

Validation:

- `python3 -m py_compile ezoptionsschwab.py`
- `git diff --check`
- `python3 -m unittest tests.test_session_levels tests.test_trade_preview`
- Extracted inline JS from `app.test_client().get('/')` to `/tmp/gex-inline-scripts.js`.
- `node --check /tmp/gex-inline-scripts.js`
- Live DB timing, interval row freshness, centroid row freshness, and volume-spike alert freshness still need a fresh Schwab token/live session.

Expected win:

- Lower SQLite lock time and less repeated work during fast polling.

Rollback:

- Disable guards or force writes if historical bubble/flow alert data becomes stale.

### Stage 5 - Reduce Active Trader browser churn

Status: Not started.

Goal:

Keep the ladder visually fresh while avoiding unnecessary DOM and chart-line work.

Frontend plan:

- Add a render signature to `renderTradeScalpTargets` similar to the ladder signature.
- Do not recreate scalp target price lines if selected symbol, basis, target profit, and line visibility have not changed.
- In the 1 second Active Trader interval, skip `renderTradeActiveTrader` if:
  - no selected contract
  - no live quote override change
  - no preview TTL display change
  - no order intent/account state change
- Keep the existing ladder signature guard, but verify it includes only fields that should trigger full ladder HTML replacement.
- If the selected option quote stream is active, use requestAnimationFrame batching for ladder updates.

Validation:

- Browser Performance trace should show fewer long tasks.
- Ladder should not lose click/drag/cancel handlers.
- Scroll position must remain stable.
- Scalp target lines must still update when quantity, basis, target, or selected contract changes.

Expected win:

- Lower UI jank when the order rail is open.

Rollback:

- Re-enable current periodic render if stale UI state appears.

### Stage 6 - Separate fast and slow analytics cadence

Status: Not started. Do this only after Stage 1 live quote behavior and Stage 2 payload reduction are validated.

Goal:

Make the scalping-critical path fast while allowing heavier analytics to refresh at a less aggressive cadence.

Plan:

- Keep selected option quote stream as fastest path.
- Keep underlying price SSE as current fast path for candles/last price.
- Consider changing full `/update` cadence after Stage 1:
  - browser: from 1000 ms to 2000-3000 ms
  - desktop: from 2000 ms to 3000-5000 ms
- Keep flow/alerts cadence acceptable for the user. Do not slow alerts until measured.
- Consider a user-facing "Scalp Mode" only after measuring. It could prioritize:
  - active option stream
  - price chart
  - Active Trader
  - nearest levels
  - compact flow alerts
  - deferred secondary Plotly payloads

Validation:

- Compare information freshness:
  - active option quote: streaming
  - underlying last price: streaming
  - analytics stats: slower but stable
  - flow alerts: still timely enough
- Confirm user does not lose critical context for 0DTE scalps.

Expected win:

- Lower steady-state CPU and network load while preserving fast trading information.

Rollback:

- Restore previous intervals if alerts or analytics feel stale.

---

## 5. Verification Commands

Run these after code changes:

```bash
python3 -m py_compile ezoptionsschwab.py
git diff --check
python3 -m unittest tests.test_session_levels tests.test_trade_preview
python3 -c "import re, pathlib, ezoptionsschwab as m; html=m.app.test_client().get('/').get_data(as_text=True); scripts=re.findall(r'<script[^>]*>(.*?)</script>', html, re.S|re.I); pathlib.Path('/tmp/gex-inline-scripts.js').write_text('\n;\n'.join(scripts)); print('scripts', len(scripts))"
node --check /tmp/gex-inline-scripts.js
```

For browser verification:

```bash
PORT=5017 FLASK_DEBUG=0 FLASK_USE_RELOADER=0 python3 ezoptionsschwab.py
```

Then open `http://127.0.0.1:5017/` and test:

- SPY selected
- nearest 0DTE expiry selected
- Active Trader open
- select ATM call and ATM put
- verify streamed selected-contract quotes update the ladder
- stage ladder prices with Auto off
- preview only if needed
- do not place live orders

---

## 6. Success Criteria

Target outcomes after all stages:

- Active ladder quote updates no longer depend on full chain refresh cadence.
- Active ladder feels responsive while SPY 0DTE quotes are moving.
- `/update` median and p95 duration drop materially from baseline.
- `/update` response size drops when only one strike-rail tab is active.
- `/trade_chain` remains fast and does not become the quote freshness bottleneck.
- Browser Performance traces show fewer long tasks with Active Trader open.
- Order safety behavior is unchanged.
- Analytics values match prior behavior for equivalent chain snapshots.

Suggested numeric targets after baseline is known:

- selected-contract stream-to-ladder render: under 250 ms median
- `/trade_chain`: under 100 ms median from cached chain
- `/update`: reduce median by at least 30 percent from baseline
- `/update` payload size: reduce by at least 30 percent in scalp-focused layout
- no repeated long tasks over 100 ms during normal ladder quote updates

---

## 7. New Session Starting Checklist

1. Confirm branch and worktree:

```bash
git branch -a
git log --oneline main..HEAD
git status --short
```

2. Read the authoritative docs:

```bash
sed -n '1,220p' docs/SCALPING_PERFORMANCE_OPTIMIZATION_PLAN.md
sed -n '1,220p' docs/OPTIONS_TRADING_RAIL_IMPLEMENTATION_PLAN.md
sed -n '1,220p' docs/ORDER_ENTRY_RAIL_SCALPING_REPAIR_PLAN.md
sed -n '1,220p' docs/ALERTS_RAIL_PHASE3_PLAN.md
```

3. Do not redo Stages 0-3 unless validation shows a regression. Start remaining implementation at Stage 4, or collect additional live-market p95 sampling first if the token session is fresh.

4. Implement one stage at a time. Commit each stage separately with timing results in the commit body.

Recommended commit subjects:

```text
perf(flow): share pulse snapshot across stats and blotter
perf(db): throttle interval writes and cleanup
perf(trade): reduce active ladder render churn
perf(update): separate fast quote and analytics cadence
```

---

## 8. Notes From The 2026-05-05 Audit

- The local Schwab SDK exposes `Stream.level_one_options(keys, fields, command="ADD")`, so a selected option quote stream is technically available.
- The dashboard already has underlying price SSE via `/price_stream/<ticker>`.
- Option streaming should be implemented as a narrow selected-contract fast lane, not as a full-chain streaming replacement.
- The current cached-chain path should remain as fallback and as the source for contract selection, helper rankings, volume/OI context, and preview validation.
- The most important user-facing speed improvement is likely the active ladder quote path, not reducing every analytics calculation.
