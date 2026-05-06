# Scalping Speed Results - Chart Pass - 2026-05-06

## Scope

Follow-up optimization pass on branch `codex/scalping-speed-followup`, based on `docs/SCALPING_SPEED_RESULTS_2026-05-06_FOLLOWUP.md`.

Goals:

- Add a browser perf ring buffer/export path so traces are not capped by the browser console.
- Use full trace capture to optimize chart main-thread work in `applyPriceData`, `renderTVPriceChart`, and `applyRealtimeCandle`.
- Prefer incremental steady-state chart/candle updates when ticker/timeframe/session/history context has not changed.
- Keep `/update_price` payload unchanged until chart application is no longer the measured bottleneck.
- Leave analytical formulas unchanged.
- Keep `/update` isolated unless traces show it blocking fast lanes.
- Keep `/trade/orders` conditional with slow-call/failure backoff preserved.

No live orders were placed. Auto Send stayed off. No preview/place/live-order endpoints were used.

## Code Changes

- Added `window.gexPerfTraceEvents`, a capped in-browser perf ring buffer with `clearGexPerfTrace()`, `dumpGexPerfTrace()`, and `exportGexPerfTrace()`.
- Added POST `/perf/browser_trace` to persist browser traces as `/tmp/gex-browser-perf-*.json` and `/tmp/gex-browser-perf-*.jsonl`.
- Added a trace-only `Trace` button when `GEX_PERF_TRACE=1`, so the in-app browser can export traces without console history.
- Added chart context/tail signatures and an incremental series update path for same-context `/update_price` refreshes.
- Cached cumulative-volume lookup data for realtime candle RVOL work.
- Debounced indicator refresh after realtime candle updates.
- Skipped unsupported historical candle corrections in the hot chart series update path; the cached candle array still receives the corrected full data, while visible series updates apply rows at or after the prior latest timestamp.
- Cached key-level/session-level render signatures so unchanged line overlays are not rebuilt on every price refresh.

## Verification

Commands run after implementation:

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
- Embedded JS extraction produced 5 inline scripts and passed `node --check`.

## Live Retest Setup

Server:

```bash
PORT=5017 GEX_PERF_TRACE=1 FLASK_DEBUG=0 FLASK_USE_RELOADER=0 python3 ezoptionsschwab.py 2>&1 | tee /tmp/gex-speed-5017-chart-pass.log
```

Browser:

- Opened `http://127.0.0.1:5017/` in the in-app browser.
- Ticker: SPY.
- Expiration: nearest 0DTE, `2026-05-06`.
- Active Trader open.
- Auto Send off.
- 1 min near-ATM call sample.
- 1 min near-ATM put sample.
- 5 min near-ATM put sample.

Final artifacts:

- Server log: `/tmp/gex-speed-5017-chart-pass.log`
- Browser trace JSON: `/tmp/gex-browser-perf-20260506T180534Z-1778090734330.json`
- Browser trace JSONL: `/tmp/gex-browser-perf-20260506T180534Z-1778090734330.jsonl`
- Browser trace event count: 3322

## Server Route Summary

Final traced run:

| Route | n | p50 ms | p95 ms | max ms | median bytes |
| --- | ---: | ---: | ---: | ---: | ---: |
| `/trade/account_details` | 8 | 710.2 | 1615.1 | 1729.3 | 545 |
| `/trade/orders` | 8 | 1126.2 | 2150.4 | 2288.5 | 251 |
| `/trade_chain` | 40 | 339.3 | 1998.5 | 2861.1 | 43186 |
| `/update` | 7 | 2943.1 | 5257.7 | 5780.2 | 254394 |
| `/update_price` | 7 | 1016.0 | 2545.1 | 2675.6 | 5052186 |

Trade chain outcomes:

| Outcome | n | p50 ms | p95 ms | max ms |
| --- | ---: | ---: | ---: | ---: |
| `cache_hit_prelock` | 15 | 16.7 | 29.5 | 30.3 |
| `fetched_stored` | 20 | 863.9 | 2184.5 | 2861.1 |
| `stale_inflight` | 2 | 16.1 | 17.4 | 17.6 |

## Browser Summary

Final traced run:

| Span | n | p50 ms | p95 ms | max ms |
| --- | ---: | ---: | ---: | ---: |
| `browser_long_task` | 60 | 371.5 | 399.2 | 437.0 |
| `fetch:/update_price` | 7 | 1022.1 | 2551.0 | 2681.4 |
| `parse:/update_price` | 7 | 5.5 | 6.4 | 6.5 |
| `parse:price_chart_payload` | 7 | 7.6 | 8.4 | 8.4 |
| `apply:/update_price` | 7 | 38.0 | 425.8 | 430.4 |
| `applyPriceData` | 7 | 37.7 | 425.4 | 430.0 |
| `renderTVPriceChart` | 7 | 18.2 | 407.7 | 410.8 |
| `applyTVSeriesData` | 7 | 16.6 | 31.0 | 31.6 |
| `applyRealtimeQuote` | 164 | 0.3 | 0.5 | 0.7 |
| `applyRealtimeCandle` | 5 | 1.8 | 2.1 | 2.1 |
| `renderTradeActiveTrader` | 575 | 1.9 | 4.5 | 6.5 |
| `buildTradeActiveLadderHtml` | 133 | 0.2 | 0.3 | 0.4 |
| `apply:/trade_chain` | 38 | 11.1 | 13.5 | 15.8 |
| `apply:/trade/orders` | 8 | 2.8 | 3.7 | 3.8 |

Chart apply modes:

| Span | Mode | n | p50 ms | p95 ms | max ms |
| --- | --- | ---: | ---: | ---: | ---: |
| `applyPriceData` | `full_initial` | 1 | 414.8 | 414.8 | 414.8 |
| `applyPriceData` | `full_context` | 2 | 404.4 | 427.4 | 430.0 |
| `applyPriceData` | `incremental` | 4 | 35.2 | 37.4 | 37.7 |
| `renderTVPriceChart` | `full_initial` | 1 | 400.4 | 400.4 | 400.4 |
| `renderTVPriceChart` | `full_context` | 2 | 385.6 | 408.3 | 410.8 |
| `renderTVPriceChart` | `incremental` | 4 | 16.6 | 18.0 | 18.2 |
| `applyTVSeriesData` | `full_initial` | 1 | 29.7 | 29.7 | 29.7 |
| `applyTVSeriesData` | `full_context` | 2 | 28.6 | 31.3 | 31.6 |
| `applyTVSeriesData` | `incremental` | 4 | 15.2 | 16.4 | 16.6 |

No `full_fallback` modes appeared in the final trace.

## Before/After Highlights

Compared to `docs/SCALPING_SPEED_RESULTS_2026-05-06_FOLLOWUP.md`:

- Browser trace coverage is now complete for the run: 3322 exported events instead of the latest 500 console messages.
- `renderTVPriceChart` steady-state chart work improved from a captured 557.6 ms full reapply to 16.6 ms p50 / 18.0 ms p95 in incremental mode.
- `applyPriceData` steady-state chart work improved from a captured 575.9 ms full apply to 35.2 ms p50 / 37.4 ms p95 in incremental mode.
- `applyRealtimeCandle` improved from a captured 360.3 ms span to 1.8 ms p50 / 2.1 ms p95.
- `/update_price` payload size remains essentially unchanged, around 5.0 MB median, by design.
- `/update_price` parse remains small: `parse:/update_price` p95 6.4 ms and nested `parse:price_chart_payload` p95 8.4 ms.
- Active Trader work remains small: `renderTradeActiveTrader` p95 4.5 ms and `apply:/trade_orders` p95 3.7 ms.
- `/trade_chain` stale fast lane remained fast: `stale_inflight` p95 17.4 ms and `cache_hit_prelock` p95 29.5 ms.

## Observed Behavior

- Active Trader stayed open and responsive through the 1 min call, 1 min put, and 5 min put samples.
- Auto Send remained off; the DOM showed the Active Trader `Auto` checkbox without a checked marker.
- The guarded live-order button remained disabled throughout the retest.
- Live quote rows continued updating while `/trade_chain`, `/update`, and `/update_price` were in flight.
- No stale-quote stuck state was observed in the DOM snapshots. Active contract headers showed `Live`.

## Bottlenecks With Evidence

1. Full chart reapplication is still expensive when context changes. `full_initial` and `full_context` `renderTVPriceChart` spans were about 360-411 ms. This is expected on reload/timeframe/session context changes.
2. Steady-state chart updates are no longer the main long task source. Incremental `renderTVPriceChart` p95 was 18.0 ms and incremental `applyPriceData` p95 was 37.4 ms.
3. `/update_price` payload size is not yet the next proven target. Parse stayed under 9 ms p95; fetch/server time and full context chart refreshes dominate the remaining route span.
4. Server latency remains mostly upstream-bound on fresh data. `/trade_chain` `fetched_stored` p95 was 2184.5 ms, while stale/cache fast lanes stayed under 30 ms p95.
5. `/update` remains heavy but isolated in this run. It had p95 5257.7 ms, but Active Trader quote rendering stayed low and live.
6. `/trade/orders` remains conditional but Schwab calls can be slow. Final `/trade/orders` p95 was 2150.4 ms, with apply work under 4 ms p95.

## Recommended Next Actions

1. Keep the browser ring buffer/export endpoint and use it for future full validation runs.
2. Keep the incremental chart update path and the historical-correction skip in the hot path.
3. Optimize remaining full-context chart work only if timeframe/session switches need to feel instantaneous; the steady-state scalping path is now much faster.
4. Do not reduce `/update_price` payload yet. The trace still shows parse is cheap relative to network/server and full-context chart application.
5. Continue leaving `/update` isolated unless a future trace correlates it with stale fast-lane quotes or long tasks.
6. Keep `/trade/orders` polling conditional and preserve slow-call/failure backoff.
