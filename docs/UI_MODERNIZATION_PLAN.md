# GEX Dashboard — UI Modernization Plan

**Status:** Proposed
**Owner:** @snoopydoopy1011
**Created:** 2026-04-18
**Target branch:** `feat/ui-modernization`
**Base:** `main` @ `2d4aaa9` (Merge feat/trader-view: Stages 1-4)

---

## 1. Context

`ezoptionsschwab.py` is a single-file Flask + Plotly + TradingView-Lightweight-Charts app (10,156 lines) that pulls live options data from the Schwab API and renders GEX/DEX/Vanna/Charm exposures, intraday candles with on-chart levels, a strike-aligned GEX side panel, a KPI strip, alerts, and a tabbed secondary-chart area.

The analytics and formulas are strong. The UI is not:

- The `.header` packs roughly 30 controls into two dense flex rows (ticker / timeframe / expiry / stream + strike-range slider + exposure metric + eight boolean toggles + two dropdowns + three color pickers + max-level mode).
- A third row of 14 chart-type checkboxes sits above the chart grid even though the secondary-chart area is already tabbed.
- Palette is harsh: pure neon `#00FF00` / `#FF0000` used as checkbox `accent-color` and chart fills, on a muddled grey ladder (`#1E1E1E / #2D2D2D / #333 / #444`).
- KPI strip and alerts strip float as loose sibling rows below the header, visually disconnected from everything around them.
- The right-side GEX panel is locked at 22 % next to the candles but only shows one view; alerts and key levels have no durable home.
- Plotly styling (bg, grid, fonts, margins) is duplicated inside each of eight chart-builder functions.

**Goal:** keep every metric and control, redistribute them into a 3-zone layout (slim top bar + collapsible left settings drawer + tabbed right rail), switch to a muted trading-terminal palette, and centralize Plotly styling. **No analytical formulas change.**

---

## 2. Target layout

```
┌──────────────────────────────────────────────────────────────────────┐
│ [☰] Ticker  Timeframe  Expiry  Stream●  │ ⟳ reconnect  Settings ⚙   │  slim top bar
├────────┬──────────────────────────────────────────────┬──────────────┤
│        │ Net GEX   Regime   EM ±1σ   Walls            │              │  KPI strip
│        ├──────────────────────────────────────────────┤  Right rail  │
│        │                                              │  ┌─┬─┬─┐     │
│ Left   │          Candles + on-chart levels           │  │G│A│L│     │  tabbed:
│ drawer │          (Call/Put walls, Γ-flip, ±1σ EM)    │  └─┴─┴─┘     │  GEX /
│ (slide │                                              │              │  Alerts /
│  in/   ├──────────────────────────────────────────────┤  panel body  │  Levels
│  out)  │ [Gam][Del][Vna][Chm][OVol][OI][Prem]…        │              │
│        │ Active chart (Plotly)                        │              │
└────────┴──────────────────────────────────────────────┴──────────────┘
```

- **Top bar** (~44 px): ticker, timeframe, expiry, stream dot + connection, reconnect, hamburger (opens drawer), gear (opens settings modal for color pickers / coloring mode). Token monitor stays on the right.
- **Left drawer** (hidden by default, ~320 px, slides in over content): houses the current ~30 header toggles grouped into collapsible sections (Strike Range, Exposure Metric, Series visibility, Price Levels, Coloring, Max-Level). Selections persist via the existing save/load settings flow.
- **Center column**: KPI strip → candles (with on-chart levels) → secondary-chart tab bar → active Plotly chart.
- **Right rail** (~300 px): tabbed `GEX profile` (default) / `Alerts` / `Key Levels`. The existing `syncGexPanelYAxisToTV()` alignment is preserved, but only runs when the GEX tab is active.
- The separate chart-type checkbox row is **removed**; each chart becomes a tab in the existing `.secondary-tabs` bar. Visibility preferences move into the drawer's "Sections" group so rarely-used charts can still be hidden.

---

## 3. Design tokens (muted trading terminal)

Add a `:root` block at the top of the inline `<style>` (line 4853) and refactor the CSS to reference tokens:

```css
:root {
  --bg-0:#0B0E11; --bg-1:#151A21; --bg-2:#1E242D; --bg-3:#262D38;
  --border:#2A313B; --border-strong:#3A424F;
  --fg-0:#E5E7EB; --fg-1:#9CA3AF; --fg-2:#6B7280;
  --call:#10B981; --put:#EF4444; --accent:#3B82F6;
  --warn:#F59E0B; --info:#3B82F6; --ok:#10B981;
  --radius:6px; --radius-lg:10px;
  --font-ui:-apple-system,BlinkMacSystemFont,"Inter","Segoe UI",sans-serif;
  --font-mono:"SF Mono","JetBrains Mono",Menlo,monospace;
}
body { font-family: var(--font-ui); background: var(--bg-0); }
.num { font-variant-numeric: tabular-nums; }
```

Find/replace inside the `<style>` block (lines 4853–5895):

| Old literal | New token | Used by |
|---|---|---|
| `#1E1E1E` | `var(--bg-0)` | body, price chart, side panel |
| `#2D2D2D` | `var(--bg-1)` | `.header` / card bodies |
| `#333` | `var(--bg-2)` | `.control-group`, inputs |
| `#444` | `var(--border)` | input borders |
| `#00FF00` (accent-color) | `var(--call)` | checkboxes |
| `#FF0000` (accent-color) | `var(--put)` | checkboxes |
| `#00D084` | `var(--call)` | KPI positive |
| `#FF4D4D` | `var(--put)` | KPI negative |

Chart fills are sourced from Python-side constants `CALL_COLOR = '#10B981'` / `PUT_COLOR = '#EF4444'` so the chart palette matches the CSS tokens.

---

## 4. Critical files and line ranges

Everything is in the single file `ezoptionsschwab.py`.

| Area | Lines | What changes |
|---|---|---|
| CSS `<style>` block | 4853–5895 | Add `:root` tokens. Refactor colors. Add `.top-bar`, `.drawer`, `.drawer.open`, `.right-rail`, `.right-rail-tabs`, `.right-rail-tab`, `.settings-modal`, `.btn-ghost`. Consolidate duplicated radii and paddings. |
| `.header` markup | 5901–6078 | Replace two-row header with slim `<nav class="top-bar">` (ticker / timeframe / expiry / stream / reconnect / hamburger / gear). Move everything else into `<aside class="drawer" id="settings-drawer">` with `<details>`-based collapsible groups. Color pickers + coloring mode go into a `<dialog class="settings-modal">`. |
| Chart-selector row | 6085–6143 | **Delete.** Visibility migrates to a "Sections" group inside the drawer; `.secondary-tabs` is the only chart-switcher. |
| `#chart-grid` | 6145–6163 | Restructure to `grid-template-columns: 1fr 300px` with KPI strip spanning the first column. GEX wrap moves out of `.price-chart-row` into `.right-rail` and renders only when its tab is active. |
| `renderTraderStats()` | 8571–8632 | Pull `.trader-stats-strip` out of header-adjacent flow; mount inside main column above candles. `compute_trader_stats()` (3297–3363) untouched. |
| `renderGexSidePanel()` + `syncGexPanelYAxisToTV()` | 8506–8568 | Run only when GEX tab is active; pause `requestAnimationFrame` loop when hidden. |
| Alerts rendering | 8571–8632 (tail) | Move chip rendering from `#trader-alerts-strip` into `#right-rail-alerts`. Add unread-count badge on the Alerts tab. |
| Key Levels tab (new) | new JS block near 8695 | Reuse `renderKeyLevels()` data (Call Wall / Put Wall / Γ-flip / ±1σ EM); render as a compact table in the right rail. |
| `updateSecondaryTabs()` | 8644–8684 | Always include the full chart set. Read visibility preferences from `localStorage` populated by the drawer. |
| Plotly theme centralization | new helper ~1580, above `create_exposure_chart` | `PLOT_THEME = dict(paper_bgcolor='#0B0E11', plot_bgcolor='#0B0E11', font=dict(family='Inter, -apple-system, sans-serif', color='#9CA3AF', size=11), xaxis=dict(gridcolor='#1E242D', zerolinecolor='#2A313B'), yaxis=dict(gridcolor='#1E242D', zerolinecolor='#2A313B'), margin=dict(l=50,r=80,t=30,b=24))` plus `CALL_COLOR='#10B981'`, `PUT_COLOR='#EF4444'`. Replace per-function `update_layout` color/font kwargs with `fig.update_layout(**PLOT_THEME)` across all eight chart builders. |

---

## 5. Reuse (don't rebuild)

- `compute_trader_stats()` (3297–3363) — already returns everything the KPI strip, alerts, and key-levels need. Do not touch.
- `renderTraderStats()` / `renderKeyLevels()` / `renderGexSidePanel()` / `syncGexPanelYAxisToTV()` — keep the core functions; only change where they mount in the DOM.
- `updateSecondaryTabs()` / `applySecondaryTabVisibility()` (8644–8684) — already handle the tab machinery; feed them the full chart list.
- Settings save/load (in header-top) — keep storage shape; drawer reads/writes the same keys.

---

## 6. Git workflow & versioning

### 6.1 Branching

- Base all work on `main` at `2d4aaa9`.
- Cut a single long-lived feature branch: `feat/ui-modernization`.
- Each stage in §7 lands as its own commit on that branch (no squashing mid-branch — keep stages bisectable).
- No pushes to `main` during the effort. Final merge is a PR from `feat/ui-modernization` → `main` with a merge commit (matching the `feat/trader-view` merge pattern already in `git log`).

### 6.2 Commit message convention

Follow the existing repo convention (Conventional-Commits-lite, seen in `feat(trader-view): …`, `fix: …`, `chore: …`):

```
<type>(<scope>): <imperative subject, ≤72 chars>

<optional body explaining why, wrapped at 72 cols>

Stage: <N>/7
Refs: docs/UI_MODERNIZATION_PLAN.md#<anchor>
```

Types in use: `feat`, `fix`, `chore`, plus `style` (CSS-only) and `refactor` for this effort.

Examples:

- `style(ui): add :root design tokens, swap palette to muted terminal`
- `refactor(plotly): centralize PLOT_THEME and CALL/PUT color constants`
- `feat(ui): replace chart-checkbox row with always-on secondary tabs`
- `feat(ui): add slim top bar and slide-in settings drawer`
- `feat(ui): tabbed right rail — GEX / Alerts / Key Levels`
- `feat(ui): move alerts into right rail, add unread badge`
- `style(kpi): polish KPI cards with new tokens and tabular numerics`

### 6.3 Tagging

Tag the pre-modernization state on `main` before the feature branch merges, so the old UI is easy to roll back to or screenshot against:

```
git tag -a v0.1.0-pre-ui-modernization 2d4aaa9 -m "Last commit before UI modernization"
git push origin v0.1.0-pre-ui-modernization
```

After the merge, tag the new UI:

```
git tag -a v0.2.0 <merge-sha> -m "UI modernization: drawer, right rail, muted palette"
git push origin v0.2.0
```

### 6.4 Pull request

Open the PR once stage 1 is on the feature branch, then keep pushing to it. PR title:

```
feat(ui): modernize dashboard layout, palette, and chart controls
```

PR body template:

```
## Summary
- Slim top bar + collapsible settings drawer replace the 2-row control header
- Tabbed right rail (GEX / Alerts / Key Levels) replaces the fixed 22% GEX panel
- 14-checkbox chart-selector row removed; tabs are the only switcher
- Muted trading-terminal palette via CSS tokens
- Centralized Plotly theme across 8 chart builders

## Screenshots
| Before | After |
|---|---|
| Screenshots/Screenshot 2026-04-17 at 9.15.52 PM.png | <after-1.png> |
| Screenshots/Screenshot 2026-04-17 at 9.16.02 PM.png | <after-2.png> |

## Test plan
- [ ] `python ezoptionsschwab.py`, open http://localhost:5001
- [ ] Stream toggles on, KPI values tick, candles advance
- [ ] GEX panel stays pixel-aligned to candles with stream on
- [ ] Drawer controls behave identically to the old header controls
- [ ] Right-rail tabs switch (GEX / Alerts / Levels), alignment restored on return
- [ ] Every secondary tab (Gamma…Centroid) renders with new PLOT_THEME
- [ ] No remaining `#1E1E1E` / `#2D2D2D` / `#00FF00` / `#FF0000` literals in inline `<style>`
- [ ] Reload restores ticker, expiry, toggles, visible tabs, active right-rail tab

## Refs
docs/UI_MODERNIZATION_PLAN.md
```

### 6.5 Commit hygiene

- Never commit `.env`, `options_data.db`, `terminal_while_running*.txt`, or `__pycache__/`. `.gitignore` already covers most — double-check `git status` before each commit.
- Stage screenshots for before/after comparison into `Screenshots/` and include them in the PR body.
- No `--no-verify`, no force pushes to `main`. Force-push to `feat/ui-modernization` is acceptable for fixup commits but avoid it once the PR has reviews.

---

## 7. Implementation stages

Each stage is one commit on `feat/ui-modernization`. Every stage leaves the app fully runnable — no half-broken intermediate states.

1. **Tokens + palette swap** (CSS-only). Add `:root`, find/replace hex values, soften neon.
   Commit: `style(ui): add :root design tokens, swap palette to muted terminal`
2. **Plotly theme consolidation.** Introduce `PLOT_THEME` / `CALL_COLOR` / `PUT_COLOR`; wire into all eight chart functions.
   Commit: `refactor(plotly): centralize PLOT_THEME and CALL/PUT color constants`
3. **Delete chart-selector row, extend tab bar.** Remove lines 6085–6143. Make `updateSecondaryTabs()` authoritative. Persist per-chart visibility to `localStorage` (defaults match today's checked/unchecked state).
   Commit: `feat(ui): replace chart-checkbox row with always-on secondary tabs`
4. **Slim top bar + drawer.** Restructure `.header` → `.top-bar` + `.drawer`. Group the ~30 controls into `<details>` sections. Hamburger toggles `.drawer.open`. Color pickers / coloring mode move to `<dialog>` opened by the gear icon.
   Commit: `feat(ui): add slim top bar and slide-in settings drawer`
5. **Right-rail restructure.** Move GEX panel out of `.price-chart-row`; create `.right-rail` with three tab buttons. Default to GEX. Gate `syncGexPanelYAxisToTV()` on active tab.
   Commit: `feat(ui): tabbed right rail — GEX / Alerts / Key Levels`
6. **Alerts & Key Levels tabs.** Render alerts into the right-rail panel with an unread-count badge; build the Key Levels table from `compute_trader_stats()` output.
   Commit: `feat(ui): move alerts into right rail, add unread badge`
7. **KPI strip polish.** Restyle `.kpi-card` with new tokens, add trend arrows (▲▼) using existing `.kpi-pos/.kpi-neg`, tabular-nums for every numeric. Mount above candles.
   Commit: `style(kpi): polish KPI cards with new tokens and tabular numerics`

---

## 8. Verification

After each stage (and again before opening the merge PR):

1. `python ezoptionsschwab.py`, open `http://localhost:5001`.
2. **Stream on**: stream dot stays green, KPI values update, price chart advances, right-rail GEX stays pixel-aligned to candles.
3. **Drawer controls**: Strike Range slider, Exposure Metric, each series-visibility toggle, Price Levels multiselect, Max-Level highlight — all behave identically to today (charts re-render, levels redraw).
4. **Right rail**: switch GEX → Alerts → Levels. GEX realigns when returning. Alerts badge reflects unread count; clicking clears. Levels table shows Call Wall / Put Wall / Γ-flip / ±1σ EM with live numbers.
5. **Chart tabs**: every secondary chart from Gamma through Centroid renders correctly with `PLOT_THEME`.
6. **Palette**: Grep the inline `<style>` block — no remaining `#1E1E1E` / `#2D2D2D` / `#00FF00` / `#FF0000` literals (only token references).
7. **Resize**: at ~1280 px the top bar stays single-line and the drawer overlays (doesn't push) content; below 1024 px the right rail collapses beneath the candles.
8. **Persistence**: reload — ticker, expiry, every toggle, visible tabs, and the active right-rail tab all restore from `localStorage`.

---

## 9. Out of scope

- Changing any analytical formula (GEX / DEX / Vanna / Charm / Speed / Vomma / Color / Volume / Premium / Centroid math).
- Changing the Schwab API integration or streaming cadence.
- Changing the SQLite schema or the historical-bubble-levels data model.
- Introducing a JS framework (React / Vue / Alpine). This effort stays in vanilla JS + CSS tokens.
- Breaking the single-file `ezoptionsschwab.py` structure. If that becomes painful, it's a separate refactor proposal.
