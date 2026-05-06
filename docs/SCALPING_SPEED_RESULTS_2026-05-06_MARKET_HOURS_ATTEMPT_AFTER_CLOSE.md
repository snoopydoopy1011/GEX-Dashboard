# Scalping Speed Results - Market-Hours Attempt After Close

Date: 2026-05-06
Branch: `main`
Server: `PORT=5017 GEX_PERF_TRACE=1`
Ticker/expiry: SPY nearest 0DTE (`2026-05-06`), nearest 1DTE (`2026-05-07`) available

## Decision

This was not a valid market-hours quote-freshness pass. The traced run started after regular option-market hours, and Active Trader quote age was already about `1h 55m` stale on first inspection. Treat the stale selected-contract quote ages as an after-hours market-data condition, not a fast-lane performance failure.

The route and browser-span evidence still passed the fast-lane performance checks that can be evaluated after hours. Auto Send stayed off, the rail remained Preview Only, no live orders were placed, and no preview/place/live-order endpoints were called.

## Runs

- 5 min 0DTE call sample: about 5 minutes, selected `SPY 260506C00734000`.
- 5 min 0DTE put sample: about 5 minutes, selected `SPY 260506P00733000`.
- 1 min selected-contract sample: about 3 minutes, selected `SPY 260506P00733000`.
- Expiry switch: dashboard `2026-05-06` to `2026-05-07` and back.
- Rail stress: collapsed/expanded trading rail and right rail with selected contract state present.
- Timeframe independence: toggled `1 min -> 5 min -> 1 min` with Active Trader open.
- Order polling containment: Auto Send off, Preview Only, no preview/place/live-order route hits.

The 5 min Chart Data Window default was confirmed in code and logs: every 5 min `/update_price` request used `lookback_days=10` and `rvol_lookback_days=10`.

## Server Route Summary

| Route / group | n | p50 ms | p95 ms | max ms | Median bytes | Notes |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `/update_price` 5 min | 14 | 410.0 | 857.9 | 1374.4 | 1,279,479 | `lookback_days=10`, `rvol_lookback_days=10` |
| `/update_price` 1 min | 13 | 866.6 | 1684.8 | 2487.7 | 5,977,066 | `lookback_days=10`, `rvol_lookback_days=10` |
| `/trade_chain` cache/prelock | 68 | 16.4 | 43.4 | 62.1 | 43,379 | Within fast-lane target |
| `/trade_chain` stale inflight | 14 | 16.3 | 27.7 | 27.8 | 43,377 | Served cached payload while refresh was inflight |
| `/trade_chain` fetched/stored | 102 | 713.8 | 1330.5 | 2793.9 | 43,405 | Schwab-bound refresh path |
| `/update` | 28 | 2155.1 | 3805.9 | 4741.9 | 243,161 | Slow analytics path; no selected quote render regression |
| `/trade/orders` | 6 | 1132.7 | 1265.9 | 1287.1 | 251 | Conditional account/order reconciliation |
| `/trade/account_details` | 6 | 796.3 | 871.3 | 882.5 | 545 | Conditional account refresh |

No `/trade/preview`, `/trade/preview_order`, `/trade/place`, `/trade/place_order`, or `/trade/live-order` hits appeared in the validation log.

## Browser Trace Summary

| Span / event | n | p50 ms | p95 ms | max ms | Notes |
| --- | ---: | ---: | ---: | ---: | --- |
| `selectedQuote:parse` | 70 | 0.0 | 0.1 | 0.2 | Mostly after-hours heartbeats |
| `applyTradeSelectedQuoteMessage` | 70 | 0.2 | 0.4 | 0.7 | Well under target |
| `renderTradeActiveTrader` | 1216 | 1.5 | 2.9 | 4.4 | Well under target |
| `buildTradeActiveLadderHtml` | 16 | 0.1 | 0.3 | 0.3 | Negligible |
| `applyRealtimeQuote` | 317 | 0.3 | 0.4 | 0.8 | Underlying stream remained cheap |
| `applyRealtimeCandle` | 19 | 1.0 | 1.4 | 1.8 | Candle apply remained cheap |
| `applyPriceData` | 27 | 101.4 | 446.4 | 470.4 | Remaining browser long-task source |
| `renderTVPriceChart` | 27 | 88.0 | 429.8 | 448.6 | Remaining browser long-task source |
| `applyTVSeriesData` | 27 | 15.0 | 38.2 | 40.2 | Below chart apply cost |
| `browser_long_task` | 249 | 99.0 | 422.6 | 478.0 | Attributed mostly to chart apply work |
| `browser_event_loop_delay` | 102 | 333.8 | 447.1 | 616.1 | Trace monitor delay; use with long-task attribution |

Chart apply modes observed:

- incremental: 13 each for `applyPriceData`, `renderTVPriceChart`, and `applyTVSeriesData`;
- full context: 6 each;
- full history: 5 each;
- full fallback: 2 each;
- full initial: 1 each.

Top long tasks aligned with `apply:/update_price` and full chart modes. The largest trace event was `478 ms`, near `renderTVPriceChart=448.6 ms` and `applyPriceData=470.4 ms` in `full_context` mode on `1 min`. Normal selected quote updates did not produce recurring long tasks.

## Visible Behavior

- Initial visible quote age was already stale (`~1h 55m`), so selected quote freshness could not be passed during this run.
- Underlying live price continued to move during samples, while selected option B/M/A and quote timestamps stayed at after-hours values.
- 5 min call sample ended with `SPY 260506C00734000`, quote age `2h 3m`, B/M/A `0.46 / 0.485 / 0.51`.
- 5 min put sample ended with `SPY 260506P00733000`, quote age `2h 8m`, B/M/A `0.02 / 0.025 / 0.03`.
- 1 min selected-contract sample ended with `SPY 260506P00733000`, quote age `2h 12m`, B/M/A `0.02 / 0.025 / 0.03`.
- Active Trader stayed open, Auto Send stayed off, and the rail badge stayed Preview Only.

## Expiry And Rail Stress

The dashboard expiry switch from `2026-05-06` to `2026-05-07` immediately cleared selected contract fields and helper candidates. After refresh, trade expiry moved to `2026-05-07` and selected `SPY 260507P00732000`.

Switching back to `2026-05-06` also cleared selected fields immediately. Contract Helper showed May 06 candidates only when the dashboard expiry was already May 06, then settled with trade expiry `2026-05-06`. No mixed-expiry helper text was observed.

Collapsing and expanding the trading rail and right rail preserved selected symbol, Active Trader header, quote age, B/M/A, and Preview Only state. The trading rail collapse disconnected/reconnected the selected quote stream as expected.

## Timeframe Independence

Toggling `1 min -> 5 min -> 1 min` did not change the selected option state:

| State | Timeframe | Selected symbol | B/M/A | Last / Mark | Ladder digest |
| --- | --- | --- | --- | --- | --- |
| Before | 1 min | `SPY 260506P00732000` | `0.01 / 0.015 / 0.02` | `0.01 / 0.02` | `0.14 ... 0.02 ASK -- BID 0.01` |
| After 5 min | 5 min | `SPY 260506P00732000` | `0.01 / 0.015 / 0.02` | `0.01 / 0.02` | `0.14 ... 0.02 ASK -- BID 0.01` |
| After 1 min | 1 min | `SPY 260506P00732000` | `0.01 / 0.015 / 0.02` | `0.01 / 0.02` | `0.14 ... 0.02 ASK -- BID 0.01` |

The only visible values that changed were chart timeframe and underlying live price. This did not reproduce timeframe coupling to selected option price, symbol, quote stream state, or ladder layout.

## Artifacts

- Server validation log: `/tmp/gex-speed-5017-market-hours.log`
- Browser traces:
  - `/tmp/gex-browser-perf-20260506T221827Z-1778105907200.jsonl`
  - `/tmp/gex-browser-perf-20260506T222403Z-1778106243303.jsonl`
  - `/tmp/gex-browser-perf-20260506T222807Z-1778106487227.jsonl`
  - `/tmp/gex-browser-perf-20260506T223120Z-1778106680609.jsonl`

## Recommended Next Actions

Run the same matrix during active SPY option trading hours. Pass/fail for selected quote age and candle freshness still needs a true market-hours window where the selected option stream should show `now` or `1s`.

If that run fails on browser long tasks, continue to target full price-chart application work (`applyPriceData` / `renderTVPriceChart`) rather than selected quote parsing, Active Trader render, ladder HTML, or `/trade_chain` cache/stale paths.
