# Right Rail Overview Reorganization Prototype Plan

**Status:** Prototype pass implemented on `codex/right-rail-overview-prototype`; review/cleanup pending  
**Created:** 2026-04-28  
**Last updated:** 2026-04-29  
**Target branch:** `codex/right-rail-overview-prototype`  
**Prototype port:** `5002`  
**Baseline port:** `5001`

This document is meant to be sufficient for a fresh implementation session with no context from the design conversation. It covers the proposed reorganization of the right overview rail, the contract-helper toolbar move, and a prototype workflow that keeps the current dashboard available on port 5001 while the new layout runs on port 5002.

## 0. Current Prototype Handoff

Implemented in `ezoptionsschwab.py` on branch `codex/right-rail-overview-prototype`:

- Added the fourth right-rail tab: `Overview | Levels | Scenarios | Flow`.
- Added `[data-rail-panel="flow"]` and tab persistence support for `flow`.
- Added `.chart-grid.rail-flow-active` so selecting Flow widens the right rail with `--rail-col-w: clamp(420px, 34vw, 520px)`.
- Added a compact Flow rail surface under `#right-rail-flow`.
- Duplicated the existing bottom Flow Blotter into the Flow rail as compact row cards derived from the existing `large_trades` HTML payload.
- Preserved the existing bottom Flow Blotter and the horizontal Live Alerts / Flow Pulse strips for comparison.
- Moved the contract-helper surface into the TradingView toolbar as `.tv-toolbar-helper`, placed after the strike overlay controls and before the right-side Auto-Range / Today / Reset controls.
- Added a hover/focus helper popover with the existing call candidate, put candidate, size guide, note, and expiry hooks.
- Removed the runtime Overview contract-helper card from the visible Overview layout.
- Consolidated Overview into four cards:
  - `#rail-card-market-state`
  - `#rail-card-hedge-read`
  - `#rail-card-iv`
  - `#rail-card-centroid`
- Reused the existing renderer hooks (`#rail-card-price`, `#rail-card-metrics`, `#rail-card-range`, `#rail-card-profile`, `#rail-card-activity`, `#dealer-impact`, and `data-met` hooks) inside the new composite cards to avoid recalculating or rewriting analytics.

Validation run on 2026-04-29:

- `python3 -m py_compile ezoptionsschwab.py` passes.
- Extracted page JavaScript parses with `new Function(...)`.
- Local Flask smoke served the updated page on `http://127.0.0.1:5002/`.
- Verified the served HTML contains the Flow tab, compact Overview card IDs, toolbar helper CSS/JS, and Flow rail renderer code.

Known prototype notes / tricky parts:

- No analytical formulas, Schwab data sources, or endpoints were changed.
- The rail Flow Blotter currently derives its compact rows by parsing the existing `large_trades` HTML table in the browser. This kept the backend contract untouched, but a later cleanup should consider returning a structured flow-blotter payload if the rail version becomes permanent.
- The Flow rail filters support All / Calls / Puts and minimum premium. Sorting is intentionally reduced for this prototype; rows inherit the existing backend/default ranking.
- The toolbar helper uses the existing `data-met="contract_*"` hooks in both the compact toolbar and popover, so `renderContractHelper(stats)` updates all helper surfaces at once.
- Runtime rebuild safety is handled by overriding `buildAlertsPanelHtml` after the original function body. The app serves and rebuilds the new Overview markup correctly, but a cleanup pass should replace the old body outright so stale `#rail-card-contract-helper` source text is removed.
- Baseline on port 5001 was not run from this same worktree after edits because it would serve the modified branch. For true side-by-side review, use a separate checkout or stash/worktree for `main` on port 5001 and this branch on port 5002.

Remaining work for the next session:

- Browser-review the prototype visually at desktop widths and check console output.
- Verify Flow tab width toggles cleanly when switching back to Overview / Levels / Scenarios.
- Verify rail Flow rows with live `large_trades` data and confirm the compact row fields are the right set.
- Check toolbar crowding at common desktop widths; adjust the collapse breakpoints if Auto-Range / Today / Reset or timeframe controls are squeezed.
- Decide whether the Flow rail should replace or continue duplicating the bottom Flow Blotter.
- Remove the stale original `buildAlertsPanelHtml` body and keep only the new canonical markup builder.
- Consider adding an expandable/details affordance for condensed Vol / Skew and Dealer / Hedge rows if review shows too much detail was hidden.
- Optionally add Playwright/browser screenshots for Overview, Flow, and the toolbar helper popover after live data is available.

## 1. Read This First

Before implementing, read:

- `AGENTS.md`
- `docs/UI_MODERNIZATION_PLAN.md`
- `docs/ANALYTICS_CHART_PHASE2_PLAN.md`
- `docs/ALERTS_RAIL_PHASE3_PLAN.md`

The current app is a single-file Flask + Plotly + TradingView-Lightweight-Charts dashboard in `ezoptionsschwab.py`. Keep that structure. Do not introduce a JS framework, build step, or new analytical formula.

Start by confirming state:

```bash
git branch -a
git log --oneline main..HEAD
```

Create a prototype branch:

```bash
git checkout -b codex/right-rail-overview-prototype
```

Run the current baseline on port 5001 and the prototype on port 5002:

```bash
PORT=5001 FLASK_DEBUG=0 FLASK_USE_RELOADER=0 python3 ezoptionsschwab.py
PORT=5002 FLASK_DEBUG=0 FLASK_USE_RELOADER=0 python3 ezoptionsschwab.py
```

Use `http://127.0.0.1:5001` as the reference UI and `http://127.0.0.1:5002` as the prototype UI.

## 2. Problem

The right rail Overview tab currently contains too many stacked cards:

1. Price
2. Net GEX / Net DEX
3. Contract Helper
4. Expected Move
5. Gamma Profile
6. Dealer Impact
7. Chain Activity
8. Skew / IV
9. Centroid Drift

Each card is useful, but the Overview tab now behaves like a full analytics report. It requires roughly two full scrolls, which makes the rail hard to scan during live trading.

The flow blotter is also too low in the layout. It appears below the chart/flow strips and secondary tab area, so it is harder to use as a live decision surface. However, the horizontal Live Alerts and Flow Pulse strips should stay horizontal because they are space-efficient and read better across the full chart width.

There is also unused horizontal space in the price-chart toolbar between the strike overlay controls and the Auto-Range / Today / Reset controls. The contract-helper logic may fit better there as a compact action readout instead of occupying a full Overview rail card.

## 3. Goals

- Make Overview a true decision summary, not a long report.
- Keep the first Overview viewport mostly scroll-free.
- Move the contract-helper readout out of the right rail and into the chart toolbar if it fits cleanly.
- Add a new right-rail `Flow` tab after `Scenarios`.
- Move the flow blotter into the new `Flow` tab.
- Keep Live Alerts and Flow Pulse in their current horizontal strip layout.
- Allow the right rail to widen for Flow if needed, while keeping Overview compact.
- Keep port 5001 available as the current baseline during prototype review.

## 4. Non-Goals

- Do not change GEX, DEX, Vanna, Charm, expected-move, key-level, flow-alert, or contract-selection formulas.
- Do not change Schwab data sources or add new endpoints unless a narrow internal payload refactor is unavoidable.
- Do not remove the horizontal Live Alerts strip.
- Do not remove the horizontal Flow Pulse strip.
- Do not introduce React, Vue, a bundler, or external frontend dependencies.
- Do not break the single-file `ezoptionsschwab.py` structure.
- Do not use new raw color literals for UI colors; use existing tokens such as `--bg-*`, `--fg-*`, `--call`, `--put`, `--warn`, `--info`, `--accent`, and `--border`.

## 5. Current Anchors

Use anchors, not line numbers. Relevant current anchors in `ezoptionsschwab.py`:

- Right rail tabs: `.right-rail-tabs`, `.right-rail-tab`, `[data-rail-tab]`
- Right rail panels: `.right-rail-panels`, `.right-rail-panel`, `[data-rail-panel]`
- Overview rebuild source: `buildAlertsPanelHtml`
- DOM rebuild path: `ensurePriceChartDom`
- Rail tab behavior: `wireRightRailTabs`, `applyRightRailTab`
- Overview cards:
  - `#rail-card-price`
  - `#rail-card-metrics`
  - `#rail-card-contract-helper`
  - `#rail-card-range`
  - `#rail-card-profile`
  - `#rail-card-dealer`
  - `#rail-card-activity`
  - `#rail-card-iv`
  - `#rail-card-centroid`
- Contract-helper hooks: `.contract-helper-*`, `[data-met="contract_call"]`, `[data-met="contract_put"]`, `[data-met="contract_size"]`
- Flow strip: `#flow-event-lane`, `#flow-event-strip-alerts`, `#flow-event-strip-pulse`
- Flow blotter: `create_flow_blotter`, `.flow-blotter`, `.flow-blotter__*`
- Toolbar shell: `#workspace-toolbar-shell`, `#tv-toolbar-container`, `createTVToolbar`
- Strike overlay toolbar controls: `.strike-overlay-toggle`, `.strike-overlay-select`, `[data-group="strike-overlay"]`
- Toolbar right controls: `.tv-toolbar-right`, Auto-Range button text `Auto-Range ON/OFF`
- Grid width variables: `.chart-grid`, `--gex-col-w`, `--rail-col-w`

Important: any persistent Overview markup change must be represented in both the initial server-rendered HTML and `buildAlertsPanelHtml()`, or tick rebuilds can drop the new elements.

## 6. Target Information Architecture

Right rail tabs should become:

```text
Overview | Levels | Scenarios | Flow
```

### Overview

Overview should contain four compact cards:

1. **Market State**
   - Current price
   - Price change
   - Expiry/date chip
   - Net GEX
   - Net DEX
   - Gamma regime/headline
   - Expected move range summary
   - Live ATM straddle mini-read if space allows

2. **Dealer / Hedge Read**
   - Combined dealer read headline
   - Conviction chip
   - Compact hedge rows:
     - Spot +1%
     - Spot -1%
     - Vol +1 pt
     - Charm by close
   - Compact chain bias meter using existing Chain Activity values
   - Do not show all current explanatory subcopy by default.

3. **Vol / Skew**
   - ATM IV
   - IV headline
   - HV20 vs ATM IV line
   - Put-call skew spread
   - Since-open skew change
   - Vol pressure mini-track

4. **Positioning Drift**
   - Centroid sparkline
   - Call centroid
   - Put centroid
   - Spread
   - One concise structure read

The contract-helper card should be removed from Overview after the toolbar version is working.

### Levels

Keep existing Levels tab behavior.

### Scenarios

Keep existing Scenarios tab behavior.

### Flow

The Flow tab should primarily contain the flow blotter.

Do not move these into the Flow tab for the first prototype:

- Live Alerts horizontal strip
- Flow Pulse horizontal strip

Those should remain inside `#flow-event-lane` across the full chart width. They are already horizontally efficient and would become too tall if rendered vertically in the rail.

The Flow tab may include a small header summary above the blotter, but avoid duplicating the full Live Alerts or Flow Pulse UI.

## 7. Contract Helper Toolbar Concept

Move the contract-helper logic from the Overview rail to the price-chart toolbar gap between the strike overlay controls and Auto-Range controls.

### Desktop Target

Use a compact single-row readout:

```text
Helper  713C  712P  Size 3/10
```

Visual treatment:

- One compact toolbar group, not a large card.
- `713C` uses call color treatment.
- `712P` uses put color treatment.
- Size score uses warn/put/call tone based on existing score semantics.
- Keep text short enough for the toolbar.
- On hover or click, show a small popover with the existing full helper details:
  - Call candidate meta
  - Put candidate meta
  - Size note
  - Expiry text

### Responsive Behavior

If toolbar width is limited:

1. Collapse to:

   ```text
   Helper 713C / 712P
   ```

2. If still too narrow, show:

   ```text
   Helper
   ```

   with the full details available in the popover.

3. On mobile or narrow tablet widths, keep the helper out of the toolbar and either:
   - hide it behind the popover trigger, or
   - leave it in Overview only for that breakpoint.

The prototype should prefer a stable desktop layout first, since the screenshots and primary workflow are desktop.

### Data and Rendering

Reuse the existing contract helper payload. Do not recalculate anything.

Recommended JS approach:

- Extract current contract-helper rendering into a focused function if needed, for example:

  ```text
  renderContractHelper(stats)
  ```

- That function should update:
  - toolbar helper compact hooks
  - optional popover hooks
  - any remaining fallback rail helper hooks during the prototype

After the toolbar helper is verified, remove `#rail-card-contract-helper` from Overview.

## 8. Overview Card Consolidation Details

### Market State Card

Replace `#rail-card-price`, `#rail-card-metrics`, `#rail-card-range`, and `#rail-card-profile` with a single card.

Suggested structure:

```text
$712.45        -0.38%  2026-04-28
Negative Gamma
Net GEX -$1.63B       Net DEX -$1.89B
EM $708.43 to $715.21  |  Live ATM $0.34
```

Keep one miniature expected-move bar if it remains readable. If space gets tight, prefer text clarity over the bar.

### Dealer / Hedge Read Card

Replace the full current Dealer Impact card plus Chain Activity card with one compact card.

Suggested structure:

```text
Dealer / Hedge Read       High Edge
Bullish momentum risk
Spot +1%    +$16.34M
Spot -1%    -$16.34M
Vol +1pt    +$13.62M
Charm       $0
Bias        [bearish ----|---- bullish]   C/P Vol 0.99
```

Reduce explanatory labels such as "hedge flow if spot lifts 1%" in the default card. Those can move to titles/tooltips if needed.

### Vol / Skew Card

Summarize current `#rail-card-iv`.

Suggested structure:

```text
Skew / IV                 Apr 28
7.8%  Upside rich
HV20 14.3%  ATM IV 7.8%  IV -6.5 pts
Put-call -5.0 pts   Since open 0.0 pts
Price under IV pace 0.19x
```

Avoid keeping the full 2-column ATM call/put/wing stat grid in Overview. If the full grid is still useful, make it expandable or move it to a future detail tab.

### Positioning Drift Card

Summarize current `#rail-card-centroid`.

Keep:

- Sparkline
- Call centroid
- Put centroid
- Current spread
- One concise read

Remove or collapse repetitive explanatory copy.

## 9. Flow Tab Details

Add a fourth right-rail tab:

```html
<button type="button" class="right-rail-tab" data-rail-tab="flow">Flow</button>
```

Add a matching panel:

```html
<div class="right-rail-panel" data-rail-panel="flow">
  ...
</div>
```

Also update the rebuild path in `ensurePriceChartDom` and tab persistence in `applyRightRailTab` if it validates known tab names.

### Blotter Layout

The existing flow blotter is a wide table. In the rail, it needs a compact layout.

Prototype options, in preferred order:

1. **Compact row list**
   - One row per contract/event.
   - Primary line: time, type, strike, expiry, side.
   - Secondary line: premium, volume, OI, V/OI, 1m delta volume.
   - Preserve sorting/filter controls at the top.

2. **Rail table with fewer columns**
   - Columns: Time, Contract, Vol, V/OI, Premium, Side.
   - Hide Bid/Mid/Ask, Last, OI, Pace behind row expansion or title attributes.

3. **Wide rail mode**
   - Keep more of the current table, but auto-widen the right rail when Flow is active.

The preferred prototype is option 1 because it uses the rail shape instead of fighting it.

### Flow Width Behavior

When Flow is active, allow the rail to widen.

Recommended CSS strategy:

```text
.chart-grid { --rail-col-w: 300px; }
.chart-grid.rail-flow-active { --rail-col-w: clamp(420px, 34vw, 520px); }
```

`applyRightRailTab('flow')` should add the class to `#chart-grid`; other tabs should remove it.

Do not widen the rail for Overview by default. Overview should prove that it can work in a compact rail.

### Flow Strip Interaction

Keep `#flow-event-lane` in the main grid as-is for prototype stage 1.

If the Flow tab proves strong enough, a later stage can test a "Flow workspace" mode where:

- Flow tab selected
- right rail widens
- horizontal Live Alerts and Flow Pulse remain visible
- secondary chart area can optionally be shorter or hidden

That is not required for the first prototype.

## 10. Implementation Stages

Each stage should leave the app runnable.

### Stage 1 - Prototype Branch and Port Discipline

- Create `codex/right-rail-overview-prototype`.
- Run baseline on 5001.
- Run prototype on 5002.
- Take screenshots of current Overview and bottom blotter for comparison.

No app behavior changes in this stage except any developer-only notes if needed.

### Stage 2 - Add Flow Tab Shell

- Add `Flow` tab after `Scenarios`.
- Add `[data-rail-panel="flow"]`.
- Update `buildAlertsPanelHtml` and rebuild path if required.
- Update `applyRightRailTab` so Flow is a valid persisted tab.
- Add `rail-flow-active` class to `#chart-grid` when Flow is active.
- Add CSS for tab-specific width.
- Keep the panel initially empty or with a temporary loading/empty state.

Acceptance:

- Overview, Levels, Scenarios still work.
- Flow tab activates and persists across reload.
- Selecting Flow widens the right rail.
- Selecting another tab returns rail width to normal.

### Stage 3 - Move/Duplicate Flow Blotter Into Flow Tab

For the prototype, duplication is acceptable if it is faster and safer:

- Keep the existing bottom flow blotter available until the rail version is verified.
- Render a compact rail version of the flow blotter inside `[data-rail-panel="flow"]`.
- Reuse existing flow blotter data and formatting helpers.
- Keep existing filter concepts: All / Calls / Puts and minimum premium.
- Prefer compact row cards/list over the full 13-column table.

Acceptance:

- Flow tab shows useful blotter rows without horizontal scrolling at the default Flow width.
- Filtering works.
- Sorting either works or is intentionally reduced to a simple default sort by premium/time for prototype review.
- Bottom blotter still exists during prototype comparison unless the user explicitly approves removal.

### Stage 4 - Contract Helper Toolbar Prototype

- Add compact toolbar helper group near the strike overlay toolbar group, before Auto-Range controls.
- Reuse existing contract-helper data hooks.
- Add compact display and popover/fallback details.
- Keep the rail contract-helper card temporarily during this stage for comparison.

Acceptance:

- Toolbar helper updates with live data.
- It does not overlap Auto-Range, Today, Reset, volume status, timeframe, or token timer.
- It collapses gracefully when the toolbar narrows.
- Full helper details remain accessible.

### Stage 5 - Remove Contract Helper From Overview

- Remove `#rail-card-contract-helper` from Overview server HTML.
- Remove it from `buildAlertsPanelHtml`.
- Ensure no JS error occurs when rail helper hooks are absent.
- Keep toolbar helper as the only desktop contract-helper surface.

Acceptance:

- Overview loses one full card.
- No console errors.
- Contract helper remains visible or accessible in the toolbar.

### Stage 6 - Consolidate Overview Cards

Replace the current Overview card stack with:

1. Market State
2. Dealer / Hedge Read
3. Vol / Skew
4. Positioning Drift

Implementation notes:

- Reuse existing data and existing renderer logic wherever possible.
- It is fine to create new card IDs such as:
  - `#rail-card-market-state`
  - `#rail-card-hedge-read`
  - `#rail-card-vol-skew`
  - `#rail-card-positioning`
- Keep old render functions if they are still useful internally, but avoid rendering removed DOM hooks without guards.
- All hook lookups should tolerate missing elements.
- Mirror markup in initial HTML and `buildAlertsPanelHtml`.

Acceptance:

- First Overview viewport shows the entire Market State card, Dealer / Hedge Read card, and at least the top of Vol / Skew on a desktop viewport similar to the provided screenshots.
- Total Overview scroll height is materially reduced.
- No major metric is lost; lower-priority detail is condensed, moved into popovers, or available in Flow/other tabs.

### Stage 7 - Prototype Review Sweep

Run side-by-side:

- Baseline: `http://127.0.0.1:5001`
- Prototype: `http://127.0.0.1:5002`

Review:

- Overview scan time
- Toolbar crowding
- Flow tab readability
- Rail width behavior
- Whether bottom blotter should remain duplicated or be removed after approval
- Whether any card should be expandable

Only after user approval should cleanup remove the old bottom blotter or any duplicate prototype surfaces.

## 11. Testing Checklist

Minimum checks:

- App starts on port 5002.
- Baseline can still run separately on port 5001.
- No browser console errors on initial load.
- SPY, 5-min timeframe, near-ATM expiry loads.
- Overview updates through multiple ticks.
- Levels tab still renders.
- Scenarios tab still renders.
- Flow tab activates and displays blotter content.
- Flow tab width class toggles correctly.
- Horizontal Live Alerts strip remains horizontal.
- Horizontal Flow Pulse strip remains horizontal.
- Contract helper toolbar updates with call/put/size data.
- Toolbar helper does not overlap controls at desktop screenshot width.
- Overview rebuild after ticker/timeframe changes keeps the new card DOM.

Regression checks:

- Settings drawer opens and closes.
- Strike rail collapse/resize still works.
- Strike overlay controls still work.
- Secondary chart tabs still work.
- Flow blotter data remains consistent with the existing bottom blotter during prototype duplication.

## 12. Success Criteria

The prototype is successful when:

- Overview no longer requires two full scrolls to understand the market state.
- The contract-helper read is available without consuming a rail card.
- Flow blotter is reachable from the right rail without losing the horizontal Live Alerts and Flow Pulse strips.
- Flow can use a wider rail without permanently shrinking the chart for other tabs.
- The implementation remains formula-neutral and single-file.
