# GEX Dashboard - Scalping Fast Lanes Follow-Up Plan

**Status:** Proposed follow-up implementation plan  
**Created:** 2026-05-06  
**Primary file:** `ezoptionsschwab.py`  
**Related plan:** `docs/SCALPING_PERFORMANCE_OPTIMIZATION_PLAN.md`  
**Target workflow:** Fast SPY 0-1 DTE option scalping with candles, option-volume context, and Active Trader open  

---

## 0. Why This Plan Exists

This plan captures a follow-up direction from browser annotations made against the running Flask dashboard at `http://127.0.0.1:5001/`.

The user called out two related points:

1. The middle `Strike Inspect` rail can likely be removed. It was kept as a way to compare two strike metrics at once, for example GEX in the overlay and DEX in the side rail, but that workflow is no longer important enough to justify the width and code cost.
2. GEX, DEX, dealer reads, scenarios, key levels, and similar analytics do not need to update at scalping speed. The user cares most about fast candles, the options chain / Active Trader ladder, and selected-contract pricing. GEX is used more as context than as a tick-by-tick map. The user would accept slower GEX updates, likely around once per minute, if the dashboard becomes faster.

This document is intentionally standalone. A new Codex session should be able to start here without needing the browser comments or prior chat history.

---

## 1. Constraints

Preserve these constraints from `AGENTS.md` and prior plan docs:

- Do not change analytical formulas for GEX, DEX, Vanna, Charm, Flow, expected move, key levels, contract helper, or scenario math.
- Do not weaken live-order safety gates.
- Do not bypass preview-token binding, cached-contract validation, `ENABLE_LIVE_TRADING=1`, or `SELL_TO_CLOSE` position caps.
- Keep the app as a single-file Flask + vanilla JS + CSS app in `ezoptionsschwab.py`.
- Do not introduce a JS framework.
- Use existing CSS tokens for colors. Do not add raw neon hex colors.
- If changing the right rail overview markup, mirror changes in `buildAlertsPanelHtml()`.
- If changing trading rail markup, mirror changes in `buildTradeRailHtml()`.
- Do not send live orders during performance validation.

---

## 2. Current Relevant Architecture

Grep by anchor name instead of trusting line numbers. Current line numbers in this section are only orientation.

### 2.1 Existing Fast Lanes

Underlying candles already have a real-time path:

- `PriceStreamer`
- `/price_stream/<path:ticker>`
- `connectPriceStream`
- `applyRealtimeQuote`
- `applyRealtimeCandle`

Selected Active Trader contract quotes already have a narrow real-time path from the earlier scalping performance work:

- `PriceStreamer.subscribe_option`
- `/trade/quote_stream/<path:contract_symbol>`
- `syncTradeSelectedQuoteStream`
- `applyTradeSelectedQuoteMessage`

Those two paths should stay fast and independent.

### 2.2 Current Polling Constants

Current frontend constants:

- `DASHBOARD_UPDATE_INTERVAL_MS = IS_DESKTOP_SHELL ? 4000 : 2500`
- `DASHBOARD_ANALYTICS_UPDATE_INTERVAL_MS = DASHBOARD_UPDATE_INTERVAL_MS`
- `TRADE_CHAIN_AUTO_REFRESH_MS = IS_DESKTOP_SHELL ? 5000 : 2500`
- `PRICE_HISTORY_REFRESH_MS = 30000`
- `PLOTLY_PRICE_LINE_MIN_INTERVAL_MS = IS_DESKTOP_SHELL ? 1000 : 500`

Anchors:

- `DASHBOARD_UPDATE_INTERVAL_MS`
- `DASHBOARD_ANALYTICS_UPDATE_INTERVAL_MS`
- `TRADE_CHAIN_AUTO_REFRESH_MS`
- `PRICE_HISTORY_REFRESH_MS`
- `updateInterval = setInterval(updateData, DASHBOARD_ANALYTICS_UPDATE_INTERVAL_MS)`
- `startTradeChainAutoRefresh`
- `requestTradeChain`

Meaning today:

- `/update` still polls every 2.5s in browser and 4s in desktop.
- `/trade_chain` polls every 2.5s in browser and 5s in desktop when the trading rail is open, but it reads `_options_cache`.
- `/update_price` is throttled to 30s unless forced.
- Candles and selected-contract bid/ask are not dependent on these intervals.

### 2.3 Current Heavy Backend Paths

`/update` is the heavy options-chain and analytics route.

Anchors:

- `@app.route('/update', methods=['POST'])`
- `fetch_options_for_date`
- `fetch_options_for_multiple_dates`
- `get_current_price`
- `_options_cache[ticker] = {'calls': calls.copy(), 'puts': puts.copy(), 'S': S}`
- `store_interval_data`
- `store_centroid_data`
- `create_strike_profile_payload`
- `create_exposure_chart`
- `create_options_volume_chart`
- `create_open_interest_chart`
- `create_premium_chart`
- `create_large_trades_table`
- `price_info`

`/update_price` sounds light, but it still does analytics work when option cache is present.

Anchors:

- `@app.route('/update_price', methods=['POST'])`
- `get_price_history`
- `prepare_price_chart_data`
- `store_interval_data`
- `create_gex_side_panel`
- `compute_key_levels`
- `compute_top_oi_strikes`
- `get_shared_flow_pulse_snapshot`
- `compute_trader_stats_full`
- `compute_key_levels_0dte`
- `compute_trader_stats_0dte`

`/trade_chain` is already fast because it reads cache and does not call Schwab directly.

Anchors:

- `@app.route('/trade_chain', methods=['POST'])`
- `_options_cache.get(ticker)`
- `build_trading_chain_payload`

Important implication:

`/trade_chain` can refresh the ladder quickly only after some other route has refreshed `_options_cache`. If `/update` is slowed to 60s without adding a separate cache-refresh lane, the ladder's contract universe, volume, OI, IV, delta, helper rankings, and options-volume overlay can go stale. The selected bid/ask/last stream stays live, but only for the selected contract.

---

## 3. Current Strike Inspect Rail Surface

The user wants to remove the middle `Strike Inspect` rail. This is not just a visual delete; it removes a Plotly chart, a grid column, resize/collapse state, y-axis synchronization, and fallback rebuild code.

Current anchors:

- CSS layout:
  - `.chart-grid`
  - `--gex-col-w`
  - `.chart-grid > .gex-col-header`
  - `.chart-grid > .gex-column`
  - `.chart-grid > .gex-resize-handle`
  - `.gex-side-panel-wrap`
  - `#gex-side-panel`
  - `.strike-rail-tabs`
  - `.strike-rail-tab`
  - `.strike-rail-select`
  - `.gex-col-toggle`
  - `.chart-grid.gex-collapsed`
- Initial HTML:
  - `<div class="gex-col-header" id="gex-col-header">`
  - `<div class="gex-resize-handle" id="gex-resize-handle"...>`
  - `<div class="gex-column" id="gex-column">`
  - `<div id="gex-side-panel"></div>`
- Rebuild path:
  - `ensurePriceChartDom`
  - `ensureStrikeRailResizeHandle`
  - `showPriceChartUI`
- Strike rail JS:
  - `STRIKE_RAIL_CHART_IDS`
  - `STRIKE_RAIL_LABELS`
  - `STRIKE_RAIL_TAB_KEY`
  - `activeStrikeRailTab`
  - `applyStrikeRailTabs`
  - `wireStrikeRailTabs`
  - `getStrikeRailTarget`
  - `renderStrikeRailEmpty`
  - `renderStrikeRailPanel`
  - `renderGexSidePanel`
  - `syncGexPanelYAxisToTV`
  - `scheduleGexPanelSync`
  - `applyGexColumnCollapse`
  - `wireGexColumnToggle`
  - `wireStrikeRailResizeHandle`
- Plotly current-price line updates:
  - `PLOTLY_PRICE_LINE_CHARTS`
  - `updateAllPlotlyPriceLines`
  - `plotIds = PLOTLY_PRICE_LINE_CHARTS.concat(['gex-side-panel'])`

Current layout at desktop widths:

```css
.chart-grid {
    --gex-col-w: 292px;
    --rail-col-w: clamp(360px, 24vw, 430px);
    --trade-rail-w: clamp(360px, 24vw, 460px);
    grid-template-columns: minmax(0, 1fr) var(--gex-col-w) var(--rail-col-w) var(--trade-rail-w);
}
```

Desired layout after removal:

```css
.chart-grid {
    --rail-col-w: clamp(360px, 24vw, 430px);
    --trade-rail-w: clamp(360px, 24vw, 460px);
    grid-template-columns: minmax(0, 1fr) var(--rail-col-w) var(--trade-rail-w);
}
```

The right rail becomes column 2 and the trade rail becomes column 3 on desktop. The price chart gains the old strike rail width.

---

## 4. Desired End State

### 4.1 Visual End State

- Remove the `Strike Inspect` column entirely.
- Keep the on-chart strike overlay as the primary strike-context tool.
- Keep the right rail overview, levels, scenarios, and flow tabs.
- Keep the Active Trader rail.
- Let the main candle chart use the reclaimed width.
- Keep responsive behavior simple:
  - Desktop: price chart, right rail, trade rail.
  - Laptop width: price chart, right rail, trade rail, with existing collapse controls.
  - Narrow width: stack price chart, right rail, trade rail, secondary charts.
- Remove user-facing controls whose only purpose is the removed rail:
  - Strike Inspect header title/context
  - Strike Inspect metric dropdown/tabs
  - Strike Inspect collapse button
  - Strike Inspect resize handle
  - `gex.sidePanelCollapsed` and `gex.sidePanelWidthPx` behavior

### 4.2 Data Freshness End State

Separate the dashboard into explicit lanes:

| Lane | Freshness target | What it updates | Should block scalping? |
| --- | ---: | --- | --- |
| Underlying price stream | live/SSE | Candle close/high/low, live price label | No |
| Selected option quote stream | live/SSE | Selected contract bid/ask/last/mark in Active Trader | No |
| Fast chain snapshot | about 5s when needed | Cached option chain, ladder rows, volume/OI/IV/delta context, options-volume strike overlay | No, should be guarded against overlap |
| Price history snapshot | about 30s, plus forced ticker/timeframe changes | Candle history, volume profile/TPO profile from price candles, session levels | No |
| Slow analytics snapshot | about 60s | GEX/DEX, dealer impact, scenarios, key levels, flow alerts, GEX/DX/Vanna/Charm strike overlays | No |
| Account/order polling | existing guarded cadence | Buying power, positions, open/recent orders | No |

The most important design change is that fast ladder and option-volume context must not require the full analytics route.

### 4.3 Strike Overlay Freshness

The on-chart strike overlay should support two freshness classes:

Fast overlay metrics:

- `options_volume`
- `open_interest`
- `voi_ratio`
- Possibly `premium` if it is cheap and still useful in the overlay

Slow overlay metrics:

- `gex`
- `gamma`
- `delta`
- `vanna`
- `charm`

Reasoning:

- Options volume and OI are direct chain aggregations and are useful to the user while scalping.
- GEX/DEX/Vanna/Charm are context. They can update about once a minute.
- Full tick-by-tick options-volume for all strikes would require broad option streaming or very frequent chain calls. That is probably not worth the Schwab/API and browser load. The selected contract can still be truly live via the existing quote stream.

---

## 5. Recommended Implementation Stages

Do these stages in order. Do not combine the full plan into one large unreviewable edit.

### Stage 0 - Baseline And Branch Hygiene

Start every implementation session with:

```bash
git branch -a
git log --oneline main..HEAD
git status --short
```

Read these docs before changing code:

- `docs/SCALPING_FAST_LANES_FOLLOWUP_PLAN.md`
- `docs/SCALPING_PERFORMANCE_OPTIMIZATION_PLAN.md`
- `docs/UI_MODERNIZATION_PLAN.md`
- `docs/ANALYTICS_CHART_PHASE2_PLAN.md`
- `docs/ALERTS_RAIL_PHASE3_PLAN.md`

Recommended branch:

```bash
git checkout -b codex/scalping-fast-lanes-followup
```

Baseline run:

```bash
python3 -m py_compile ezoptionsschwab.py
git diff --check
```

Optional but useful:

```bash
PORT=5017 GEX_PERF_TRACE=1 FLASK_DEBUG=0 FLASK_USE_RELOADER=0 python3 ezoptionsschwab.py
```

Then open `http://127.0.0.1:5017/`, load SPY 0DTE, open Active Trader, and capture route timings with browser console perf trace enabled:

```js
localStorage.setItem('gexPerfTrace', '1')
```

### Stage 1 - Remove Strike Inspect Rail UI

Goal:

Remove the middle rail from layout and DOM without changing data cadences yet.

CSS tasks:

- Remove `--gex-col-w` from `.chart-grid`.
- Change desktop grid columns from four columns to three:
  - before: price, strike inspect, right rail, trade rail
  - after: price, right rail, trade rail
- Reassign grid columns:
  - `.workspace-toolbar-shell` -> column 1 row 1
  - `.right-rail-tabs` -> column 2 row 1
  - `.trade-rail-header` -> column 3 row 1
  - `.price-chart-container` -> column 1 row 2
  - `.right-rail-panels` -> column 2 row 2
  - `.trade-rail` -> column 3 row 2
  - `.right-rail-resize-handle` -> column 2 row 1 / span 2
  - `.trade-rail-resize-handle` -> column 3 row 1 / span 2
- Delete or leave unreachable only if necessary:
  - `.gex-col-header`
  - `.gex-column`
  - `.gex-resize-handle`
  - `.gex-side-panel-wrap`
  - `#gex-side-panel`
  - `.strike-rail-*`
  - `.gex-col-toggle`
  - `.chart-grid.gex-collapsed`
- Update responsive media blocks:
  - `@media screen and (max-width: 1400px)` should no longer move the strike rail below the price chart.
  - `@media screen and (max-width: 1024px)` should remove the strike rail rows from `grid-template-rows`.
  - Renumber later rows after removing `.gex-col-header` and `.gex-column`.

Initial HTML tasks:

- Remove the initial `<div id="gex-col-header">`.
- Remove the initial `<div id="gex-resize-handle">`.
- Remove the initial `<div id="gex-column">` and `#gex-side-panel`.
- Keep `right-rail-tabs`, `right-rail-panels`, `trade-rail-header`, `trade-rail`, `flow-event-lane`, and `journal-workspace`.

JS rebuild tasks:

- In `ensurePriceChartDom`, stop creating:
  - `.gex-col-header`
  - `#strike-rail-tabs`
  - `#gex-resize-handle`
  - `.gex-column`
  - `#gex-side-panel`
- Remove calls to:
  - `ensureStrikeRailResizeHandle`
  - `applyStrikeRailTabs`
  - `renderStrikeRailPanel`
- In `showPriceChartUI`, remove these ids from the display list:
  - `gex-col-header`
  - `gex-resize-handle`
  - `gex-column`
- In the `price` hidden branch inside `updateCharts`, stop trying to hide/purge `gex-column`.
- In the global resize handler, remove the block that applies `--gex-col-w`.

JS strike rail tasks:

- Remove or dead-code-prune the side-panel-only functions:
  - `getVisibleStrikeRailTabs`
  - `applyStrikeRailTabs`
  - `wireStrikeRailTabs`
  - `getStrikeRailTarget`
  - `renderStrikeRailEmpty`
  - `_strikeRailLastPayloadByTab`
  - `getStrikeInspectRowsForActiveTab`
  - `updateStrikeInspectContext`
  - `getStrikeRailPayloadKey`
  - `getStrikeRailSyncSpec`
  - `applyStrikeRailSyncToFigure`
  - `lockStrikeRailFigureInteractions`
  - `buildStrikeRailFigure`
  - `renderStrikeRailPanel`
  - `renderGexSidePanel`
  - `syncGexPanelYAxisToTV`
  - `scheduleGexPanelSync`
  - `getGexColWidthConstraints`
  - `clampGexColWidth`
  - `applyGexColWidth`
  - `scheduleGexResizeRefresh`
  - `ensureStrikeRailResizeHandle`
  - `isGexColumnCollapsed`
  - `applyGexColumnCollapse`
  - `wireGexColumnToggle`
  - `wireStrikeRailResizeHandle`
  - `restoreGexColumnCollapse`
  - `restoreGexColumnWidth`
- Keep the on-chart strike overlay functions:
  - `setStrikeOverlayProfiles`
  - `drawTVStrikeOverlay`
  - `scheduleTVStrikeOverlayDraw`
  - `renderStrikeOverlayControls`
  - `syncStrikeOverlayControls`
  - strike overlay localStorage keys
- Update `renderStrikeOverlayControls` so turning the overlay on does not call `applyGexColumnCollapse(true)`.
- Update any code that checks `isGexColumnCollapsed()` before drawing or syncing. For overlay code, this check should disappear. For deleted side rail code, the function should disappear entirely.

Plotly current-price line tasks:

- Remove `gex-side-panel` from `updateAllPlotlyPriceLines`.
- `PLOTLY_PRICE_LINE_CHARTS` should only include real secondary Plotly chart ids.

Backend tasks in this stage:

- Stop returning or rendering `gex_panel` if no frontend consumes it.
- In `/update_price`, remove the `create_gex_side_panel` block or gate it behind a temporary disabled flag.
- It is acceptable to leave `create_gex_side_panel` defined but unused for one stage if that reduces risk. A later cleanup can delete the function after `rg "create_gex_side_panel|gex_panel|gex-side-panel"` confirms no live references.

Stage 1 validation:

```bash
python3 -m py_compile ezoptionsschwab.py
git diff --check
python3 -c "import re, pathlib, ezoptionsschwab as m; html=m.app.test_client().get('/').get_data(as_text=True); scripts=re.findall(r'<script[^>]*>(.*?)</script>', html, re.S|re.I); pathlib.Path('/tmp/gex-inline-scripts.js').write_text('\\n;\\n'.join(scripts)); print('scripts', len(scripts))"
node --check /tmp/gex-inline-scripts.js
rg -n "gex-side-panel|gex-column|gex-col-header|gex-resize-handle|Strike Inspect|strike-inspect|strike-rail|renderStrikeRailPanel|syncGexPanelYAxisToTV|scheduleGexPanelSync|create_gex_side_panel" ezoptionsschwab.py
```

Expected `rg` result after a full cleanup: no live references. If `create_gex_side_panel` is intentionally left unused for one commit, document that in the commit message and keep the rest at zero.

Browser smoke:

- The price chart should reclaim the old middle rail width.
- Right rail tabs still work.
- Active Trader still opens and keeps selected quote stream live.
- Strike overlay still toggles on the price chart.
- No blank column appears between chart and right rail.
- Resizing right rail and trade rail still works.

### Stage 2 - Introduce Explicit Lane Constants

Goal:

Make code terminology match the desired architecture before moving endpoint work.

Recommended constants:

```js
const ANALYTICS_REFRESH_MS = 60000;
const FAST_CHAIN_REFRESH_MS = IS_DESKTOP_SHELL ? 5000 : 5000;
const PRICE_HISTORY_REFRESH_MS = 30000;
const TRADE_CHAIN_AUTO_REFRESH_MS = FAST_CHAIN_REFRESH_MS;
```

Notes:

- Keep selected option quote SSE live; do not put it behind `FAST_CHAIN_REFRESH_MS`.
- Keep underlying price SSE live.
- Keep the 1-second Active Trader render stale-check if it remains signature-gated.
- Consider `FAST_CHAIN_REFRESH_MS = 2500` in browser only if market-hours testing shows `/trade_chain` plus cache refresh is still light enough. Start conservatively at 5000 ms because selected bid/ask is already live.

Update names:

- `updateInterval` can become `analyticsUpdateInterval`.
- `DASHBOARD_ANALYTICS_UPDATE_INTERVAL_MS` can become `ANALYTICS_REFRESH_MS`.
- Keep compatibility if renaming would cause a large diff. Clear comments are more important than perfect naming.

Stage 2 validation:

- Auto-update button still pauses and resumes all polling timers as expected.
- Pausing Auto-Update should disconnect underlying price stream, stop analytics polling, and stop fast chain polling. If selected option quote stream currently stops only through existing collapse/selection logic, confirm pause behavior intentionally.
- Resuming Auto-Update should reconnect underlying price stream and restart timers.

### Stage 3 - Extract Shared Options Cache Refresh Helper

Goal:

Create a backend helper that can refresh `_options_cache` without building analytics charts.

Recommended helper shape:

```python
def refresh_options_cache_snapshot(
    ticker,
    expiry_dates,
    *,
    exposure_metric="Open Interest",
    delta_adjusted=False,
    calculate_in_notional=True,
    min_age_ms=0,
    force=False,
):
    """Fetch Schwab chain and current price if cache is stale, then update _options_cache.

    Returns:
        {
            "calls": calls,
            "puts": puts,
            "S": S,
            "cache_hit": bool,
            "fetched": bool,
            "cache_key": str,
            "fetched_at": epoch_ms,
        }
    """
```

Cache metadata should include enough fields to avoid reusing the wrong chain:

- ticker
- selected expiries
- exposure metric
- delta adjusted flag
- calculate-in-notional flag
- fetched timestamp
- current price timestamp if available

Suggested `_options_cache[ticker]` shape after this stage:

```python
_options_cache[ticker] = {
    "calls": calls.copy(),
    "puts": puts.copy(),
    "S": S,
    "meta": {
        "ticker": ticker,
        "expiry_dates": list(expiry_dates),
        "exposure_metric": exposure_metric,
        "delta_adjusted": bool(delta_adjusted),
        "calculate_in_notional": bool(calculate_in_notional),
        "fetched_at_ms": int(time.time() * 1000),
        "cache_key": cache_key,
    },
}
```

Threading:

- Flask runs with `threaded=True`.
- Add a narrow `threading.Lock` around cache refresh to avoid two simultaneous Schwab chain fetches for the same ticker/settings.
- Do not hold the lock while doing slow analytics calculations if avoidable.
- A per-key in-flight guard is better than one global lock, but a simple lock is acceptable if scoped tightly.

Use the helper from `/update` first, preserving current behavior:

- `/update` should call the helper with `force=True` or with a low `min_age_ms`.
- It should still get fresh chain data at the current analytics cadence until later stages slow it down.
- Preserve existing error handling and response shapes.

Stage 3 validation:

- `/update` behavior is unchanged from the user's perspective.
- `_options_cache` still feeds `/trade_chain`.
- Selected contract validation still works.
- No formulas change.

### Stage 4 - Add Fast Chain Snapshot Lane

Goal:

Let Active Trader and fast strike overlay metrics update without invoking the full analytics route.

Implementation options:

Option A, preferred: extend `/trade_chain`.

- Add request flag `refresh_cache: true`.
- When present, call `refresh_options_cache_snapshot(...)` before `build_trading_chain_payload`.
- Keep default `/trade_chain` behavior as cache-only for callers that do not request refresh.

Option B: add a new endpoint.

- Add `/trade_chain_snapshot` or `/fast_chain_snapshot`.
- It refreshes cache and returns:
  - trade chain payload
  - fast strike profiles
  - cache metadata
  - minimal price info if useful

Option A has less frontend churn. Option B makes the lane boundary clearer. Either is acceptable if the implementation is well documented.

Recommended response additions for fast lane:

```json
{
  "contracts": [],
  "expiries": [],
  "selected_expiries": [],
  "warnings": [],
  "cache_meta": {
    "fetched": true,
    "cache_hit": false,
    "fetched_at_ms": 1234567890
  },
  "strike_profiles": {
    "options_volume": [],
    "open_interest": [],
    "voi_ratio": []
  }
}
```

Backend helper for fast profiles:

- Modify `create_strike_profile_payload` to accept an optional `metrics` list.
- Or add a wrapper:

```python
def create_fast_strike_profile_payload(calls, puts, S, strike_range, selected_expiries=None):
    return create_strike_profile_payload(
        calls,
        puts,
        S,
        strike_range,
        selected_expiries=selected_expiries,
        metrics=["options_volume", "open_interest", "voi_ratio"],
    )
```

Do not recompute GEX/DEX/Vanna/Charm in the fast endpoint.

Frontend changes:

- Update `requestTradeChain` to send `refresh_cache: true` only on the periodic open-rail fast timer, not on every user click.
- Keep no-overlap protection:
  - If `tradeRailState.loading` for the same key, skip.
  - Keep `lastChainRequestAt`.
  - Add a separate `lastCacheRefreshAt` if needed.
- On response:
  - Continue rendering Active Trader from the returned trade chain.
  - Merge fast `strike_profiles` into `tvStrikeOverlayProfiles`.
  - Do not erase slow metrics when only fast metrics are returned.

Recommended merge helper:

```js
function mergeStrikeOverlayProfiles(nextProfiles, source = 'fast') {
    if (!nextProfiles || typeof nextProfiles !== 'object') return;
    tvStrikeOverlayProfiles = Object.assign({}, tvStrikeOverlayProfiles, nextProfiles);
    if (lastData) {
        lastData.strike_profiles = Object.assign({}, lastData.strike_profiles || {}, nextProfiles);
    }
    scheduleTVStrikeOverlayDraw();
}
```

Then:

- `setStrikeOverlayProfiles` can remain for full replacement from slow analytics.
- Fast chain lane should call `mergeStrikeOverlayProfiles`.

Stage 4 validation:

- With Auto-Update on and Active Trader open, `/trade_chain` or the new fast endpoint should refresh the cache at `FAST_CHAIN_REFRESH_MS`.
- Selected contract bid/ask should still update through SSE even between chain snapshots.
- Options volume overlay should update on fast chain cadence.
- GEX overlay should not be overwritten or blanked by fast profile updates.
- If Schwab chain refresh is slow, the UI should skip overlapping requests rather than stacking them.

### Stage 5 - Slow Analytics To About Once Per Minute

Goal:

Move GEX-style analytics to a slow cadence after the fast chain lane exists.

Frontend changes:

- Analytics timer uses `ANALYTICS_REFRESH_MS = 60000`.
- `updateData` remains the slow analytics refresh function unless renamed.
- User setting changes still call `updateData()` immediately when they materially affect analytics.
- Ticker/expiry changes should force both:
  - fast chain refresh immediately
  - slow analytics refresh immediately
- Timeframe changes should force price history immediately but should not necessarily force full analytics unless analytics output depends on timeframe. Current `compute_trader_stats(..., price_data, timeframe)` does use price data for some context, so safest initial behavior is to force analytics on timeframe change and optimize later.

Backend changes:

- `/update` can remain the slow analytics route.
- It should use `refresh_options_cache_snapshot` with `min_age_ms` so it can reuse a very recent fast-chain cache instead of refetching the chain immediately.
- It should still force refresh when:
  - ticker changed
  - expiry selection changed
  - exposure metric changed
  - delta-adjusted setting changed
  - calculate-in-notional changed
  - cache is older than the analytics freshness target

Response shape:

- Preserve existing fields consumed by right rail and secondary charts.
- Since Strike Inspect is removed, do not build old strike-rail Plotly payloads just for side-panel tabs.
- Slow analytics should still return slow strike overlay profiles:
  - `gex`
  - `gamma`
  - `delta`
  - `vanna`
  - `charm`
- If secondary Plotly charts remain enabled, they can update on slow cadence. This is acceptable for scalping.

Important:

- Do not remove `create_exposure_chart`, `create_options_volume_chart`, or secondary chart support unless explicitly choosing a later cleanup. The user asked to remove Strike Inspect, not the lower secondary charts.

Stage 5 validation:

- Confirm route cadence in browser network panel:
  - `/price_stream/...`: live
  - `/trade/quote_stream/...`: live when contract selected
  - fast chain endpoint: around every 5s when trading rail open
  - `/update_price`: at most every 30s unless forced
  - `/update`: around every 60s unless forced
- Right rail GEX/DEX values should update around once per minute.
- Options volume overlay should update faster than GEX if enabled.
- Selected contract bid/ask should not wait for `/update`.

### Stage 6 - Diet `/update_price`

Goal:

Keep price history fast and stop `/update_price` from doing slow analytics work every 30s.

Current problem:

`/update_price` currently returns:

- price history
- session levels
- key levels
- 0DTE key levels
- top OI
- trader stats
- 0DTE trader stats
- GEX panel

Recommended target:

`/update_price` should normally return only:

- `price`
- `session_levels`
- `session_levels_meta`
- `top_oi` only if it is cheap and required for active indicators

Move these to slow analytics:

- `gex_panel` (or delete with Strike Inspect)
- `key_levels`
- `key_levels_0dte`
- `trader_stats`
- `stats_0dte`
- `flow_pulse_snapshot_shared`

Frontend changes:

- In `fetchPriceHistory`, stop calling:
  - `renderGexSidePanel`
  - `renderTraderStats`
  - `redrawGexScope`
- Keep:
  - `applyPriceData`
  - `renderSessionLevels`
  - `renderTopOI` if returned
- Slow analytics response should call:
  - `renderTraderStats`
  - `renderRailKeyLevels`
  - `renderKeyLevels`
  - `renderScenarioTable` when active
  - `mergeStrikeOverlayProfiles` or `setStrikeOverlayProfiles`

Potential concern:

Key-level price lines on the chart may update less often. That is the point for GEX-derived levels. The user indicated GEX is contextual, not the fastest scalp input. Underlying candles and selected quote stay live.

Stage 6 validation:

- `/update_price` response size should drop materially.
- `/update_price` server time should drop materially, especially when cache exists.
- Candle history should still load quickly on first page load.
- Session levels and volume/TPO profile should still render.
- Right rail analytics should not blank during the 30s price refresh.

### Stage 7 - Cleanup And Remove Dead Server Code

Goal:

After all behavior is verified, remove dead code left behind for safety.

Candidate removals:

- `create_gex_side_panel` if no longer referenced.
- Strike Inspect CSS.
- Strike Inspect localStorage keys.
- GEX side-panel collapse/resize functions.
- `active_strike_rail_tab` request payload, if it no longer drives anything.
- `_strikeRailLastPayloadByTab`.
- `gex_panel` response field.
- Any `gexScope` UI that only controlled right-rail 0DTE vs all should be reviewed carefully before removal. Do not delete it if it still controls overview rail scope.

Required greps:

```bash
rg -n "gex-side-panel|gex-column|gex-col-header|gex-resize-handle|Strike Inspect|strike-inspect|strike-rail|active_strike_rail_tab|renderStrikeRailPanel|create_gex_side_panel|gex_panel" ezoptionsschwab.py docs
```

Only expected matches after cleanup should be historical docs unless the code intentionally keeps compatibility fields.

---

## 6. Acceptance Criteria

Functional:

- Candles still update live while Auto-Update is on.
- Selected Active Trader contract bid/ask/last updates live through the option quote stream.
- Active Trader can still preview orders using cached-contract validation.
- The price chart gets the old Strike Inspect width.
- No blank middle column remains.
- Right rail overview, levels, scenarios, and flow tabs still render.
- On-chart strike overlay still works.
- Options volume overlay updates on the fast chain cadence.
- GEX/DEX analytics update on the slow cadence and do not blank between refreshes.
- Ticker/expiry changes force immediate chain, price, and analytics refreshes.
- Pausing Auto-Update stops polling and live streams consistently with existing behavior.

Performance:

- `/trade_chain` or the new fast chain endpoint does not stack overlapping requests.
- `/update` route frequency is around 60s during normal scalping.
- `/update_price` route frequency remains around 30s and is lighter than before.
- `/update_price` no longer spends time in `compute_trader_stats_full`, `compute_trader_stats_0dte`, or `create_gex_side_panel` during normal price refreshes.
- Browser long tasks decrease during active scalping.
- Main candle chart and selected contract quote feel responsive even when slow analytics is refreshing.

Regression:

- No analytical formula changes.
- No order safety changes.
- No framework introduction.
- `buildAlertsPanelHtml()` still mirrors right rail overview markup.
- `buildTradeRailHtml()` still mirrors trading rail markup.

---

## 7. Suggested Validation Commands

Minimum local validation after each code stage:

```bash
python3 -m py_compile ezoptionsschwab.py
git diff --check
python3 -m unittest tests.test_session_levels tests.test_trade_preview
python3 -c "import re, pathlib, ezoptionsschwab as m; html=m.app.test_client().get('/').get_data(as_text=True); scripts=re.findall(r'<script[^>]*>(.*?)</script>', html, re.S|re.I); pathlib.Path('/tmp/gex-inline-scripts.js').write_text('\\n;\\n'.join(scripts)); print('scripts', len(scripts))"
node --check /tmp/gex-inline-scripts.js
```

Browser validation:

1. Start traced server:

```bash
PORT=5017 GEX_PERF_TRACE=1 FLASK_DEBUG=0 FLASK_USE_RELOADER=0 python3 ezoptionsschwab.py
```

2. Open `http://127.0.0.1:5017/`.
3. In browser console:

```js
localStorage.setItem('gexPerfTrace', '1')
```

4. Load SPY 0DTE.
5. Open Active Trader and select a near-ATM call or put.
6. Confirm selected quote stream shows live updates.
7. Confirm route cadence in network panel.
8. Toggle strike overlay to `Options Vol`, then to `GEX`, and confirm fast vs slow freshness behavior.

Server trace expectations:

- Fast chain route logs should show low route time when cache is fresh and bounded route time when refresh is needed.
- Slow `/update` logs should appear about once per minute during steady state.
- `/update_price` should no longer log heavy trader stats spans after Stage 6.

---

## 8. Risks And Mitigations

Risk: Slowing `/update` makes `_options_cache` stale.

Mitigation:

- Do not slow `/update` until Stage 4 adds a fast cache refresh lane.
- Keep selected option quote SSE live for bid/ask/last.

Risk: Fast chain refresh calls Schwab too often.

Mitigation:

- Start at 5s cadence.
- Add no-overlap guards.
- Reuse cache when settings have not changed and cache age is below `min_age_ms`.
- Consider pausing fast refresh when trade rail is collapsed and options-volume overlay is off.

Risk: Fast profile updates erase slow GEX overlay data.

Mitigation:

- Merge fast profile subsets into `tvStrikeOverlayProfiles`; do not replace the whole object.
- Keep slow analytics profiles until the next slow refresh.

Risk: Removing Strike Inspect breaks right-rail/toolbar alignment.

Mitigation:

- Update both initial markup and `ensurePriceChartDom`.
- Update desktop and responsive CSS together.
- Test desktop, <=1400px, and <=1024px widths.

Risk: `/update_price` diet removes key-level overlays users expect.

Mitigation:

- Preserve the last-known key-level overlays from slow analytics.
- Force slow analytics on ticker/expiry/settings changes.
- Make the freshness tradeoff explicit in chart context if needed.

Risk: Order entry loses cached-contract validation.

Mitigation:

- Keep `/trade_chain` or fast snapshot as the source of contract universe.
- Do not build orderable symbols manually.
- Keep preview/place validation paths unchanged.

---

## 9. Implementation Notes For A Fresh Session

Useful grep anchors:

```bash
rg -n "DASHBOARD_UPDATE_INTERVAL_MS|DASHBOARD_ANALYTICS_UPDATE_INTERVAL_MS|TRADE_CHAIN_AUTO_REFRESH_MS|PRICE_HISTORY_REFRESH_MS|updateInterval|startTradeChainAutoRefresh|requestTradeChain" ezoptionsschwab.py
rg -n "gex-side-panel|gex-column|gex-col-header|gex-resize-handle|Strike Inspect|strike-inspect|strike-rail|renderStrikeRailPanel|scheduleGexPanelSync|syncGexPanelYAxisToTV" ezoptionsschwab.py
rg -n "@app.route\\('/update'|@app.route\\('/update_price'|@app.route\\('/trade_chain'|_options_cache|create_strike_profile_payload|compute_trader_stats|create_gex_side_panel" ezoptionsschwab.py
rg -n "setStrikeOverlayProfiles|drawTVStrikeOverlay|scheduleTVStrikeOverlayDraw|activeStrikeOverlayMetric|STRIKE_OVERLAY" ezoptionsschwab.py
```

Suggested commit boundaries:

1. `docs(perf): plan scalping fast lanes follow-up`
2. `refactor(ui): remove strike inspect rail`
3. `perf(update): name fast and slow dashboard lanes`
4. `perf(chain): refresh cached option chain without analytics`
5. `perf(overlay): split fast strike profiles from slow greeks`
6. `perf(analytics): slow GEX rail refresh cadence`
7. `perf(price): remove analytics work from price history refresh`
8. `chore(perf): remove dead strike rail code`

Do not skip Stage 1 validation before starting endpoint work. Removing the visual rail first reduces the number of moving parts when splitting data lanes.

---

## 10. Progress Log

### Stage 1 - Strike Inspect rail removal landed

Branch: `codex/scalping-fast-lanes-followup`
Commit subject: `refactor(ui): remove strike inspect rail`

What changed:

- Removed the middle `Strike Inspect` rail from the desktop, laptop, and narrow responsive grid. The main price chart now sits directly beside the right rail and Active Trader rail.
- Removed initial DOM and `ensurePriceChartDom()` rebuild creation for `gex-col-header`, `gex-resize-handle`, `gex-column`, and `gex-side-panel`.
- Removed the side-panel-only JS path: rail tab state, collapse/resize persistence, y-axis sync, Plotly render cache, and the `renderGexSidePanel()`/`renderStrikeRailPanel()` flow.
- Kept the on-chart strike overlay and renamed the shared metric label/list constants so overlay code is no longer coupled to the removed rail.
- Stopped `/update_price` from building or returning `gex_panel`, and removed the unused `create_gex_side_panel()` backend function.
- Kept right rail tabs, Active Trader rail, candle rendering, selected option quote SSE, order preview/place safety paths, and secondary Plotly charts intact.

Tricky parts:

- Stage 2 of the older performance plan had narrowed `/update` chart payloads around the active Strike Inspect tab. Once that rail was removed, the secondary Plotly charts needed to render normally again, so `buildUpdateChartVisibilityPayload()` now sends plain chart visibility and `updateCharts()` no longer filters gamma/delta/vanna/charm/options-volume/OI/premium out as rail-only charts.
- Right-rail and trade-rail resize constraints previously subtracted `--gex-col-w`; those constraints now compute available width without any middle-rail width.
- The strike overlay used the old rail metric labels and previously collapsed the rail when toggled on. The overlay now owns its metric list directly and toggles without changing layout.
- `create_gex_side_panel()` was removed in Stage 1 rather than left unused, so cleanup greps against `ezoptionsschwab.py` are expected to return no live matches.

Validation completed:

```bash
python3 -m py_compile ezoptionsschwab.py
git diff --check
python3 -c "import re, pathlib, ezoptionsschwab as m; html=m.app.test_client().get('/').get_data(as_text=True); scripts=re.findall(r'<script[^>]*>(.*?)</script>', html, re.S|re.I); pathlib.Path('/tmp/gex-inline-scripts.js').write_text('\\n;\\n'.join(scripts)); print('scripts', len(scripts))"
node --check /tmp/gex-inline-scripts.js
rg -n "gex-side-panel|gex-column|gex-col-header|gex-resize-handle|Strike Inspect|strike-inspect|strike-rail|renderStrikeRailPanel|syncGexPanelYAxisToTV|scheduleGexPanelSync|create_gex_side_panel" ezoptionsschwab.py
python3 -m unittest tests.test_session_levels tests.test_trade_preview
```

Notes:

- `py_compile` passed with the pre-existing inline-template `SyntaxWarning: invalid escape sequence '\('`.
- The Stage 1 cleanup grep returned no matches in `ezoptionsschwab.py`; broader greps still match historical plan docs by design.

### Stage 2 - Explicit lane constants landed

Branch: `codex/scalping-fast-lanes-followup`
Commit subject: `perf(update): name fast and slow dashboard lanes`

What changed:

- Replaced the older `DASHBOARD_UPDATE_INTERVAL_MS` / `DASHBOARD_ANALYTICS_UPDATE_INTERVAL_MS` naming with an explicit `ANALYTICS_REFRESH_MS` slow-lane constant.
- Added `FAST_CHAIN_REFRESH_MS` and made `TRADE_CHAIN_AUTO_REFRESH_MS` an alias of that lane, starting conservatively at 5 seconds in both desktop and browser shells.
- Renamed the auto-update interval handle to `analyticsUpdateInterval` and added `startAnalyticsAutoRefresh()` / `stopAnalyticsAutoRefresh()` helpers so pause/resume and unload cleanup name the timer they control.
- Kept the underlying price SSE path, selected Active Trader quote SSE path, and the 1-second signature-gated Active Trader stale-check path intact.
- Added a displayed-Plotly resize guard for secondary tabs and fullscreen/window resize paths so hidden Plotly divs are not resized while inactive tabs are `display: none`.

Tricky parts:

- This stage intentionally does not add the Stage 3/4 cache-refresh helper yet, so the fast chain lane still reads the existing cached `/trade_chain` payload. The selected option bid/ask/last lane remains live through SSE between chain snapshots.
- Pausing Auto-Update now stops the analytics timer, fast chain timer, and underlying price stream through named helpers. The selected option quote stream is still controlled by its existing selection/collapse lifecycle, which preserves the live Active Trader quote path requested for scalping.
- The reported PySide terminal error (`Resize must be passed a displayed plot div element`) came from Plotly resize/react work against hidden secondary-tab containers. Hidden chart renders now disable Plotly's responsive resize hook, and explicit resize calls skip non-displayed plots.

Validation completed:

```bash
python3 -m py_compile ezoptionsschwab.py
git diff --check
python3 -c "import re, pathlib, ezoptionsschwab as m; html=m.app.test_client().get('/').get_data(as_text=True); scripts=re.findall(r'<script[^>]*>(.*?)</script>', html, re.S|re.I); pathlib.Path('/tmp/gex-inline-scripts.js').write_text('\\n;\\n'.join(scripts)); print('scripts', len(scripts))"
node --check /tmp/gex-inline-scripts.js
rg -n "gex-side-panel|gex-column|gex-col-header|gex-resize-handle|Strike Inspect|strike-inspect|strike-rail|renderStrikeRailPanel|syncGexPanelYAxisToTV|scheduleGexPanelSync|create_gex_side_panel" ezoptionsschwab.py
python3 -m unittest tests.test_session_levels tests.test_trade_preview
```

Notes:

- `py_compile` passed with the pre-existing inline-template `SyntaxWarning: invalid escape sequence '\('`.
- Inline script extraction found 5 scripts, and `node --check /tmp/gex-inline-scripts.js` passed.
- The targeted Strike Inspect / gex-side-panel grep returned no matches in `ezoptionsschwab.py`.
- The focused unit suite ran 36 tests successfully. The test harness printed the expected `disabled in tests` Schwab client messages.

### Stage 3 - Shared options cache refresh helper landed

Branch: `codex/scalping-fast-lanes-followup`
Commit subject: `perf(chain): refresh cached option chain without analytics`

What changed:

- Added `refresh_options_cache_snapshot(...)` as the shared backend lane for refreshing `_options_cache` without building Plotly charts, trader stats, key levels, flow pulse snapshots, or order data.
- Added cache metadata keyed by ticker, selected expiries, exposure metric, delta-adjusted flag, calculate-in-notional flag, and fetch timestamps.
- Added a narrow `_options_cache_refresh_lock` around cache refresh work so simultaneous callers do not overlap Schwab chain fetches.
- Routed `/update` through the helper with `force=True`, preserving the current user-facing behavior that a slow analytics tick still fetches a fresh chain and current price.
- Kept `/trade_chain` cache-only for this stage, so selected-contract cached validation and trade-chain response shape stay unchanged until Stage 4 intentionally adds the fast refresh flag.

Tricky parts:

- The helper preserves the existing `/update` perf span names (`fetch_chain`, `get_current_price`, `options_cache_copy`) by accepting the route perf tracer rather than hiding that timing inside one opaque helper span.
- Cache hits are intentionally only allowed when callers pass a positive `min_age_ms`; the current `/update` path uses `force=True` so Stage 3 does not silently change freshness behavior.
- Empty option-chain fetches or missing current price do not replace the last good cache entry. `/update` still returns the same error responses for empty chain data or missing current price.
- The lock is held only for the cache refresh lane. Slow analytics and chart construction still run outside the lock after the helper returns.

Validation completed:

```bash
python3 -m py_compile ezoptionsschwab.py
git diff --check
python3 -c "import re, pathlib, ezoptionsschwab as m; html=m.app.test_client().get('/').get_data(as_text=True); scripts=re.findall(r'<script[^>]*>(.*?)</script>', html, re.S|re.I); pathlib.Path('/tmp/gex-inline-scripts.js').write_text('\\n;\\n'.join(scripts)); print('scripts', len(scripts))"
node --check /tmp/gex-inline-scripts.js
rg -n "gex-side-panel|gex-column|gex-col-header|gex-resize-handle|Strike Inspect|strike-inspect|strike-rail|renderStrikeRailPanel|syncGexPanelYAxisToTV|scheduleGexPanelSync|create_gex_side_panel" ezoptionsschwab.py
python3 -m unittest tests.test_session_levels tests.test_trade_preview
```

Notes:

- `py_compile` passed with the pre-existing inline-template `SyntaxWarning: invalid escape sequence '\('`.
- Inline script extraction found 5 scripts, and `node --check /tmp/gex-inline-scripts.js` passed.
- The targeted Strike Inspect / gex-side-panel grep returned no matches in `ezoptionsschwab.py`.
- The focused unit suite ran 36 tests successfully. The test harness printed the expected `disabled in tests` Schwab client messages.

Next stage:

- Stage 4 should add the fast chain snapshot lane, most likely by extending `/trade_chain` with `refresh_cache: true`, and merge fast strike profiles without overwriting slow GEX/DEX/Vanna/Charm overlay data.

---

## 11. Open Decisions

These should be decided during implementation based on live timings:

- Fast chain cadence: start at 5s. If live market testing shows it is cheap and useful, consider 2.5s in browser.
- Whether fast chain refresh should run when the trading rail is collapsed but options-volume overlay is active. Recommended: yes, if active overlay metric is `options_volume`; no otherwise.
- Whether `/trade_chain` should be extended with `refresh_cache` or a new endpoint should be created. Recommended: extend `/trade_chain` unless response shape gets too muddy.
- Whether `top_oi` belongs in `/update_price` or slow analytics. Recommended: keep only if an enabled indicator needs it and it stays cheap.
- Whether GEX key-level overlays should show an "updated Xs ago" label. Recommended: add only if the slower cadence causes confusion.
