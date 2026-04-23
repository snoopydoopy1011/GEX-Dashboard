# GEX Dashboard — TradingView Session Levels + IB Port Plan

**Status:** Implementation, polish, and committed synthetic regression coverage are landed locally on `codex/session-level-colors`; browser/live Schwab validation still needed
**Owner:** Codex
**Created:** 2026-04-22
**Suggested branch:** `codex/session-level-colors`
**Base:** `main`

**Read these first:**
- [`UI_MODERNIZATION_PLAN.md`](UI_MODERNIZATION_PLAN.md)
- [`ANALYTICS_CHART_PHASE2_PLAN.md`](ANALYTICS_CHART_PHASE2_PLAN.md)
- [`RIGHT_RAIL_AND_INDICATOR_CONTROLS_PLAN.md`](RIGHT_RAIL_AND_INDICATOR_CONTROLS_PLAN.md)

This document scopes a port of the user's TradingView "Key Levels + IB" indicator into the existing GEX Dashboard chart. It is intentionally limited to the parts the user still wants after trimming scope in discussion.

---

## Implementation status at top

### What landed in the first pass

- Added backend session-level helpers in `ezoptionsschwab.py`:
  - `normalize_session_level_config()`
  - `get_session_level_candles()`
  - `compute_session_levels()`
- Extended `/update_price` with additive payload keys:
  - `session_levels`
  - `session_levels_meta`
- Added a separate frontend overlay registry:
  - `tvSessionLevelLines`
  - `clearSessionLevels()`
  - `renderSessionLevels()`
- Added a master toolbar pill in `buildTVToolbar()`:
  - `Sess Lvls`
- Added a new drawer section for session-level controls.
- Added save/load persistence through `gatherSettings()` and `applySettings()`.
- Added basic dedupe so session levels do not stack directly on top of existing dealer-flow key levels.
- Wired session-level prices into the chart autoscale flow.

### What landed in the follow-up pass

- Fixed `/update_price` response handling so session-level rendering reads the current dealer-flow key-level cache before dedupe runs.
- Fixed ticker-switch cleanup so key/session overlay caches are cleared together and stale lines do not survive symbol changes.
- Switched the session-level default for `After Hours` from off to on, matching the current product recommendation.
- Validated the pure session-level calculator with a synthetic candle test covering:
  - today / yesterday
  - premarket
  - after-hours fallback
  - opening range
  - IB extensions
  - anchor-date selection

### What landed in the style/editor pass

- Added a dedicated `Levels` toolbar button that opens a `Key Level Styles` modal.
- Added per-level controls for dealer-flow and session levels:
  - Show / hide
  - color
  - line width
  - line style
- Persisted level preferences in both:
  - `localStorage`
  - saved settings via `price_level_prefs`
- Moved dealer-flow key level rendering to the editable preference model instead of hard-coded color/style definitions.
- Changed session-level defaults to direction-aware colors:
  - highs green
  - lows red
  - opens gray / orange dashed
  - `YDC` thick gray
- Added explicit drawer copy that Today and Yesterday are RTH-based.

### What landed in the OR / IB midpoint + cloud pass

- Added `ORM` / Opening Range Mid to the backend session-level payload.
- Added `ORM` to the session-level renderer and `Levels` modal.
- Added drawer controls:
  - `Show ORM (50%)`
  - `Opening Range Cloud`
  - `Initial Balance Cloud`
- Added an SVG session-cloud overlay that fills the price band between:
  - `ORH` and `ORL`
  - `IBH` and `IBL`
- Clouds use the current editable high/low colors and redraw on chart zoom / pan / resize paths.

### What landed in the token color pass

- Switched default price-level colors to resolve from CSS design tokens before rendering.
- Added distinct default tones for session-level families:
  - Today remains directional call / put with warn for open.
  - Yesterday uses muted foreground gray.
  - Premarket uses info / accent blue.
  - After Hours uses warn orange.
  - Opening Range uses accent blue with muted midpoint.
  - Initial Balance remains call / put with warn midpoint.
- Added a narrow migration so persisted session-level colors that still match the previous defaults move to the new family defaults, while edited custom colors are preserved.

### What landed in the Near Open pass

- Added `Near Open` session-level support to the backend calculator and `/update_price` payload:
  - `NOH`
  - `NOL`
- Added drawer controls for:
  - `Near Open (NOH / NOL)`
  - `Near Open Minutes`
- Added per-level style controls for `NOH` / `NOL` in the `Levels` modal.
- Persisted near-open visibility and minute-window settings through the existing save/load path.
- Kept overnight out of scope; this pass only ports the pre-open window immediately preceding the 09:30 ET cash open.

### What landed in the regression-test pass

- Added a committed synthetic regression test file for `compute_session_levels()`.
- Covered:
  - today / yesterday RTH levels
  - premarket
  - near open
  - opening range
  - initial balance and extensions
  - previous-session after-hours fallback
  - same-day after-hours selection
  - explicit `anchor_date` after-hours selection when newer sessions also exist
- Fixed the backend after-hours selection bug for explicit `anchor_date` runs by resolving the AH block from the anchor session's latest candle instead of the global latest candle.

### What is implemented right now

- Today:
  - `TDH`
  - `TDL`
  - `TDO`
- Yesterday:
  - `YDH`
  - `YDL`
  - `YDO`
  - `YDC`
- Near Open:
  - `NOH`
  - `NOL`
- Premarket:
  - `PMH`
  - `PML`
- After Hours:
  - `AHH`
  - `AHL`
- Opening Range:
  - `ORH`
  - `ORL`
  - `ORM`
  - Optional OR cloud between high and low
- Initial Balance:
  - `IBH`
  - `IBL`
  - `IBM`
  - `IBHx2`
  - `IBLx2`
  - `IBHx3`
  - `IBLx3`
  - Optional IB cloud between high and low

### Tricky parts already discovered

- The session-level family must stay separate from dealer-flow `compute_key_levels()`. Mixing them makes scope switching, dedupe, and visual priority harder.
- OR / IB must come from raw 1-minute candles. Using the visible chart timeframe produces incorrect values on 15m / 30m / 60m.
- The chart already has multiple overlay families contributing to autoscale. Session levels need their own price registry and must be folded into the shared autoscale list, or lines can render off-screen after zoom/reset.
- The chart has ticker-scoped overlay caches. Session-level caches must be cleared on ticker change or stale lines can survive symbol switches.
- The `/update_price` handler must update overlay caches before redraw paths run. If key levels are cached after `applyPriceData()`, session-level dedupe can compare against stale dealer-flow levels and render duplicates that should have been suppressed.
- The toolbar pill and drawer state both control the same family. Future edits must keep those two entry points synchronized.
- `After Hours` requires “most recent completed block” logic. During RTH, same-day after-hours does not exist yet, so the fallback must use the previous trading day’s `16:00-20:00` block.
- The current implementation only computes `session_levels` when the family is enabled. If a future session wants right-rail summaries or analytics based on these values even while hidden, move computation out of the enabled gate.
- Lightweight Charts `createPriceLine()` cannot draw range fills, so OR / IB clouds use a separate non-interactive SVG overlay.
- Cloud overlay redraws must remain wired to price-scale / time-scale changes. Current implementation schedules redraws from the same zoom/pan paths used by the historical and drawing overlays.
- Session-level dedupe now compares against rendered dealer-flow prices, not every raw dealer-flow level, so hiding a dealer-flow line can reveal a same-price session line.
- `Near Open` is defined here as the last configurable pre-open window before `09:30 ET`, clipped to the available `04:00-09:30 ET` Schwab extended-hours feed. It is not a broader overnight proxy.
- Persisted session-level color defaults now migrate only when a stored color still matches the old default. Custom edits are intentionally preserved, so future default-color changes need the same narrow migration pattern.

### What is left to do

- Manual browser/live validation remains recommended but is no longer blocking this implementation pass:
  - verify save/load round-trip from the real UI
  - verify ticker switches, Heikin-Ashi toggles, and timeframe changes in-browser
  - verify `Near Open` visibility and value changes across different `Near Open Minutes` settings
  - verify OR / IB cloud positioning on 1m / 5m / 15m / 30m / 60m
  - verify the `Levels` modal round-trips all per-level preferences through save / load
- Decide whether `Today` / `Yesterday` should remain hard-labeled RTH-only or expose user-facing RTH toggles.
- Optional later:
  - IB intermediate levels
  - synthetic overnight
  - per-group style presets
  - cloud opacity controls

---

## 0. Requested outcome

The user wants the dashboard to show a subset of the TradingView script's session-based horizontal levels on the main price chart, with:

- axis labels, not floating labels ahead of the candle
- a quick on/off control near the existing chart indicators
- settings to choose which level groups are shown
- enough configuration to control the important periods and display choices

The user explicitly does **not** want to port:

- VWAP
- moving averages
- custom manual levels
- week / month / quarter / year levels
- merged labels
- proximity filters
- older-level persistence

The user is open to:

- today / yesterday levels
- opening range
- initial balance
- premarket
- after-hours
- possibly overnight, if the data source actually supports it cleanly

---

## 1. Product decision summary

These decisions are locked for the first implementation:

1. **Axis-label rendering only.**
   Use Lightweight Charts `createPriceLine()` with `axisLabelVisible: true`. Do not build candle-forward floating labels in v1.

2. **Treat this as a new overlay family, not part of existing dealer-flow key levels.**
   Keep dealer-flow levels (`call_wall`, `put_wall`, `gamma_flip`, `HVL`, expected move) separate from session levels. Do not overload `compute_key_levels()`.

3. **Use a master pill plus detailed drawer settings.**
   The chart gets one fast toggle pill near the other indicator controls. Fine-grained enable/disable and parameters live in the drawer.

4. **Compute session levels from dedicated raw 1-minute candles.**
   Do not compute opening range or initial balance from the currently selected chart timeframe. That will be wrong on 15m / 30m / 60m.

5. **Overnight is not a v1 default.**
   Schwab price history returns extended-hours bars from 04:00 ET to 20:00 ET, but not true 20:00-04:00 overnight bars. A "true overnight" level cannot be reproduced exactly from this source for equities. Premarket and after-hours are supported. Overnight needs a separate explicit product decision.

---

## 2. Scope

### 2.1 In scope for v1

- Today high / low
- Near Open high / low
- Opening range high / low
- Premarket high / low
- After-hours high / low
- Yesterday high / low
- Yesterday open / close
- Today open
- Initial balance:
  - IBH
  - IBL
  - IBM
  - IB extensions x2 / x3
  - optional intermediate IB levels can be deferred behind a setting

### 2.2 Explicitly out of scope for v1

- VWAP
- moving averages
- custom levels
- week / month / quarter / year O/H/L/C/A
- merged labels
- label background merging
- ATR proximity zones
- proximity-only visibility
- max line length / truncation controls
- older-level persistence
- floating labels ahead of the candle

### 2.3 Recommended defer

- Overnight high / low

This still needs an explicit product definition that makes sense for the Schwab candle feed.

---

## 3. Current-state reuse inventory

Use grep anchors, not line numbers.

### Existing backend / route anchors

- `get_price_history`
- `filter_market_hours`
- `prepare_price_chart_data`
- `/update_price`
- `/save_settings`
- `/load_settings`

### Existing frontend anchors

- `TV_INDICATOR_DEFS`
- `buildTVToolbar`
- `gatherSettings`
- `applySettings`
- `getChartVisibility`
- `setAllChartVisibility`
- `renderChartVisibilitySection`
- `renderKeyLevels`
- `tvCandleSeries.createPriceLine`

### Existing behavior worth reusing

- The price chart already renders multiple overlay families via `createPriceLine()`.
- Indicator toggles already exist in the chart toolbar.
- Drawer-backed settings persistence already exists through `gatherSettings()` / `applySettings()` and `/save_settings`.
- Overlay visibility is already split between chart families and line overlays.

### Existing behavior that should stay separate

- `compute_key_levels()` is dealer-flow math. Do not mix session-range logic into it.
- `renderKeyLevels()` should remain responsible for dealer-flow overlays only.

---

## 4. Data-source constraints

This section is critical for whoever implements this later.

### 4.1 What the current chart feed includes

`filter_market_hours()` keeps candles from:

- `04:00 ET` through `20:00 ET`

That means the app already has:

- premarket
- RTH
- after-hours

It does **not** have:

- true 20:00-04:00 overnight bars

### 4.2 Why this matters

The Pine script can reason over session segments in a way that assumes a richer ETH session model. For this app:

- **Premarket** can be ported directly.
- **After-hours** can be ported directly.
- **Overnight** cannot be ported 1:1 for equities from the current Schwab feed.

### 4.3 Locked recommendation

For v1:

- implement `Premarket`
- implement `After Hours`
- **do not implement `Overnight` by default**

If the team later wants an overnight approximation, define it explicitly as:

- previous day's `16:00-20:00` after-hours plus current day's `04:00-09:30` premarket

If that approximation is adopted, label it clearly as synthetic ETH behavior, not true overnight.

### 4.4 Raw-candle requirement

Session levels must be computed from raw 1-minute candles, not from:

- Heikin-Ashi output
- aggregated 5m / 15m / 30m / 60m display candles

Otherwise:

- a 15-minute opening range can drift on higher timeframes
- IB windows can be wrong on 30m / 60m
- session highs/lows can be inaccurate around segment boundaries

---

## 5. Recommended architecture

## 5.1 New backend function

Add a new backend function, separate from dealer-flow logic:

```python
def compute_session_levels(
    candles_1m,
    *,
    anchor_date=None,
    timezone='US/Eastern',
    config=None,
):
    ...
```

Recommended return shape:

```python
{
  "today_high": {"price": 711.45, "label": "TDH", "group": "today"},
  "today_low": {"price": 707.14, "label": "TDL", "group": "today"},
  "today_open": {"price": 708.92, "label": "TDO", "group": "today"},

  "yesterday_high": {"price": 711.27, "label": "YDH", "group": "yesterday"},
  "yesterday_low": {"price": 706.22, "label": "YDL", "group": "yesterday"},
  "yesterday_open": {"price": 707.91, "label": "YDO", "group": "yesterday"},
  "yesterday_close": {"price": 710.88, "label": "YDC", "group": "yesterday"},

  "premarket_high": {"price": 709.74, "label": "PMH", "group": "premarket"},
  "premarket_low": {"price": 707.14, "label": "PML", "group": "premarket"},

  "after_hours_high": {"price": 710.02, "label": "AHH", "group": "after_hours"},
  "after_hours_low": {"price": 708.66, "label": "AHL", "group": "after_hours"},

  "opening_range_high": {"price": 709.61, "label": "ORH", "group": "opening_range"},
  "opening_range_low": {"price": 709.22, "label": "ORL", "group": "opening_range"},

  "ib_high": {"price": 710.70, "label": "IBH", "group": "initial_balance"},
  "ib_low": {"price": 708.23, "label": "IBL", "group": "initial_balance"},
  "ib_mid": {"price": 709.47, "label": "IBM", "group": "initial_balance"},
  "ib_high_x2": {"price": 713.17, "label": "IBHx2", "group": "initial_balance_ext"},
  "ib_low_x2": {"price": 705.76, "label": "IBLx2", "group": "initial_balance_ext"},
  "ib_high_x3": {"price": 715.64, "label": "IBHx3", "group": "initial_balance_ext"},
  "ib_low_x3": {"price": 703.29, "label": "IBLx3", "group": "initial_balance_ext"},

  "meta": {
    "anchor_date": "2026-04-22",
    "timezone": "US/Eastern",
    "overnight_supported": false,
    "source_frequency_minutes": 1
  }
}
```

### 5.2 New raw-candle helper

Add a helper dedicated to session-level calculations:

```python
def get_session_level_candles(ticker, lookback_days=5):
    ...
```

Requirements:

- always request `frequency=1`
- always request `needExtendedHoursData=True`
- keep the current 04:00-20:00 ET filter behavior
- return enough history for:
  - current session
  - previous RTH session
  - previous after-hours block

Do not reuse aggregated display candles from `get_price_history(timeframe=...)`.

### 5.3 Route integration

Extend `/update_price` to compute and return:

```json
{
  "session_levels": { ... },
  "session_levels_meta": {
    "anchor_date": "2026-04-22",
    "overnight_supported": false
  }
}
```

Keep this additive. Do not alter existing keys like:

- `key_levels`
- `key_levels_0dte`
- `trader_stats`
- `stats_0dte`

### 5.4 New frontend registry

Add a separate frontend registry for these lines:

- `tvSessionLevelLines`
- `clearSessionLevels()`
- `renderSessionLevels(levels, settings)`

Do not reuse `tvKeyLevelLines`, because that registry is for dealer-flow overlays.

---

## 6. Level definitions

All definitions below use Eastern Time and the latest visible trading day as the anchor session.

### 6.1 Anchor session

Define `anchor_date` as:

- the date of the latest candle in the 1-minute filtered candle set

### 6.2 Today

Use `anchor_date` RTH:

- session: `09:30-16:00`
- `Today High`: highest high in that range
- `Today Low`: lowest low in that range
- `Today Open`: first bar open at or after `09:30`

Optional setting:

- `today_rth_only: true` by default

### 6.3 Yesterday

Use the previous trading date before `anchor_date`:

- `Yesterday High`: previous RTH session high
- `Yesterday Low`: previous RTH session low
- `Yesterday Open`: previous RTH open
- `Yesterday Close`: previous RTH close

Optional setting:

- `yesterday_rth_only: true` by default

### 6.4 Premarket

Use `anchor_date`:

- session: `04:00-09:30`
- `PMH`: highest high
- `PML`: lowest low

### 6.5 Near Open

Use `anchor_date`:

- session: last `N` minutes before `09:30 ET`
- default `N = 60`
- clamp the start to `04:00 ET` so the window stays inside the available Schwab extended-hours feed
- `NOH`: highest high in that window
- `NOL`: lowest low in that window

### 6.6 After Hours

Use the most recent completed after-hours block.

Rules:

- if latest candle is between `16:00-20:00` on `anchor_date`, use that active same-day AH block
- otherwise use the previous trading date's `16:00-20:00` block

Return:

- `AHH`
- `AHL`

This prevents the level from disappearing during RTH just because the current day's AH session has not happened yet.

### 6.7 Opening Range

Default:

- start: `09:30`
- length: `15 minutes`

Compute from dedicated 1-minute bars:

- `ORH`: max high in `09:30 <= t < 09:45`
- `ORL`: min low in `09:30 <= t < 09:45`

### 6.8 Initial Balance

Default:

- start: `09:30`
- end: `10:30`

Compute from dedicated 1-minute bars:

- `IBH`: max high in the window
- `IBL`: min low in the window
- `IBM`: midpoint `(IBH + IBL) / 2`
- `IB range = IBH - IBL`
- `IBHx2 = IBH + 1 * range`
- `IBLx2 = IBL - 1 * range`
- `IBHx3 = IBH + 2 * range`
- `IBLx3 = IBL - 2 * range`

Optional later:

- `IBM+` and `IBM-` intermediate levels

### 6.9 Overnight

Not in v1.

If later approved, define it explicitly before implementation. Do not silently improvise.

---

## 7. UI / UX plan

### 7.1 Toolbar pill

Add one master toggle beside the existing indicator pills in `buildTVToolbar()`:

- key: `session_levels`
- label: `Sess Lvls`
- title: `Session levels and Initial Balance`

Behavior:

- single click toggles the whole overlay family on/off
- no sub-settings in the toolbar itself

### 7.2 Drawer section

Add a new drawer section:

`Session Levels`

Recommended controls:

- `Show Session Levels` master checkbox
- `Today`
- `Yesterday`
- `Near Open`
- `Near Open Minutes` default `60`
- `Premarket`
- `After Hours`
- `Opening Range`
- `Initial Balance`
- `Show IBM (50%)`
- `Show IB Extensions`
- `Show IB Intermediate Levels` default off
- `Opening Range Minutes` default `15`
- `IB Start` default `09:30`
- `IB End` default `10:30`
- `Abbreviate Labels` default on
- `Append Price To Labels` default on

Optional but not required in first pass:

- per-group color pickers
- per-group line-style selectors

### 7.3 Why drawer instead of modal

The chart already uses:

- toolbar pills for fast visibility
- drawer for persistent configuration

That is a better fit than stuffing this into the indicator style modal, which is currently line-style oriented and would become too crowded.

### 7.4 Label format

Default label text:

- `TDH 711.45`
- `YDL 706.22`
- `ORH 709.61`
- `IBH 710.70`

No floating labels in front of candles.

---

## 8. Rendering rules

### 8.1 Style hierarchy

Session levels must remain visually secondary to dealer-flow levels.

Recommended defaults:

- Today / Yesterday: width `1`, solid
- Premarket / After Hours: width `1`, dotted
- Opening Range: width `1`, solid
- IBH / IBL: width `2`, solid
- IBM: width `1`, dotted
- IB extensions: width `1`, dotted

### 8.2 Color defaults

Use existing tokens only. No new neon hex palette.

Recommended defaults:

- Today / Yesterday / Premarket / After Hours / Opening Range: separate token-backed defaults per family; do not collapse everything into `--warn`
- IBH: `--call`
- IBL: `--put`
- IBM: `--warn`
- IB extensions: same family with reduced alpha

Current implementation note:

- IBH / IBL / IB extensions already split into call/put-family colors.
- Other session-level families now use token-backed defaults with separate tones for Today, Yesterday, Near Open, Premarket, After Hours, and Opening Range.

### 8.3 Collision rules

Do not implement merged labels in v1, but do avoid obvious duplicate clutter.

Recommended dedupe rules:

1. Existing dealer-flow levels win over session levels if prices match within one tick.
2. Within session levels, higher priority wins:
   - IB
   - Opening Range
   - Today / Yesterday
   - Premarket / After Hours
3. Skip rendering a lower-priority duplicate if it lands on the same price.

This can be done before calling `createPriceLine()`.

---

## 9. Persistence

Extend `gatherSettings()` and `applySettings()` with a new `session_levels` block:

```json
{
  "session_levels": {
    "enabled": true,
    "today": true,
    "yesterday": true,
    "near_open": false,
    "premarket": true,
    "after_hours": true,
    "opening_range": false,
    "initial_balance": true,
    "show_ib_mid": true,
    "show_ib_extensions": true,
    "near_open_minutes": 60,
    "opening_range_minutes": 15,
    "ib_start": "09:30",
    "ib_end": "10:30",
    "abbreviate_labels": true,
    "append_price": true
  }
}
```

Requirements:

- saving settings persists this block to `settings.json`
- loading settings restores it
- toolbar pill state and drawer state stay in sync

---

## 10. Implementation stages

Each stage should leave the app working.

### Stage 1 — Backend raw-candle source + pure calculator

Status: completed in first pass

- add `get_session_level_candles()`
- add `compute_session_levels()`
- no frontend wiring yet

### Stage 2 — `/update_price` payload + frontend line renderer

Status: completed in first pass

- add `session_levels` to `/update_price`
- add `tvSessionLevelLines`
- add `renderSessionLevels()`
- render lines when data is present

### Stage 3 — Toolbar pill + drawer settings

Status: completed in first pass

- add `Sess Lvls` toggle in `buildTVToolbar()`
- add drawer section
- add persistence through `gatherSettings()` / `applySettings()`

### Stage 4 — Collision handling + style polish

Status: completed pending browser/live validation

- dedupe against dealer-flow levels
- clear overlay caches on ticker change
- fix redraw ordering so session-level dedupe sees the current dealer-flow cache
- refine colors, labels, widths, and default visibility
- distinct token-backed defaults for each session-level family

### Stage 5 — Optional follow-up

Status: partially completed

- Near Open implemented
- revisit synthetic overnight only if explicitly approved

---

## 11. Verification checklist

### Functional

1. `Sess Lvls` pill toggles the entire overlay family on and off.
2. Drawer toggles hide only the selected subgroups.
3. OR and IB values remain correct on:
   - 1m
   - 5m
   - 15m
   - 30m
   - 60m
4. Switching Heikin-Ashi on does not change session-level prices.
5. `Near Open` levels reflect only the configured final pre-open window before `09:30 ET`.
6. Premarket levels appear before 09:30 ET.
7. Yesterday and prior after-hours levels remain visible during the next RTH session.
8. Save / load settings round-trip restores all session-level options.

### Visual

1. Axis labels appear on the price scale, not ahead of candles.
2. Session levels do not overpower call wall / put wall / gamma flip lines.
3. Duplicate labels at the same price are suppressed by the priority rules.
4. No console errors on repeated ticker switches.

### Data integrity

1. OR and IB use 1-minute source candles even when display timeframe is higher.
2. No overnight level is shown unless the product decision changes.
3. Premarket and after-hours definitions match the documented time ranges.

---

## 12. Open questions for the implementation session

These are the only decisions that should still be revisited when work starts:

1. Should `Today` and `Yesterday` keep optional `RTH` checkboxes, or should v1 hard-lock them to RTH?
   Recommendation: hard-lock to RTH in v1 unless there is a strong user need.

2. Should `After Hours` be shown by default?
   Recommendation: yes, if the master family is enabled, because it is part of the requested TradingView parity.

3. Should `Near Open` stay default-off in v1?
   Recommendation: yes. It is implemented now, but keeping it opt-in avoids adding more pre-open clutter by default.

4. Should per-group colors be configurable in v1?
   Recommendation: no. Use sensible defaults first.

5. Should the master pill read `Sess Lvls`, `Key Lvls`, or `IB/Levels`?
   Recommendation: `Sess Lvls` because it avoids confusion with existing dealer-flow key levels.

---

## 13. Final recommendation

Implement this as a **new session-level overlay family** with:

- dedicated 1-minute backend calculations
- a master toolbar pill
- drawer-controlled subgroup visibility
- axis labels only
- no overnight in v1

That gives the user the useful TradingView parity items without tangling them into the existing dealer-flow overlay stack or recreating Pine-specific behaviors that do not map cleanly to this app.
