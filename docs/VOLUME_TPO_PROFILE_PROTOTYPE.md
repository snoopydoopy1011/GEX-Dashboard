# Volume + TPO Profile Prototype

Branch: `codex/volume-tpo-profile-prototype`

## Idea

This branch prototypes two price-chart overlays built from the historical minute candle stream:

- A right-axis volume profile that can be toggled on/off and configured as current session, composite last N sessions, or visible range.
- A fixed-range volume profile drawing tool that lets the user click a start and end time window on the chart.
- A first-pass TPO market profile using 30-minute periods and 1-minute candles for the current trading session.

The important modeling caveat is that Schwab historical candles provide OHLCV buckets, not true volume-at-price. The profile therefore estimates volume-at-price by allocating each candle's total volume across price bins. The prototype supports uniform and triangular allocation, with triangular centered around `(high + low + close) / 3`.

## What Was Tried

- Added server-side profile builders near the price chart payload path:
  - `build_volume_profile_payload()`
  - `build_tpo_profile_payload()`
  - `_build_modeled_volume_bins()`
- Extended `/update_price` and `prepare_price_chart_data()` to include `volume_profile` and `tpo_profile` payloads.
- Preserved candle volume inside the Lightweight Charts candle payload so client-side visible-range and fixed-range profiles can rebuild without another server call.
- Added drawer controls under `Volume / Market Profile`:
  - Right-axis volume profile toggle
  - Range mode: composite days, current session, visible range, custom date range
  - Composite day count
  - Custom start/end dates for right-axis VP
  - Right-axis VP color
  - Volume profile bin size
  - Fixed-range VP default side: left or right
  - Allocation method
  - TPO toggle and bin size
- Added an SVG profile overlay layer that draws:
  - Right-edge modeled volume profile bars
  - VP point-of-control marker
  - TPO count bars and compact TPO letter strings
  - Fixed-range volume profile bars
- Extended the existing drawing system with a `VP Range` tool, reusing its persistence, preview, selection, hide/show, undo, and clear behavior.

## Issues Encountered

- Live Schwab candle validation was blocked by `401 Unauthorized` from `get_price_history()`. The UI server loads, but real profile rendering needs a refreshed Schwab token.
- The prototype server was run on `http://127.0.0.1:5012/` to avoid disturbing the normal port.
- The existing file still emits the pre-existing Python template warning:
  - `SyntaxWarning: invalid escape sequence '\('`
- This is visually functional prototype code, not polished chart architecture. It deliberately stays inside the single-file app and vanilla JS/SVG constraints.
- Follow-up browser testing found that fixed-range VP could draw the selection box without visible profile bars. The SVG rows were being created, but their coordinates could land offscreen because fixed VP normalization discarded the clicked logical candle anchors after storing timestamps. Fixed VP now preserves `l1`/`l2` so the histogram rebuild uses the selected candle slice directly.
- The first pass also wired redraw/update listeners for the VP/TPO inputs, but not the enable checkboxes. The `vp_enabled` and `tpo_enabled` controls now trigger the same refresh path as the other profile settings.

## Latest Follow-Up

Implemented after initial prototype review:

- Added a `Custom Date Range` option to the right-axis volume profile range selector.
  - The drawer now shows start/end date inputs only when custom mode is selected.
  - The server-side profile filter supports `mode: custom` and swaps reversed start/end dates defensively.
  - Custom range settings persist through the existing settings save/load path.
- Added a right-axis VP color picker.
  - The selected color drives the right-edge VP bars and the VP POC line.
  - It persists as part of `volume_profile`.
- Added fixed-range VP side controls.
  - The drawer-level `Fixed VP Side` setting controls the default side for newly drawn VP ranges.
  - Each selected fixed VP drawing also gets a `Profile Side` override in the drawing editor with `Use Setting`, `Right`, and `Left`.
  - Existing drawings without an override continue to follow the global setting.
- Fixed the drawing editor title for selected fixed VP drawings so it shows `Fixed VP` instead of falling through to `Text Label`.
- Added profile value-area metadata and overlay levels.
  - Volume profiles and TPO profiles now return/draw 70% VAH/VAL plus POC from the existing modeled rows.
  - Fixed-range VP rebuilds its own value area client-side when anchors or per-drawing settings change.
- Added app-native VP/TPO hover details.
  - Hover rows expose price, modeled volume or TPO count, percent of max, percent of total where applicable, and value-area membership.
  - TPO hover includes recent session letter groups when using multi-session mode.
- Added selected fixed-VP anchor editing.
  - Selected fixed VP drawings now show draggable start/end handles.
  - Dragging an anchor updates the saved logical/time anchor and immediately rebuilds the profile.
- Added per-drawing fixed-VP controls.
  - The drawing editor now exposes `VP Bin` and `Allocation` for selected fixed VP drawings.
  - Existing drawings retain their saved overrides; new drawings still inherit current drawer defaults at creation.
- Added TPO styling and session controls.
  - Drawer controls now include TPO session mode, composite days, custom date range, color, and opacity.
  - TPO payloads can aggregate by latest session, composite days, or custom date range while preserving per-session letter buckets for tooltips.
- Tightened the VP/TPO interaction polish after browser review.
  - Profile tooltips are now constrained to normal tooltip dimensions instead of stretching over the chart.
  - Clicking a right-axis VP or TPO row opens the settings drawer and scrolls directly to the relevant profile controls.
  - Fixed-range VP now prefers saved timestamp anchors when rebuilding its candle slice, with logical indexes as fallback. This fixes cases where the selection box renders but the profile rows are empty over prior-session spans.
- Made value area and POC visually distinct.
  - Outside-value-area bars render gray.
  - Inside-value-area bars render in the selected profile/drawing color.
  - POC bars and POC lines render magenta (`var(--rvol-hot)`) for right-axis VP, fixed-range VP, and TPO.
- Polished right-edge TPO layout after live browser review.
  - TPO letters now occupy the same right-profile column where the first bar-based prototype rendered its rows.
  - The displayed letter order is preserved while the profile is right-anchored, so the widest/POC rows project left toward the candles.
  - TPO VAH/POC/VAL guide lines now stop before the left edge of the widest visible TPO letter row instead of running underneath the letters.
  - TPO VAH/POC/VAL text labels are hidden by default and can be restored with `#tpo_show_level_labels`.
  - The compact top TPO summary panel can now be shown/hidden with `#tpo_show_summary`.

Tricky parts:

- The fixed VP bars live in the separate profile SVG overlay, while the selected drawing box and editor live in the drawing overlay path. Side changes therefore need to schedule both overlay redraw paths in a few places.
- The global side selector should not rewrite older saved drawings, so the drawing definition keeps an empty `profileSide` as "inherit from setting".
- Fixed VP has two possible anchor models now: saved timestamps and saved logical candle positions. Timestamp-based slicing is more reliable after reloads and across multi-session windows, but logical anchors are still useful as a fallback while drawing/previewing.
- The profile SVG overlay still keeps `pointer-events: none` so chart drag/zoom behavior remains intact. Row hover/click uses a separate client-side hit map populated during SVG drawing.
- The local `5002` Flask server may keep serving an old in-memory template after file edits. Restart the listener if new drawer/editor controls do not appear after refresh.
- SVG-native hover titles were considered, but the profile overlay intentionally uses `pointer-events: none` so chart interactions pass through. A real tooltip should be implemented as an app-native hover layer rather than relying on SVG `<title>`.
- TPO line clipping is based on the same compact/truncated display calculation used by `appendTpoLetterRows()`. This keeps VAH/POC/VAL lines out of the letter text even when compact-label mode changes the rendered string to `(count)` or when long rows are truncated with `+`.
- The summary and level-label controls are client-only display settings. They are intentionally threaded through `getTpoProfileSettingsFromDom()`, the redraw listener list, and `loadSettings()` so they persist and redraw immediately without changing server-side TPO math.

## Validation Done

- `python3 -m py_compile ezoptionsschwab.py`
- Synthetic candle smoke test for:
  - Volume profile bin generation
  - TPO row generation
  - `prepare_price_chart_data()` carrying candle volume plus profile payloads
- Follow-up synthetic smoke for VP/TPO value-area fields and session-aware TPO rows.
- `git diff --check`
- Local Flask server boot on prototype port `5012`.
- Follow-up browser smoke on `http://127.0.0.1:5012/`:
  - Confirmed TPO letters face the candles while staying in the right-profile column.
  - Confirmed TPO VAH/POC/VAL guide lines stop before the widest visible letter row.
  - Confirmed TPO level labels are hidden by default and return when `Show TPO Level Labels` is enabled.
  - Confirmed `Show TPO Summary` can hide/show the compact top TPO stats panel.
- Follow-up smoke on `http://127.0.0.1:5002/`:
  - `/update_price` returned `939` candles, `70` volume-profile bins, and `10` TPO rows for SPY.
  - Browser test confirmed a newly drawn fixed VP over real candles renders visible histogram bars.
  - Drawing a VP range over empty/future chart space still produces no bars, which is expected because there are no candles in that selected interval.

## Left To Do

- Continue tuning overlay spacing so right-axis VP, TPO letters, price labels, and existing strike overlays do not crowd each other in every viewport/zoom state.
- Continue browser polish on VP/TPO hover hit areas, profile-click settings behavior, and label placement with real Schwab candles.
- Decide whether these should remain chart overlays, become formal indicators, or move into a dedicated chart settings menu once the interaction model is settled.

## Proposed TPO Expansion

The next TPO pass should move the current first-pass market profile closer to a TradingView-style `TPO Bars Back, Fixed Range and Anchored` indicator, while keeping the app's existing constraints: no PineScript, no JS framework, no analytical-formula churn outside the profile-specific TPO model, and no split away from the single-file Flask/vanilla JS structure.

The goal is to make TPO useful for fast composite market-profile work without the manual session split/merge workflow required by TradingView's native TPO tools. A user should be able to build a time-based profile for recent bars, a fixed historical window, or a live anchored period, then immediately see POC, VAH, VAL, value-area membership, single prints, and readable labels.

Useful source concept:

- TradingView-style indicator behavior:
  - Time-based analysis: count how often price crosses each price level by time block, not by volume.
  - Configurable block time: 15m/30m/1h/4h-style buckets instead of hardcoded 30-minute letters.
  - Multiple operating modes: bars back, fixed range, and anchored live profile.
  - Automatic composite generation across arbitrary date ranges without manual session merging.
  - Automatic POC, VAH, VAL, configurable value-area percentage, and single-print detection.
  - Visual distinction between value-area rows, outlying rows, POC, VAH/VAL, and single prints.

Current implementation already covers part of this:

- TPO enable/disable drawer control.
- Current session, composite days, custom date range, bars-back, and anchor modes.
- Server-side TPO row generation in `build_tpo_profile_payload()`.
- Configurable 15/30/60/240-minute period buckets.
- POC, VAH, VAL, configurable value-area metadata, single-print metadata, and summary metadata.
- Right-edge TPO bars, compact letters, optional single-print lines/boxes, summary panel, hover details, and profile-click settings behavior.

### TPO Expansion Todos

Status legend: `[x]` shipped on `codex/volume-tpo-profile-prototype` in Phase 1, `[ ]` still open. See "TPO Expansion — Phase 1 Implementation" below for what shipped.

- [x] Add TPO operating modes:
  - [x] `Bars Back`: analyze the last N candles/bars (clamped 10-500, default 200).
  - [x] `Anchor`: analyze from a selected datetime through the latest candle.
  - [x] Keep existing `Current Session`, `Composite Days`, and `Custom Date Range` modes.
- [x] Add configurable TPO block time.
  - [x] Replaced the hardcoded 30-minute period with a `block_minutes` setting.
  - [x] Options: 15, 30, 60, 240. Invalid values fall back to 30.
  - [x] Per-price counts now use `(session_key, block_index)` tuples so multi-session composites stay correct.
- [x] Add configurable value-area percentage.
  - [x] Bounded 50-95, default 70.
  - [x] Threaded through `_profile_value_area()` (new `target` parameter), `build_tpo_profile_payload()`, client settings, save/load, and tooltip text.
- [x] Add single-print detection (toggle-gated via `#tpo_show_single_prints`).
  - [x] Per-row `is_single_print` flag and a dedicated `single_prints` list on the payload.
  - [x] Drawn as dashed horizontal lines using a new `--tpo-single` CSS token (purple, no neon literal).
  - [x] Single-print status surfaced in TPO hover.
  - [x] Boxes display option (`#tpo_single_print_boxes`) adds a subtle filled band behind the dashed single-print bounds.
- [x] Improve dense TPO label handling (toggle-gated via `#tpo_compact_labels`, default on).
  - [x] When row pixel height is below threshold the letter string is replaced with `(count)`.
  - [x] Avoid collisions between TPO letters and TPO VAH/POC/VAL labels/guide lines by default-hiding level labels and clipping guide lines before the widest letter row.
  - [ ] Continue tuning collisions with VP labels, price labels, and strike overlays across zoom/viewport states.
- [x] Add fixed-range TPO drawing.
  - [x] Added a `TPO Range` drawing tool.
  - [x] Reuses the fixed VP anchor model: saved timestamp anchors first, logical indexes as fallback.
  - [x] The selected drawing editor exposes TPO-specific bin/block/value-area/single-print/IB settings only for TPO drawings.
- [x] Add anchored TPO drawing.
  - [x] A chart anchor creates a live profile from that timestamp to the latest candle.
  - [x] Shares the fixed-range TPO implementation, with the end anchor implicitly tracking the latest candle.
- [x] Add initial-balance levels for TPO drawings.
  - [x] Computes IB high/low/mid from the first configurable N minutes of the selected TPO range.
  - [x] Draws IBH/IBL/IBM inside fixed-range and anchored TPO drawings when enabled.
- [x] TPO summary metadata returned on the payload (`total_tpo`, `period_count`, `single_print_count`, `price_high`, `price_low`, `session_count`).
  - [x] Surfaced in a compact chart overlay summary panel when right-edge TPO is enabled.
  - [x] Added `#tpo_show_summary` so the compact summary panel can be hidden when it competes with chart space.

### Suggested Implementation Order

1. Expand right-edge TPO first: bars back, anchor, configurable block time, configurable value-area percentage.
2. Add single prints and dense-label handling.
3. Browser-test with real Schwab candles and tune spacing/hit areas.
4. Add fixed-range TPO drawing if the right-edge interaction model feels good.
5. Add anchored TPO drawing and optional initial-balance levels.

Primary anchors:

- Python: `build_tpo_profile_payload()`, `_filter_profile_candles()`, `_profile_value_area()`.
- HTML controls: `#tpo_enabled`, `#tpo_bin_size`, `#tpo_mode`, `#tpo_days`, `#tpo_start_date`, `#tpo_end_date`, `#tpo_color`, `#tpo_opacity`.
- JS settings/rendering: `getTpoProfileSettingsFromDom()`, `syncTpoProfileSettingsVisibility()`, `drawTVProfileOverlay()`, `appendProfileRows()`, `appendProfileLevelLine()`, `formatProfileTooltip()`.
- Settings persistence: existing `volume_profile` / `tpo_profile` save-load path near `saveSettings()` and `loadSettings()` handling.

## TPO Expansion — Phase 1 Implementation

Implemented on `codex/volume-tpo-profile-prototype`. Covers steps 1–2 of the suggested order. Default behavior is unchanged: every new feature is off or set to its current value unless the user toggles it, so the existing first-pass simple TPO still works as the baseline (important since Schwab/TOS does not provide tick-by-tick candles and a simple TPO is sometimes preferable).

### Latest visual review

Reviewed the right-edge TPO profile with `Current Session` and default settings against live chart output. The default view is now usable enough to ship as the next prototype checkpoint:

- TPO bars are readable and do not dominate the candle chart.
- Value-area rows, out-of-value rows, and POC distinction are visible at a glance.
- Letter strings remain legible for the current-session density shown in review.
- The compact summary panel is useful without taking much chart space.
- Single-print count and session/range metadata are now available without requiring hover.

The remaining concern is layout polish, not core behavior: when TPO, VP, price labels, moving-average tags, and key-level labels are all enabled, the right edge can still become crowded. That should be handled as a follow-up spacing/label-priority pass rather than blocking this checkpoint.

### What was done

Server-side (`ezoptionsschwab.py`):

- Extended `build_tpo_profile_payload()` to accept the following settings, all backward-compatible:
  - `mode`: now also `bars_back` (last N candles) and `anchor` (from a chosen datetime to latest), in addition to the existing `session`, `days`, `custom`.
  - `bars_back`: integer, clamped 10–500, default 200.
  - `anchor_datetime`: parsed against `%Y-%m-%dT%H:%M`, `%Y-%m-%dT%H:%M:%S`, `%Y-%m-%d %H:%M`, `%Y-%m-%d`. Localizes to the chart timezone. Falls back to current session if unparseable.
  - `block_minutes`: 15, 30, 60, or 240. Replaces the hardcoded 30-minute period split. Invalid values fall back to 30.
  - `value_area_pct`: float, clamped 50–95, default 70. Threaded into `_profile_value_area()` via a new `target` parameter.
  - `show_single_prints`: bool, default false. When on, populates a `single_prints` list and per-row `is_single_print` flag.
- Updated `_profile_value_area()` to accept `target` (with safe clamping). VP still calls it with the default 70%, so VP behavior is unchanged.
- TPO row count now uses `(session_key, block_index)` tuples instead of just letter sets, so configurable `block_minutes` produces correct counts even across multi-session composites.
- New `summary` block on the payload: `total_tpo`, `period_count`, `single_print_count`, `price_high`, `price_low`, `session_count`.

Drawer controls (HTML, near the existing TPO section):

- `#tpo_mode` select extended with `bars_back` and `anchor` options.
- New rows: `#tpo_bars_back_row`, `#tpo_anchor_row` (datetime-local), `#tpo_block_minutes` select, `#tpo_value_area_pct`, `#tpo_show_single_prints` checkbox, `#tpo_single_print_boxes` checkbox, `#tpo_compact_labels` checkbox.

Client-side JS:

- `getTpoProfileSettingsFromDom()` reads all new fields and clamps numeric inputs.
- `syncTpoProfileSettingsVisibility()` shows/hides the bars-back and anchor rows based on mode (matches the existing days/custom logic).
- The redraw-listener id list (~line 16000) and `loadSettings()` TPO branch were both extended so the new controls trigger redraws and persist across reloads.
- `drawTVProfileOverlay()` now draws single-print rows as a pair of dashed horizontal lines plus a small `SP` label, using a new `--tpo-single` CSS token (purple, `#A855F7`, no neon literal).
- `#tpo_single_print_boxes` optionally fills each single-print row with a subtle `--tpo-single` band behind the dashed bounds.
- `updateTpoProfileSummary()` shows the payload `summary` block as a compact chart overlay with total TPO, periods, single prints, sessions, and price range.
- Compact-label mode: when row pixel height is below ~9px the letter string is replaced with `(count)`. Toggleable via `#tpo_compact_labels` (default on) — turning it off reverts to the original letter-only behavior.
- Follow-up visual polish:
  - TPO letters are right-anchored within the right-profile column so the profile shape faces the candle chart.
  - VAH/POC/VAL guide lines stop before the left edge of the widest displayed TPO text row.
  - `#tpo_show_level_labels` restores TPO VAH/POC/VAL text labels when desired; default is off.
  - `#tpo_show_summary` controls the compact top TPO summary panel; default is on.
- `formatProfileTooltip()` now reflects the configured value-area percent and shows a `Single Print: Yes` row when applicable. Value-area percent is plumbed through `appendProfileRows` -> hover record so each profile can tooltip its own VA%.

Settings: the `tpo_profile` save/load path persists `bars_back`, `anchor_datetime`, `block_minutes`, `value_area_pct`, `show_single_prints`, `single_print_boxes`, `compact_labels`, `show_level_labels`, and `show_summary`. Existing saved settings without these fields fall back to the original defaults.

### Tricky parts

- **TPO count needs tuples, not letter sets.** With a configurable `block_minutes`, two different blocks can map to the same letter once block_minutes != 30 spreads sessions across the alphabet. The implementation now keeps a per-price `set` of `(session_key, block_index)` keys for the count and only uses `letters` for display, which keeps single-print detection correct (`count == 1` means "exactly one block").
- **Value-area threading.** `_profile_value_area` is shared with VP. The default `target=0.70` keeps VP untouched, while TPO passes its configured percentage. Adding a sanity clamp inside the helper avoids silent breakage if a future caller passes garbage.
- **Anchor parsing.** `<input type="datetime-local">` returns local time without a timezone. The server localizes to the chart's `pytz` zone (US/Eastern) before converting to ms, so anchor times match the displayed candles.
- **Compact labels and existing letter slicing.** Old behavior used `String(row.letters).slice(0, 14)` regardless of row height. The new code only swaps to `(count)` when the row is genuinely too narrow to read a letter. With `tpo_compact_labels` off, behavior is the original 14-char slice.
- **Guide-line clipping uses rendered text metrics.** The VAH/POC/VAL guide lines need to stop before the letters, but the letter width changes when compact labels or truncation are active. `getTpoLetterDisplayMetrics()` mirrors the actual row-display logic, then the level-line endpoint is derived from the widest rendered row.
- **Default-off discipline.** The "baseline simple TPO" case is preserved by ensuring every new branch reads `false`/default from the DOM when the corresponding control is missing or unchecked. Important for the first browser load before settings have been saved.

### Validation

- `python3 -m py_compile ezoptionsschwab.py` (only the pre-existing unrelated `\(` escape-sequence warning remains).
- Synthetic candle smoke covering: baseline preserved, `bars_back` mode, `anchor` mode, `block_minutes=60` with `value_area_pct=80` and `show_single_prints=True`, `enabled: False` short-circuit, invalid `block_minutes` fallback, and VP value-area untouched. All passing.
- Render smoke on `http://127.0.0.1:5012/` confirmed the updated template loads after the summary/box controls.
- Visual review against real current-session candles confirmed the default TPO view is readable enough for the prototype checkpoint.
- Follow-up visual smoke on `http://127.0.0.1:5012/` confirmed:
  - TPO rows face the candle chart from the right-profile column.
  - VAH/POC/VAL guide lines stop before the letter text.
  - Level labels are hidden by default and return when `Show TPO Level Labels` is enabled.
  - The compact TPO summary panel can be hidden/shown with `Show TPO Summary`.

## TPO Expansion — Visual Readability Pass

Implemented after current-session screenshot review:

- Right-edge TPO now renders as a market-profile letter column instead of histogram bars with labels beside them.
  - VP and fixed-range VP still use histogram bars.
  - TPO gets a dedicated SVG renderer (`appendTpoLetterRows`) with monospace period letters and invisible hover bands.
  - POC/value-area/outside-value-area distinction is carried by letter color/opacity instead of bar fill.
  - Letters are now **right-aligned** at a fixed right anchor and the per-row letter string is **reversed** before rendering, so the first period of the day (A) sits closest to the price axis and each new period extends the row leftward toward the candles. This matches standard TPO market-profile orientation (latest period faces the price action) and visually mirrors the way the original histogram bars used to grow leftward as count increased. Truncation for very long rows keeps the leftmost (latest-period) characters and adds a `+` on the right.
  - The right anchor sits ~132px from the chart's right edge with up to ~150px of letter width, so even long rows stay well clear of the price-axis labels, matching the spacing the original bar histogram had. VAH/VAL/POC dashed lines come in from the left and terminate just before the leftmost extent of the letters, with labels right-anchored immediately to the left of the letter column.
- TPO VAH/VAL/POC labels now sit left of the TPO letter column.
  - The level lines stop before the letters, so labels no longer print over crowded letter rows.
  - Single-print `SP` labels follow the same left-side placement.
- Price chart toolbar now includes `VP` and `TPO` buttons for quickly toggling the price-axis volume profile and TPO profile.
  - The buttons mirror the existing drawer checkboxes and reuse the existing profile update/save-load path.
- Compact-label behavior remains available for truly compressed rows.
  - When row height is too small, compact mode still falls back to `(count)`.
  - Long rows are width-clamped with a `+` suffix instead of spilling into the right price labels.

Tricky parts:

- **TPO direction matters.** The first letter-only pass anchored the text on the left, so rows appeared to grow rightward. The desired price-axis behavior is the opposite: anchor near the price scale and let longer TPO rows extend left into the chart.
- **Keep VP and TPO rendering separate.** The shared profile-row renderer still draws real histograms for VP and fixed VP. TPO now has its own renderer so market-profile letters are not coupled to volume-profile bar behavior.
- **Toolbar buttons should not create parallel state.** The new `VP` and `TPO` toolbar buttons mirror `#vp_enabled` and `#tpo_enabled`, dispatch the existing change event, and let the existing `/update_price`, save/load, and overlay draw paths do the work.
- **Hover hit areas moved with the text anchor.** Since TPO text now extends left from the right anchor, the invisible hover bands also had to be computed from the right edge back toward the candles.

Validation:

- `python3 -m py_compile ezoptionsschwab.py` (only the pre-existing unrelated `\(` escape-sequence warning remains).
- `git diff --check`
- Fresh `http://127.0.0.1:5012/` template smoke confirmed the right-aligned TPO renderer and toolbar buttons are present.

### Letter-orientation iteration (post-screenshot review)

After three rounds of visual review on real current-session candles, the TPO letter column was re-anchored to match standard market-profile convention:

- First letter-only pass anchored letters on the **left** so rows grew **rightward**. Visually wrong: rows grew toward the price axis instead of toward the candles.
- Second pass kept right-anchoring but the column landed flush against the price-axis labels. Pulled the column ~30px off the price axis.
- Third pass flipped to **left-anchored** so rows grew rightward from a fixed left baseline (matching how the histogram bars grew rightward from a baseline). Direction was still wrong: in real market profile, the most recent period should face the candles.
- Final pass (current state): **right-anchored, letter string reversed**. First period (A) sits closest to the price axis; each newer period extends the row leftward toward the candles. Truncation slices the leading reversed chars and appends `+` on the right, so the latest periods remain visible when the row gets long. Hover hit-boxes were re-derived from `xEnd` extending leftward to match.

Tricky parts of this iteration:

- **Direction is not just an anchor flip.** Right-anchoring alone keeps the alphabetical order rendering with A still leftmost. The string itself has to be reversed for the convention "earliest period closest to the price axis, latest period closest to the candles" to read correctly.
- **Truncation side matters.** With reversed letters, `slice(0, n) + '+'` keeps the *latest* periods (most relevant for trading) and drops earliest-period letters with a `+` glyph against the price axis side. This is the opposite of the slicing the bar-era code did.
- **Level-line endpoints stay at `xStart - 10`.** Since the letters now extend leftward toward `xStart` (and `xStart` is computed as the leftmost extent of the column), the existing VAH/VAL/POC dashed-line and label geometry kept working unchanged — they still terminate just before the letter cluster from the left.
- **Hover bands had to flip with the anchor.** Each previous orientation change required re-deriving `x1`/`x2` for the invisible hover rectangles; the rectangles are now `[xEnd - hitWidth, xEnd]`.

### Still left to do

- Browser-test non-default TPO modes (`Bars Back`, `Anchor`, custom range, composite days) against real Schwab candles.
- Visual-test the toolbar toggles in-browser with live data, including save/load behavior after toggling from the toolbar rather than the drawer.
- Continue tuning the right edge with many simultaneous overlays enabled, especially price labels, moving-average tags, key-level labels, strike labels, VP, and TPO.
- Validate the new right-anchored / reversed-letter orientation against composite-day and bars-back modes (so far only verified visually against `Current session`).
- Tune the `width - 132` right-edge constant if the letter column ends up too close to the price axis on narrower viewports / when the price scale grows (e.g. 4-digit prices with decimals).
- Consider whether the truncation `+` indicator should appear on the candle side instead of the price-axis side when a row is dominated by very late-day periods — current choice favors keeping latest periods visible, which is the right default but may want a setting later.
- Decide whether `block_minutes < timeframe.in_seconds(chart_tf) / 60` should be flagged in the UI. Pine indicator allows it, but with 1-minute candles a 15-minute block is fine — only relevant if the chart timeframe ever drops below 1m.
- Implement the deferred TradingView features when the right-edge interaction model feels good:
  - Fixed-range TPO drawing tool (reusing the fixed VP anchor model: timestamp anchors first, logical fallback).
  - Anchored TPO drawing tool (likely shares most of the fixed-range implementation, with the end anchor tracking the latest candle).
  - Initial-balance lines (high/low from the first N blocks of the period, plus optional extensions).
  - Periodic / Daily / Weekly / Monthly / Quarterly / Monthly-OPEX / Quarterly-OPEX modes from the Pine indicator.
- [x] VAH/VAL/POC line-style and line-width controls.
  - [x] Drawer exposes `VP Line Style` / `VP Line Width` and `TPO Line Style` / `TPO Line Width` (solid/dashed/dotted, 0.5–5).
  - [x] Applied at right-edge VP, right-edge TPO, fixed-VP, fixed-TPO, and anchored-TPO drawings via `buildLevelLineStyle()`.
  - [x] Defaults preserve current visual (dashed, width 1); persists through `volume_profile` / `tpo_profile` save/load.

## TPO Cleanup Pass (post-Phase 1 review)

After live current-session review on real candles, three issues remained:

- Right-axis `Current Session` TPO included after-hours/pre-market candles because `_filter_profile_candles` keyed on calendar date alone. A 17:00 ET wick produced a long tail of `P` letters at prices the visible RTH range never traded.
  - Fix: `_filter_profile_candles` now restricts mode `session` to RTH (9:30–16:00 ET, weekdays). Other modes (`days`, `custom`, `bars_back`, `anchor`) still respect their full window.
- Initial-balance lines existed only inside the per-drawing TPO editor, so users without a fixed/anchored TPO drawing had no way to see IB on the right-edge profile.
  - Fix: drawer now exposes `Show Initial Balance` and `IB Minutes`. Persists through `tpo_profile` save/load. Server computes `initial_balance` from the first N minutes of the selected TPO candle window and the right-edge renderer draws IBH/IBL/IBM with the same clipping/labelling rules as VAH/VAL/POC. New fixed/anchored TPO drawings inherit these as defaults; the per-drawing editor still overrides.
- Fixed-range / anchored TPO drawings ignored `profileSide='left'` (letters stayed in the box middle), and never clipped VAH/VAL/POC/IB lines off the letter column. Level labels were always drawn.
  - Fix: `appendTpoLetterRows` and `getTpoLetterDisplayMetrics` accept `side`. For `left`, letters anchor at the box's left edge with `text-anchor='start'` and are not reversed (latest period grows toward the candles on the right). For `right`, the existing reversed/end-anchored layout is preserved. Drawings clip VAH/VAL/POC and IB lines past the letter column on whichever side and place labels just outside the cluster. The drawer's `Show TPO Level Labels` toggle now also gates labels on TPO drawings, matching right-edge behavior.
