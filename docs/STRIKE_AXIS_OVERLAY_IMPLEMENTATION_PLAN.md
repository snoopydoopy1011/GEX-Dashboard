# Strike Axis Overlay Prototype Plan

**Status:** Prototype implementation complete
**Created:** 2026-04-26  
**Implemented:** 2026-04-26 on `codex/strike-axis-overlay-prototype`
**Target branch:** `codex/strike-axis-overlay-plan` for this doc; implementation used `codex/strike-axis-overlay-prototype`.
**Scope:** Prototype an on-chart, right-side strike profile overlay while keeping the existing strike rail available.

---

## 0. Read This First

The goal is to prototype the strike rail data directly on the price chart, similar to a volume-profile/GEX-profile overlay in the blank area to the right of the latest candles. The existing standalone strike rail must remain available during the prototype.

This is exploratory implementation, not a final redesign. Get the overlay working and aligned first. Styling, exact metric semantics, and richer call/put display can be refined later.

Current confirmed branch state when this doc was written:

```bash
git branch -a
git log --oneline main..HEAD
```

At doc creation, the workspace was on `main`, and `main..HEAD` was empty before creating this doc branch.

Line numbers in `ezoptionsschwab.py` drift quickly. Grep by these anchors instead of trusting line numbers:

- Layout/CSS: `.chart-grid`, `.price-chart-container`, `#price-chart`, `.gex-col-header`, `.gex-column`, `.gex-side-panel-wrap`, `.strike-rail-tabs`, `.strike-rail-select`, `.tv-historical-overlay`, `.tv-session-cloud-overlay`, `.tv-drawing-overlay`.
- Python chart/data builders: `create_gex_side_panel`, `create_exposure_chart`, `create_options_volume_chart`, `create_open_interest_chart`, `compute_greek_exposures`.
- JS chart setup: `renderTradingViewPriceChart`, `LightweightCharts.createChart`, `tvPriceChart`, `tvCandleSeries`, `tvLastCandles`.
- JS strike rail: `STRIKE_RAIL_CHART_IDS`, `STRIKE_RAIL_LABELS`, `activeStrikeRailTab`, `applyStrikeRailTabs`, `wireStrikeRailTabs`, `renderStrikeRailPanel`, `renderGexSidePanel`, `syncGexPanelYAxisToTV`.
- JS overlays: `ensureTVDrawingOverlay`, `ensureSessionLevelCloudOverlay`, `ensureTVHistoricalOverlay`, `priceToCoordinate`, `coordinateToPrice`, `scheduleGexPanelSync`.
- Price labels/levels: `renderKeyLevels`, `renderSessionLevels`, `renderTopOI`, `createPriceLine`.
- Update response: `response['gex_panel']`, `response['gamma']`, `response['delta']`, `response['vanna']`, `response['charm']`, `response['options_volume']`, `response['open_interest']`, `response['premium']`.

Inherited ground rules:

- No analytical-formula changes.
- No JS framework introduction.
- Keep `ezoptionsschwab.py` as a single-file app.
- Use CSS tokens for colors, especially `--call`, `--put`, `--warn`, `--info`, `--accent`, `--border`, `--bg-*`, `--fg-*`.
- Any new DOM under rebuilt chart-grid regions must be mirrored in `ensurePriceChartDom()` if the rebuild path can drop it.

---

## Implementation Update - 2026-04-26

Prototype work is implemented in `ezoptionsschwab.py` on `codex/strike-axis-overlay-prototype`.

Completed:

- Added `/update` `strike_profiles` data via `create_strike_profile_payload()`.
- Added a disabled-by-default on-chart strike overlay with toolbar toggle and metric dropdown.
- Supported overlay metrics: GEX, Gamma, Delta, Vanna, Charm, Open Interest, and Options Volume. Premium remains deferred.
- Kept the existing Plotly strike rail intact for side-by-side comparison.
- Drew bars left from a fixed right-side anchor in the blank price-chart area, using `--call` and `--put` color tokens with magnitude-based opacity.
- Reserved room near the price axis so chart price labels and level labels remain readable.
- Redraw the overlay on chart render, pan/zoom, resize, reset/focus, live candle updates, toggle changes, and metric changes.

Issues encountered and fixes:

- Backend profile rows were present, but the first SVG overlay renderer produced no visible output in the browser. The renderer was changed to absolutely positioned DOM bars for better Safari/in-app-browser reliability and easier inspection.
- Toggling the overlay on after data had already arrived could leave the overlay with empty local state. The toggle path now recovers profiles from `lastData.strike_profiles`.
- The overlay element could report `0x0` dimensions even after insertion, causing the draw routine to exit. CSS now gives the overlay explicit dimensions, and the draw path falls back to the parent chart dimensions.
- Price coordinate mapping was timing-sensitive in some chart states. The overlay now uses `priceToCoordinate()` when available and falls back to a visible-range mapping derived from `coordinateToPrice(0)` and `coordinateToPrice(plotBottom)`.
- Safari and the in-app browser can cache inline JavaScript during rapid local testing. Use a hard refresh or cache-busted URL after code changes.

Verification completed:

- `python3 -m py_compile ezoptionsschwab.py`
- Node syntax check of all inline `<script>` blocks
- `git diff --check`
- Local `/update` response returned 29 GEX profile rows for SPY in the test state
- Browser verification on `127.0.0.1:5002` showed 11 visible overlay bars in the current price viewport

Remaining follow-up work:

- Decide whether the on-chart overlay should eventually replace, collapse, or coexist with the standalone strike rail.
- Add richer hover/tooltip details if users want per-strike values on the overlay itself.
- Consider split call/put rendering for Open Interest and Options Volume after the simple net view is validated.
- Add user-facing controls for right offset, anchor width, or max bar width if the fixed defaults need tuning.
- Refine mobile behavior later; the prototype remains targeted at the large-monitor desktop layout.
- Add Premium only after confirming the desired signed net convention.

---

## 1. User Goal

The user wants to explore moving the current strike rail bars onto the price chart's right-side price area, like the reference dashboard screenshot.

Target behavior:

- Bars line up vertically with the strike/price they represent.
- Bars sit in the open space to the right of the most recent candles.
- Bars face left from a fixed right anchor.
- Positive values use green styling from the app (`--call`).
- Negative values use red styling from the app (`--put`).
- Positive and negative bars do **not** split around a midline; both face left.
- Existing chart labels such as VWAP, SMA labels, Gamma Flip, EM, wall labels, session labels, etc. remain visible in front of or beside the bars.
- A dropdown/select still chooses which strike metric is displayed.
- The old standalone strike rail remains available while this is evaluated.
- Mobile behavior is not important for this prototype. The target environment is a Mac mini with a large monitor.

For OI/options volume, keep the first version simple. Use whichever representation is easiest to make stable. Recommended first-pass behavior:

- `open_interest`: net call OI minus put OI by strike.
- `options_volume`: net call volume minus put volume by strike.
- Use sign color from the net value and left-facing width from `abs(value)`.

This is intentionally simpler than the existing split call/put rail and can be improved after alignment and refresh behavior are proven.

---

## 2. Current Architecture Summary

The app currently has three main chart-grid columns:

```text
price chart | strike rail column | overview/levels/scenarios rail
```

Relevant current layout:

- `.chart-grid` defines `grid-template-columns: minmax(0, 1fr) var(--gex-col-w) var(--rail-col-w)`.
- `.price-chart-container` holds `#price-chart`.
- `.gex-column` holds the current Plotly strike rail in `#gex-side-panel`.
- `.right-rail-panels` holds overview/levels/scenarios cards.

The current strike rail is a Plotly chart:

- `create_gex_side_panel()` builds the GEX profile JSON.
- Other metrics use existing Plotly chart builders (`create_exposure_chart`, `create_options_volume_chart`, `create_open_interest_chart`, `create_premium_chart`).
- `renderStrikeRailPanel()` chooses the payload for `activeStrikeRailTab`.
- `syncGexPanelYAxisToTV()` mirrors the visible Lightweight chart y-range into the Plotly rail.

The price chart is a TradingView Lightweight Charts instance:

- `tvPriceChart = LightweightCharts.createChart(...)`
- `tvCandleSeries = tvPriceChart.addCandlestickSeries(...)`
- `tvCandleSeries.priceToCoordinate(price)` maps a price/strike to chart pixel y.
- `tvCandleSeries.coordinateToPrice(y)` maps chart y pixels back to price.
- `tvPriceChart.timeScale()` controls the right-side time gutter and visible range.

The app already has custom HTML/SVG overlays inside `#price-chart`:

- `.tv-historical-overlay`
- `.tv-session-cloud-overlay`
- `.tv-drawing-overlay`

Use this same overlay pattern for the strike overlay. It is less risky than trying to embed Plotly inside the chart.

Official library context checked while drafting:

- Lightweight Charts 4.2 supports plugin/custom visualizations, but this app is not using a bundled build system. A simple DOM/SVG overlay is more pragmatic for a prototype.
- `TimeScaleOptions.rightOffset` provides blank future space to the right of the latest bars.
- `IPriceScaleApi.width()` returns the width of the visible price scale, useful for keeping bars away from the price axis labels.

Docs:

- https://tradingview.github.io/lightweight-charts/docs/4.2/plugins/intro
- https://tradingview.github.io/lightweight-charts/docs/4.2/api/interfaces/TimeScaleOptions
- https://tradingview.github.io/lightweight-charts/docs/4.2/api/interfaces/IPriceScaleApi

---

## 3. Recommended Prototype Shape

Implement the overlay as a new Lightweight-chart-adjacent SVG layer inside `#price-chart`.

Suggested names:

- DOM/CSS:
  - `.tv-strike-overlay`
  - `.tv-strike-overlay-svg`
  - `.strike-overlay-toggle`
  - `.strike-overlay-select`
- JS state:
  - `STRIKE_OVERLAY_ENABLED_KEY`
  - `STRIKE_OVERLAY_METRIC_KEY`
  - `tvStrikeOverlayPending`
  - `tvStrikeOverlayProfiles`
  - `activeStrikeOverlayMetric`
- JS functions:
  - `ensureTVStrikeOverlay()`
  - `scheduleTVStrikeOverlayDraw()`
  - `drawTVStrikeOverlay()`
  - `clearTVStrikeOverlay()`
  - `setStrikeOverlayProfiles(data)`
  - `renderStrikeOverlayControls()`
  - `wireStrikeOverlayControls()`
  - `applyStrikeOverlayRightOffset()`

The overlay should draw below user drawings and tooltips, but above the chart canvas.

Recommended z-index layering:

- Session clouds: current `z-index: 3`
- Historical dots: current `z-index: 4`
- New strike overlay: `z-index: 5`
- Drawing overlay: current `z-index: 6`
- OHLC tooltip and hover UI: higher

This lets user drawing tools and important interaction layers remain in front.

---

## 4. Data Contract

Do not parse Plotly JSON to build the overlay. Add a simple backend payload that contains normalized strike profile data.

Add a new field to the `/update` response:

```python
response['strike_profiles'] = {
    'gex': [
        {'strike': 714.0, 'value': 12345678.0, 'call': 15500000.0, 'put': 3154322.0},
        ...
    ],
    'gamma': [...],
    'delta': [...],
    'vanna': [...],
    'charm': [...],
    'open_interest': [...],
    'options_volume': [...],
    'premium': [...]
}
```

Minimum required row fields:

- `strike`: numeric strike/price level.
- `value`: signed numeric net value used for color and width.

Optional but useful:

- `call`: call-side value.
- `put`: put-side value.
- `abs_value`: absolute magnitude if helpful.
- `label`: formatted value for future hover/tooltip.

Keep the payload bounded to the same `strike_range` currently used by the strike rail. Do not ship the full chain unless needed.

Recommended net conventions for the first prototype:

- `gex`: same as current displayed GEX side panel, call GEX minus put GEX.
- `gamma`: same as `gex` if using `GEX` exposure; this can map to the existing `gex` profile.
- `delta`: call DEX plus put DEX, matching current net DEX convention.
- `vanna`: call VEX plus put VEX.
- `charm`: call Charm plus put Charm.
- `open_interest`: call OI minus put OI.
- `options_volume`: call volume minus put volume.
- `premium`: use the existing premium chart convention if straightforward; otherwise defer premium from the overlay until the core metrics work.

Important: do not change how GEX/DEX/Vanna/Charm are calculated. Only aggregate existing dataframe columns by strike.

---

## 5. Backend Implementation Steps

All backend work stays in `ezoptionsschwab.py`.

### 5.1 Add A Helper To Build Strike Profiles

Add a helper near `create_gex_side_panel()` or near the chart-builder helpers:

```python
def create_strike_profile_payload(calls, puts, S, strike_range=0.02, selected_expiries=None):
    ...
```

Suggested internal helpers:

```python
def _filter_profile_df(df):
    # filter selected expiries and strike range

def _sum_by_strike(df, col):
    # return {strike: sum}

def _rows_from_maps(call_map, put_map, mode):
    # combine strikes and compute signed value
```

Column mapping:

```python
profiles = {
    'gex': ('GEX', 'call_minus_put'),
    'gamma': ('GEX', 'call_minus_put'),
    'delta': ('DEX', 'call_plus_put'),
    'vanna': ('VEX', 'call_plus_put'),
    'charm': ('Charm', 'call_plus_put'),
    'open_interest': ('openInterest', 'call_minus_put'),
    'options_volume': ('volume', 'call_minus_put'),
}
```

For call-minus-put:

```python
value = call_value - put_value
```

For call-plus-put:

```python
value = call_value + put_value
```

Why the distinction:

- Current GEX display treats puts as a positive input and subtracts them for net GEX.
- DEX/VEX/Charm rows already carry sign conventions from the Greek calculation, so summing is the current convention used elsewhere.

Guardrails:

- If a column is missing, return an empty list for that metric.
- Convert all values to plain `float`; avoid numpy/pandas scalar JSON surprises.
- Sort rows by strike ascending.
- Drop rows where `value`, `call`, and `put` are all zero.
- Keep selected expiries filtering consistent with existing chart builders.

### 5.2 Add The Payload To `/update`

In the update response builder, after chart payload creation is fine:

```python
try:
    response['strike_profiles'] = create_strike_profile_payload(
        calls, puts, S, strike_range,
        selected_expiries=expiry_dates
    )
except Exception as e:
    print(f"[strike_profiles] build failed: {e}")
    response['strike_profiles'] = {}
```

Use the same `calls`, `puts`, `S`, `strike_range`, and selected expiries that drive the current strike rail.

Do not add a new endpoint for the prototype.

---

## 6. Frontend Implementation Steps

All frontend work stays in the inline JS/CSS in `ezoptionsschwab.py`.

### 6.1 State And Defaults

Place this near current strike rail constants:

```javascript
const STRIKE_OVERLAY_ENABLED_KEY = 'gex.strikeOverlayEnabled';
const STRIKE_OVERLAY_METRIC_KEY = 'gex.strikeOverlayMetric';
const STRIKE_OVERLAY_RIGHT_OFFSET_KEY = 'gex.strikeOverlayRightOffset';

let tvStrikeOverlayProfiles = {};
let tvStrikeOverlayPending = false;
let strikeOverlayEnabled = false;
let activeStrikeOverlayMetric = 'gex';
```

Load persisted state from `localStorage`, defaulting to disabled.

Reason: the first prototype should not unexpectedly replace the known-good rail on load.

### 6.2 Controls

Use the current strike rail selector if possible, but keep behavior explicit for the prototype.

Recommended first pass:

- Add a compact toggle in the workspace toolbar: `Overlay`.
- Add/select a metric dropdown next to it.
- Reuse `STRIKE_RAIL_LABELS` and `getVisibleStrikeRailTabs()`.
- If overlay is disabled, current strike rail behavior is unchanged.
- If overlay is enabled, do **not** automatically hide the old strike rail yet. Let the user compare both.

Later, add an option to collapse the old rail automatically when overlay is enabled.

### 6.3 Overlay DOM

Follow the existing overlay pattern:

```javascript
function ensureTVStrikeOverlay() {
    const container = document.getElementById('price-chart');
    if (!container) return null;
    let overlay = container.querySelector('.tv-strike-overlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.className = 'tv-strike-overlay';
        overlay.innerHTML = '<svg class="tv-strike-overlay-svg" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"></svg>';
        const drawingOverlay = container.querySelector('.tv-drawing-overlay');
        if (drawingOverlay) container.insertBefore(overlay, drawingOverlay);
        else container.appendChild(overlay);
    }
    return overlay;
}
```

CSS:

```css
.tv-strike-overlay {
    position: absolute;
    inset: 0;
    z-index: 5;
    pointer-events: none;
    overflow: hidden;
}
.tv-strike-overlay svg {
    width: 100%;
    height: 100%;
    overflow: visible;
}
```

Add this around the current overlay CSS near `.tv-historical-overlay`, `.tv-session-cloud-overlay`, and `.tv-drawing-overlay`.

### 6.4 Drawing Algorithm

`drawTVStrikeOverlay()` should:

1. Ensure overlay and SVG exist.
2. Clear existing SVG children.
3. Exit if overlay is disabled.
4. Exit if `tvPriceChart`, `tvCandleSeries`, or current profile rows are missing.
5. Determine chart dimensions from overlay/client size.
6. Determine price scale width:

```javascript
const priceScaleWidth = tvPriceChart.priceScale('right').width ? tvPriceChart.priceScale('right').width() : 0;
```

7. Determine plot bottom so bars do not draw into the time axis:

```javascript
const tsH = tvPriceChart.timeScale && tvPriceChart.timeScale().height
    ? tvPriceChart.timeScale().height()
    : 0;
const plotBottom = Math.max(0, overlay.clientHeight - tsH);
```

8. Create a right anchor just left of the price scale:

```javascript
const rightPad = Math.max(priceScaleWidth + 8, 56);
const anchorX = overlay.clientWidth - rightPad;
```

9. Choose maximum bar width:

```javascript
const maxBarWidth = Math.min(260, Math.max(80, overlay.clientWidth * 0.16));
```

10. Normalize by max absolute value of visible rows.
11. For each row:

```javascript
const y = tvCandleSeries.priceToCoordinate(row.strike);
if (y == null || Number.isNaN(y) || y < 0 || y > plotBottom) return;
const width = Math.max(2, Math.round(maxBarWidth * Math.abs(row.value) / maxAbs));
const x = anchorX - width;
```

12. Draw a rounded-ish SVG `rect`:

```javascript
rect.setAttribute('x', x);
rect.setAttribute('y', y - barHeight / 2);
rect.setAttribute('width', width);
rect.setAttribute('height', barHeight);
rect.setAttribute('fill', row.value >= 0 ? callColor : putColor);
rect.setAttribute('opacity', opacity);
```

Recommended first-pass bar height:

```javascript
const barHeight = 8;
```

Optional, better first-pass bar height:

- Estimate nearest neighboring strike pixel distance.
- Use `Math.min(12, Math.max(3, nearestGap * 0.65))`.
- This prevents overlap on dense strikes.

Opacity:

```javascript
const opacity = 0.28 + 0.62 * (Math.abs(row.value) / maxAbs);
```

This matches the alpha-intensity idea already used in the current GEX rail.

### 6.5 Keeping Labels Visible

Price line labels are rendered by Lightweight Charts near the right price scale. To keep them readable:

- Stop bars before the price axis using `priceScale('right').width()`.
- Add a right padding gap of at least 8-12 px.
- Keep opacity below 0.9.
- Put the overlay below `.tv-drawing-overlay`.

If labels still conflict:

- Reduce `maxBarWidth`.
- Shift `anchorX` farther left.
- Add a small transparent label-safe zone:

```javascript
const labelSafeWidth = 96;
const anchorX = overlay.clientWidth - priceScaleWidth - labelSafeWidth;
```

The safest prototype default is to reserve more label room than needed. It is better for bars to be slightly shorter than to obscure Gamma Flip/VWAP/SMA labels.

### 6.6 Reserving Future Chart Space

To make room to the right of candles, use `rightOffset` on the time scale when overlay is enabled.

```javascript
function applyStrikeOverlayRightOffset() {
    if (!tvPriceChart) return;
    const offset = strikeOverlayEnabled ? 18 : 0;
    tvPriceChart.timeScale().applyOptions({ rightOffset: offset });
}
```

Consider a user setting later, but hard-code first.

Important: changing `rightOffset` may affect the user's current zoom/pan feel. Keep it modest in the prototype.

### 6.7 Draw Scheduling

Call `scheduleTVStrikeOverlayDraw()` anywhere the y-axis, chart dimensions, or data changes:

- After candle data updates.
- After key levels/session levels render.
- In `subscribeVisibleLogicalRangeChange`.
- On wheel/mouseup/touchend handlers where existing overlays are redrawn.
- After toggling overlay enabled/disabled.
- After changing active overlay metric.
- After `Plotly` chart-grid resize callbacks if applicable.
- After `tvFitAll()` and session focus operations.

Existing places to mirror:

- Current `scheduleSessionLevelCloudDraw()`
- Current `scheduleTVHistoricalOverlayDraw()`
- Current `scheduleGexPanelSync()`

Add the new scheduler alongside those calls, not as a replacement.

### 6.8 Update Response Handling

Find where `/update` data is consumed and stored in `lastData`.

When the response includes `strike_profiles`:

```javascript
tvStrikeOverlayProfiles = data.strike_profiles || {};
scheduleTVStrikeOverlayDraw();
```

If `activeStrikeOverlayMetric` is not present in the new payload, fall back to `gex` or the first available key.

Do not block normal chart rendering if the payload is missing.

---

## 7. Keeping The Existing Strike Rail Available

Do not delete:

- `.gex-col-header`
- `.gex-column`
- `.gex-side-panel-wrap`
- `#gex-side-panel`
- `renderStrikeRailPanel()`
- `renderGexSidePanel()`
- `syncGexPanelYAxisToTV()`
- `STRIKE_RAIL_CHART_IDS`
- existing Plotly chart builders

For the first prototype, overlay and rail can both display at once.

After the prototype works, a follow-up can add one of these UX options:

1. `Overlay only`: automatically collapse `.gex-column` when overlay is enabled.
2. `Compare`: keep both visible.
3. `Rail only`: current behavior.

Do not start with this full mode system unless the basic overlay is already stable.

---

## 8. Expected Edge Cases

### 8.1 Bars Not Aligned After Zoom/Pan

Likely missing a scheduler call. Confirm `scheduleTVStrikeOverlayDraw()` runs from:

- `subscribeVisibleLogicalRangeChange`
- wheel/mouseup/touchend handlers
- after candle updates
- after y-axis autoscale changes

### 8.2 Bars Cover Price Labels

Increase label-safe right gap or reduce `maxBarWidth`. Use `priceScale('right').width()` and add extra padding.

### 8.3 Bars Draw Into Time Axis

Use `timeScale().height()` and skip rows where `y > plotBottom`.

### 8.4 Bars Too Dense

For SPY $1 strikes this may be okay, but SPX/other symbols can vary. Add dynamic bar height based on nearest y gap.

### 8.5 Overlay Disappears After DOM Rebuild

Check `ensurePriceChartDom()`. If the price chart container is rebuilt, the overlay must be re-created when the chart is re-rendered. The `ensureTVStrikeOverlay()` function should be called during chart setup.

### 8.6 Overlay Stale After Expiry Or Metric Changes

Make sure `tvStrikeOverlayProfiles` updates whenever `/update` returns. Clear it on ticker/expiry/context changes if needed.

### 8.7 Autoscale Gets Too Wide

The overlay should not affect y-axis autoscale. Do not add strike profile prices to `tvAllLevelPrices`. Only existing key/session/top-OI levels should drive autoscale.

### 8.8 Hover/Crosshair Issues

Set `pointer-events: none` on the overlay. This keeps chart pan/zoom/crosshair/drawing interactions intact.

---

## 9. Suggested Implementation Order

1. Create implementation branch:

   ```bash
   git checkout main
   git pull --ff-only origin main
   git checkout -b codex/strike-axis-overlay-prototype
   ```

2. Add `create_strike_profile_payload()` backend helper.

3. Add `response['strike_profiles']` to `/update`.

4. Add frontend state/localStorage keys.

5. Add CSS and `ensureTVStrikeOverlay()`.

6. Add `drawTVStrikeOverlay()` with hard-coded `gex` first.

7. Wire update response into `tvStrikeOverlayProfiles`.

8. Add scheduling calls on chart data update, pan/zoom, and resize.

9. Add overlay toggle and metric dropdown.

10. Expand from `gex` to the other metrics once GEX alignment is solid.

11. Browser-test against SPY 5-min on a large viewport.

12. Only after the overlay works, consider auto-collapsing the old rail when overlay is enabled.

---

## 10. Verification Checklist

Run app:

```bash
python ezoptionsschwab.py
```

Open:

```text
http://localhost:5001
```

Manual checks:

- App starts without Python traceback.
- SPY loads with the normal price chart.
- Existing strike rail still renders.
- Overlay is disabled by default.
- Enabling overlay creates bars in the blank right-side chart area.
- GEX bars line up with the correct price/strike grid.
- Positive values are green, negative values are red.
- Both positive and negative bars face left.
- Panning/zooming keeps bars aligned.
- Reset/auto-range keeps bars aligned.
- Changing ticker/expiry refreshes bars.
- Changing overlay metric refreshes bars.
- VWAP/SMA/Gamma Flip/EM/wall/session labels remain readable.
- Crosshair and drawing tools still work.
- Existing right overview/levels/scenarios rail still works.
- Existing secondary charts still render.
- No console errors during `/update` ticks.

Useful local grep checks before finishing:

```bash
rg -n "strikeOverlay|tv-strike-overlay|strike_profiles|create_strike_profile_payload" ezoptionsschwab.py
rg -n "ensurePriceChartDom|renderTradingViewPriceChart|subscribeVisibleLogicalRangeChange|scheduleGexPanelSync" ezoptionsschwab.py
```

---

## 11. Non-Goals For First Prototype

Do not spend first-pass time on:

- Perfect final styling.
- Mobile layout.
- Rich hover tooltips.
- Split call/put OI and volume display.
- Removing the old strike rail.
- Replacing Plotly chart builders.
- Refactoring the single-file app.
- New analytics or formula changes.

The first milestone is simple: signed strike bars on the price chart, correctly aligned, safely layered, refreshable, and toggleable.
