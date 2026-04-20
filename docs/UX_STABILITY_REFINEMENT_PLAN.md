# GEX Dashboard — UX Stability + Decision-Speed Refinement Plan

**Status:** In Progress
**Owner:** Codex
**Created:** 2026-04-20
**Target branch:** `feat/ux-stability-refinement`
**Base:** `main`
**Prior initiatives (complete):**
- [`UI_MODERNIZATION_PLAN.md`](UI_MODERNIZATION_PLAN.md)
- [`ANALYTICS_CHART_PHASE2_PLAN.md`](ANALYTICS_CHART_PHASE2_PLAN.md)
- [`ALERTS_RAIL_PHASE3_PLAN.md`](ALERTS_RAIL_PHASE3_PLAN.md)

Read the three prior docs first. This plan assumes their layout, tokens, and no-formula-change rules remain in force.

**Current state (as of 2026-04-20):** Stage 1 is landed on the active worktree branch `codex-ux-stability-refinement-plan`. Manual smoke review passed from user-provided screenshots across `1 min`, `5 min`, `15 min`, `30 min`, and `1 hour` with the timer staying on the first toolbar row and no obvious timeframe bucketing regressions visible in the captured bars.

---

## 0. Why this phase exists

The dashboard now has strong analytics coverage, but three things still block fast decision-making:

1. **Live chart behavior is not trustworthy enough.**
   - The candle timer wraps onto a second line in the chart toolbar.
   - Live candles can flash oversized/inaccurate bars before snapping back.
   - Intrabar volume appears stale until candle close.
   - The OI overlay button is wired, but top-OI lines are not reliably appearing.

2. **The right rail is analytically rich but visually dense.**
   - The Alerts / Levels / Scenarios system works, but the cards still read as stacked data dumps rather than a decision ladder.
   - Important context like distance from spot and drift since open is missing, so the user must mentally compute what matters.

3. **The lower analytics tabs are functional but clunky.**
   - Gamma / Delta / Vanna / Charm / OI / Volume all render as separate Plotly surfaces with inconsistent visual hierarchy.
   - The current `Large Trades` tab is not actually a large-trades blotter; it is an options chain table, which is the wrong tool for fast reading.

**Goal:** improve trust, readability, and time-to-decision without changing the underlying GEX/DEX/Vanna/Charm/Flow formulas.

This phase is explicitly a **stability + presentation** phase. It does **not** add new Greeks, new endpoints, or a new framework.

---

## 1. Ground rules

- **No analytical-formula changes.** Existing Greek, expected-move, wall, and flow math stay intact.
- **No JS framework introduction.** Vanilla JS + CSS tokens only.
- **No breaking the single-file app.** `ezoptionsschwab.py` remains the only app file.
- **Tokens only for colors.** Reuse the existing `:root` palette.
- **Do not add more permanent stats until scanability improves.**
- **If a metric is not directly actionable in under 2 seconds, demote it behind a tab, toggle, or compact detail row.**

---

## 2. Scope

### In scope

- Fix live chart rendering defects.
- Fix OI overlay reliability.
- Add a toggle for historical key-level dots/bubbles on the price chart.
- Tighten the price-chart toolbar so the timer stays on the first line.
- Refresh the right rail for better scanning.
- Modernize the lower analytics panel layout and chart presentation.
- Replace the misleading `Large Trades` view with a true flow/trade blotter.

### Out of scope

- Rewriting Greek calculations.
- New data vendors or streaming backends.
- Full multi-page app navigation.
- Splitting `ezoptionsschwab.py`.

---

## 3. Reuse inventory

Use these anchors instead of trusting line numbers:

### Live chart + overlays

- `connectPriceStream`
- `applyRealtimeQuote`
- `applyRealtimeCandle`
- `renderTVPriceChart`
- `applyPriceData`
- `renderTopOI`
- `ensureTopOILoaded`
- `renderKeyLevels`
- `build_historical_levels_overlay`
- `.tv-toolbar-container`
- `.candle-close-timer`

### Right rail

- `buildAlertsPanelHtml`
- `applyRightRailTab`
- `renderTraderStats`
- `renderRailAlerts`
- `renderDealerImpact`
- `renderMarketMetrics`
- `renderRangeScale`
- `renderGammaProfile`
- `renderChainActivity`
- `renderRailKeyLevels`
- `.right-rail-tabs`
- `.right-rail-panels`
- `.rail-card`
- `.dealer-impact`

### Lower analytics area

- `updateSecondaryTabs`
- `create_exposure_chart`
- `create_open_interest_chart`
- `create_options_volume_chart`
- `create_premium_chart`
- `create_large_trades_table`

### Drawer / visibility

- `renderChartVisibilitySection`
- `CHART_IDS`
- `LINE_OVERLAY_IDS`
- `getChartVisibility`
- `setAllChartVisibility`

---

## 4. Product direction

This phase should make the dashboard read more like a trading workstation and less like a debug surface.

### What stays always visible

- Live price and change
- Gamma regime
- Nearest Call Wall
- Nearest Put Wall
- Gamma Flip
- Highest-signal live alert
- A compact activity bias read

### What gets visually demoted

- Full dealer-impact grid
- Full scenario table
- Secondary chain ratios
- Exhaustive per-strike tables

### What gets added

- Distance-from-spot context on the Levels surface
- Drift-since-open context for major levels
- A toggle for historical level dots/bubbles
- A proper flow / large-trades blotter

---

## 5. Workstreams

### Stage 1 — Live chart trust fixes

**Why:** The chart is the primary decision surface. If live bars flash wrong, the rest of the dashboard is undermined.

**Files / anchors:**

- `ezoptionsschwab.py`
- `connectPriceStream`
- `applyRealtimeQuote`
- `applyRealtimeCandle`
- `renderTVPriceChart`
- `.tv-toolbar-container`
- `.candle-close-timer`

**Changes:**

- Prevent the chart toolbar from wrapping in normal desktop width.
- Reserve a dedicated non-wrapping slot for the candle-close timer.
- Refactor the quote/candle merge path so quote ticks cannot temporarily create visibly incorrect high/low ranges.
- Ensure the realtime bucket-opening logic matches the historical bucketing logic exactly for all supported timeframes.
- Make intrabar volume behavior explicit:
  - If Schwab streaming fields can support live volume, wire them through.
  - If not, stop implying that intrabar volume is final before candle close.

**Acceptance criteria:**

- Candle timer stays on the first toolbar row at standard desktop widths.
- No oversized candle flash when live quotes hit.
- 1m / 5m / 15m / 30m / 60m all roll buckets cleanly.
- Volume bars no longer appear misleadingly stale or jump inconsistently.

**Commit:**

`fix(chart): stabilize realtime candles and compact toolbar timer`

**Progress note (2026-04-20):**

- Landed in `ezoptionsschwab.py`.
- Manual smoke evidence came from captured `1 min`, `5 min`, `15 min`, `30 min`, and `1 hour` views.
- The toolbar now keeps the timer in a dedicated right-side slot.
- Intrabar volume is explicitly labeled as `Vol: 1m confirmed` to avoid implying quote-level volume finality.

---

### Stage 2 — OI overlay + chart overlay controls

**Why:** The OI button is a core trading aid and currently breaks user trust when it appears to do nothing.

**Files / anchors:**

- `compute_top_oi_strikes`
- `/update`
- `/update_price`
- `renderTopOI`
- `ensureTopOILoaded`
- `renderChartVisibilitySection`
- `LINE_OVERLAY_IDS`
- `build_historical_levels_overlay`

**Changes:**

- Fix the top-OI overlay data lifecycle so the OI button reliably draws:
  - top 5 call strikes
  - top 5 put strikes
  - gold overlap strikes
- Add a visibility toggle for historical wall/level dots under the existing chart overlay controls.
- Keep the existing Call Wall / Put Wall / Gamma Flip / HVL / EM line behavior unchanged.
- Ensure overlay toggles redraw from cache without a full expensive refetch where possible.

**Acceptance criteria:**

- Toggling `OI` always produces the expected horizontal lines once data exists.
- Overlap strikes render gold.
- Historical dots can be toggled on/off without affecting the underlying level lines.
- Ticker and timeframe changes preserve correct overlay behavior.

**Commit:**

`fix(overlays): restore top-oi lines and add historical-dot toggle`

---

### Stage 3 — Right rail readability refresh

**Why:** The right rail contains useful data, but the reading order is weak and too much of it is raw-value-first.

**Files / anchors:**

- `buildAlertsPanelHtml`
- `renderMarketMetrics`
- `renderRangeScale`
- `renderGammaProfile`
- `renderDealerImpact`
- `renderChainActivity`
- `renderRailKeyLevels`
- `renderRailAlerts`
- `.rail-card`
- `.dealer-impact`
- `.right-rail-panel`

**Changes:**

- Keep the existing three tabs: `Alerts`, `Levels`, `Scenarios`.
- Redesign card internals for faster scanning, not more content.
- Make `Levels` a first-class decision surface:
  - Level
  - Price
  - Distance from spot
  - Since-open drift where available
- Tighten `Alerts` so the top alert is visually dominant and stale/low-priority alerts recede.
- Reduce visual weight of the dealer-impact block by turning it into compact labeled rows instead of a large grid.
- Improve spacing, numeric alignment, and typography for all right-rail cards.

**Acceptance criteria:**

- A trader can identify regime, nearest level, and active alert in under 2 seconds.
- Levels tab reads clearly without mental subtraction.
- Alerts tab no longer feels like a wall of equally-weighted cards.
- No new formulas are introduced.

**Commit:**

`style(rail): improve right-rail scanning and level context`

---

### Stage 4 — Lower analytics panel modernization

**Why:** The bottom area works, but the current tab surfaces feel disconnected and heavy.

**Files / anchors:**

- `updateSecondaryTabs`
- `create_exposure_chart`
- `create_open_interest_chart`
- `create_options_volume_chart`
- `create_premium_chart`
- Plotly theme/layout blocks reused by those builders

**Changes:**

- Normalize chart layout, margins, titles, and number placement across all lower tabs.
- Give each lower chart a clearer “headline + supporting context” structure.
- Improve chart-title consistency so the active metric is obvious immediately.
- Reduce wasted dark space and rebalance label density.
- Consider consolidating repeated chart controls into a common visual pattern.
- Do **not** add more tabs in this stage.

**Acceptance criteria:**

- Gamma / Delta / Vanna / Charm / OI / Volume / Premium feel like one system.
- Titles, net readouts, and strike context are easier to parse.
- The user does not need to relearn each tab’s layout.

**Commit:**

`style(charts): modernize lower analytics panel surfaces`

---

### Stage 5 — Replace `Large Trades` with a true flow blotter

**Why:** The current `Large Trades` tab is an options chain table, not a large-trades tool.

**Files / anchors:**

- `create_large_trades_table`
- secondary-tab label map for `large_trades`
- relevant options data already returned from Schwab chain fetches

**Changes:**

- Replace the current table with a trade/flow blotter focused on actionability.
- Default sort should favor recency and/or premium magnitude, not strike.
- Candidate columns:
  - Time
  - Type
  - Strike
  - Expiry
  - Last / bid-mid-ask context
  - Volume
  - OI
  - V/OI
  - Premium
  - Side classification (`bid`, `mid`, `ask`, `unknown`) if derivable
- Add lightweight filters for calls / puts / all and maybe a minimum premium threshold.
- If precise trade classification is not possible from the current data, label the limits clearly and still prioritize the highest-signal fields.

**Acceptance criteria:**

- The tab surfaces unusual activity faster than the current options-chain table.
- Sorting and filtering are useful with one or two clicks.
- The tab title and content finally match.

**Commit:**

`feat(flow): replace large-trades table with actionable blotter`

---

### Stage 6 — Regression sweep and polish

**Why:** This phase touches the chart, overlays, rail, and lower tabs. It needs a focused cleanup pass.

**Files / anchors:**

- all touched surfaces in `ezoptionsschwab.py`

**Changes:**

- Run a full regression sweep across ticker changes, timeframe changes, streaming pause/resume, and rail tab persistence.
- Clean up spacing, copy, stale comments, and any mismatched labels.
- Confirm the rebuild path mirrors the server-rendered HTML for touched rail surfaces.

**Acceptance criteria:**

- No console errors during normal use.
- No regressions in drawer behavior, rail tabs, GEX panel sync, or secondary tabs.
- Streaming remains stable across pause/resume and ticker switches.

**Commit:**

`chore(ui): regression sweep for ux stability refinement`

---

## 6. Recommended implementation order

Do the stages in this exact order:

1. Stage 1 — live chart trust fixes
2. Stage 2 — OI overlay + dot toggle
3. Stage 3 — right rail readability refresh
4. Stage 4 — lower analytics modernization
5. Stage 5 — real flow blotter
6. Stage 6 — regression sweep

Reason:

- The user called out chart bugs first.
- Layout work should not hide unresolved live-data defects.
- The flow blotter depends on getting the rest of the information hierarchy under control first.

---

## 7. Manual test matrix

- SPY, QQQ, TSLA, and one illiquid single-name
- 1 / 5 / 15 / 30 / 60 min
- Single expiry and multiple expiries
- Stream on, then pause, then resume
- Toggle OI on/off before and after `/update` data arrives
- Toggle historical dots on/off repeatedly
- Switch right-rail tabs mid-stream
- Cycle all lower tabs after the modernization pass

Golden-path checks:

- price chart feels stable
- OI lines show reliably
- timer stays in place
- right rail is readable
- lower tabs feel consistent
- `Large Trades` is finally useful

---

## 8. Success definition

This phase is successful if the dashboard becomes easier to trust and easier to act on without adding more analytical clutter.

Specifically:

- live chart behavior feels stable
- key overlays always show when requested
- the right rail answers “what matters right now?” quickly
- the lower tabs feel modern and coherent
- the flow/trade surface becomes genuinely decision-useful

If there is tension between “more information” and “faster comprehension,” choose faster comprehension.
