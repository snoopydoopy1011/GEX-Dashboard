# GEX Dashboard Audit Findings and Implementation Plan

Last finalized: 2026-05-02 America/Chicago / 2026-05-03 UTC, after a direct smoke test of `http://127.0.0.1:5014/`.

## 1. Executive Summary

This audit assumes the dashboard is primarily for SPY 0-1 DTE directional scalps with 2-10 minute holds. The main product goal is fast read, fast contract selection, fast entry/exit, and low cognitive load. The existing dashboard has the right raw ingredients: a Lightweight Charts price chart, VWAP/EMA/session-level support, manual drawing tools, dealer levels, GEX/DEX state, flow alerts, a contract helper, and guarded Schwab order entry. The current issue is not lack of capability. The issue is prioritization, state clarity, and the number of surfaces competing with the price chart.

The highest-confidence work should start with bugs and state consistency:

- Fix renderer/markup mismatches in the right rail.
- Stabilize alert identity and scope alert state to the actual expiry/context.
- Preserve manual trade-rail expiry selection across refreshes.
- Make stale quote, preview age, and selected contract state impossible to miss.
- Bring the Levels rail into parity with the levels actually drawn on the chart.
- Relabel "Since Open" values when the baseline is really first sample after app start.

No analytical formula changes are recommended in Phase 1. Several formula-adjacent labels and baselines need clearer wording, but the math should remain untouched unless a targeted implementation pass proves an actual bug.

## 1.1 Port 5014 Smoke-Test Addendum

Smoke test target: `http://127.0.0.1:5014/`, SPY, visible expiry `2026-05-04`, 5 minute chart, Strike Rail expanded, Overview rail open, Order Entry rail open. The smoke test happened on 2026-05-02 CDT / 2026-05-03 UTC against the running local app.

Important correction:

- The first visual pass accidentally inspected an older scrolled portion of the chart. After the chart was scrolled/focused back to the current day, the visible price chart, Market State, Strike Rail, and trade rail all aligned around SPY `$720.00`. Do not treat the earlier `673-674` view as a chart/spot synchronization bug.
- Future smoke tests must explicitly focus the current session before judging spot alignment. Use the visible `Today` control or current-session focus, then verify the price axis, Market State spot, Strike Rail y-axis, selected contract strikes, and current-price line are all in the same region.

Confirmed visually on port 5014:

- The chart is the dominant surface, but core context remains split across the chart toolbar, settings drawer, right rail, bottom alerts lane, and trade rail. There is still no compact always-visible chart context strip for symbol, expiry, timeframe, stream/options freshness, nearest level, and top alert.
- Live Alerts are still too far from the chart. The bottom lane showed structural cards such as `Long-gamma regime — dealer hedging dampens moves` and `Approaching Gamma Flip @ 718.27`, while the chart and Overview card did not surface one high-priority active alert near the decision point.
- Structural gamma regime is being rendered as an alert card. That is useful market state, but it should not keep competing with event-style alerts unless the regime just changed.
- The Levels tab switched and rendered cleanly, but it still listed only the narrower core level set while the chart can render more dealer levels. The visible current session showed charted levels such as Call Wall `$725.00`, Gamma Flip `$718.27`, VWAP, EMA9, and historical markers; the Levels rail still needs to become the explanatory source of truth for every dealer level the chart can draw.
- Chain Activity mismatch is confirmed. The visible activity block exposes `VOL` and `V/OI`, while code inspection confirms `renderChainActivity()` still writes `oi_fill`, `oi_cp`, and `oi_split` targets that do not exist in the server HTML or `buildAlertsPanelHtml()`.
- Order Entry has the right primitives but remains dense. The selected-contract card visibly showed bid/mid/ask, spread, IV/delta, quote/trade time, and a `Stale quote` warning, but quote age is not prominent in the top Active Trader fast path. Preview TTL is also not visible near the fast action controls.
- The selected contract and ticket can preserve a stale limit by design. That is safer than silently moving a live order, but the UI needs explicit "quote moved since limit/preview set" state plus one-click reprice actions.
- The right rail hierarchy issue is real in the live layout. Overview starts with Market State, then Dealer / Hedge Read, Skew / IV, and Centroid Drift; the nearest actionable level stack and top alert are not first-class near the chart.
- Strike Rail is visually useful when aligned to the current session, but it consumes major horizontal space as a primary surface. This reinforces the plan to keep it as "Strike Inspect" rather than making it part of the default scalp read.
- Flow Pulse remained in an empty-state panel (`Pulse data builds after a minute of live flow history`) while Live Alerts had active cards. This may be expected history gating, but visually it reads as inactive/dead space and should be reviewed when alerts are reworked.
- Browser console smoke check was clean except for the existing Plotly CDN warning that `plotly-latest` points to an old v1.x release.

Adjusted or not visually reproduced:

- The Scenarios tab rendered and switched cleanly on port 5014. The server/rebuild markup mismatch remains a code fragility finding, but it was not visible as a broken tab during this smoke test.
- Trade-rail expiry reset was not visually reproduced because the current picker showed only `2026-05-04`. The code still clears `tradeRailState.expiry` during normal open-rail refresh, so the bug remains high-confidence for multi-expiry sessions.
- Overlay density is partly a default/preset issue rather than the exact current user state. On the live drawer, several heavy overlays were off, while the code defaults still enable `hvl`, `em_2s`, `walls_2`, `live_gex_extrema`, and `historical_dots`. Implement a Scalp preset instead of blindly changing the user's current saved preferences.

Implementation consequence:

- Phase 1 remains valid, but do not add a "chart spot mismatch" fix. The smoke test strengthens the priority for right-rail markup parity, deterministic/scoped alert state, trade-rail state persistence, quote/preview freshness, Levels rail parity, and clearer baseline labels.

## 1.2 Final Port 5014 Pass Notes

The final pass reopened port `5014`, focused the current session with `Today`, and visually checked Overview, Levels, Scenarios, Flow, Order Entry, the Settings drawer, the Strike Rail, the bottom alerts/pulse lane, and the Journal workspace.

Additional confirmations:

- The chart, Strike Rail, Overview spot, and selected 720C order-entry context all aligned around SPY `$720.00` after focusing the current session.
- Console smoke remained clean except for the existing Plotly CDN warning that `plotly-latest` resolves to old Plotly v1.58.5.
- The Levels tab is not broken. It rendered a useful nearest-level stack with Gamma Flip, EM bounds, Call Wall, Put Wall, and OI Max Pain. The issue is placement: the nearest actionable level stack is hidden behind the Levels tab instead of being promoted into Overview or the chart context strip.
- The Scenarios tab rendered seven rows and the `Current` Net GEX row matched the Overview Net GEX within rounding. The server/rebuild markup mismatch remains a fragility issue, not a visible tab failure.
- The Flow tab rendered a populated Flow Blotter (`58 shown` in this session). The empty bottom Flow Pulse panel therefore should not be interpreted as "no flow data"; it is a separate pulse-history/eligibility empty state.
- The Settings drawer confirmed that saved user state differs from code defaults: HVL, secondary walls, live max +/-GEX, and historical dots were off, while +/-2 sigma EM lines were on. Implement a Scalp preset without blindly overwriting saved preferences.
- The closed chart view still lacks an always-visible symbol/expiry/context read. Symbol, expiry, and some high-impact state are only obvious after opening the drawer, while timeframe and chart controls are visible in the chart toolbar.
- The Settings drawer showed `Exposure Metric: Open Interest` while the visible Strike Rail selector was `GEX`. This may be technically correct because the drawer metric affects option-chain weighting, but the labels read as conflicting surfaces during a fast visual scan.
- The Journal workspace opened cleanly and showed two local previewed-order events, but `Deterministic P/L` remained `Not inferred`. This confirms that the journal is useful for event capture, not yet for quick scalp lifecycle review.

## 2. Trading Workflow Assumptions

- Primary instrument: SPY options.
- Typical expiry: 0DTE or 1DTE.
- Directional trades: calls or puts, not multi-leg structures.
- Hold time: roughly 2-10 minutes.
- Decision stack: price action first, then VWAP/EMA/session levels, then dealer/key levels, then flow/alert confirmation, then contract liquidity/spread.
- Needed at all times: current symbol, selected expiry scope, candle timeframe, live/stale status, nearest actionable levels, active alert, selected contract quote age, current position, working orders, and preview/live-trading state.
- Lower-priority context: full scenario table, centroid history, IV/skew detail, full flow blotter, full contract picker, bracket planner, and historical bubble inspection.

## 3. Section-by-Section Findings

### 3.1 Price Chart + Top Bar

Relevant anchors: `ensurePriceChartDom`, `buildTVToolbar`, `renderTVPriceChart`, `fetchPriceHistory`, `updateData`, `renderKeyLevels`, `renderSessionLevels`, `tvRefreshOverlayLevelPrices`, `updatePriceInfo`, `renderPriceHeader`.

Strengths:

- `renderTVPriceChart` uses Lightweight Charts for the primary price surface and keeps the chart alive across updates instead of rebuilding it every tick.
- `connectPriceStream`, `applyRealtimeQuote`, and `applyRealtimeCandle` separate live price/candle updates from the heavier `/update` options-chain poll.
- `calcVwapTosStyle`, EMA/SMA support, RVOL volume coloring, candle-close timer, and manual H-line/channel/AVWAP tools are directly useful for 2-10 minute scalps.
- `tvFocusCurrentSession` correctly avoids falling back to an older session when today's data is not loaded yet.
- `tvApplyAutoscale` has a session mode that tries to protect the candle view from far-off level lines by only including levels inside the session focus range.

Findings:

- The core trading context is split. Symbol and expiry live in the drawer, timeframe lives in both the drawer and `buildTVToolbar`, market state is in the right rail, and live alerts are in the bottom flow lane. For scalping, the chart needs a compact always-visible context strip near the chart with symbol, expiry scope, timeframe, last update age, stream status, selected GEX scope, nearest key level, and active alert.
- The default overlay set is too dense for fast decisions. `CHART_VISIBILITY_DEFAULTS` enables `hvl`, `em_2s`, `walls_2`, `live_gex_extrema`, and `historical_dots` by default. `renderKeyLevels` can draw primary walls, secondary walls, HVL, max positive/negative GEX, +/-1 sigma EM, and +/-2 sigma EM. `renderSessionLevels` can then add today, yesterday, premarket, opening range, initial balance, and after-hours levels. This is powerful, but it can bury candles and VWAP/EMA context.
- Port 5014 smoke-test nuance: the current saved drawer state had several heavy overlays off, so the density concern should be handled as a default/preset/workflow issue, not as proof that the user's current chart is always overloaded. The right implementation is a Scalp preset and clearer overlay grouping, not forcing all saved preferences to a new state.
- The chart autoscale still has to consider `tvAllLevelPrices`, which includes historical bubbles, key levels, session levels, top OI, and manual drawings. Session mode limits this, but fit-all mode and some focus cases can still make price action feel compressed if many far-off levels are enabled.
- `fetchPriceHistory` is throttled to 30 seconds unless forced, while `/update` runs every second and SSE updates live candles. That split is good for performance, but the UI does not make it obvious which values are streaming live, which values are from the last `/update_price`, and which values depend on cached options data.
- `updateData` uses `JSON.stringify(data) !== JSON.stringify(lastData)` before rendering options/rail updates. For a large payload this is expensive at a 1 second cadence and can still update too much UI when only small fields change.
- There is a legacy chart implementation still present around the old `renderPriceChart` path and old globals like `tvChart`, `tvCandle`, `tvIndSeries`, and duplicate indicator color definitions. Even if dead, this raises maintenance risk because future fixes can land in the wrong renderer.
- The modern toolbar still contains text-heavy controls (`Indicators`, `Tools`, `Chart`, `Range ON/OFF`, `Today`) and several custom symbols. It is workable, but the highest-frequency scalping controls should be visually separated from lower-frequency configuration.
- Existing code contains raw color literals in chart and indicator setup. Do not expand this pattern. New work should route colors through existing tokens or central preferences.
- Port 5014 correction: after focusing the current session, the visible chart price region aligned with Market State and Strike Rail around `$720.00`. There is no current evidence of a chart/spot synchronization bug from this smoke test. Any future chart-sync investigation should first force the current-day view.
- Final port 5014 pass: the chart toolbar exposed timeframe, indicators, drawing tools, overlay mode, range, and current-session focus, but not the full trading context (`SPY`, selected expiry, options-chain freshness, nearest level, top alert). This confirms the chart-context strip should be Phase 2, not optional polish.

Recommendations:

- Add a chart-context strip inside `workspace-toolbar-shell` or adjacent to `tv-toolbar-container`: `SPY`, selected expiry/scope, `1m/2m/5m`, stream state, options update age, nearest level, and top active alert.
- Create a "Scalp" overlay preset: candles, volume/RVOL, VWAP, EMA9/EMA21, manual drawings, current session levels, nearest primary dealer levels only. Move secondary walls, +/-2 sigma EM, historical dots, max +/- GEX, and full session ladders behind explicit toggles.
- Keep manual drawing tools, but make H-line, AVWAP, hide/show, and clear the primary drawing controls. Put low-frequency drawing types behind the existing Tools menu.
- Add a stale-data indicator that differentiates live price stream, `/update_price`, and `/update` option-chain age.
- During implementation cleanup, remove or quarantine the legacy renderer only after confirming no code path still calls it.

### 3.2 Strike Rail

Relevant anchors: `create_gex_side_panel`, `create_strike_profile_payload`, `applyStrikeRailTabs`, `isGexColumnCollapsed`, `wireGexColumnToggle`, `renderStrikeRailPanel`, `getStrikeRailSyncSpec`, `buildStrikeRailFigure`, `syncGexPanelYAxisToTV`.

Strengths:

- The rail has real unique value as an inspect surface: exact per-strike bars, GEX plus secondary metric tabs, and y-axis sync to the price chart.
- It defaults hidden when the TradingView strike overlay is enabled, which is the right default for chart-first scalping.
- `wireGexColumnToggle` disables the strike overlay when the strike rail is expanded, reducing duplicate GEX surfaces.

Findings:

- For normal scalp decisions, the rail duplicates the chart overlay and costs horizontal space. It should not compete with candles by default.
- The rail has hidden value that is easy to miss. Since it is collapsed when the overlay is enabled, the user may forget it can inspect gamma, delta, vanna, charm, open interest, options volume, and premium in the same y-range as the chart.
- `buildStrikeRailFigure` uses raw Plotly background fallbacks and generic Plotly chart payloads. This is not a functional bug, but it is visual-token drift compared with the modern CSS-token rail.
- The current rail is either fully present or absent. It does not have a narrow contextual mode showing just the nearest strikes around spot and selected contract.
- Port 5014 smoke test showed the expanded Strike Rail was correctly aligned with the current-day `$720` chart after focus correction, and the per-strike bars were useful. The issue is not sync in that state; it is that the expanded rail consumes too much horizontal attention for a default scalp layout.

Recommendation:

- Keep the strike rail hidden by default. Do not remove it yet.
- Reframe it as "Strike Inspect" rather than a primary rail.
- Phase 2 should add a contextual mini readout near the chart or Levels rail for nearest strike GEX/volume/OI, leaving the full strike rail for inspection.
- Phase 3 can decide whether to merge strike inspection into the Levels/Flow experience after usage proves the full rail is rarely needed.

### 3.3 Overview / Levels / Scenarios / Flow Rail

Relevant anchors: `buildAlertsPanelHtml`, `ensurePriceChartDom`, `wireRightRailTabs`, `applyRightRailTab`, `renderMarketMetrics`, `renderDealerImpact`, `renderGammaProfile`, `renderRangeScale`, `renderChainActivity`, `renderRailKeyLevels`, `renderScenarioTable`, `renderRailFlowBlotter`, `renderFlowPulse`, `renderRailAlerts`, `compute_trader_stats`, `compute_key_levels`, `_compute_session_deltas`.

Strengths:

- The Overview rail correctly starts with Market State: price, GEX/DEX, regime/profile, drift, GEX scope, expected move, and live straddle.
- Dealer Impact is formula-aligned in code: `hedge_on_up_1pct = -0.01 * net_gex` and `hedge_on_down_1pct = +0.01 * net_gex`, with comments explaining long-gamma fade versus short-gamma reinforce.
- The Flow tab has useful sorting/filtering state through `initFlowBlotter`.
- Scenario GEX reuses `_recompute_gex_row` and `calculate_greek_exposures`, so it does not create a second exposure formula.

Findings:

- The right rail order is not yet optimized for fast scalping. `Market State` is useful, but `Dealer / Hedge Read`, `Skew / IV`, and `Centroid Drift` appear before a complete nearest-level/action list. A scalper usually needs "what level am I approaching now?" before full dealer context.
- Live Alerts and Flow Pulse are no longer in the right rail panel; they are in `buildFlowEventLaneHtml` as a bottom lane. However, the IDs and function names still say `right-rail-alerts` and `rail-flow-pulse`. This creates naming confusion and also places the highest urgency signal away from the chart.
- The Levels tab is incomplete relative to the chart. `renderRailKeyLevels` only lists call wall, put wall, gamma flip, max pain, and +/-1 sigma EM. `renderKeyLevels` can draw secondary walls, HVL, max positive/negative GEX, and +/-2 sigma EM. This means the chart can show lines that the Levels rail cannot explain.
- Chain Activity has a JS/HTML mismatch. `renderChainActivity` tries to update `oi_fill`, `oi_cp`, and `oi_split`, but both the server HTML and `buildAlertsPanelHtml` only contain VOL and V/OI rows. Open-interest values are computed in `compute_trader_stats` but not visibly rendered in the current card.
- The server-rendered Scenarios panel markup differs from the `ensurePriceChartDom` rebuild string. Around the server HTML scenarios block there is an extra/misaligned close before the Flow panel. The rebuild path has the cleaner expected nesting. This can create fragile DOM behavior when panels are rebuilt.
- Port 5014 smoke-test status: Levels, Scenarios, and Flow tabs all switched and rendered without a visible tab failure. This reduces urgency on the Scenarios nesting issue as a user-visible bug, but it remains worth fixing because divergent server/rebuild markup is fragile.
- Port 5014 smoke-test status: the Overview rail visibly placed Market State, Dealer / Hedge Read, Skew / IV, and Centroid Drift before any full nearest-level/action stack. This confirms the hierarchy issue as a live workflow problem, not just an abstract information-architecture concern.
- Port 5014 smoke-test status: Flow Pulse stayed in an empty state while Live Alerts had active cards. That may be correct history gating, but the visual result is a large bottom-lane area that feels inactive during an otherwise data-rich session.
- Final port 5014 pass: the Flow tab itself was populated (`58 shown`) while the bottom Flow Pulse panel was empty. Do not treat the pulse empty state as a missing-flow-data bug until `build_flow_pulse_snapshot`/`summarize_flow_pulse` eligibility is reviewed; the UX issue is that the empty panel does not explain why no pulse is available.
- Final port 5014 pass: the Levels tab's nearest-level cards are strong enough to reuse. Phase 2 should promote the top one or two nearest cards into Overview/chart context rather than inventing a separate nearest-level presentation from scratch.
- `renderRailKeyLevels` labels level drift as "Since Open", but `_compute_level_session_deltas` captures the first in-process sample for that ticker/day/scope. If the app starts at 10:45 ET, "Since Open" is actually "since first app sample".
- `renderMarketMetrics` and `renderNetExSparkline` partly handle open versus first-sample anchoring through `netexSparkAnchorMode`, but the user-facing card still needs clearer baseline wording.
- The Scenarios tab is useful but too heavy for the main scalp loop. It should remain available, not promoted.

Recommended information hierarchy:

1. Chart context strip: price, stream/options freshness, active alert, nearest level.
2. Right rail Overview top: Market State plus nearest key levels.
3. Right rail Overview middle: Live Alerts / Flow Pulse compact list.
4. Right rail Overview lower: Dealer Impact and Chain Activity.
5. Right rail lower/secondary: IV/skew, Centroid Drift.
6. Separate tabs: full Levels, Scenarios, full Flow Blotter.

### 3.4 Order Entry

Relevant anchors: `tradeRailState`, `renderTradeRail`, `renderTradeSelected`, `renderTradeTicket`, `renderTradeActiveTrader`, `requestTradeChain`, `previewTradeOrder`, `placeTradeOrder`, `build_trading_chain_payload`, `trade_preview_order`, `trade_place_order`, `TRADE_PREVIEW_TTL_SECONDS`.

Strengths:

- The live order path is guarded well. `trade_place_order` requires `ENABLE_LIVE_TRADING=1`, final confirmation, a non-expired preview token, exact account/ticker/contract/instruction/quantity/limit match, and matching order JSON.
- `SELL_TO_CLOSE` checks position quantity before preview and again before live placement.
- Active Trader auto-send starts unarmed and uses exact preview binding before live placement.
- Quick contract buttons and contract helper candidates are aligned with the actual cached contract list before selecting.
- The UI includes Buy Ask, Sell Bid, Flatten, position, preview, working orders, and quantity presets, which are the right primitives for scalping.

Findings:

- `updateData` resets `tradeRailState.expiry = ''` before every forced `requestTradeChain` while the trade rail is open. That can wipe a manual order-entry expiry selection and snap the rail back to dashboard expiries during the 1 second polling loop.
- Port 5014 smoke-test status: this reset was not visually reproduced because the visible contract picker only had `2026-05-04`, but the code path is explicit. Re-test with multiple available expiries before and after the fix.
- `renderTradeSelected` auto-fills limit price from ask for BTO or bid for STC only when `tradeRailState.limitPrice` is blank. After a quote refresh, the limit can remain stale by design. That is safer than silently moving the order, but the UI needs an explicit "quote moved since preview/limit set" state and one-click reprice.
- `build_trading_chain_payload` flags `stale_quote` only when quote age is missing or older than 15 minutes. For 0DTE scalping, 15 minutes is too permissive for live-send decisions. If Schwab quote timestamps are imperfect, add a separate "old snapshot" warning while keeping the backend guard conservative.
- Port 5014 smoke-test status: the selected-contract card did show `Quote / Trade` times and a `Stale quote` warning, but that warning is below the fast Active Trader area. The top-of-rail fast path still needs quote age/preview TTL chips near Buy Ask, Sell Bid, quantity, and preview state.
- Final port 5014 pass: Active Trader showed a selected 720C with bid/mid/ask/limit and Preview status, but there was still no visible quote-age chip or preview TTL near the Buy Ask / Sell Bid / Flatten controls. The order rail also kept the bracket planner visible in the active workflow despite "planning only" copy.
- `renderTradeTicket` dumps raw preview and placement JSON into `data-trade-preview-response`. That is useful for debugging but too noisy for a scalp ticket.
- Bracket templates are "Planning only", but they live inside Active Trader and the full ticket. The warning text is present, but labels like TRG/OCO can still imply Schwab bracket behavior that is not actually sent in `build_single_option_limit_order`.
- The trade rail is dense. It contains Active Trader, Contract Helper, Quick Contracts, Position, Contract Picker, Selected Contract, Order Ticket, Bracket Plan, Submit, Orders, and Journal. The fast path is present but competes with slower inspection tools.

Recommendations:

- Fix expiry persistence first: only clear `tradeRailState.expiry` on ticker/dashboard expiry reset or when the selected expiry no longer exists.
- Add quote freshness and preview TTL to the Active Trader surface.
- Add "Reprice Ask", "Reprice Mid", and "Reprice Bid" actions that invalidate preview clearly.
- Replace raw preview JSON with a compact summary: action, qty, contract, limit, estimated debit/credit, Schwab status, preview age. Move raw JSON behind a details disclosure.
- Make bracket planner collapsed by default or label it "Exit Planner - not sent" in the panel title.
- Keep the full picker, but default the top of the rail to a fast action mode: selected contract, bid/mid/ask, spread, quote age, qty, buy/sell/flatten, position, preview/live state, working orders.

### 3.5 Live Alerts + Flow Pulse

Relevant anchors: `compute_flow_alerts`, `_fetch_vol_spike_data`, `_extract_key_level_prices`, `_alert_cooldown_ok`, `build_flow_pulse_snapshot`, `summarize_flow_pulse`, `renderRailAlerts`, `_clusterRailAlerts`, `_alertPriorityScore`, `renderFlowPulse`, `buildFlowEventLaneHtml`.

Strengths:

- Alert types cover useful scalp context: wall proximity, gamma flip proximity, gamma regime, wall shifts, volume spikes, V/OI, IV surge, and Flow Pulse.
- `compute_flow_alerts` has cooldowns and optional key-level gating, which prevents some noise.
- `renderRailAlerts` buffers, clusters, pins a lead alert, shows supporting alerts, and summarizes overflow.
- Flow Pulse prioritizes recent contract-level flow with volume, premium, pace, V/OI, and directional lean metadata.

Findings:

- Rule-based regime alerts are emitted on every stats build. Regime is structural market state, not an alert. It should live in Market State unless the regime changes.
- Port 5014 smoke-test status: the bottom Live Alerts lane visibly showed `Long-gamma regime — dealer hedging dampens moves` as an alert card while Market State already showed Positive Gamma. That confirms regime demotion as a live UX issue.
- Rule-based alert IDs use Python's built-in `hash(a['text'])`. Python hash randomization means these IDs are not stable across process restarts.
- `_LAST_WALLS` is keyed only by ticker. Wall-shift alerts should also include selected expiry scope and GEX scope. Otherwise switching from all expiries to 0DTE or changing selected expiries can look like a market wall shift.
- `_fetch_vol_spike_data` uses `interval_data` keyed by ticker/date/strike/time and not by expiry scope. Because `store_interval_data` writes whatever chain is currently selected, changing dashboard expiries can mix baselines for the same ticker/strike.
- IV surge uses an in-process IV buffer and a z-score, but it does not require meaningful volume, premium, or spread quality before alerting. Key-level gating helps, but low-quality contracts can still generate noise.
- Volume spike alerts are strike-level, not side-specific, because `interval_data.net_volume` stores calls positive and puts negative but `_fetch_vol_spike_data` uses absolute deltas. For directional scalping, "call volume spike" versus "put volume spike" is more actionable than "Vol spike @ strike".
- Live Alerts and Flow Pulse are rendered in a bottom lane. The highest-priority alert should appear closer to the chart, ideally in the chart context strip or near the current price axis.
- Port 5014 smoke-test status: `Approaching Gamma Flip @ 718.27` appeared in the bottom lane while the chart was trading around `$720.00`. That is exactly the kind of near-spot structural alert that should be promoted into the chart context strip or near the price axis.
- Port 5014 smoke-test status: Flow Pulse remained empty while Live Alerts were populated. Verify whether this is expected warmup/history gating, and if so make the empty state explain freshness/window requirements more concretely.

Recommendations:

- Demote continuous regime alerts into Market State. Emit an alert only on regime change or gamma flip proximity.
- Replace built-in `hash` with deterministic IDs, for example a normalized text slug plus alert type/level/strike/ticker/scope.
- Scope `_LAST_WALLS` and relevant cooldown keys by ticker, selected expiry set, strike range, and GEX scope.
- Add side-aware volume spike storage or derive side from call/put volume deltas before alert wording.
- Add liquidity filters to IV surge alerts: minimum volume, premium, and acceptable spread.
- Promote only critical, recent, near-spot alerts to the chart context strip. Keep the bottom lane or Flow tab for the full feed.

### 3.6 Trading Journal

Relevant anchors: `_write_trade_event`, `_read_trade_event`, `trade_journal_attach_screenshot`, `captureTradeChartScreenshotDataUrl`, `uploadTradePlacementScreenshot`, `buildTradeJournalLifecycle`, `renderTradeJournalStats`, `renderTradeJournalLifecycle`, `renderTradeJournalWorkspace`, `renderTradeJournalWorkspaceDetail`.

Strengths:

- Trade events are persisted locally with statuses, tags, setup, thesis, notes, and outcome.
- Successful live placements automatically create a `placed_order` journal event and attempt to attach a chart screenshot.
- The bottom journal workspace adds stats, lifecycle grouping, summaries, filters, detail, and media display.
- Manual journal entries can use selected contract/order context.

Findings:

- P/L is mostly "Not inferred" unless explicit values exist or a current position day P/L is available. This limits post-session usefulness for scalp review.
- Port 5014 final pass confirmed this in the live Journal workspace: two local previewed-order events were present, but deterministic P/L was still "Not inferred" and lifecycle grouping was preview-event oriented rather than completed-trade oriented.
- Lifecycle grouping uses `order_hash` or a fallback including ticker/contract/account/instruction. That groups preview/place events but does not robustly pair BTO and STC into a complete scalp trade with hold time, entry, exit, and realized P/L.
- Screenshots are only attached after successful live placements, not after exits/cancels/manual journal marks.
- `captureTradeChartScreenshotDataUrl` captures `canvas` layers inside `#price-chart`. DOM/SVG overlays such as historical bubbles, drawing overlays, tooltips, and some labels may not be included. That means the screenshot can miss the exact manual levels and context that mattered at entry.
- Journal fields are good for detailed review but not optimized for one-tap post-trade tagging during a fast session.

Recommendations:

- Add trade lifecycle pairing by account, contract, side, and time window: entry event, exit event, hold duration, entry/exit limits, realized/estimated P/L, and screenshot links.
- Add quick tags after placement/exit: breakout, VWAP reclaim, level reject, trend continuation, chase, early exit, late exit, rule break.
- Capture screenshots on both entry and exit, and include DOM overlays or use a broader capture method that includes manual drawings and key level labels.
- Add a session review summary: number of scalps, win/loss when known, average hold, best/worst setup tags, and screenshots needing notes.

### 3.7 Settings / Hamburger Menu

Relevant anchors: `.drawer`, `.settings-modal`, `gatherSettings`, `loadSettings`, `buildTVToolbar`, `openPriceLevelEditor`, `openTVIndicatorEditor`, `getChartVisibility`, `PRICE_LEVEL_PREFS_KEY`, `CHART_VISIBILITY_DEFAULTS`.

Strengths:

- Most configuration exists and is persisted: timeframe, expiries, chart visibility, indicator styles, price level styles, session levels, RVOL, exposure settings, volume profile/TPO, and alert gating.
- The drawer keeps configuration out of the main chart surface.
- Chart-specific controls have started moving into the toolbar, which is the right direction.

Findings:

- High-frequency controls are split between drawer and toolbar. Expiry/symbol stay in the drawer, timeframe is duplicated, chart overlays are in toolbar menus, and GEX scope is in the Overview card.
- The settings drawer is a mixed list of workspace, chart history, price axis countdown, volume coloring, chart sections, strike range, exposure, series, options volume, price levels, session levels, and profiles. It is comprehensive but not task-oriented.
- Port 5014 smoke-test status: the drawer confirmed that some heavy chart overlays were currently off in saved user state, even though code defaults enable them. Treat saved user state, code defaults, and proposed Scalp preset as separate concepts during implementation.
- Final port 5014 pass: the drawer's `Exposure Metric: Open Interest` label can be confused with the visible Strike Rail metric selector (`GEX`). The implementation may be correct, but the settings label should clarify whether it controls option-chain exposure weighting, secondary charts, or strike profile display.
- Some labels are developer/analytics-oriented rather than workflow-oriented. A scalper needs "Chart Read", "Levels", "Flow Alerts", "Order Entry", and "Advanced Analytics" more than internal implementation categories.
- Existing raw color literals appear in indicator defaults, chart setup, Plotly fallback layouts, and inline style paths. New work should not add more; cleanup can be a later phase.

Recommended structure:

- Workspace: symbol, expiry presets, timeframe, streaming/update state.
- Scalp Chart: indicators, VWAP/EMA, session levels, drawing defaults, overlay preset.
- Dealer Levels: walls, gamma flip, EM, HVL, secondary levels, historical dots.
- Flow Alerts: gate near key levels, alert types, severity thresholds, surface location.
- Order Entry: trade rail defaults, active trader, preview/live safety, quote age.
- Advanced Analytics: scenarios, centroid, profiles, exposure chart settings.

## 4. Bugs or Likely Logic Errors

1. Chain Activity OI row mismatch.
   - Code: `renderChainActivity` writes `oi_fill`, `oi_cp`, and `oi_split`.
   - Markup: server HTML and `buildAlertsPanelHtml` only include VOL and V/OI rows.
   - Impact: computed OI context is silently invisible, and the renderer has stale targets.
   - Port 5014 status: visually confirmed. The card showed `VOL` and `V/OI`, with no OI row, while code still targets OI fields.

2. Scenarios panel markup mismatch.
   - Code: server HTML around `[data-rail-panel="scenarios"]` appears mis-nested compared with the clean `ensurePriceChartDom` rebuild string.
   - Impact: tab/panel DOM can become fragile or inconsistent after rebuilds.
   - Port 5014 status: not visibly reproduced. The Scenarios tab switched and rendered seven rows correctly; the `Current` row matched Overview Net GEX within rounding. Treat this as markup debt/fragility rather than a currently visible broken panel.

3. Alert IDs are not stable across restarts.
   - Code: `compute_trader_stats` uses `hash(a['text'])`.
   - Impact: frontend alert seen/buffer state can reset or duplicate after app restart.
   - Port 5014 status: code-confirmed only. A restart comparison was not performed during the smoke test.

4. Wall-shift state is keyed too broadly.
   - Code: `_LAST_WALLS[ticker]`.
   - Impact: changing expiry scope can generate false wall-shift alerts.
   - Port 5014 status: code-confirmed only. Scope switching was not exhaustively tested in the browser.

5. Trade rail expiry selection is reset during normal refresh.
   - Code: `updateData` sets `tradeRailState.expiry = ''` before `requestTradeChain({ force: true })`.
   - Impact: manual 0DTE/1DTE picker selection can be lost while the rail is open.
   - Port 5014 status: code-confirmed, not visually reproduced because only `2026-05-04` was visible in the picker during the smoke test.

6. "Since Open" labels can mean first sample after app start.
   - Code: `_compute_session_deltas` and `_compute_level_session_deltas` capture in-process baselines.
   - Impact: drift labels can overstate precision when the app was not running at 09:30 ET.
   - Port 5014 status: visually confirmed as a labeling risk. `Since Open` appeared in Skew / IV while the underlying baseline semantics still depend on captured samples.

7. Trade screenshots can miss non-canvas chart context.
   - Code: `captureTradeChartScreenshotDataUrl` draws only `canvas` layers in `#price-chart`.
   - Impact: manual DOM/SVG overlays, some labels, and contextual alert visuals may be absent from journal screenshots.
   - Port 5014 status: not tested. Keep this in Phase 3 unless journal screenshot work becomes part of the current pass.

8. Legacy chart renderer remains in file.
   - Code: old `renderPriceChart`/`tvChart`/`tvCandle` path still exists alongside the modern `renderTVPriceChart` path.
   - Impact: future edits can target dead or wrong code, especially indicator and overlay fixes.
   - Port 5014 status: code-maintenance risk only. The visible chart was the modern Lightweight Charts surface.

## 5. UX/Layout Issues

- The price chart should dominate the first read, but the dashboard currently spreads urgent state across chart toolbar, drawer, right rail, bottom flow lane, and trade rail.
- The Overview rail starts with useful market state, but the nearest actionable level and active alert should appear before deeper dealer/IV/centroid context.
- The strike rail is correctly hidden by default but should be framed as an inspect mode, not another primary surface.
- Order entry contains the necessary fast controls, but the fast path competes with contract picker, helper, bracket planner, journal, orders, and raw preview output.
- The bottom journal workspace is useful after the session, but it should not compete for attention during active scalping unless explicitly opened.
- Port 5014 smoke test showed a large bottom Flow Pulse area in an empty state while Live Alerts had active cards. Either the Flow Pulse empty state needs clearer timing/window copy, or the layout should collapse empty pulse space until meaningful pulse data exists.
- Final port 5014 pass showed the Flow tab populated while the bottom Flow Pulse remained empty. This narrows the issue to pulse eligibility/empty-state communication, not a general flow ingestion failure.
- The visible current-day chart/rail alignment was good after focusing the session. The UX risk is not chart sync; it is making sure the current-session focus is obvious and easy before a user judges live context.

## 6. Opportunities to Make the Dashboard Faster and More Useful

- Add a chart-first "Now" strip: `SPY | 0DTE | 1m | stream live | options 3s ago | nearest level +0.18 | active alert`.
- Add a Scalper overlay preset that reduces chart lines to only VWAP/EMA/session current levels/manual drawings/nearest dealer levels.
- Promote a single active alert near the chart and keep the full alert feed lower.
- Add a nearest-level stack sorted by distance to spot, including every charted dealer level and session level group.
- Add quote-age and preview-age chips to Active Trader.
- Add one-click reprice actions with explicit preview invalidation.
- Pair journal entries into actual scalp trades with hold time and P/L where possible.
- Rename baseline labels to match actual state: "vs open" only when an open-era sample is available, otherwise "vs first sample".
- Add explicit empty-state rules for Flow Pulse: show the warmup window, last eligible flow time, or collapse the panel when no pulse can be computed yet.
- Clarify the drawer's exposure metric labeling so it does not read as conflicting with the Strike Rail metric selector.

## 7. Recommended Information Architecture Changes

Primary screen layout should be:

1. Chart area: candles, VWAP/EMA, volume/RVOL, manual drawings, key levels, compact context strip.
2. Right rail Overview: nearest levels, market state, live alert/pulse, dealer read.
3. Trade rail: fast action mode by default, full picker/details below.
4. Bottom lane: optional full alert feed and journal workspace, collapsed unless reviewing.
5. Strike rail: hidden inspect mode.
6. Settings drawer: task-oriented settings groups.

The most important promotion is moving nearest levels and top active alert closer to the chart. The most important demotion is keeping scenarios, centroid, full flow blotter, and historical bubbles out of the default scalp read.

## 8. Prioritized Implementation Roadmap

### Phase 1: High-Confidence Fixes

No analytical formula changes.

- Fix the Chain Activity OI renderer/markup mismatch. Either add the OI row to both server HTML and `buildAlertsPanelHtml`, or remove stale OI DOM writes from `renderChainActivity`.
- Fix the Scenarios panel nesting in server-rendered HTML and keep it mirrored with `ensurePriceChartDom`.
- Replace Python built-in alert hashing with deterministic alert IDs.
- Scope wall-shift state and alert cooldown keys by ticker plus expiry/scope context.
- Preserve manual trade-rail expiry selection across `updateData` refreshes.
- Add selected contract quote age and preview age/TTL display in Active Trader and Order Ticket.
- Replace raw preview JSON display with a compact preview summary plus optional raw details.
- Expand `renderRailKeyLevels` to include every dealer level that `renderKeyLevels` can draw.
- Relabel drift baselines based on whether the sample is truly 09:30 ET open or first available app sample.
- Add a code comment near `TRADE_PREVIEW_TTL_SECONDS` and UI copy showing 5 minute preview TTL.
- Add a small Flow Pulse empty-state clarification if it can be done without broader layout churn: show the warmup/history requirement or last eligible flow timestamp.

### Phase 2: Workflow/Layout Improvements

- Build the chart-context strip in or near `workspace-toolbar-shell`.
- Add a "Scalp" overlay preset and make it the recommended default for SPY scalping.
- Reorder Overview content so nearest levels and active alerts are above deeper dealer/IV/centroid blocks.
- Move the top active alert from the bottom lane into the chart context strip while keeping the full feed available.
- Add fast reprice buttons and quote-moved warnings to order entry.
- Collapse or demote the bracket planner by default.
- Add journal quick tags and entry/exit lifecycle grouping.
- Collapse or visually de-emphasize empty bottom-lane pulse space when there is no current Flow Pulse.

### Phase 3: Larger Redesign or Optional Cleanup

- Convert Strike Rail into "Strike Inspect" with a narrower contextual state.
- Clean up legacy chart renderer code after confirming no active path depends on it.
- Refactor settings into task-oriented groups.
- Add side-aware volume spike alerts if the interval schema is extended safely.
- Improve screenshot capture to include DOM/SVG overlays and chart labels.
- Consider a dedicated session review screen for journal analytics and screenshots.
- Revisit Plotly CDN pinning instead of `plotly-latest`, since the browser console warns that the current URL resolves to old Plotly v1.x. This is not part of the scalp workflow fix unless Plotly rendering becomes a blocker.

## 9. Concrete TODO Checklist with File/Function Anchors

- `ezoptionsschwab.py::renderChainActivity`
  - Add or remove OI row handling so JS targets match server HTML and `buildAlertsPanelHtml`.

- `ezoptionsschwab.py` HTML around `.right-rail-panel[data-rail-panel="scenarios"]`
  - Correct nesting and mirror the same structure in `ensurePriceChartDom`.

- `ezoptionsschwab.py::buildAlertsPanelHtml`
  - Keep all Overview card changes mirrored with server-rendered markup.

- `ezoptionsschwab.py::compute_trader_stats`
  - Replace `hash(a['text'])` alert IDs with deterministic IDs.
  - Demote continuous regime alerts or emit them only on regime change.

- `ezoptionsschwab.py::compute_flow_alerts`
  - Scope `_LAST_WALLS` by ticker, selected expiry set, strike range, and GEX scope.
  - Review cooldown keys for the same scope issue.
  - Add liquidity gates to IV surge alerts.

- `ezoptionsschwab.py::_fetch_vol_spike_data` and `store_interval_data`
  - Document current scope limitation.
  - Phase 3: consider storing side/scope-aware volume deltas.

- `ezoptionsschwab.py::renderRailKeyLevels`
  - Add secondary walls, HVL, max positive/negative GEX, and +/-2 sigma EM.
  - Add a clearer source/meta label for each level.

- `ezoptionsschwab.py::_compute_session_deltas` and `_compute_level_session_deltas`
  - Return baseline metadata: baseline time, baseline type, and label.
  - Update `renderMarketMetrics`, `renderNetExSparkline`, and `renderRailKeyLevels` labels.

- `ezoptionsschwab.py::updateData`
  - Stop clearing `tradeRailState.expiry` on every open-rail refresh.

- `ezoptionsschwab.py::renderTradeSelected`
  - Show quote age prominently.
  - Detect when quote changed after limit/preview.

- `ezoptionsschwab.py::renderTradeTicket`
  - Replace raw JSON with a compact preview/placement summary.
  - Put raw JSON behind a details block.

- `ezoptionsschwab.py::renderTradeActiveTrader`
  - Promote quote freshness and preview TTL into the fast action surface, not only the lower selected-contract card.
  - Include a clear stale/old snapshot cue near Buy Ask, Sell Bid, Flatten, and Preview.

- `ezoptionsschwab.py::requestTradeChain`
  - Preserve selected expiry when it remains available.
  - Validate that selected contract still exists before auto-switching.

- `ezoptionsschwab.py::renderFlowPulse`
  - Add or improve empty-state copy so users can tell whether the pulse is warming up, missing eligible flow, or unavailable.
  - Phase 2: collapse or de-emphasize empty pulse space when no actionable pulse exists.

- `ezoptionsschwab.py` drawer markup around `#exposure_metric`
  - Clarify the label/help text so it is clear this is exposure-weighting/input selection, not necessarily the same thing as the visible Strike Rail metric dropdown.

- `ezoptionsschwab.py::captureTradeChartScreenshotDataUrl`
  - Audit which overlays are excluded.
  - Phase 3: include DOM/SVG overlays or add explicit metadata snapshot to screenshots.

- `ezoptionsschwab.py::buildTVToolbar`
  - Add chart-context strip or reserve space for it.
  - Keep high-frequency controls visible and lower-frequency settings in menus.

- `ezoptionsschwab.py::CHART_VISIBILITY_DEFAULTS`
  - Add a Scalp preset rather than changing every existing default blindly.

- `docs/DASHBOARD_AUDIT_FINDINGS_AND_IMPLEMENTATION_PLAN.md`
  - Keep this document updated as implementation decisions change.

## 10. Testing/Verification Plan

Basic repo checks:

- Confirm branch and worktree before each implementation phase: `git branch -a`, `git log --oneline main..HEAD`, `git status --short`.
- Run the app with `python ezoptionsschwab.py`.
- Open `http://localhost:5001`.
- If validating against the user's already-running instance, open `http://localhost:5014` and note that this may carry saved UI state, scroll position, selected contract, and cached options state.

Dashboard smoke tests:

- Load SPY with 0DTE selected.
- Before judging chart/spot sync, explicitly focus the current session with `Today` or the current-session focus path. Verify the price chart axis, current price line, Market State spot, Strike Rail y-axis, and selected contract strikes are all in the same price region.
- Confirm candles render, SSE updates live price, and `/update` updates options rails without console errors.
- Switch 0DTE/1DTE/multiple expiries and confirm chart levels, right rail, alerts, and trade chain stay in scope.
- Toggle GEX scope all/0DTE and confirm Market State, Levels, chart lines, alerts, and Flow Pulse update consistently.
- Toggle chart overlays and confirm the Scalp preset reduces clutter.
- Expand/collapse Strike Rail and confirm it syncs y-axis with the price chart.
- Switch Overview, Levels, Scenarios, and Flow tabs and confirm each panel renders after the tab switch and after any DOM rebuild path.
- In Scenarios, confirm the `Current` row matches Overview Net GEX within rounding.
- In Flow, distinguish Flow Blotter population from Flow Pulse availability; a populated blotter with an empty pulse means the pulse empty state needs explanation, not necessarily that flow ingestion failed.
- Check Chain Activity in Overview and confirm every value written by `renderChainActivity` has matching visible markup.
- Verify Flow Pulse empty state: when no pulse is available, the panel should explain whether it is warming up, has no eligible recent flow, or lacks data.

Order-entry tests with live trading disabled:

- Load accounts if available.
- Select quick contracts and helper candidates.
- Change trade-rail expiry and confirm refresh does not reset it unexpectedly.
- Test the expiry persistence case with at least two available expiries; a single-expiry session cannot reproduce the reset bug visually.
- Preview BTO and STC tickets.
- Confirm changed qty/limit/contract invalidates preview.
- Confirm Place Live Order remains blocked unless `ENABLE_LIVE_TRADING=1`.
- Confirm quote-age and preview-age displays update in both the selected-contract card and the top Active Trader fast path.
- Confirm stale quote/old snapshot warnings appear near fast action controls, not only below the fold.

Alert tests:

- Restart the app and confirm deterministic alert IDs remain stable for equivalent alerts.
- Switch expiry scope and confirm no false wall-shift alert is emitted from the scope change alone.
- Confirm regime displays as market state instead of repeating as a fresh alert every tick.
- Confirm critical alert appears near the chart and full feed remains available.

Journal tests:

- Create manual journal entries.
- Preview an order and confirm journal event is recorded.
- In a safe/live-enabled test only, place a tiny test order if appropriate and confirm screenshot attachment works.
- Review whether screenshots include all needed chart context.
- Confirm lifecycle grouping and session summary remain usable with multiple entries/exits.

Visual tests:

- Test desktop wide, laptop width, and narrow browser widths.
- Confirm no text overlaps in chart context strip, right rail tabs, Active Trader, and Levels rows.
- Confirm chart remains visually dominant with both right rail and trade rail open.
- Confirm bottom-lane Live Alerts / Flow Pulse does not consume large visual space when it has no actionable content.

## 11. GitHub Workflow

- Work on a feature branch for each implementation pass, preferably using the `codex/` prefix.
- Make small commits by section or phase.
- Use clear commit messages such as:
  - `fix: align right rail activity markup`
  - `fix: preserve trade rail expiry on refresh`
  - `fix: stabilize flow alert ids`
  - `feat: add chart scalp context strip`
  - `feat: add scalp overlay preset`
- Do not mix unrelated changes.
- Do not change analytical formulas unless a specific bug is documented first.
- Keep `ezoptionsschwab.py` as the single-file app.
- Do not introduce a JS framework.
- Use existing CSS tokens and design patterns.
- Keep this audit document updated as implementation progresses.
- Before opening a PR, include screenshots or notes for chart, right rail, order entry, alerts, and journal verification.

## 12. Phase 1 Implementation Notes

2026-05-03 implementation pass:

Accomplished:

- Added the missing Chain Activity OI row to both server-rendered Overview markup and `buildAlertsPanelHtml`, preserving the existing JS renderer fields.
- Cleaned the server-rendered Scenarios panel nesting to match the rebuild path while preserving the existing seven-row Scenarios behavior.
- Replaced built-in Python `hash()` alert IDs with deterministic SHA-1-based IDs, and scoped alert cooldown/wall-shift state by ticker plus expiry/range/scope context.
- Preserved manual trade-rail expiry selection across normal refreshes when the selected expiry remains available.
- Added selected-contract quote age and five-minute preview TTL visibility in Active Trader and Order Ticket, with preview expiry blocking live placement until a fresh preview is created.
- Replaced raw preview/placement JSON with a compact summary and optional raw-details disclosure.
- Expanded the Levels rail to include secondary walls, HVL, max +/- GEX, and +/-2 sigma expected-move levels when present.
- Relabeled IV, Levels, and Net GEX/DEX drift baselines so first-sample captures do not read as open baselines.
- Clarified the Flow Pulse empty state so a populated Flow Blotter with no pulse reads as pulse-history eligibility, not missing flow ingestion.
- Renamed the drawer setting to `Exposure Weighting Metric` so it does not visually conflict with the Strike Rail metric selector.

Tricky parts / implementation notes:

- The Chain Activity OI fix had to be mirrored in two separate markup paths: initial server HTML and `buildAlertsPanelHtml`. Otherwise `renderChainActivity` would still update dropped DOM nodes after a tick rebuild.
- The Scenarios issue was markup debt, not a visible tab failure on 5014. The fix preserves behavior and reduces fragility by aligning server and rebuild nesting.
- Wall-shift state and alert cooldowns now use the same scope token style as trader stats: ticker plus strike range, expiry set, and scope label. This prevents a scope/expiry switch from looking like a real structural wall move.
- Quote age comes from the cached chain payload and can legitimately show stale weekend/after-hours snapshots. Preview TTL is a local five-minute guard from successful preview creation time, not a Schwab-side guarantee.
- Levels rail expansion uses the existing `compute_key_levels` payload only; no GEX/DEX/Vanna/Charm/Flow formulas were changed.
- The Net GEX/DEX drift labels now distinguish true open-era baselines from first app sample baselines. The existing `net_gex_vs_open`/`net_dex_vs_open` field names remain for compatibility.

5014 smoke notes:

- `python3 -m py_compile ezoptionsschwab.py` passed; the only output was the existing template-string invalid-escape warning.
- Port `5014` was restarted on the patched file and reloaded in the in-app browser. Fresh console logs after reload showed only the known Plotly CDN warning.
- The Settings drawer showed `Exposure Weighting Metric`; the old `Exposure Metric:` label was not present.
- Overview showed the new Chain Activity OI row along with VOL and V/OI targets. The Net GEX/DEX drift footer read `Δ first sample`, and IV showed `Since First Sample`.
- Flow showed a populated Flow Blotter (`58 shown`) while Flow Pulse showed the clarified pulse-history empty state.
- Active Trader and Order Ticket both showed `Quote Age` and `Preview TTL`; the selected contract quote age reflected stale weekend data.
- Trade expiry preservation could not be visually tested with multiple expiries because the restarted trade-chain payload only exposed `2026-05-04`.
- Chart/Today/Levels/Scenarios data-state verification was limited because the restarted after-hours `update_price` calls returned `No market-hour candles returned from Schwab API`, leaving the price chart without the current-session toolbar/data needed for a full spot-sync check.
- After this pass, the detached `5014` server was intentionally killed so the next Codex session can start it cleanly.

Still left / follow-up:

- Re-run the blocked market-hours verification on 5014: press/focus `Today`, then confirm chart, Strike Rail, Overview spot, and selected contract context align.
- Re-test Levels and Scenarios when `update_price` can return current-session candles; Scenarios should still render seven rows and `Current` should match Overview Net GEX within rounding.
- Re-test manual trade-rail expiry persistence in a session with at least two available expiries.
- Decide whether Phase 2 should begin with the chart-context strip, the Scalp overlay preset, Overview reordering, or top active alert promotion.
- Keep Plotly CDN pinning as a separate optional cleanup unless it becomes a rendering blocker.

## 13. Copy/Paste Prompt for Next Codex Session

```text
Read AGENTS.md first, then read:
- docs/UI_MODERNIZATION_PLAN.md
- docs/ANALYTICS_CHART_PHASE2_PLAN.md
- docs/ALERTS_RAIL_PHASE3_PLAN.md
- docs/DASHBOARD_AUDIT_FINDINGS_AND_IMPLEMENTATION_PLAN.md

Confirm branch/worktree with:
- git branch -a
- git log --oneline main..HEAD
- git status --short

Phase 1 is implemented. Do not reimplement it unless verification finds a regression.

Before editing:
1. Kill any existing process listening on 5014, then start the app cleanly on port 5014.
2. Open the in-app browser to http://127.0.0.1:5014/.
3. Press/focus `Today` before judging chart/spot sync.
4. Re-run the Phase 1 verification that was blocked after-hours:
   - chart, Strike Rail, Overview spot, and selected contract context align;
   - Overview Chain Activity shows OI, VOL, and V/OI rows;
   - Levels includes secondary walls, HVL, max +/- GEX, and +/-2σ EM when present;
   - Scenarios renders seven rows and `Current` matches Overview Net GEX within rounding;
   - Flow Blotter can be populated while Flow Pulse explains pulse-history gating;
   - trade rail expiry does not reset during normal refresh when multiple expiries are available;
   - quote age and preview TTL are visible in Active Trader and Order Ticket;
   - no new browser console errors except the known Plotly CDN warning.

Constraints:
- Do not change analytical formulas unless you find a clear bug and document it first.
- Do not introduce a JS framework.
- Keep the single-file ezoptionsschwab.py structure.
- Use existing CSS tokens and design patterns.
- Grep by anchors rather than trusting line numbers.
- Any new or changed Overview/right-rail markup must be mirrored between server-rendered HTML and buildAlertsPanelHtml/ensurePriceChartDom rebuild paths.
- Keep docs/DASHBOARD_AUDIT_FINDINGS_AND_IMPLEMENTATION_PLAN.md updated with implementation notes as work progresses.

Goal:
After the Phase 1 verification pass, implement Phase 2 from docs/DASHBOARD_AUDIT_FINDINGS_AND_IMPLEMENTATION_PLAN.md only if the user confirms continuing into Phase 2.

Recommended Phase 2 order:
1. Build the chart-context strip in or near `workspace-toolbar-shell`.
2. Add a `Scalp` overlay preset and make it the recommended default for SPY scalping.
3. Reorder Overview so nearest levels and the top active alert sit above deeper dealer/IV/centroid blocks.
4. Promote the top active alert from the bottom lane into the chart context strip while keeping the full feed available.
5. Add fast reprice buttons and quote-moved warnings to order entry.
6. Collapse or demote the bracket planner by default.
7. Add journal quick tags and entry/exit lifecycle grouping.
8. Collapse or visually de-emphasize empty bottom-lane pulse space when no actionable pulse exists.

After changes, smoke test SPY current-session chart, right rail, alerts, trade rail preview flow, settings drawer, and journal visibility on port 5014. Make small focused commits by section.
```
