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

Tricky parts:

- The fixed VP bars live in the separate profile SVG overlay, while the selected drawing box and editor live in the drawing overlay path. Side changes therefore need to schedule both overlay redraw paths in a few places.
- The global side selector should not rewrite older saved drawings, so the drawing definition keeps an empty `profileSide` as "inherit from setting".
- The local `5002` Flask server may keep serving an old in-memory template after file edits. Restart the listener if new drawer/editor controls do not appear after refresh.
- SVG-native hover titles were considered, but the profile overlay intentionally uses `pointer-events: none` so chart interactions pass through. A real tooltip should be implemented as an app-native hover layer rather than relying on SVG `<title>`.

## Validation Done

- `python3 -m py_compile ezoptionsschwab.py`
- Synthetic candle smoke test for:
  - Volume profile bin generation
  - TPO row generation
  - `prepare_price_chart_data()` carrying candle volume plus profile payloads
- Local Flask server boot on prototype port `5012`.
- Follow-up smoke on `http://127.0.0.1:5002/`:
  - `/update_price` returned `939` candles, `70` volume-profile bins, and `10` TPO rows for SPY.
  - Browser test confirmed a newly drawn fixed VP over real candles renders visible histogram bars.
  - Drawing a VP range over empty/future chart space still produces no bars, which is expected because there are no candles in that selected interval.

## Left To Do

- Tune overlay spacing so right-axis VP, TPO letters, price labels, and existing strike overlays do not crowd each other.
- Add hover tooltip details for VP/TPO rows: price, modeled volume, percent of max, TPO letters/count.
- Improve fixed-range VP editing:
  - draggable start/end anchors
  - per-drawing bin size and allocation controls in the drawing editor
- Add value area calculations, e.g. VAH/VAL/POC for volume profile and TPO.
- Consider exposing TPO color/opacity separately from VP once the overlay spacing is settled.
- Consider session segmentation for TPO instead of only the latest/current RTH session.
- Decide whether these should remain chart overlays, become formal indicators, or move into a dedicated chart settings menu once the interaction model is settled.
