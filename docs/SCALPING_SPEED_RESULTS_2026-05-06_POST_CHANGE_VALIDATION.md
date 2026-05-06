# Scalping Speed Results - Post-Change Validation

Date: 2026-05-06
Branch: `main`
Server: `PORT=5017 GEX_PERF_TRACE=1`
Ticker/expiry: SPY nearest 0DTE (`2026-05-06`), nearest 1DTE (`2026-05-07`) available

## Decision

Fast-lane validation passed with an after-hours caveat: selected quote age could not be treated as a market-hours pass/fail signal because the sample ran after regular option-market hours and the UI legitimately showed stale quote state in places. No live orders were placed, Auto Send stayed off, and no preview/place/live-order endpoints were called.

The only code change from this pass is the Contract Helper mixed-expiry cleanup. No analytical formulas, order endpoints, or extra performance optimizations were changed.

The next performance target, if future market-hours validation fails, is full price-chart application work. The evidence does not point to selected option quote parsing, Active Trader render, ladder build, or `/trade_chain` cache/stale paths as the current bottleneck.

## Runs

- 5 min 0DTE call sample: about 5 minutes, selected `SPY 260506C00734000`.
- 5 min 0DTE put sample: about 5 minutes, selected `SPY 260506P00733000`.
- 1 min selected-contract sample: about 3 minutes, selected put stream stayed live.
- Expiry switch: dashboard `2026-05-06` to `2026-05-07` and back.
- Rail stress: collapsed/expanded trading rail and right rail while selected quote stream was live.
- Order polling containment: `/trade/orders` and `/trade/account_details` stayed conditional; no preview/place/live-order route hits.

The validation was performed on `http://127.0.0.1:5017/` with `PORT=5017 GEX_PERF_TRACE=1`. The SPY nearest 0DTE was `2026-05-06`; the nearest 1DTE was `2026-05-07`.

## Server Route Summary

| Route / group | n | p50 ms | p95 ms | max ms | Median bytes | Notes |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `/update_price` 5 min | 14 | 422.4 | 1522.2 | 2398.5 | 1,273,484 | `lookback_days=10`, `rvol_lookback_days=10` |
| `/update_price` 1 min | 12 | 982.4 | 1735.5 | 1962.8 | 5,951,865 | `lookback_days=10`, `rvol_lookback_days=10` |
| `/trade_chain` cache hit/prelock | 63 | 16.4 | 37.9 | 80.7 | 43,3xx | Within fast-lane target |
| `/trade_chain` stale inflight | 10 | 15.6 | 30.2 | 30.4 | 44,8xx | Served cached payload while refresh was inflight |
| `/trade_chain` fetched/stored | 96 | 775.0 | 1555.0 | 5631.6 | 43,3xx | Upstream Schwab-bound refresh path |
| `/update` | 26 | 2273.4 | 6120.0 | 7014.6 | 241,292 | Slow analytics path; did not block selected quote/Active Trader spans |
| `/trade/orders` | 8 | 1225.2 | 1764.7 | 1916.6 | 251 | Conditional order reconciliation only |
| `/trade/account_details` | 8 | 893.2 | 1219.2 | 1250.1 | 545 | Conditional account refresh only |

No `/trade/preview`, `/trade/place`, or `/trade/live-order` hits appeared in either validation log.

## Browser Trace Summary

| Span / event | n | p50 ms | p95 ms | max ms | Notes |
| --- | ---: | ---: | ---: | ---: | --- |
| `selectedQuote:parse` | 68 | 0.0 | 0.1 | 0.2 | Selected option stream parse |
| `applyTradeSelectedQuoteMessage` | 68 | 0.3 | 0.6 | 4.4 | Selected quote apply path |
| `renderTradeActiveTrader` | 1084 | 2.2 | 6.1 | 139.7 | Warmup outlier; steady samples low |
| `buildTradeActiveLadderHtml` | 20 | 0.1 | 0.2 | 0.2 | Ladder HTML path stayed negligible |
| `applyRealtimeQuote` | 170 | 0.7 | 1.6 | 3.0 | Underlying quote apply path |
| `applyRealtimeCandle` | 17 | 1.6 | 2.5 | 2.6 | Realtime candle apply path |
| `applyPriceData` | 24 | 109.8 | 500.9 | 530.2 | Remaining long-task source on full chart applies |
| `renderTVPriceChart` | 24 | 93.5 | 457.7 | 486.3 | Remaining chart-apply target if future validation fails |
| `browser_long_task` | 199 | 99.0 | 429.6 | 537.0 | Long tasks aligned mostly with chart/apply work, not quote ticks |
| `browser_event_loop_delay` | 182 | 749.6 | 777.0 | 70453.2 | Includes trace monitor gaps; not used alone as a failure signal |

Long-task context was useful. Top attributed long tasks were `apply:/update_price` with `applyPriceData` and `renderTVPriceChart` in `full_history`, `full_context`, or `full_fallback` chart modes. Normal selected quote updates did not create recurring long tasks.

The trace profile by scenario was:

- 5 min call sample: one large initial/fallback chart apply reached about `530 ms`; selected quote apply remained under `4 ms`.
- 5 min put sample: long-task p95 improved to about `111 ms`; Active Trader and selected quote spans stayed low.
- 1 min selected-contract sample: long tasks clustered around `430-510 ms` and aligned with full 1 min price-history chart application.
- Expiry/rail stress: rail collapse/expand did not produce expensive ladder work; full chart application remained the dominant long-task source.

## Visible Behavior

- The 5 min Chart Data Window default was confirmed as 10 days.
- Active Trader remained open during the samples.
- Selected option stream/Active Trader rendering stayed responsive by span timing.
- After-hours quote age/stale quote text remained visible in places, so quote-age freshness needs a market-hours rerun before treating it as a final UI freshness pass.
- Candle and ladder behavior did not show route-bound blocking during the tested interactions.
- Rail collapse/expand did not arm Auto Send or enable live order placement.

Important caveat: this was not a complete market-hours freshness pass because the sample was after regular option-market hours. A follow-up during active SPY option trading should specifically confirm that selected quote age stays `now` or `1s`, that candles keep moving on the live price stream, and that the ladder changes from selected option quote stream updates rather than waiting for `/trade_chain`.

## Contract Helper Cleanup

The mixed-expiry bug reproduced before the fix: immediately after dashboard expiry switch to `2026-05-07`, the Contract Helper could still show `May 06` candidates until the trade-chain refresh completed.

The cleanup now:

- compares rendered helper expiry to the dashboard-selected expiry before enabling helper candidates;
- clears candidate buttons and shows `Contract helper updating for selected expiry.` while the new expiry is loading;
- forces an immediate helper re-render when dashboard expiry sync resets the trade rail context.

Post-fix browser verification:

- `2026-05-06` to `2026-05-07`: helper immediately cleared old `260506` candidates, then repopulated as `May 07` with `SPY 260507...` candidates.
- `2026-05-07` to `2026-05-06`: helper immediately cleared old `260507` candidates, then repopulated as `May 06` with `SPY 260506...` candidates.

Implementation details:

- `renderContractHelper(stats)` now normalizes the dashboard-selected expiry and compares it against `stats.contract_helper.expiry`, `call.expiry`, or `put.expiry`.
- If the helper payload is stale for the selected dashboard expiry, the helper clears both candidate buttons and keeps them disabled until fresh data arrives.
- `syncTradeRailToDashboardExpirySelection()` now requests an immediate helper re-render after selected contract context is reset, so stale helper UI does not remain visible while `/trade_chain` is loading.

Test plan for future edits to this area:

1. Start `PORT=5017 GEX_PERF_TRACE=1`.
2. Select SPY 0DTE and wait for helper candidates.
3. Switch dashboard expiry to 1DTE.
4. Immediately inspect only `[data-trade-helper-candidate]` buttons; old `260506` symbols must be cleared or disabled before fresh 1DTE data arrives.
5. Wait for `/trade_chain`; helper should repopulate with `260507` symbols.
6. Switch back to 0DTE and repeat the same checks in reverse.
7. Confirm selected contract and quote stream also reset/reconnect, with no stale selected option quote mutating the new selection.

## Findings

- The 5 min chart-history default is correctly 10 days. The 5 min `/update_price` route stayed below the p95 target and returned about `1.27 MB`, consistent with the intended reduced history window.
- The 1 min `/update_price` route also stayed below the route p95 target, but it still returns about `5.95 MB`. The browser long-task source during 1 min samples was full chart application, not route time alone.
- `/trade_chain` cache-hit, prelock, and stale-inflight paths are healthy. The fetched/stored path is still upstream-bound and should not be optimized by lowering cadence.
- `/update` can exceed the nominal p95 target, but the captured traces did not show selected quote, Active Trader, or ladder spans being blocked by `/update` apply work.
- Active Trader render cost is low in steady state. The one high `renderTradeActiveTrader` value was a warmup/outlier; p95 remained well below target.
- Ladder HTML generation stayed effectively negligible.
- Browser long tasks over `100 ms` were attributed. The recurring actionable source is price-chart apply work in full modes.
- Order polling containment held: `/trade/orders` and `/trade/account_details` were conditional; preview/place/live-order routes were not called.

## Recommended Next Actions

### 1. Market-Hours Rerun

Run the same matrix during active SPY option market hours before treating quote/candle freshness as final. The after-hours run proves route/span isolation, but not live quote-age behavior under active ticks.

Implementation:

- Use the same command: `PORT=5017 GEX_PERF_TRACE=1 FLASK_DEBUG=0 FLASK_USE_RELOADER=0 python3 ezoptionsschwab.py 2>&1 | tee /tmp/gex-speed-5017-market-hours.log`.
- Use SPY nearest 0DTE when available; otherwise nearest 1DTE.
- Keep Active Trader open, Auto Send off, and avoid preview/place/live-order endpoints.
- Export browser traces after each sample.

Pass criteria:

- selected quote age stays `now` or `1s` while stream status is `Live`;
- `applyTradeSelectedQuoteMessage` p95 remains under `5 ms`;
- `renderTradeActiveTrader` p95 remains under `20 ms`;
- `/trade_chain` cache/stale p95 remains under `100 ms`;
- `/update_price` p95 remains under `3000 ms`;
- no normal quote tick creates recurring `browser_long_task` events.

### 2. Chart Apply Optimization Only If Needed

If the market-hours rerun still shows repeated long tasks tied to `applyPriceData` or `renderTVPriceChart`, target full chart application. Do not start with selected quote, Active Trader, ladder, or `/trade_chain`.

Potential implementation paths:

- Add or tighten signatures so unchanged overlays, markers, and price lines are not rebuilt during full-context or full-history chart applies.
- Split noncritical overlay application into staged `requestAnimationFrame` chunks so selected quote/candle work can run between chart work.
- Measure whether VP/TPO/RVOL/top-OI overlay work is responsible for the full apply cost before reducing payload size.
- Keep the existing 10-day 5 min default unless a specific trace proves history size is still the limiting factor.

Correctness test:

- Capture before/after browser traces with the same symbol, timeframe, expiry, and overlays.
- Compare `applyPriceData`, `renderTVPriceChart`, `applyTVSeriesData`, and `browser_long_task` p50/p95/max.
- Confirm chart candles, EM lines, VWAP/EMA, volume, RVOL, strike overlay, and selected contract ladder still render correctly.
- Confirm selected quote spans do not regress.

### 3. Active Trader Timeframe Independence Investigation

Open question: changing the chart candle timeframe from `1 min` to `5 min` should not change the selected option contract price. The option price should come from the selected contract quote stream and chain snapshots, not from the chart timeframe. The timeframe should affect chart history requests and candle rendering only.

Next-session investigation:

- Select a SPY 0DTE contract in Active Trader.
- Record `tradeRailState.selectedSymbol`, selected quote stream URL, bid/mid/ask, ladder center, and quote age.
- Toggle chart timeframe between `1 min` and `5 min`.
- Confirm the selected contract symbol and quote stream do not change solely because timeframe changed.
- Confirm `/update_price` changes timeframe/lookback metadata, while `/trade/quote_stream/<contract_symbol>` remains tied to the same contract.
- Confirm no preview/place/live-order routes are called.

If a bug reproduces:

- Inspect `ensurePriceChartDom`, `updatePriceInfo`, timeframe change handlers, and any path that calls `renderTradeRail`, `requestTradeChain`, or selected contract reset.
- Fix only the coupling path. Do not alter option-pricing math.

### 4. Active Trader Auto Send Flow Investigation

Open question: with Auto Send enabled, the intended scalping flow may be that `Buy Ask` or ladder buy/sell sends the limit order immediately when live trading is explicitly allowed. In the observed disabled-live-trading flow, the user had to click multiple controls: buy ask, preview, live trading, and a browser confirm, then the request was rejected because live trading was disabled.

This must be a separate, safety-scoped investigation. Do not place live orders.

Next-session investigation:

- Read the order-entry safety code and config flags first: live trading enable flag, Auto Send state, preview state, confirmation dialog, and route guards.
- Map the exact state machine from `Buy Ask` / ladder click to preview, live toggle, confirmation, and final route.
- With live trading disabled, test only the rejected/safe path and confirm which route is called.
- Decide whether Auto Send should bypass preview only when all explicit live-trading safeguards are already enabled.
- Document the expected click count for each mode:
  - Preview Only / Auto off;
  - Preview Only / Auto on;
  - Live disabled / Auto on;
  - Live explicitly enabled / Auto on.

Potential implementation paths, after the safety model is confirmed:

- If Auto Send is intended to be one-click only in fully armed live mode, keep preview and confirmation for all other modes.
- Make the UI state explicit: show why a click staged, previewed, confirmed, rejected, or sent.
- Keep a hard server-side guard so a client UI bug cannot place a live order unless live trading is enabled in config and the request carries the expected confirmation state.

Correctness test:

- In disabled-live mode, clicking `Buy Ask` with Auto Send on must not place an order and must return a clear disabled/rejected state.
- In preview-only mode, clicking `Buy Ask` must produce only preview/staged behavior.
- Route logs must show no live place endpoint unless the user has explicitly requested a safe, disabled/rejection-flow test.
- Any future live-enabled test must be deliberately authorized, use tiny size, and should be treated as outside normal speed validation.

## Future Speed Validation Prompt

Use this as the starting prompt for the next market-hours run:

```text
We are in /Users/scottmunger/Desktop/Trading/Dashboards/GEX-Dashboard on main.

Read first:
- AGENTS.md
- docs/speed/README.md
- docs/speed/AGENTS.md
- docs/SCALPING_SPEED_VALIDATION_PLAN.md
- docs/SCALPING_SPEED_RESULTS_2026-05-06_POST_CHANGE_VALIDATION.md

Confirm git branch/log/status.

Do not place live orders. Keep Auto Send off. Do not use preview/place/live-order endpoints unless I explicitly ask for a safe disabled/rejection-flow investigation.

Goal: run a market-hours scalping-speed validation and verify quote/candle freshness.

Use PORT=5017 with GEX_PERF_TRACE=1. Validate SPY nearest 0DTE if available, otherwise nearest 1DTE. Confirm 5 min Chart Data Window default is 10 days. Keep Active Trader open.

Run:
1. 5 min 0DTE call sample for about 5 minutes.
2. 5 min 0DTE put sample for about 5 minutes.
3. 1 min selected-contract sample for 3-5 minutes.
4. Dashboard expiry switch 0DTE to 1DTE and back if available.
5. Collapse/expand trading rail and right rail while selected quote stream is live.
6. Verify order polling containment with Auto off and no preview/place/live-order calls.
7. Investigate whether changing chart timeframe between 1 min and 5 min changes selected contract price, symbol, quote stream, or ladder behavior. It should not; chart timeframe should only affect chart history/candles.

Capture route timings/bytes, update_price lookback metrics, browser long-task attribution, event-loop delay, selected quote spans, Active Trader spans, chart modes, visible stale quote/candle/ladder behavior, and whether Contract Helper shows mixed-expiry text.

If validation fails, use attribution to choose one narrow optimization target. If timeframe appears coupled to option quote/Active Trader state, investigate that coupling separately from chart performance. Do not change analytical formulas.
```

## Artifacts

- Server validation log: `/tmp/gex-speed-5017-post-change-validation.log`
- Server post-fix verification log: `/tmp/gex-speed-5017-post-change-validation-fixed.log`
- Browser traces:
  - `/tmp/gex-browser-perf-20260506T210930Z-1778101770478.jsonl`
  - `/tmp/gex-browser-perf-20260506T211607Z-1778102167738.jsonl`
  - `/tmp/gex-browser-perf-20260506T211938Z-1778102378051.jsonl`
  - `/tmp/gex-browser-perf-20260506T212334Z-1778102614115.jsonl`

## Checks

- `python3 -m py_compile ezoptionsschwab.py` passed with the pre-existing invalid escape sequence warning near the embedded chart HTML.
- Inline script extraction produced 5 scripts and `node --check /tmp/gex-inline-scripts.js` passed.
- `git diff --check` passed.
