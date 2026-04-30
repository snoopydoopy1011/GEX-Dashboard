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
  - Range mode: composite days, current session, visible range
  - Composite day count
  - Volume profile bin size
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

## Validation Done

- `python3 -m py_compile ezoptionsschwab.py`
- Synthetic candle smoke test for:
  - Volume profile bin generation
  - TPO row generation
  - `prepare_price_chart_data()` carrying candle volume plus profile payloads
- Local Flask server boot on prototype port `5012`.

## Left To Do

- Refresh Schwab auth and visually test with real SPY candles in the browser.
- Tune overlay spacing so right-axis VP, TPO letters, price labels, and existing strike overlays do not crowd each other.
- Add hover tooltip details for VP/TPO rows: price, modeled volume, percent of max, TPO letters/count.
- Improve fixed-range VP editing:
  - draggable start/end anchors
  - optional profile side selection
  - per-drawing bin size and allocation controls in the drawing editor
- Add value area calculations, e.g. VAH/VAL/POC for volume profile and TPO.
- Consider session segmentation for TPO instead of only the latest/current RTH session.
- Decide whether these should remain chart overlays, become formal indicators, or move into a dedicated chart settings menu once the interaction model is settled.
