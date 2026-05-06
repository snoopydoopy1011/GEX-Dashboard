# Scalping Speed Results - Attribution, History Window, Expiry Sync - 2026-05-06

## Scope

Follow-up implementation from `docs/SCALPING_SPEED_RESULTS_2026-05-06_FULL_VALIDATION.md`.

No live orders were placed. Auto Send stayed off. No preview, place, replace, cancel, or live-order endpoints were used.

## Code Changes

- Added browser long-task attribution:
  - retained a bounded recent app-span buffer with `start_ms`, `end_ms`, duration, and compact detail;
  - attached nearby app spans to `browser_long_task` events;
  - included route/apply context, chart apply mode, selected timeframe, selected expiries, visible rail/panel state, selected contract, selected quote stream status, and document visibility.
- Added trace-only browser event-loop delay sampling:
  - samples every `250 ms`;
  - emits `browser_event_loop_delay` only when delay is at least `100 ms`;
  - uses the same attribution payload as long-task events.
- Raised the browser trace cap to `50000` events when `GEX_PERF_TRACE=1`; non-trace sessions keep the `10000` cap.
- Added extra browser spans around rail/chart surfaces that were under-attributed: `applyRightRailTab`, `renderRailAlerts`, `renderChartContextStrip`, and `renderFlowPulse`.
- Added `/update_price` trace metadata for `timeframe`, `lookback_days`, and `rvol_lookback_days` on both browser spans and server route logs.
- Changed the 5 min chart-history default from `60` days to `10` days:
  - backend `LOOKBACK_DEFAULT_BY_TF[5] = 10`;
  - frontend 5 min default `10`, soft cap `30`, hard cap unchanged at `180`.
- Fixed dashboard expiry to trade-rail expiry sync:
  - when the dashboard selection no longer includes the current trade-rail expiry, the rail immediately clears selected contract, selected quote override, limit price, account/order request state, preview state, and order polling;
  - forces one `/trade_chain` refresh for the new expiry when the rail is open;
  - guards selected quote `EventSource` callbacks with a stream sequence so stale messages from a closed stream cannot mutate the new selected contract.

Analytical formulas were not changed. `/trade_chain` cache/stale behavior and conditional `/trade/orders` polling/backoff were preserved.

## Verification

Commands run:

```bash
python3 -m py_compile ezoptionsschwab.py
python3 -c "import re, pathlib, ezoptionsschwab as m; html=m.app.test_client().get('/').get_data(as_text=True); scripts=re.findall(r'<script[^>]*>(.*?)</script>', html, re.S|re.I); pathlib.Path('/tmp/gex-inline-scripts.js').write_text('\n;\n'.join(scripts)); print('scripts', len(scripts), 'bytes', pathlib.Path('/tmp/gex-inline-scripts.js').stat().st_size)"
node --check /tmp/gex-inline-scripts.js
git diff --check
```

Results:

- `py_compile` passed with the pre-existing invalid escape sequence warning near the embedded chart HTML.
- Inline script extraction produced 5 scripts and `node --check` passed.
- `git diff --check` passed.

## Live Setup

Server:

```bash
PORT=5017 GEX_PERF_TRACE=1 FLASK_DEBUG=0 FLASK_USE_RELOADER=0 python3 ezoptionsschwab.py 2>&1 | tee /tmp/gex-speed-5017-*.log
```

Browser state:

- URL: `http://127.0.0.1:5017/`
- Ticker: SPY
- Nearest 0DTE: `2026-05-06`
- Nearest 1DTE: `2026-05-07`
- Timeframe for the experiment: 5 min
- Active Trader: open
- Auto Send: off

Artifacts:

- Short attribution server log: `/tmp/gex-speed-5017-attribution.log`
- Short attribution browser trace: `/tmp/gex-browser-perf-20260506T195941Z-1778097581026.jsonl`
- History-window server log: `/tmp/gex-speed-5017-history-window.log`
- History-window traces:
  - `/tmp/gex-browser-perf-20260506T201001Z-1778098201348.jsonl`
  - `/tmp/gex-browser-perf-20260506T201046Z-1778098246473.jsonl`
  - `/tmp/gex-browser-perf-20260506T201130Z-1778098290310.jsonl`
  - `/tmp/gex-browser-perf-20260506T201214Z-1778098334607.jsonl`
- Post-fix expiry-sync server log: `/tmp/gex-speed-5017-postfix.log`

## Long-Task Attribution Check

Short traced validation:

| Metric | Value |
| --- | ---: |
| Browser trace events | 1205 |
| Browser spans | 1182 |
| `browser_long_task` events | 14 |
| `browser_event_loop_delay` events | 9 |
| Long tasks attributed | 14 / 14 |
| `browser_long_task` p50 | 380.0 ms |
| `browser_long_task` p95 | 417.1 ms |
| `browser_long_task` max | 447.0 ms |

Every long task included nearby spans or explicit context. Sample details included route context, chart mode, selected timeframe, selected contract, trade rail open/collapsed state, selected quote stream status, and visible right-rail panel.

Hot-path spans stayed small:

| Span | p95 ms |
| --- | ---: |
| `applyRealtimeQuote` | 0.4 |
| `applyRealtimeCandle` | 1.6 |
| `applyTradeSelectedQuoteMessage` | 0.6 |
| `renderTradeActiveTrader` | 3.5 |

## 5 Min History-Window Experiment

SPY 5 min, nearest 0DTE, same visible layout, Active Trader open. The 60-day sample is the previous default/current baseline before changing the default.

### Browser Summary

| 5 min history | Events | Long tasks | Attributed | Long p50 ms | Long p95 ms | Long max ms | `/update_price` fetch p50 ms | fetch p95 ms | fetch max ms | parse p95 ms | apply p95 ms | full chart apply | incremental chart apply |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| 60d | 521 | 10 | 10 / 10 | 350.5 | 381.9 | 399.0 | 1145.7 | 1502.5 | 1542.1 | 7.0 | 374.2 | `renderTVPriceChart` 376.2 ms | 14.1 ms |
| 30d | 493 | 8 | 8 / 8 | 190.0 | 209.4 | 215.0 | 1199.5 | 1412.4 | 1436.0 | 3.0 | 202.2 | `renderTVPriceChart` 198.2 ms | 7.9 ms |
| 20d | 508 | 8 | 8 / 8 | 150.5 | 164.6 | 167.0 | 939.3 | 1402.3 | 1453.8 | 4.4 | 155.7 | `renderTVPriceChart` 150.3 ms | 6.3 ms |
| 10d | 501 | 9 | 9 / 9 | 99.0 | 126.2 | 135.0 | 682.0 | 966.7 | 998.3 | 4.2 | 125.0 | `renderTVPriceChart` 116.8 ms | 4.4 ms |

Active Trader hot paths stayed small across all samples:

| History | Selected quote p95 ms | `renderTradeActiveTrader` p95 ms |
| --- | ---: | ---: |
| 60d | 0.7 | 3.5 |
| 30d | 0.7 | 3.6 |
| 20d | 0.7 | 3.8 |
| 10d | 0.7 | 3.8 |

### Server `/update_price` Summary

Grouped by logged 5 min `lookback_days` from `/tmp/gex-speed-5017-history-window.log`.

| 5 min history | n | Median bytes | total p50 ms | total p95 ms | total max ms | `get_price_history_ms` p50 | get p95 | get max | `prepare_price_chart_data_ms` p50 | prep p95 | prep max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 60d | 5 | 5,075,094 | 863.9 | 1913.7 | 2007.9 | 642.1 | 1242.7 | 1380.1 | 99.5 | 1060.3 | 1298.5 |
| 30d | 4 | 2,682,822 | 1194.3 | 1468.8 | 1475.3 | 1093.9 | 1374.9 | 1381.8 | 60.0 | 68.8 | 70.1 |
| 20d | 4 | 1,947,530 | 528.6 | 1319.8 | 1449.4 | 447.6 | 1244.3 | 1374.5 | 53.9 | 56.8 | 57.2 |
| 10d | 4 | 1,268,732 | 658.5 | 980.6 | 994.0 | 593.3 | 913.0 | 924.9 | 43.9 | 52.8 | 53.5 |

### Decision

Reducing the 5 min default to 10 days is worth implementing.

Evidence:

- median `/update_price` bytes dropped from about `5.1 MB` to `1.3 MB`;
- browser full chart apply dropped from about `376 ms` to `117 ms`;
- long-task p95 dropped from about `382 ms` to `126 ms`;
- `/update_price` server p95 dropped from about `1914 ms` to `981 ms`;
- quote stream and Active Trader spans did not regress.

The 20-day window was also much better than 60 days, but 10 days produced the clearest browser and route improvement for the scalping 5 min workflow. The hard cap remains available for manual long-window review.

## Expiry Sync Validation

Visible UI path used:

1. Opened the dashboard expiry dropdown.
2. Added `2026-05-07`.
3. Removed `2026-05-06`.

Immediate state after the switch:

- Dashboard expiry display showed `2026-05-07`.
- Active Trader cleared stale selected contract state and showed `Choose a contract`.
- Buy/Sell controls were disabled.
- Quote timestamp cleared to `—`.
- UI message changed to `Dashboard expiry changed. Preview again.`

After the forced trade-chain refresh:

- Active Trader selected `SPY 260507C00735000`.
- Header showed `SPY 735C · 1DTE`.
- Selected quote stream showed `Live`.
- Server log showed the old quote stream closing and the new selected quote stream connecting:
  - `GET /trade/quote_stream/SPY%20%20%20260506C00735000?ticker=SPY`
  - `GET /trade/quote_stream/SPY%20%20%20260507C00735000?ticker=SPY`
  - `[PriceStreamer] Subscribed to option SPY   260507C00735000`
  - `[PriceStreamer] Unsubscribed from option SPY   260506C00735000`

No live-order endpoints were called during the switch.

## Bottlenecks

1. Fresh `/trade_chain` remains upstream-bound.
   - Cache/stale paths are still fast, but fresh refreshes often spend hundreds of milliseconds in Schwab chain/quote work.
   - This pass did not change that behavior.

2. `/update` remains slow but isolated.
   - The expiry-sync validation still produced slow `/update` samples when the dashboard expiry context changed.
   - Quote and Active Trader spans stayed small, so this is still not the first scalping hot-path optimization target.

3. Full-context chart work is still visible, but the 5 min default reduction materially reduces its size.
   - 10-day full chart apply was about `117 ms` instead of about `376 ms`.
   - Incremental 5 min chart work stayed small.

4. Contract Helper can still show mixed context text during a rapid expiry transition.
   - Active Trader selected quote state corrected quickly to `SPY 260507... · 1DTE`.
   - The helper snapshot still contained some previous-expiry descriptive text while also showing 1DTE quick buttons.
   - This is a UI consistency follow-up, not a selected quote stream bug.

## What Is Next

The next step is more validation before another broad optimization pass.

This pass changed the measured shape of the 5 min path enough that the old long-task numbers are no longer the right baseline. The immediate priority is to run a longer market-hours validation with the new 10-day 5 min default and the new attribution payload. If that validation passes the fast-lane targets, the next code change should be the smaller Contract Helper context cleanup. If it does not pass, use the new attribution detail to choose a narrow optimization target.

Recommended order:

1. Run a full post-change market-hours validation.
2. Fix Contract Helper mixed-expiry display if it still reproduces.
3. Only then optimize remaining long tasks, using attribution to pick the exact lane.

## Next Implementation Actions

### 1. Full Post-Change Validation

Purpose:

- Confirm that the 5 min 10-day default holds up beyond the short experiment.
- Confirm the dashboard expiry sync fix is stable during normal use.
- Establish a new baseline for long tasks after the history-window change.

Implementation:

- No code change required.
- Start the traced server with `GEX_PERF_TRACE=1`.
- Use SPY nearest 0DTE if available, otherwise nearest 1DTE.
- Keep Active Trader open and Auto Send off.
- Export browser traces at checkpoints instead of relying on console history.

Test protocol:

1. 5 min SPY, nearest 0DTE, Active Trader selected near ATM call, 5 minutes.
2. 5 min SPY, nearest 0DTE, switch to near ATM put, 5 minutes.
3. 1 min SPY, selected contract live, 3-5 minutes.
4. Dashboard expiry switch: 0DTE to 1DTE and back if both are available.
5. Rail stress: collapse/expand trading rail and right rail while selected quote stream is live.
6. Order polling containment: keep Auto off; stage a local ladder price only if needed; do not preview or place. Open/close Orders and confirm polling starts/stops only when expected.

Pass criteria:

- `applyTradeSelectedQuoteMessage` p95 under `5 ms`.
- `renderTradeActiveTrader` p95 under `20 ms`.
- `buildTradeActiveLadderHtml` p95 under `10 ms`.
- `applyRealtimeQuote` p95 under `20 ms`.
- `/trade_chain` cache-hit or stale paths p95 under `100 ms`.
- `/update_price` p95 under `3000 ms`, with 5 min median bytes near the new 10-day range rather than the old 60-day range.
- No repeated browser long tasks over `100 ms` caused by normal quote ticks.
- At least 80% of browser long tasks over `100 ms` include a useful attribution context.
- Quote age stays `now` or `1s` while selected quote stream status is `Live`.
- No preview/place/live-order endpoints appear in the server log.

Artifacts to save:

- Server log under `/tmp/gex-speed-5017-<label>.log`.
- Browser trace JSONL under `/tmp/gex-browser-perf-*.jsonl`.
- A dated result note under `docs/`.

### 2. Contract Helper Expiry Context Cleanup

Purpose:

- Remove the remaining mixed-context UI during dashboard expiry switches.
- Active Trader already moves to the new selected contract quickly; the helper should not show previous-expiry header/candidate text during that transition.

Likely implementation:

1. Add a helper payload context check before rendering Contract Helper rows.
   - Compare helper payload expiry/request key against `tradeRailState.expiry`.
   - If they differ, render a loading/empty helper state instead of previous-expiry candidates.
2. On `syncTradeRailToDashboardExpirySelection()`, clear any helper-specific cached selection/candidate state that is derived from the old `tradeRailState.payload`.
3. When the forced `/trade_chain` response returns, render helper rows only from the new payload.
4. Keep the selected Active Trader contract logic separate. The quote stream should continue to use `tradeRailState.selectedSymbol` and the stream sequence guard.

How to test:

1. Start traced server on `PORT=5017`.
2. Open SPY nearest 0DTE, Active Trader open, Auto off.
3. Select a visible helper candidate.
4. Switch dashboard expiry from 0DTE to 1DTE.
5. Immediately verify:
   - Active Trader says `Choose a contract` or a new 1DTE contract, never the old 0DTE selected header.
   - Contract Helper does not show old `May 06` candidate text while the selected dashboard expiry is `2026-05-07`.
   - Buy/Sell are disabled until a valid selected contract exists.
6. After `/trade_chain` completes, verify:
   - helper header/candidates match 1DTE;
   - selected quote stream is `Live` for the new symbol;
   - old stream messages do not mutate the new selected state.

Static checks:

```bash
python3 -m py_compile ezoptionsschwab.py
node --check /tmp/gex-inline-scripts.js
git diff --check
```

### 3. Long-Task Follow-Up Only If Needed

Purpose:

- Avoid optimizing blindly. The new trace detail should show which app span or context is closest to any remaining long task.

Decision tree:

- If long tasks align with `renderTVPriceChart` or `applyPriceData` full-context mode, focus on full-context chart work.
- If long tasks align with rail rendering spans, add signature skips or defer non-visible panel work.
- If long tasks align with `/update` apply/update chart spans, split slow analytics DOM rendering from fast visible updates.
- If long tasks have event-loop delay but no nearby app span, add temporary spans around the next suspected surface before changing behavior.

Implementation options by attribution:

| Attribution points to | Candidate implementation | Test |
| --- | --- | --- |
| Full-context chart apply | Reduce nonessential synchronous overlay rebuilds, keep line/marker signature skips, and consider staged `requestAnimationFrame` application for noncritical overlays | Switch timeframe/expiry repeatedly and compare `renderTVPriceChart`, `applyPriceData`, and long-task p95 |
| Right rail or alert rendering | Render only active/visible panel work immediately; defer hidden panels; add stable render signatures for unchanged alert/flow payloads | Toggle right-rail tabs during live quote stream and verify quote spans stay under target |
| Trade rail helper rendering | Signature-skip unchanged helper/selected-contract blocks; avoid rebuilding ladder unless quote signature or price grid changed | Selected quote stream live for 5 minutes; compare `renderTradeActiveTrader` and helper spans |
| `/update` apply work | Defer noncritical analytics panel DOM updates; avoid touching formulas; keep fast lanes isolated | Force `/update` while selected quote stream is live and verify quote age remains current |

### 4. More Testing Protocols Worth Trying

Use these only after the standard SPY validation passes or when a specific risk needs coverage.

| Protocol | Why run it | What to compare |
| --- | --- | --- |
| Cross-symbol sample: QQQ and IWM | Confirms the speed profile is not SPY-specific | `/update_price` bytes, `/trade_chain` contracts, selected quote p95 |
| Volatile period sample: first/last 30 minutes | Stresses quote/candle frequency and upstream Schwab latency | quote age, selected quote event count, browser long-task rate |
| Quiet-market sample | Separates route/browser cost from perceived market movement | route p50/p95 and trace spans even when quote changes are sparse |
| Rail state matrix | Verifies hidden/collapsed panels are not doing heavy work | right rail open/closed, trading rail open/closed, Orders open/closed |
| Overlay matrix | Finds expensive optional chart surfaces | VP/TPO/RVOL/indicators on/off with same timeframe/history |
| Expiry-switch loop | Regresses stale contract/stream bugs | 0DTE to 1DTE to 0DTE, repeated 3 times, no stale mutation |

## Plan For The Next Full Retest

1. Start:

```bash
PORT=5017 GEX_PERF_TRACE=1 FLASK_DEBUG=0 FLASK_USE_RELOADER=0 python3 ezoptionsschwab.py 2>&1 | tee /tmp/gex-speed-5017-post-history-default.log
```

2. Preflight:

- Check `/token_health`.
- Confirm 5 min Chart Data Window shows `10`.
- Confirm Auto Send is off.
- Confirm Active Trader is open.

3. Run the matrix:

- 5 min 0DTE call for 5 minutes.
- 5 min 0DTE put for 5 minutes.
- 1 min selected contract for 3-5 minutes.
- 0DTE to 1DTE expiry switch.
- Rail collapse/expand.
- Orders open/closed with no preview/place calls.

4. Export traces:

- Use the in-page Trace button after each major sample if possible.
- Keep the server log from the full run.

5. Summarize:

- Route table: `/update_price`, `/trade_chain`, `/update`, `/trade/orders`, `/trade/account_details`.
- Browser table: long tasks, event-loop delay, selected quote, Active Trader, realtime quote/candle, chart apply modes.
- Attribution summary: top long-task nearby spans and contexts.
- Safety audit: prove no preview/place/live-order endpoints were used.
- Decision: pass, Contract Helper cleanup only, or targeted long-task optimization.

## Prompt For The Next Session

Use this prompt to start the next Codex session:

```text
We are in /Users/scottmunger/Desktop/Trading/Dashboards/GEX-Dashboard on main.

Read first:
- AGENTS.md
- docs/speed/README.md
- docs/speed/AGENTS.md
- docs/SCALPING_SPEED_VALIDATION_PLAN.md
- docs/SCALPING_SPEED_RESULTS_2026-05-06_ATTRIBUTION_HISTORY_SYNC.md

Confirm:
- git branch -a
- git log --oneline main..HEAD
- git status --short

Do not place live orders. Keep Auto Send off. Do not use preview/place/live-order endpoints unless I explicitly ask for safe preview-only behavior.

Goal: run the next post-change scalping-speed validation before optimizing further.

Use PORT=5017 with GEX_PERF_TRACE=1. Validate SPY nearest 0DTE if available, otherwise nearest 1DTE. Confirm the 5 min Chart Data Window default is 10 days. Keep Active Trader open.

Run this matrix:
1. 5 min 0DTE call sample for about 5 minutes.
2. 5 min 0DTE put sample for about 5 minutes.
3. 1 min selected-contract sample for 3-5 minutes.
4. Dashboard expiry switch 0DTE to 1DTE and back if both are available.
5. Collapse/expand trading rail and right rail while selected quote stream is live.
6. Verify order polling containment with Auto off and no preview/place/live-order calls.

Capture:
- server route p50/p95/max and bytes;
- /update_price lookback_days, get_price_history_ms, prepare_price_chart_data_ms;
- browser_long_task and browser_event_loop_delay p50/p95/max with attribution summary;
- selected quote, Active Trader, realtime quote/candle spans;
- chart apply modes;
- visible stale quote/candle/ladder behavior;
- whether Contract Helper still shows mixed-expiry text after expiry switch.

If validation passes, implement only the Contract Helper mixed-expiry cleanup if it still reproduces. If validation fails, use long-task attribution to choose one narrow optimization target. Preserve analytical formulas, single-file ezoptionsschwab.py, vanilla JS, /trade_chain cache/stale behavior, and conditional /trade/orders polling/backoff.

Afterward, run py_compile, extracted inline JS node --check, and git diff --check. Add a dated docs/SCALPING_SPEED_RESULTS_YYYY-MM-DD_<LABEL>.md note and update docs/speed/README.md if it becomes the latest reference.
```
