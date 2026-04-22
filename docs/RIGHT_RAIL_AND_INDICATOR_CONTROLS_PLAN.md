# GEX Dashboard - Right Rail Prioritization + Indicator Controls Plan

**Status:** Partially complete — stages 1-4 landed on `main`
**Owner:** Codex
**Created:** 2026-04-22
**Target branch:** `feat/right-rail-indicator-controls`
**Suggested base:** `codex-ux-stability-refinement-plan` until that branch is merged; otherwise rebase onto whichever branch already contains the landed work from [`UX_STABILITY_REFINEMENT_PLAN.md`](UX_STABILITY_REFINEMENT_PLAN.md)

**Current state (as of 2026-04-22):**
- Stages 1-4 from this plan landed directly on `main` in commit `6b0d754` (`feat(chart): add built-in indicator controls`).
- The `Alerts` rail order now matches the target priority order in both the server-rendered HTML and `buildAlertsPanelHtml()`.
- Built-in indicator defaults, style normalization, toolbar editor modal, and settings persistence are implemented.
- Stage 5 remains as a manual regression sweep / smoke-test pass rather than a separate shipped code change.

**Read these first:**
- [`UI_MODERNIZATION_PLAN.md`](UI_MODERNIZATION_PLAN.md)
- [`ALERTS_RAIL_PHASE3_PLAN.md`](ALERTS_RAIL_PHASE3_PLAN.md)
- [`UX_STABILITY_REFINEMENT_PLAN.md`](UX_STABILITY_REFINEMENT_PLAN.md)
- [`CALCULATION_CONSISTENCY_REMEDIATION_PLAN.md`](CALCULATION_CONSISTENCY_REMEDIATION_PLAN.md)

This is a follow-up UX/control-surface plan. It is intentionally separate from the calculation-remediation doc. That remediation plan fixed correctness issues in alert math, expected-move selection, scoped baselines, scoped chain activity, and IV-surge buffering. It did **not** own right-rail ordering or built-in indicator editing.

---

## 0. Why this phase exists

Two UX gaps are still open after the current refinement work:

1. **The Alerts tab in the right rail does not present cards in strict decision priority.**
   - The current DOM order puts `Live Alerts` at the bottom of the `Alerts` stack.
   - That means the most time-sensitive card sits below slower context cards like `Skew / IV`, `Flow Pulse`, and `Centroid Drift`.
   - The rail reads cleanly, but the reading order is still suboptimal for fast scanning.

2. **Built-in chart indicators are toggleable but not user-editable.**
   - The price-chart toolbar exposes buttons for `SMA20`, `SMA50`, `SMA200`, `EMA9`, `EMA21`, `VWAP`, `BB`, `RSI`, `MACD`, `ATR`, and `OI`.
   - Their styles are hard-coded inside `applyIndicators()`.
   - The existing editor with color/width/style controls applies only to user drawings, not built-in indicators.
   - The top-bar gear modal only manages global call/put/max-level colors and exposure coloring mode.

The result is a visible UX inconsistency: user drawings are editable, but built-in indicators are not.

---

## 1. Goals

- Promote the highest-signal right-rail cards higher in the `Alerts` stack.
- Keep the `Alerts`, `Levels`, and `Scenarios` tab structure intact.
- Add editable style controls for built-in price-chart indicators without adding a framework.
- Persist indicator preferences through existing save/load settings flows.
- Preserve all analytical formulas and existing data sources.

---

## 2. Non-goals

- No changes to GEX / DEX / Vanna / Charm / Flow calculations.
- No rework of alert formulas or alert thresholds in this phase.
- No new indicators.
- No replacement of Lightweight Charts or Plotly.
- No split of `ezoptionsschwab.py`.
- No broad theme redesign beyond the controls needed for this feature.

---

## 3. Current-state inventory

Use grep anchors instead of line numbers.

### Right rail

- `buildAlertsPanelHtml`
- `ensurePriceChartDom`
- `applyRightRailTab`
- `wireRightRailTabs`
- `renderMarketMetrics`
- `renderRangeScale`
- `renderGammaProfile`
- `renderDealerImpact`
- `renderChainActivity`
- `renderFlowPulse`
- `renderRailAlerts`
- `.right-rail-panel`
- `.rail-card`
- `#rail-card-alerts`

**Current `Alerts` tab order in the DOM:**

1. `Price`
2. `Net GEX / Net DEX`
3. `Expected Move`
4. `Gamma Profile`
5. `Skew / IV`
6. `Dealer Impact`
7. `Chain Activity`
8. `Flow Pulse`
9. `Centroid Drift`
10. `Live Alerts`

Important implementation detail:

- The `Alerts` panel markup exists in **two places**:
  - the initial server-rendered HTML
  - `buildAlertsPanelHtml()` for the rebuild path used by `ensurePriceChartDom()`
- Any reordering must be mirrored in both places or ticker switches / DOM rebuilds will drift.

### Built-in indicators

- `buildTVToolbar`
- `applyIndicators`
- `tvActiveInds`
- `tvIndicatorSeries`
- `mkLineSeries`
- `ensureTVDrawingEditor`
- `.tv-toolbar-container`
- `.tv-toolbar-group`
- `.tv-tb-btn`
- `.settings-modal`

**Current behavior:**

- `buildTVToolbar()` defines toggle-only buttons for built-in indicators.
- `applyIndicators()` hard-codes colors and widths for the built-in series:
  - `sma20` -> `#f0c040`
  - `sma50` -> `#40a0f0`
  - `sma200` -> `#e040fb`
  - `ema9` -> `#ff9900`
  - `ema21` -> `#00e5ff`
  - `vwap` -> `#ffffff`
  - `bb` -> fixed RGBA blue variants
- The chart drawing editor already supports:
  - `Color`
  - `Thickness`
  - `Style`
- That editing path is for user drawings only and does not touch built-in indicator series.

### Settings persistence

- `gatherSettings`
- `applySettings`
- `/save_settings`
- `/load_settings`
- `settings_schema_version`
- `call_color`
- `put_color`
- `max_level_color`
- `coloring_mode`

**Current behavior:**

- The top-bar gear opens `#settings-modal`.
- That modal currently manages only:
  - `Coloring Mode`
  - `Call Color`
  - `Put Color`
  - `Max Level Color`
- `gatherSettings()` and `applySettings()` do not currently store any per-indicator style config.

---

## 4. Product decisions

### 4.1 Right-rail target order

Use this target order for the `Alerts` tab:

1. `Price`
2. `Net GEX / Net DEX`
3. `Expected Move`
4. `Gamma Profile`
5. `Live Alerts`
6. `Dealer Impact`
7. `Chain Activity`
8. `Skew / IV`
9. `Flow Pulse`
10. `Centroid Drift`

**Why this order:**

- `Price`, `Net GEX / Net DEX`, `Expected Move`, and `Gamma Profile` are the structural top-of-book context.
- `Live Alerts` should appear immediately after that structural context, not below tertiary confirmation cards.
- `Dealer Impact` and `Chain Activity` are useful response/context cards, but slower than active alerts.
- `Skew / IV`, `Flow Pulse`, and `Centroid Drift` are secondary reads and should sit lower.

This is a deliberate prioritization pass, not a request to add more content.

### 4.2 Indicator-controls UX

Do **not** overload the existing drawing editor with built-in indicator state. Keep drawings and built-in indicators separate.

Recommended UX:

- Add a dedicated `Indicators` control surface tied to the price-chart toolbar.
- Keep the existing one-click toggle pills for fast on/off.
- Add one secondary entry point for style editing:
  - either an `Indicators` button in the toolbar actions group
  - or a small gear/sliders button adjacent to the indicator group
- Open a lightweight modal or popover listing built-in indicators with editable fields.

Recommended first-pass editable indicators:

- `SMA20`
- `SMA50`
- `SMA200`
- `EMA9`
- `EMA21`
- `VWAP`
- `BB`

Recommended first-pass editable properties:

- `Visible`
- `Color`
- `Line width`
- `Line style`

Out of first-pass scope unless the implementation stays clean:

- `RSI`
- `MACD`
- `ATR`
- `OI`

Reason: those are either multi-series sub-pane indicators or not simple price-overlay lines, so they should not block delivery of the primary user request.

### 4.3 Bollinger Bands rule

Treat `BB` as one grouped indicator in the first pass.

- One shared base color control.
- One shared line width.
- One shared line style.
- Upper/lower bands use the base color.
- Mid band uses the same hue at reduced opacity or a clearly-derived variant.

Do not build a three-row BB editor unless needed later.

---

## 5. Workstreams

### Stage 1 - Right-rail reprioritization

**Why:** This is the smallest, highest-signal UX win. It fixes reading order without changing data.

**Files / anchors:**

- `ezoptionsschwab.py`
- server-rendered `Alerts` tab markup
- `buildAlertsPanelHtml`
- `ensurePriceChartDom`
- `#rail-card-alerts`
- `.rail-card:last-child`

**Changes:**

- Reorder the `Alerts` tab cards to the target order from section 4.1.
- Mirror the change in both the initial HTML and `buildAlertsPanelHtml()`.
- Check for CSS assumptions tied to `:last-child` or implicit card order.
- Keep all `data-met`, `data-di`, and `id` hooks unchanged so render functions continue to bind cleanly.

**Acceptance criteria:**

- `Live Alerts` is above `Dealer Impact`, `Chain Activity`, `Skew / IV`, `Flow Pulse`, and `Centroid Drift`.
- Ticker switches and defensive DOM rebuilds preserve the same card order.
- No card loses its data bindings after reordering.

**Commit:**

`style(rail): move live alerts into decision-priority position`

**Progress note (2026-04-22):**

- Landed as part of `6b0d754` on `main`.
- `Live Alerts` now sits directly under `Gamma Profile` in both the initial HTML and the rebuild path.
- Existing `id`, `data-met`, and `data-di` bindings were preserved.

---

### Stage 2 - Built-in indicator preference model

**Why:** The code needs a stable preference model before the UI can edit anything.

**Files / anchors:**

- `applyIndicators`
- `buildTVToolbar`
- `tvActiveInds`
- `tvIndicatorSeries`
- `gatherSettings`
- `applySettings`
- `settings_schema_version`

**Changes:**

- Introduce a centralized default preference map for built-in indicators.
- Separate toggle state from style state:
  - toggle state remains `tvActiveInds`
  - style state becomes a new preference object keyed by indicator id
- Add helpers for:
  - default prefs
  - schema normalization / migration
  - mapping app-level line styles to Lightweight Charts line-style constants
- Update `applyIndicators()` so it reads style prefs instead of hard-coded values.

**Suggested shape:**

```javascript
const DEFAULT_TV_INDICATOR_PREFS = {
  sma20:  { color: '#f0c040', lineWidth: 1, lineStyle: 'solid' },
  sma50:  { color: '#40a0f0', lineWidth: 1, lineStyle: 'solid' },
  sma200: { color: '#e040fb', lineWidth: 1, lineStyle: 'solid' },
  ema9:   { color: '#ff9900', lineWidth: 1, lineStyle: 'solid' },
  ema21:  { color: '#00e5ff', lineWidth: 1, lineStyle: 'solid' },
  vwap:   { color: '#ffffff', lineWidth: 1, lineStyle: 'solid' },
  bb:     { color: '#64b4ff', lineWidth: 1, lineStyle: 'solid' }
};
```

**Acceptance criteria:**

- No built-in overlay line color/style is hard-coded inline in the indicator branches anymore.
- Existing indicator toggles still work.
- Defaults match the current visual behavior closely enough that the dashboard does not unexpectedly restyle for existing users.

**Commit:**

`refactor(chart): centralize built-in indicator style preferences`

**Progress note (2026-04-22):**

- Landed as part of `6b0d754` on `main`.
- Added `DEFAULT_TV_INDICATOR_PREFS`, normalization helpers, and Lightweight Charts line-style mapping helpers.
- `applyIndicators()` now reads centralized prefs for first-pass editable overlays instead of hard-coding style values inline.

---

### Stage 3 - Indicator editor UI

**Why:** This is the user-facing control surface for the new preference model.

**Files / anchors:**

- `buildTVToolbar`
- `.tv-toolbar-group`
- `.tv-tb-btn`
- `.settings-modal`
- `ensureTVDrawingEditor`

**Changes:**

- Add a dedicated built-in indicator editor UI.
- Keep it visually aligned with the existing toolbar and modal language.
- Provide one row per first-pass editable indicator with:
  - visibility toggle
  - color input
  - width select
  - style select
- Support styles:
  - `Solid`
  - `Dashed`
  - `Dotted`
- Reapply the active indicator set immediately after edits without requiring a full `/update` cycle.

**Recommended interaction model:**

- Clicking indicator pills still toggles visibility quickly.
- Clicking the new `Indicators` button opens the editor.
- Changing a control updates the live chart immediately.

**Acceptance criteria:**

- A user can turn on `SMA20`, open the indicator editor, change color/style/width, and see the result immediately.
- The editor does not interfere with the existing user-drawing editor.
- The toolbar remains one-row usable at normal desktop widths.

**Commit:**

`feat(chart): add editable built-in indicator controls`

**Progress note (2026-04-22):**

- Landed as part of `6b0d754` on `main`.
- Added an `Indicators` button to the toolbar actions group and a dedicated `#indicator-settings-modal`.
- The editor currently covers `SMA20`, `SMA50`, `SMA200`, `EMA9`, `EMA21`, `VWAP`, and `BB` with live `Visible`, `Color`, `Width`, and `Style` controls.
- Built-in indicators are edited from the modal, not by clicking the overlay line on-chart.

---

### Stage 4 - Save/load integration

**Why:** Indicator customization is not finished if it disappears on reload.

**Files / anchors:**

- `gatherSettings`
- `applySettings`
- `/save_settings`
- `/load_settings`
- `settings_schema_version`

**Changes:**

- Add built-in indicator prefs to the saved settings payload.
- Bump `settings_schema_version` from `3` to `4`.
- Add a migration path so older settings files fall back to defaults cleanly.
- Ensure indicator prefs restore before or during the first indicator render path.

**Acceptance criteria:**

- Saving settings persists indicator styles.
- Loading settings restores indicator styles and visibility correctly.
- Older settings files without indicator prefs still load without errors.

**Commit:**

`feat(settings): persist built-in indicator styles`

**Progress note (2026-04-22):**

- Landed as part of `6b0d754` on `main`.
- `settings_schema_version` was bumped from `3` to `4`.
- Added `tv_active_indicators` and `tv_indicator_prefs` to the saved settings payload.
- Older settings files still normalize cleanly back to defaults when the new fields are absent.

---

### Stage 5 - Regression sweep

**Why:** This touches a dense part of the chart UI where regressions are easy to introduce.

**Focus areas:**

- rail card order on first load and rebuild
- ticker switch behavior
- timeframe switch behavior
- `Auto-Update` on/off transitions
- saved settings round-trip
- indicator redraws during live streaming
- interaction with `use_heikin_ashi`
- interaction with `Auto-Range`

**Acceptance criteria:**

- No duplicate indicator lines after repeated toggles.
- No stale indicator series survive when an indicator is turned off.
- No rebuild path drops reordered rail cards.
- No layout regression causes the toolbar to wrap badly on common desktop widths.

**Commit:**

`chore(ui): regression sweep for rail order and indicator controls`

**Progress note (2026-04-22):**

- Not yet completed as a distinct pass.
- Code-level verification completed: `python3 -m py_compile ezoptionsschwab.py` passed before `6b0d754` was committed.
- Manual browser smoke coverage for save/load round-trip, timeframe switching, ticker switching, and repeated indicator toggle/edit cycles is still the remaining follow-up.

---

## 6. Implementation notes for the next session

- Stage 1-4 implementation is in `main` at `6b0d754`.
- The remaining work is primarily manual regression coverage, not another feature slice.
- Focus the next session on smoke-testing ticker switches, timeframe changes, repeated indicator edits, and save/load restoration.
- If a follow-up bug appears, keep built-in indicator prefs separate from the user-drawing persistence path.

---

## 7. Verification checklist

Manual verification:

1. Open the `Alerts` tab and confirm `Live Alerts` now sits directly below the structural context cards.
2. Change ticker and confirm the rail rebuild keeps the new order.
3. Toggle `SMA20`, edit color, style, and width, and confirm live chart updates immediately.
4. Toggle `VWAP` and `BB`, edit them, then change timeframe and confirm styles persist.
5. Save settings, reload the page, and confirm indicator preferences restore.
6. Load an older settings file with no indicator prefs and confirm defaults apply without errors.
7. Verify the drawing editor still edits user drawings only.

Code-level verification:

- `python3 -m py_compile ezoptionsschwab.py`
- grep `buildAlertsPanelHtml` and the server-rendered `Alerts` markup to confirm matching order
- grep `applyIndicators` to confirm hard-coded per-indicator style literals were replaced by preference lookups

---

## 8. Risks

- The rail reorder itself is low risk, but forgetting the mirrored rebuild path will reintroduce DOM drift on ticker switch.
- Indicator editing is medium risk because `applyIndicators()` already owns a lot of chart-side state and can easily accumulate duplicate series or stale series references.
- Save/load is medium risk because the settings file is permissive JSON, so migration logic needs to tolerate partial or older payloads.

---

## 9. Definition of done

This phase is done when:

- the right-rail `Alerts` tab reflects the target decision order
- built-in overlay indicators can be styled by the user
- those styles survive save/load
- no chart or rail rebuild regression is introduced

**Completion snapshot (2026-04-22):**

- The first three bullets above are complete on `main`.
- The remaining open item is the explicit regression sweep / manual smoke validation.
