# Claude: read this first

## Active initiative

**UI modernization** — see [`docs/UI_MODERNIZATION_PLAN.md`](docs/UI_MODERNIZATION_PLAN.md).

If the user asks about the UI, layout, palette, chart controls, side panel, KPI strip, alerts, drawer, or "the plan" — that doc is authoritative. Read it before proposing changes.

### Before starting implementation work

1. `git branch -a` — is `feat/ui-modernization` checked out or does it need to be created from `main`?
2. `git log --oneline feat/ui-modernization` (if it exists) — match commit subjects against the 7 stages in §7 of the plan to determine which stage is next. Commit subject prefixes map 1:1 to stages (see §6.2 of the plan).
3. Line numbers in the plan are a snapshot as of commit `3d26533` and have drifted heavily since stages 1–5 landed. **Grep by anchor name** rather than trusting the numbers. Useful current anchors: `.top-bar`, `.drawer`, `.drawer-section`, `.settings-modal`, `.secondary-tabs`, `.right-rail-tabs`, `.right-rail-panels`, `.right-rail-panel`, `.gex-side-panel-wrap`; functions `ensurePriceChartDom`, `wireRightRailTabs`, `applyRightRailTab`, `renderChartVisibilitySection`, `renderGexSidePanel`, `syncGexPanelYAxisToTV`, `updateSecondaryTabs`, `compute_trader_stats`, `getChartVisibility`. The pre-Stage-4 anchors `.header` / `.header-top` / `.header-bottom` / `.chart-selector` / `.chart-checkbox` no longer exist; the pre-Stage-5 `.price-chart-row` is also gone (GEX panel lives in `.right-rail-panels` now).

### Ground rules from the plan

- No analytical-formula changes (GEX/DEX/Vanna/Charm math stays put).
- No JS framework introduction — vanilla JS + CSS tokens only.
- No breaking the single-file `ezoptionsschwab.py` structure.
- One commit per stage; follow the commit-message convention in §6.2.
- No pushes to `main` during the effort — merge via PR at the end.

## Project shape

- Single-file Flask + Plotly + TradingView-Lightweight-Charts app: `ezoptionsschwab.py` (~10k lines).
- Pulls live options data via the `schwabdev` SDK; SQLite stores historical bubble levels.
- Run: `python ezoptionsschwab.py` → http://localhost:5001.
- Do not commit: `.env`, `options_data.db`, `terminal_while_running*.txt`, `__pycache__/`.
