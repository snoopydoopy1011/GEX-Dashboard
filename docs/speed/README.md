# Scalping Speed Reference

This folder is the entry point for future scalping speed, fast-lane, and live validation work. The source docs stay in `docs/` so existing links do not break; this index explains what to read and when.

## Start Here

1. Read root `AGENTS.md` if it exists in the local checkout.
2. Read [`docs/speed/AGENTS.md`](AGENTS.md) for speed-specific guardrails.
3. Read the latest dated result note first, then work backward only as needed.

## Current State

- Latest implementation result: [`SCALPING_SPEED_RESULTS_2026-05-06_CHART_PASS.md`](../SCALPING_SPEED_RESULTS_2026-05-06_CHART_PASS.md)
- Full live validation checklist: [`SCALPING_SPEED_VALIDATION_PLAN.md`](../SCALPING_SPEED_VALIDATION_PLAN.md)
- Current implementation branch used for the latest pass: `codex/scalping-speed-followup`
- Core file: [`ezoptionsschwab.py`](../../ezoptionsschwab.py)

## Speed Docs Map

| Doc | Use it for |
| --- | --- |
| [`SCALPING_SPEED_RESULTS_2026-05-06_CHART_PASS.md`](../SCALPING_SPEED_RESULTS_2026-05-06_CHART_PASS.md) | Latest chart-pass result, browser trace export, incremental chart update evidence, final route/browser summaries. |
| [`SCALPING_SPEED_RESULTS_2026-05-06_FOLLOWUP.md`](../SCALPING_SPEED_RESULTS_2026-05-06_FOLLOWUP.md) | Fast-lane cache lock attribution, browser perf attribution before the chart-pass fix, and the prompt that led to the chart pass. |
| [`SCALPING_SPEED_RESULTS_2026-05-06.md`](../SCALPING_SPEED_RESULTS_2026-05-06.md) | Earlier same-day baseline and first speed results. |
| [`SCALPING_SPEED_VALIDATION_PLAN.md`](../SCALPING_SPEED_VALIDATION_PLAN.md) | Market-hours test matrix, success targets, capture requirements, bottleneck diagnosis, and results template. |
| [`SCALPING_PERFORMANCE_OPTIMIZATION_PLAN.md`](../SCALPING_PERFORMANCE_OPTIMIZATION_PLAN.md) | Original staged performance architecture: fast/slow lanes, cadence changes, and validation from earlier stages. |
| [`SCALPING_FAST_LANES_FOLLOWUP_PLAN.md`](../SCALPING_FAST_LANES_FOLLOWUP_PLAN.md) | Follow-up implementation details for fast chain/context lane behavior and acceptance criteria. |
| [`ORDER_ENTRY_RAIL_SCALPING_REPAIR_PLAN.md`](../ORDER_ENTRY_RAIL_SCALPING_REPAIR_PLAN.md) | Active Trader/order-entry safety, Auto Send behavior, order polling, and repair context. |

## Latest Findings To Carry Forward

- The steady-state chart path is now incremental. In the latest trace, incremental `renderTVPriceChart` was about 16-18 ms and incremental `applyPriceData` was about 34-38 ms.
- Browser trace export is available through `GEX_PERF_TRACE=1` and `/perf/browser_trace`; use the Trace button instead of relying on capped console history.
- `/update_price` payload size should not be reduced until traces show parse or payload transfer is still the limiting factor after chart application.
- `/update` should stay isolated unless a trace ties it to stale fast-lane quotes, candle lag, or browser long tasks.
- `/trade/orders` polling must remain conditional and preserve slow-call/failure backoff.
- Analytical formulas are out of scope for speed work.

## Where To Put New Results

Add future dated results notes in `docs/` using this pattern:

```text
docs/SCALPING_SPEED_RESULTS_YYYY-MM-DD_<SHORT_LABEL>.md
```

Then update this README's "Current State" and "Speed Docs Map" if the new result becomes the latest reference.
