# GEX Dashboard - Live Scalping Speed Validation Plan

**Status:** Ready for live market-hours validation
**Created:** 2026-05-06
**Primary file:** `ezoptionsschwab.py`
**Target workflow:** SPY 0-1 DTE option scalping with candles, options context, and Active Trader open

---

## 0. Purpose

This document is the market-hours test plan for validating whether the recent performance work actually made the dashboard fast enough for scalp execution.

The implementation plans already describe what changed. This document describes how to measure the current system, how to identify bottlenecks, and what evidence should trigger the next optimization pass.

Do not send live orders during this validation unless the user explicitly asks at action time. The normal speed test uses Auto Send off, or Auto Send on with `ENABLE_LIVE_TRADING` disabled so order placement rejects safely.

---

## 1. Source Review

Current worktree review on 2026-05-06:

- Branch: `main`
- `git log --oneline main..HEAD`: no commits ahead of `main`
- Untracked file present and ignored for this plan: `Trading_from_dashboard.txt`

Recent relevant commits on `main`:

- `d90bf7a` merge of `codex/scalping-fast-lanes-followup`
- `fcf45c1` `fix(perf): harden fast lane cache refresh`
- `15a7f67` `chore(perf): remove dead strike rail code`
- `9114aa9` `perf(price): remove analytics work from price history refresh`
- `30d0376` `perf(analytics): slow GEX rail refresh cadence`
- `42ec3af` `perf(overlay): split fast strike profiles from slow greeks`
- `0fc6769` `perf(chain): refresh cached option chain without analytics`
- `34710fc` `perf(update): name fast and slow dashboard lanes`
- `a795620` merge of `codex/scalping-performance-plan`
- `956c770` `perf(update): separate fast quote and analytics cadence`
- `b423a67` `perf(trade): reduce active ladder render churn`
- `75ceafc` `perf(db): throttle interval writes and cleanup`
- `7556926` `perf(flow): share pulse snapshot across stats and blotter`
- `7c2ef28` `perf: instrument scalping dashboard and stream option quotes`

Authoritative docs reviewed:

- `docs/SCALPING_PERFORMANCE_OPTIMIZATION_PLAN.md`
- `docs/SCALPING_FAST_LANES_FOLLOWUP_PLAN.md`
- `docs/ORDER_ENTRY_RAIL_SCALPING_REPAIR_PLAN.md`
- `docs/DASHBOARD_AUDIT_FINDINGS_AND_IMPLEMENTATION_PLAN.md`
- `docs/ALERTS_RAIL_PHASE3_PLAN.md`
- `docs/UX_STABILITY_REFINEMENT_PLAN.md`

---

## 2. Current Fast/Slow Lane Model

The current architecture intentionally separates scalping-critical paths from heavier analytics.

| Lane | Endpoint or path | Current cadence | Primary consumer | Expected behavior |
| --- | --- | --- | --- | --- |
| Underlying quote/candles | `/price_stream/<ticker>` SSE, `applyRealtimeQuote`, `applyRealtimeCandle` | Live stream | Price chart candles and last price | Must not wait for `/update`, `/update_price`, or `/trade_chain` |
| Selected option quote | `/trade/quote_stream/<contract_symbol>` SSE, `applyTradeSelectedQuoteMessage`, `scheduleTradeActiveTraderRender` | Live stream | Active Trader bid/mid/ask/ladder | Must update selected contract between chain snapshots |
| Fast chain/context | `/trade_chain` with periodic `refresh_cache: true` | 5 seconds while trade rail is open | Contract picker, ladder rows, volume/OI/IV/delta context, fast strike overlays | Should be guarded against overlap and cheap on cache hits |
| Price history snapshot | `/update_price` | 30 seconds unless forced by ticker/settings/timeframe | Historical candles, session levels, VP/TPO/RVOL/top OI overlay support | Should not rebuild trader stats or key levels |
| Slow analytics | `/update` | 60 seconds | GEX/DEX charts, key levels, trader stats, flow blotter, scenarios | Can be slower, but must not freeze candles or Active Trader |
| Order/account polling | `/trade/orders`, `/trade/account_details` | Guarded and conditional | Active order reconciliation, buying power, positions | Should run only when needed and stay out of quote/candle hot path |

Frontend constants in `ezoptionsschwab.py`:

```js
const ANALYTICS_REFRESH_MS = 60000;
const FAST_CHAIN_REFRESH_MS = 5000;
const PRICE_HISTORY_REFRESH_MS = 30000;
const TRADE_CHAIN_AUTO_REFRESH_MS = FAST_CHAIN_REFRESH_MS;
```

Server tracing is controlled by `GEX_PERF_TRACE=1`. Browser tracing is controlled by:

```js
localStorage.setItem('gexPerfTrace', '1')
```

---

## 3. Prior Baselines To Compare Against

From the existing implementation docs:

- Baseline `/trade_chain` cached path: usually about 8-18 ms, with occasional 40-58 ms ticks.
- Baseline `/update_price`: about 1.1-3.3 seconds, dominated by price history and trader stats work before the price-lane diet.
- Baseline `/update`: mostly about 1.0-1.8 seconds, with Schwab chain/quote latency spikes.
- Active Trader render baseline: median about 1.5 ms, ladder HTML about 0.2 ms.
- Browser long tasks baseline: sampled median about 392 ms, max about 1019 ms.
- Live selected quote stream sample: 22 selected-contract quote events in 25 seconds with about 144 ms median quote-time-to-receive latency.
- Stage 5 live route sample, before the final fast-lane follow-up:
  - `/update`: n=5, median 977.6 ms, p95 1417.0 ms, about 146 KB responses.
  - `/update_price`: n=5, median 1094.1 ms, p95 2264.4 ms, about 5.0 MB responses.
  - `/trade_chain`: n=10, median 5.7 ms, p95 6.5 ms, 58 contracts, about 34 KB response.

The final follow-up changed the cadence and ownership of these routes, so compare the new results by lane:

- `/update` can still take around a second, but it should run only about once per minute.
- `/trade_chain` should carry fast chain context without running slow analytics.
- `/update_price` should no longer include `trader_stats`, `key_levels`, `stats_0dte`, or `key_levels_0dte`.
- Selected option quote and underlying candle SSE should continue updating while slow requests are in flight.

---

## 4. Success Targets

Use these as practical scalp-performance targets. If the market is unusually quiet, focus more on route timing and browser long tasks than perceived quote movement.

| Metric | Target | Optimization trigger |
| --- | --- | --- |
| Selected option stream to Active Trader render | p50 under 250 ms, p95 under 500 ms when quote timestamps are available | p95 over 750 ms, or ladder waits for `/trade_chain` |
| `renderTradeActiveTrader` browser span | p50 under 5 ms, p95 under 20 ms | Repeated p95 over 50 ms or visible ladder jank |
| `buildTradeActiveLadderHtml` browser span | p50 under 2 ms, p95 under 10 ms | Repeated p95 over 25 ms |
| Underlying `applyRealtimeQuote` browser span | p50 under 5 ms, p95 under 20 ms | Repeated p95 over 50 ms or candle freezes |
| `/trade_chain` cache-hit route | p50 under 25 ms, p95 under 100 ms | Cache-hit p95 over 150 ms |
| `/trade_chain` refresh route | Bounded and no overlap; acceptable if Schwab fetch dominates | Concurrent refreshes, blank payloads, or repeated multi-second route times |
| `/update_price` route | p50 under 1500 ms, p95 under 3000 ms | Repeated p95 over 4000 ms or chart blanks/flickers |
| `/update` route | p50 under 1500 ms, p95 under 3000 ms at 60s cadence | Slow analytics causes browser long tasks or blocks fast lanes |
| Browser long tasks | No repeated long tasks over 100 ms during normal quote updates | Long tasks align with every quote, chain refresh, or analytics response |
| Trade rail scroll stability | No jump back to Active Trader while scrolled lower | Any repeated scroll jump during streaming updates |
| Quote age display | Selected contract quote age stays current while stream is live | Quote age rises despite stream status `Live` |

---

## 5. Test Setup

### 5.1 Start a traced server

Run a dedicated traced server on a non-conflicting port:

```bash
PORT=5017 GEX_PERF_TRACE=1 FLASK_DEBUG=0 FLASK_USE_RELOADER=0 python3 ezoptionsschwab.py 2>&1 | tee /tmp/gex-speed-5017.log
```

If port 5017 is busy, use another approved test port and keep the log path obvious:

```bash
PORT=5021 GEX_PERF_TRACE=1 FLASK_DEBUG=0 FLASK_USE_RELOADER=0 python3 ezoptionsschwab.py 2>&1 | tee /tmp/gex-speed-5021.log
```

Before measuring, confirm token health:

```bash
curl -s http://127.0.0.1:5017/token_health
```

Expected: `access_token_valid=true` and `api_ok=true`. If token health is not good, refresh the Schwab session before collecting timing samples.

### 5.2 Browser setup

1. Open `http://127.0.0.1:5017/`.
2. Open browser DevTools.
3. Console:

   ```js
   localStorage.setItem('gexPerfTrace', '1')
   ```

4. Hard reload.
5. Network panel:
   - Preserve log: on
   - Disable cache: on
   - Filter: `fetch`, `xhr`, `eventstream`
6. Console:
   - Keep `[perf]` lines visible.
   - Clear console immediately before each timed run.
7. Performance panel:
   - Record at least one 60-120 second sample with Active Trader open.

### 5.3 Test state

Use the same starting state for each run:

- Ticker: `SPY`
- Expiry: nearest 0DTE if available; otherwise nearest 1DTE
- Timeframe: `1 min` first, then `5 min`
- Active Trader rail: open
- Selected contracts: near-ATM call and near-ATM put, tested separately
- Auto Send: off for the normal test
- Live trading env: leave disabled unless explicitly testing rejection flow
- Overlays: start with scalp-relevant defaults, then test options volume and heavier overlays separately

Warm up for 2 minutes before counting samples. The first load includes cache population and should be recorded separately from steady state.

---

## 6. Data To Capture

For every run, capture:

- Server log containing `[perf] route=...` lines.
- Browser console copy or screenshot containing `[perf] span=...` lines.
- Network HAR if route cadence or payload size looks suspicious.
- A short note of what was on screen: ticker, expiry, selected contract, timeframe, overlays, Active Trader open/collapsed, Auto Send state.
- Subjective observation: candle lag, ladder lag, scroll jumps, chart flicker, stale quote warnings.

Recommended sample sizes:

- `/trade_chain`: at least 100 steady-state samples.
- `/update_price`: at least 20 steady-state samples.
- `/update`: at least 10 steady-state samples, which requires about 10 minutes at 60s cadence.
- Selected option quote stream: at least 50 quote events per selected contract during active movement.
- Browser Performance recording: at least 60 seconds with Active Trader open and selected quote stream live.

---

## 7. Test Matrix

### Test A - First load and cache warm-up

Goal: confirm the first cache population does not leave the chart or trade rail in a broken state.

Steps:

1. Start traced server.
2. Load SPY.
3. Select nearest 0DTE or 1DTE expiry.
4. Open Active Trader.
5. Wait for the first `/update`, `/update_price`, and `/trade_chain`.

Record:

- First `/update` total time, bytes, `options_cache_hit`, `options_cache_fetched`.
- First `/update_price` total time, bytes, `get_price_history_ms`, `prepare_price_chart_data_ms`.
- First `/trade_chain` total time, `refresh_cache`, `cache_refresh_hit`, `cache_refresh_fetched`, `contracts`.
- Browser console errors.

Pass:

- Price chart renders.
- Trade rail contract picker renders.
- First cached chain produces contracts.
- No traceback in server log.
- Browser console has no errors except known Plotly CDN warning.

### Test B - Underlying candle stream responsiveness

Goal: prove candles update through SSE and do not wait for slow analytics.

Steps:

1. Use `1 min` timeframe.
2. Keep SPY chart visible.
3. Let the chart run for 5 minutes during active price movement.
4. Repeat on `5 min` timeframe.
5. Watch a slow `/update` in flight and confirm live candles still move.

Record:

- Browser spans: `applyRealtimeQuote`, `applyRealtimeCandle`, `browser_long_task`.
- Network eventstream status for `/price_stream/SPY`.
- Any oversized candle flash, stale intrabar volume, or candle timer wrap.
- Whether `applyRealtimeQuote` spans cluster around slow `/update` responses.

Pass:

- Last price and current candle update while `/update` is running.
- No repeated `applyRealtimeQuote` spans over 50 ms.
- No recurring browser long tasks over 100 ms caused by quote ticks.
- Candle timer stays in place.

### Test C - Selected Active Trader quote stream

Goal: prove selected-contract bid/ask/last updates without waiting for `/trade_chain`.

Steps:

1. Open Active Trader.
2. Select a near-ATM 0DTE call.
3. Wait for quote stream status to show live selected quote behavior.
4. Watch for 3-5 minutes.
5. Switch to a near-ATM put and repeat.
6. Collapse Active Trader, expand it again, and confirm the stream reconnects.

Record:

- Browser spans: selected quote apply path, `renderTradeActiveTrader`, `buildTradeActiveLadderHtml`.
- Network eventstream status for `/trade/quote_stream/<contract_symbol>`.
- Quote age chip.
- Whether ladder bid/ask changes between `/trade_chain` responses.
- Stream reconnect behavior after contract switch and collapse/expand.

Pass:

- Quote age stays current while stream is live.
- Active Trader quote text shows live updates.
- Ladder prices update between `/trade_chain` polls.
- No stale stream event mutates the newly selected contract after switching.
- Render p95 stays below the targets in section 4.

### Test D - Active Trader ladder render churn and viewport stability

Goal: catch browser-side jank even if network routes are fast.

Steps:

1. Keep a selected contract streaming.
2. Scroll the trade rail below Active Trader to Contract Picker or Orders.
3. Leave it there for 3-5 minutes.
4. Stage a ladder price with Auto Send off.
5. Clear the local marker.
6. Repeat with bid/ask moving if the market is active.

Record:

- `renderTradeActiveTrader` and `buildTradeActiveLadderHtml` spans.
- Any `browser_long_task` spans.
- Whether the rail scroll position jumps upward.
- Whether ladder center changes on normal quote movement.
- Whether gray current-price marker moves inside stable rows.

Pass:

- No scroll jump back to Active Trader.
- Ladder rows do not recenter every tick.
- Local staged marker appears immediately and clears cleanly.
- Bid/ask shading remains cell-based, not full-row wash.

### Test E - Fast chain/context lane

Goal: validate `/trade_chain` as a fast context refresh lane and not a hidden analytics path.

Steps:

1. Keep trade rail open.
2. Leave Auto Update on.
3. Observe `/trade_chain` requests for at least 10 minutes.
4. Toggle strike overlay to `Options Vol`.
5. Toggle strike overlay back to `GEX`.
6. Change expiry or exposure setting once and observe immediate cache refresh behavior.

Record:

- Route cadence: expected about every 5 seconds while trade rail is open.
- Server fields: `refresh_cache`, `cache_refresh_hit`, `cache_refresh_fetched`, `cache_refresh_error`, `cache_hit`, `contracts`, `create_fast_strike_profile_payload_ms`.
- Response payload `cache_meta`.
- Whether requests overlap in Network panel.
- Whether fast profile keys are limited to `open_interest`, `options_volume`, and `voi_ratio` on refresh-cache calls.

Pass:

- Periodic open-rail requests use `refresh_cache: true`.
- Manual clicks do not force Schwab chain fetches unless context changed.
- No overlapping `/trade_chain` refreshes for the same context.
- Cache hit route stays under target.
- Cache refresh failure shows old cached chain with warning instead of blanking the rail.

### Test F - Slow analytics lane isolation

Goal: verify `/update` can be slow without affecting candles or Active Trader.

Steps:

1. Keep Active Trader open and selected quote stream live.
2. Run for at least 10 minutes.
3. Observe `/update` cadence and route timing.
4. During a visible `/update` request, watch candle and ladder updates.

Record:

- Route cadence: expected about every 60 seconds.
- Server spans: `fetch_chain`, `get_current_price`, `options_cache_copy`, `store_interval_data`, chart builders, `flow_pulse_snapshot_shared`, `create_large_trades_table`, `analytics_price_history`, `compute_key_levels`, `compute_trader_stats_full`, `compute_key_levels_0dte`, `compute_trader_stats_0dte`.
- Browser spans after `/update`: `updateData`, `fetch:/update`, `updateCharts`, Plotly render spans if emitted, `browser_long_task`.
- Any UI blanking or stale chart state.

Pass:

- `/update` runs about once per minute.
- Candles and selected option quote continue moving while `/update` is in flight.
- No repeated browser long task over 100 ms after each `/update`.
- Slow analytics data updates without blanking GEX/DEX/key-level rails.

### Test G - Price history lane diet

Goal: verify `/update_price` is now a candle/history lane, not a hidden analytics lane.

Steps:

1. Keep price chart visible.
2. Run for at least 10 minutes.
3. Change timeframe from `1 min` to `5 min`, then back.
4. Toggle RVOL, VP, and TPO if those features are relevant to the active layout.

Record:

- Route cadence: expected every 30 seconds unless forced.
- Server spans: `get_price_history`, `compute_session_levels`, `prepare_price_chart_data`, `store_interval_data`, `compute_top_oi_strikes`.
- Confirm missing heavy fields in the JSON response: no `trader_stats`, no `key_levels`, no `stats_0dte`, no `key_levels_0dte`.
- Response bytes.
- Chart flicker or axis snap after refresh.

Pass:

- `/update_price` does not log `compute_trader_stats_full` or `compute_key_levels`.
- Candle history refresh does not blank right-rail analytics.
- Forced timeframe changes work.
- VP/TPO/RVOL toggles do not make `/update_price` repeatedly exceed targets.

### Test H - Browser main-thread pressure

Goal: identify whether the bottleneck is server latency, JSON parsing, Plotly rendering, or DOM work.

Steps:

1. Start a browser Performance recording.
2. Keep Active Trader open and selected quote stream live.
3. Record 60-120 seconds.
4. Repeat once with heavy secondary charts visible.
5. Repeat once with secondary charts hidden or inactive.

Record:

- Long tasks over 50 ms and over 100 ms.
- Long task timing relative to `/update`, `/update_price`, `/trade_chain`, quote stream events, and Plotly updates.
- Scripting vs rendering vs painting cost in the recording.
- Console `[perf] span=browser_long_task` lines.

Pass:

- Normal selected quote updates do not create long tasks.
- Any long tasks align mostly with slow analytics or explicit chart toggles, not with every quote tick.

### Test I - SQLite and flow-alert hot path

Goal: catch DB work or flow-alert scans that can stall the slow lane or price-history lane.

Steps:

1. Keep Alerts rail visible.
2. Let fresh market-hours interval samples accumulate.
3. Run steady state for 10-15 minutes.
4. Watch for flow alerts and volume-spike alerts.

Record:

- `/update` spans: `store_interval_data`, `flow_pulse_snapshot_shared`, `create_large_trades_table`, `compute_trader_stats_full`, `compute_trader_stats_0dte`.
- `/update_price` span: `store_interval_data`.
- Whether `_VOL_SPIKE_CACHE` behavior appears effective indirectly through stable `compute_trader_stats` times.
- Any SQLite lock errors.
- Whether `interval_data` rows are fresh enough for alerts.

Pass:

- `store_interval_data` stays low after the first write in each minute.
- No SQLite lock errors.
- Flow alerts do not cause recurring slow-lane spikes.
- Alert wording and cooldowns do not duplicate side-aware and net alerts.

### Test J - Order/account polling containment

Goal: ensure order/account polling does not compete with quote and candle lanes.

Steps:

1. Auto Send off.
2. Stage a ladder price.
3. Preview only if needed.
4. Open Orders panel.
5. Close Orders panel or clear active intent.

Record:

- `/trade/orders` cadence while active intents or Orders panel are present.
- `/trade/orders` stops when there are no active intents and Orders panel is closed.
- Active Trader quote stream remains live.
- No live order endpoint is used unless explicitly testing disabled rejection.

Pass:

- Order polling starts only when needed.
- Order polling stops when no longer needed.
- Quote/candle responsiveness is unchanged.

### Test K - Context switch stress

Goal: catch cache-key mismatches and stale stream state.

Steps:

1. Start with SPY 0DTE call selected.
2. Switch call to put.
3. Change expiry to 1DTE.
4. Change strike range.
5. Change exposure metric or notional toggle.
6. Switch timeframe.
7. Collapse and expand the trade rail.

Record:

- Immediate `/trade_chain refresh_cache` after analytics context changes.
- Selected quote stream disconnect/reconnect.
- Any cache warning in `/trade_chain`.
- Whether selected contract resets correctly when unavailable.
- Whether price chart and overlays remain coherent.

Pass:

- No stale selected option quote mutates a new selection.
- Cache context changes force refresh when needed.
- Old cached chain is used only with an explicit warning after refresh failure.

---

## 8. Log Parsing

Quick route extraction:

```bash
rg '^\[perf\] route=' /tmp/gex-speed-5017.log
```

Quick browser span extraction after pasting console output to a file:

```bash
rg '^\[perf\] span=' /tmp/gex-browser-perf.log
```

Use this parser for route summaries:

```bash
python3 - <<'PY'
import re, statistics, sys
path = sys.argv[1] if len(sys.argv) > 1 else '/tmp/gex-speed-5017.log'
routes = {}
with open(path, 'r', errors='ignore') as f:
    for line in f:
        if '[perf]' not in line or 'route=' not in line:
            continue
        route = re.search(r'route=([^ ]+)', line)
        total = re.search(r'total_ms=([0-9.]+)', line)
        bytes_ = re.search(r'bytes=([0-9]+)', line)
        if not route or not total:
            continue
        row = routes.setdefault(route.group(1), {'ms': [], 'bytes': []})
        row['ms'].append(float(total.group(1)))
        if bytes_:
            row['bytes'].append(int(bytes_.group(1)))

def pct(vals, p):
    vals = sorted(vals)
    if not vals:
        return 0
    k = (len(vals) - 1) * p / 100
    lo = int(k)
    hi = min(lo + 1, len(vals) - 1)
    if lo == hi:
        return vals[lo]
    return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)

for route, row in sorted(routes.items()):
    ms = row['ms']
    b = row['bytes']
    print(
        route,
        'n=', len(ms),
        'p50_ms=', round(statistics.median(ms), 1),
        'p95_ms=', round(pct(ms, 95), 1),
        'max_ms=', round(max(ms), 1),
        'median_bytes=', int(statistics.median(b)) if b else ''
    )
PY
```

Use this parser for browser span summaries after saving console output:

```bash
python3 - <<'PY'
import re, statistics, sys
path = sys.argv[1] if len(sys.argv) > 1 else '/tmp/gex-browser-perf.log'
spans = {}
with open(path, 'r', errors='ignore') as f:
    for line in f:
        if '[perf]' not in line or 'span=' not in line:
            continue
        span = re.search(r'span=([^ ]+)', line)
        dur = re.search(r'duration_ms=([0-9.]+)', line)
        if not span or not dur:
            continue
        spans.setdefault(span.group(1), []).append(float(dur.group(1)))

def pct(vals, p):
    vals = sorted(vals)
    if not vals:
        return 0
    k = (len(vals) - 1) * p / 100
    lo = int(k)
    hi = min(lo + 1, len(vals) - 1)
    if lo == hi:
        return vals[lo]
    return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)

for span, vals in sorted(spans.items()):
    print(
        span,
        'n=', len(vals),
        'p50_ms=', round(statistics.median(vals), 1),
        'p95_ms=', round(pct(vals, 95), 1),
        'max_ms=', round(max(vals), 1)
    )
PY
```

---

## 9. Results Template

Copy this table into a follow-up note after each live session.

| Field | Value |
| --- | --- |
| Date/time ET | |
| Market condition | quiet / normal / fast |
| Ticker | SPY |
| Expiry | |
| Contract tested | |
| Timeframe | |
| Trade rail state | open / collapsed |
| Auto Send | off / on-disabled |
| Visible overlays | |
| Server log | |
| Browser perf log | |
| HAR/performance recording | |

Route summary:

| Route | n | p50 ms | p95 ms | max ms | median bytes | Notes |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `/trade_chain` cache hit | | | | | | |
| `/trade_chain` refresh | | | | | | |
| `/update_price` | | | | | | |
| `/update` | | | | | | |
| `/trade/orders` | | | | | | |

Browser summary:

| Span | n | p50 ms | p95 ms | max ms | Notes |
| --- | ---: | ---: | ---: | ---: | --- |
| `applyRealtimeQuote` | | | | | |
| `applyRealtimeCandle` | | | | | |
| selected quote apply path | | | | | |
| `renderTradeActiveTrader` | | | | | |
| `buildTradeActiveLadderHtml` | | | | | |
| `updateCharts` | | | | | |
| `browser_long_task` | | | | | |

Observed issues:

| Issue | Evidence | Likely lane | Next action |
| --- | --- | --- | --- |
| | | | |

---

## 10. Bottleneck Diagnosis Matrix

| Symptom | Most likely area | Evidence to confirm | Likely next optimization |
| --- | --- | --- | --- |
| Ladder waits 5 seconds to move | Selected quote SSE not connected or not merging | No `/trade/quote_stream` eventstream, quote age rises, ladder only changes after `/trade_chain` | Fix stream subscription/reconnect or selected-symbol validation |
| Ladder moves but page janks | Browser render churn | High `renderTradeActiveTrader`, ladder, or long-task spans | Make selected quote update more granular or extend signature skip |
| `/trade_chain` slow on cache hits | Payload building or DOM parse/render | `cache_refresh_fetched=0`, high `build_trading_chain_payload_ms`, high browser fetch span | Reduce contract payload, virtualize picker, or skip unchanged chain render |
| `/trade_chain` slow on refresh | Schwab chain fetch or cache lock | `cache_refresh_fetched=1`, high `fetch_chain_ms` or `get_current_price_ms` | Increase fast-chain cadence, reuse longer cache, or refresh only when rail/overlay needs it |
| `/update_price` repeatedly over target | Price history, VP/TPO/RVOL, or price chart payload size | High `get_price_history_ms` or `prepare_price_chart_data_ms`, large bytes | Reduce lookback for scalp mode, defer VP/TPO, compress payload, or cache price history |
| `/update` slow but UI remains smooth | Acceptable slow analytics cost | Route p95 high, but no long tasks and fast lanes live | Leave as is unless CPU is too high |
| `/update` causes browser freeze | JSON parse or Plotly/render work after slow response | Long tasks align with `/update` completion or `updateCharts` | Hide/defer secondary Plotly work, split analytics UI render, or reduce payload |
| Candle stalls while route is in flight | SSE or main-thread contention | `/price_stream` disconnected or long tasks align with route response | Fix stream reconnect, reduce post-fetch render blocking |
| SQLite spikes | Interval writes or flow-alert reads | High `store_interval_data`, SQLite lock errors, slow stats spans | Add more specific indexes, widen TTL, or batch flow-alert reads |
| Quote age stale despite stream live | Schwab option stream partial/missing fields or merge bug | Eventstream connected, messages present, UI not updating | Inspect message fields and merge logic; add receive timestamp instrumentation |
| Scroll jumps in trade rail | DOM replacement or scroll anchoring | Jump occurs when ladder signature changes | Preserve scroll more tightly or update cells in place |

---

## 11. Optimization Decision Rules

Do not optimize based on a single slow sample. Use the rules below:

1. If a slow route does not affect candles, selected quote, ladder, or browser long tasks, leave it unless CPU usage is unacceptable.
2. If selected option quote stream is stale or disconnected, fix that before tuning `/trade_chain`.
3. If `/trade_chain` cache-hit p95 exceeds 150 ms, inspect payload size and `build_trading_chain_payload` before changing cadence.
4. If `/trade_chain` refresh repeatedly exceeds 2 seconds because Schwab is slow, do not lower the 5s cadence; consider a longer cache reuse window.
5. If `/update_price` is the main bottleneck, test with VP/TPO/RVOL disabled before changing candle history lookback.
6. If `/update` causes browser long tasks, reduce rendering and payload churn before changing analytics formulas.
7. If browser long tasks happen on every quote tick, make the Active Trader quote update path more granular.
8. If flow alerts become the hotspot, preserve formulas and alert semantics; optimize query shape, cache TTL, and indexes first.

---

## 12. Measurement Gaps Worth Filling Later

Current instrumentation is good enough for the first live validation, but these additions would make the next pass easier if a bottleneck remains unclear:

- Add receive-time deltas to selected option quote stream logs when Schwab quote timestamps are present.
- Add a browser counter for skipped vs executed `renderTradeActiveTraderIfStale` calls.
- Add an in-flight counter for `/trade_chain refresh_cache` to prove no overlap directly in logs.
- Add optional response-size logging by browser fetch span, not only server response bytes.
- Add a compact in-app perf overlay for the current lane freshness: price stream age, selected quote age, chain cache age, price history age, analytics age.

Do not add these until the live validation shows where the uncertainty is. The current trace output may already be enough.
