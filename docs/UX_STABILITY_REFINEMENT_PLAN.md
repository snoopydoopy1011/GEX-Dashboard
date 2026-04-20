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

**Current state (as of 2026-04-20):** Stages 1-5 are landed on the active worktree branch `codex-ux-stability-refinement-plan`. Stage 1 manual smoke review passed from user-provided screenshots across `1 min`, `5 min`, `15 min`, `30 min`, and `1 hour` with the timer staying on the first toolbar row and no obvious timeframe bucketing regressions visible in the captured bars. Stage 2 restores top-OI lines reliably and adds a historical-dots drawer toggle. Stage 3 refreshes the right rail into a clearer decision ladder with stronger alert hierarchy and level-context rows. Stage 4 promotes strike-aligned analytics into the center strike rail. Stage 5 replaces the misleading `Large Trades` table with an actionable flow blotter and leaves Stage 6 as the next implementation target.

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
   - Strike-aligned tabs like Gamma / Delta / Vanna / Charm / OI are buried in the bottom analytics area even though they belong next to price.
   - GEX has the only strike-aligned rail, so users must mentally switch between the center chart and a disconnected lower tab strip to compare per-strike structure.
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
- Promote strike-aligned analytics into a shared center strike rail and modernize the remaining lower utility area.
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

### Strike rail + lower utility area

- `updateSecondaryTabs`
- `renderGexSidePanel`
- `syncGexPanelYAxisToTV`
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

- A tabbed strike rail beside the price chart with `GEX` as the default view
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
  - top N call strikes
  - top N put strikes
  - gold overlap strikes
- Add a visibility toggle for historical wall/level dots under the existing chart overlay controls.
- Keep the existing Call Wall / Put Wall / Gamma Flip / HVL / EM line behavior unchanged.
- Ensure overlay toggles redraw from cache without a full expensive refetch where possible.
- Add a drawer setting for `Top OI lines / side` with a bounded count so the user can control how many ranked OI strikes render per side.
- Keep OI lines out of the manual price-scale autoscale path so they do not force the chart to zoom back out when `Auto-Range` is off.

**Acceptance criteria:**

- Toggling `OI` always produces the expected horizontal lines once data exists.
- Overlap strikes render gold.
- Historical dots can be toggled on/off without affecting the underlying level lines.
- Ticker and timeframe changes preserve correct overlay behavior.

**Commit:**

`fix(overlays): restore top-oi lines and add historical-dot toggle`

**Progress note (2026-04-20):**

- Landed in `ezoptionsschwab.py`.
- `compute_top_oi_strikes` now tolerates either `expiration_date` or `expiration` chain schemas, which fixed the empty top-OI payload that made the toolbar `OI` toggle appear broken.
- `/update` and `/update_price` both accept a bounded `top_oi_count` and return the current ranked OI payload from cache, so OI lines survive normal chart refreshes instead of depending on the slower full update cycle.
- The settings drawer now exposes `Top OI lines / side` with a `1-10` clamp and save/load support.
- Ranked call/put OI labels now show their ordinal in the axis tag (for example `C OI #1`), while overlap strikes remain gold and unlabeled by rank.
- Historical bubbles now have their own `Historical dots` overlay toggle in the drawer and redraw from cached chart data without a refetch.
- OI lines no longer participate in the manual price autoscale range, so with `Auto-Range OFF` the user can zoom into candles without the chart snapping back out to fit distant OI strikes.

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

**Progress note (2026-04-20):**

- Landed in `ezoptionsschwab.py`.
- The `Alerts`, `Levels`, and `Scenarios` tabs are unchanged structurally, but the alert stack now emphasizes the top signal and demotes stale or lower-priority alerts.
- The `Levels` tab now renders nearest-first cards with price, distance-from-spot, and since-open drift in one scan.
- Dealer impact shifted from a heavier grid into compact labeled rows so the block reads as supporting context instead of competing with the primary cards.
- KPI, range, gamma-profile, and chain-activity cards now share tighter spacing, stronger numeric alignment, and more consistent typography.

---

### Stage 4 — Promote strike-aligned analytics into a center strike rail

**Why:** The dashboard should not force traders to look down to compare strike structure. GEX already proves that a strike-aligned side rail works; the next step is to turn that single-purpose panel into a shared strike rail and leave only non-strike utilities in the lower area.

**Files / anchors:**

- `updateSecondaryTabs`
- `renderGexSidePanel`
- `syncGexPanelYAxisToTV`
- `create_exposure_chart`
- `create_open_interest_chart`
- `create_options_volume_chart`
- `create_premium_chart`
- Plotly theme/layout blocks reused by those builders

**Changes:**

- Replace the dedicated `GEX` panel with a generic strike rail that sits between the price chart and the right rail.
- Add strike-rail tabs for the strike-aligned surfaces only:
  - `GEX` (default)
  - `Gamma`, `Delta`, `Vanna`, `Charm`
  - `OI`, `Options Vol`, `Premium`
- Keep non-strike surfaces in the lower utility area:
  - `Volume`, `Centroid`, `Chain`, and the future flow blotter / `Large Trades` replacement
- Ensure only the active strike-rail tab renders and runs Y-axis sync against the price chart.
- Normalize chart layout, margins, titles, and number placement across the strike-rail tabs so they feel like one system.
- Add a responsive collapse rule so the strike rail drops below the price chart on narrower widths instead of crushing the candle pane.

**Acceptance criteria:**

- A trader can switch between `GEX`, `Gamma`, `Delta`, `Vanna`, `Charm`, `OI`, `Options Vol`, and `Premium` without moving their eyes away from the price/gamma area.
- The price chart and active strike-rail tab stay visually aligned by strike.
- Non-strike tabs remain available below without being forced into a fake strike-aligned format.
- The main chart remains usable on laptop widths because the strike rail collapses instead of over-squeezing the center layout.

**Commit:**

`feat(layout): promote strike-aligned tabs into center strike rail`

**Progress note (2026-04-20):**

- Landed in `ezoptionsschwab.py`.
- The former GEX-only middle panel is now a shared strike rail with `GEX`, `Gamma`, `Delta`, `Vanna`, `Charm`, `Options Vol`, `OI`, and `Premium` tabs.
- Strike-aligned surfaces render in the middle rail while the lower utility area is reduced to non-strike views such as `Volume`, `Large Trades`, and `Centroid`.
- The strike rail reuses the price-chart sync path so the active strike surface stays aligned to the visible candle price range.
- The rail now mounts and refreshes through a stable Plotly lifecycle, which fixed the blank/disappearing panel regression during live updates.
- On narrower widths, the strike rail drops below the price chart instead of compressing the candle pane and right rail into an unreadable layout.

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

**Progress note (2026-04-20):**

- Landed in `ezoptionsschwab.py`.
- The former strike-sorted options-chain table is now a `Flow Blotter` tab with aligned columns for `Time`, `Type`, `Strike`, `Expiry`, `Last`, `Bid / Mid / Ask`, `Vol`, `OI`, `V/OI`, `Premium`, and inferred side.
- When Schwab chain snapshots expose `tradeTimeInLong` or `quoteTimeInLong`, the blotter defaults to recency-first sorting; otherwise it falls back to premium-first ranking and labels that limitation clearly in the UI.
- The blotter adds one-click `All` / `Calls` / `Puts` filters plus a minimum premium threshold so unusual activity can be narrowed quickly.
- User-selected sort direction, active sort key, contract-side filter, and minimum premium threshold now persist across normal live refreshes instead of snapping back to the default order.
- Empty-filter and no-data states are explicitly called out so the tab no longer looks broken when a filter removes all rows.

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
4. Stage 4 — promote strike-aligned analytics into center strike rail
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
- strike-aligned tabs live beside price and feel consistent
- `Large Trades` is finally useful

---

## 8. Success definition

This phase is successful if the dashboard becomes easier to trust and easier to act on without adding more analytical clutter.

Specifically:

- live chart behavior feels stable
- key overlays always show when requested
- the right rail answers “what matters right now?” quickly
- strike-aligned analytics sit next to price while the remaining lower tabs stay useful and coherent
- the flow/trade surface becomes genuinely decision-useful

If there is tension between “more information” and “faster comprehension,” choose faster comprehension.
