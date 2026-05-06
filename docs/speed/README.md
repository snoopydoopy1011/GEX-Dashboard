# Scalping Speed Reference

This folder is the entry point for future scalping speed, fast-lane, and live validation work. The source docs stay in `docs/` so existing links do not break; this index explains what to read and when.

## Start Here

1. Read root `AGENTS.md` if it exists in the local checkout.
2. Read [`docs/speed/AGENTS.md`](AGENTS.md) for speed-specific guardrails.
3. Read the latest dated result note first, then work backward only as needed.

## Current State

- Latest validation result: [`SCALPING_SPEED_RESULTS_2026-05-06_ATTRIBUTION_HISTORY_SYNC.md`](../SCALPING_SPEED_RESULTS_2026-05-06_ATTRIBUTION_HISTORY_SYNC.md)
- Latest implementation result: [`SCALPING_SPEED_RESULTS_2026-05-06_ATTRIBUTION_HISTORY_SYNC.md`](../SCALPING_SPEED_RESULTS_2026-05-06_ATTRIBUTION_HISTORY_SYNC.md)
- Full live validation checklist: [`SCALPING_SPEED_VALIDATION_PLAN.md`](../SCALPING_SPEED_VALIDATION_PLAN.md)
- Current implementation branch used for the latest pass: `main`
- Core file: [`ezoptionsschwab.py`](../../ezoptionsschwab.py)

## Speed Docs Map

| Doc | Use it for |
| --- | --- |
| [`SCALPING_SPEED_RESULTS_2026-05-06_ATTRIBUTION_HISTORY_SYNC.md`](../SCALPING_SPEED_RESULTS_2026-05-06_ATTRIBUTION_HISTORY_SYNC.md) | Latest implementation and validation result: long-task attribution, event-loop delay tracing, 5 min history-window experiment, 10-day 5 min default, and dashboard expiry-to-trade-rail sync fix. |
| [`SCALPING_SPEED_RESULTS_2026-05-06_CHART_PASS.md`](../SCALPING_SPEED_RESULTS_2026-05-06_CHART_PASS.md) | Latest chart-pass result, browser trace export, incremental chart update evidence, final route/browser summaries. |
| [`SCALPING_SPEED_RESULTS_2026-05-06_FULL_VALIDATION.md`](../SCALPING_SPEED_RESULTS_2026-05-06_FULL_VALIDATION.md) | Longer market-hours validation after the chart pass: 1 min/5 min candle samples, call/put Active Trader streams, route cadence, order polling containment, context-switch stress, and final browser/server bottleneck evidence. |
| [`SCALPING_SPEED_RESULTS_2026-05-06_FOLLOWUP.md`](../SCALPING_SPEED_RESULTS_2026-05-06_FOLLOWUP.md) | Fast-lane cache lock attribution, browser perf attribution before the chart-pass fix, and the prompt that led to the chart pass. |
| [`SCALPING_SPEED_RESULTS_2026-05-06.md`](../SCALPING_SPEED_RESULTS_2026-05-06.md) | Earlier same-day baseline and first speed results. |
| [`SCALPING_SPEED_VALIDATION_PLAN.md`](../SCALPING_SPEED_VALIDATION_PLAN.md) | Market-hours test matrix, success targets, capture requirements, bottleneck diagnosis, and results template. |
| [`SCALPING_PERFORMANCE_OPTIMIZATION_PLAN.md`](../SCALPING_PERFORMANCE_OPTIMIZATION_PLAN.md) | Original staged performance architecture: fast/slow lanes, cadence changes, and validation from earlier stages. |
| [`SCALPING_FAST_LANES_FOLLOWUP_PLAN.md`](../SCALPING_FAST_LANES_FOLLOWUP_PLAN.md) | Follow-up implementation details for fast chain/context lane behavior and acceptance criteria. |
| [`ORDER_ENTRY_RAIL_SCALPING_REPAIR_PLAN.md`](../ORDER_ENTRY_RAIL_SCALPING_REPAIR_PLAN.md) | Active Trader/order-entry safety, Auto Send behavior, order polling, and repair context. |

## Latest Findings To Carry Forward

- Browser long tasks now include nearby app spans, route/apply context, chart mode, selected timeframe, selected contract, trade rail state, and visible panel state; trace-only `browser_event_loop_delay` events are also captured.
- With `GEX_PERF_TRACE=1`, the browser trace cap is 50,000 events. Browser trace export remains available through `/perf/browser_trace`; use the Trace button instead of relying on capped console history.
- The 5 min chart-history default is now 10 days. The 2026-05-06 history-window experiment showed median `/update_price` bytes dropping from about 5.1 MB to 1.3 MB and long-task p95 dropping from about 382 ms to 126 ms versus the prior 60-day default.
- The steady-state chart path is incremental. In the latest 5 min 10-day trace, incremental `renderTVPriceChart` was about 4.4 ms and full initial `renderTVPriceChart` was about 116.8 ms.
- Dashboard expiry changes that remove the trade-rail expiry now immediately clear stale selected contract state, force a trade-chain refresh, reconnect the selected quote stream, and guard against stale quote stream messages.
- `/update` should stay isolated unless a trace ties it to stale fast-lane quotes, candle lag, or browser long tasks.
- `/trade/orders` polling must remain conditional and preserve slow-call/failure backoff.
- Analytical formulas are out of scope for speed work.

## Where To Put New Results

Add future dated results notes in `docs/` using this pattern:

```text
docs/SCALPING_SPEED_RESULTS_YYYY-MM-DD_<SHORT_LABEL>.md
```

Then update this README's "Current State" and "Speed Docs Map" if the new result becomes the latest reference.
