# Claude: read this first

## Active initiative

_None — Phase 3 (Alerts Rail + Flow Alerts) wrapped on `feat/analytics-phase3`; branch pending PR._

## Completed initiatives

- **UI modernization** — [`docs/UI_MODERNIZATION_PLAN.md`](docs/UI_MODERNIZATION_PLAN.md). 7-stage layout/palette/design-token refresh.
- **Analytics + Chart Phase 2** — [`docs/ANALYTICS_CHART_PHASE2_PLAN.md`](docs/ANALYTICS_CHART_PHASE2_PLAN.md). Dealer Impact block, Scenarios tab, HVL/±2σ EM/secondary walls on-chart, extended candle history.
- **Alerts Rail Phase 3** — [`docs/ALERTS_RAIL_PHASE3_PLAN.md`](docs/ALERTS_RAIL_PHASE3_PLAN.md). Alpha-intensity GEX bars, 7-card alerts rail, live flow alerts engine (vol spike, V/OI, IV surge, wall shift) + SQLite index for the hot path.

If the user asks about the UI, layout, palette, chart controls, side panel, KPI strip, alerts, drawer, dealer impact, scenarios, or flow alerts — the relevant plan doc above is authoritative. Read it before proposing changes.

### When starting new implementation work

1. Confirm the branch: `git branch -a` and `git log --oneline main..HEAD`.
2. **Grep by anchor name** rather than trusting line numbers in plan docs — markup drifts fast across stages. Useful anchors in this codebase:
   - HTML/CSS: `.top-bar`, `.drawer`, `.settings-modal`, `.secondary-tabs`, `.right-rail-tabs`, `.right-rail-panels`, `.right-rail-panel`, `[data-rail-panel="alerts"]`, `.rail-card`, `.dealer-impact`, `.gex-side-panel-wrap`, `.scenario-table`.
   - JS: `ensurePriceChartDom`, `wireRightRailTabs`, `applyRightRailTab`, `buildAlertsPanelHtml`, `renderGexSidePanel`, `renderRailAlerts`, `renderDealerImpact`, `renderMarketMetrics`, `renderRangeScale`, `renderGammaProfile`, `renderChainActivity`, `updatePriceInfo`, `updateSecondaryTabs`.
   - Python: `compute_trader_stats`, `compute_key_levels`, `compute_greek_exposures`, `compute_flow_alerts`, `_fetch_vol_spike_data`, `create_gex_side_panel`, `_compute_session_deltas`.
   - SQLite: tables `interval_data`, `interval_session_data`, `centroid_data`; index `idx_interval_data_ticker_date_ts`.

### Standing ground rules

- No analytical-formula changes (GEX/DEX/Vanna/Charm/Flow math stays put).
- No JS framework introduction — vanilla JS + CSS tokens only.
- No breaking the single-file `ezoptionsschwab.py` structure.
- Tokens only for colors (`--bg-*`, `--fg-*`, `--call`, `--put`, `--warn`, `--info`, `--accent`, `--border`); no neon hex literals.
- Any new element under `[data-rail-panel="alerts"]` must also appear in `buildAlertsPanelHtml()` or tick rebuilds drop it.

## Project shape

- Single-file Flask + Plotly + TradingView-Lightweight-Charts app: `ezoptionsschwab.py` (~10k lines).
- Pulls live options data via the `schwabdev` SDK; SQLite stores historical bubble levels.
- Run: `python ezoptionsschwab.py` → http://localhost:5001.
- Do not commit: `.env`, `options_data.db`, `terminal_while_running*.txt`, `__pycache__/`.
