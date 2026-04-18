# GEX Dashboard — UI Modernization Plan

**Status:** In progress — 4 of 7 stages landed
**Owner:** @snoopydoopy1011
**Created:** 2026-04-18
**Target branch:** `feat/ui-modernization`
**Base:** `main` @ `2d4aaa9` (Merge feat/trader-view: Stages 1-4)

---

## 0. Where are we? (read this first)

**Current state (as of 2026-04-18):** Stages 1–4 have landed on `feat/ui-modernization`. Next stage: **5** — tabbed right rail (GEX / Alerts / Key Levels). See §10 for per-stage progress notes and deviations from spec.

Line numbers throughout this doc are a snapshot as of commit `3d26533` (the commit that introduced the doc). **They drift as soon as Stage 1 lands.** Grep by anchor name — CSS class names (`.header`, `.secondary-tabs`, `.kpi-card`, `.gex-side-panel-wrap`), function names (`renderGexSidePanel`, `renderTraderStats`, `syncGexPanelYAxisToTV`, `updateSecondaryTabs`, `compute_trader_stats`, `create_exposure_chart`), or element IDs (`#chart-grid`, `#trader-stats-strip`, `#gex-side-panel`) — rather than trusting the numbers.

To determine which stage is next:

```bash
git branch -a                              # does feat/ui-modernization exist?
git log --oneline main..feat/ui-modernization   # what's landed?
```

Match commit subjects against the 7 stages in §7. Subjects follow §6.2 exactly, so stage N's commit subject is stable. If the branch doesn't exist yet, cut it from `main` and start at Stage 1. If N stages have landed, start at N+1.

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
   **Status:** ✅ Landed as `cc31ad2` — see §10.1 for notes.
2. **Plotly theme consolidation.** Introduce `PLOT_THEME` / `CALL_COLOR` / `PUT_COLOR`; wire into all eight chart functions.
   Commit: `refactor(plotly): centralize PLOT_THEME and CALL/PUT color constants`
   **Status:** ✅ Landed as `ee20f4d` — see §10.2 for notes (deviated from `**PLOT_THEME` unpack to value-dereference; reasoning captured there).
3. **Delete chart-selector row, extend tab bar.** Remove lines 6085–6143. Make `updateSecondaryTabs()` authoritative. Persist per-chart visibility to `localStorage` (defaults match today's checked/unchecked state).
   Commit: `feat(ui): replace chart-checkbox row with always-on secondary tabs`
   **Status:** ✅ Landed — see §10.3 for notes (Stage 4 drawer needed before chart on/off UI returns).
4. **Slim top bar + drawer.** Restructure `.header` → `.top-bar` + `.drawer`. Group the ~30 controls into `<details>` sections. Hamburger toggles `.drawer.open`. Color pickers / coloring mode move to `<dialog>` opened by the gear icon.
   Commit: `feat(ui): add slim top bar and slide-in settings drawer`
   **Status:** ✅ Landed — see §10.4 for notes (also closes the Stage-3 chart-visibility UI gap via a "Sections" drawer group).
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

---

## 10. Progress log & deviations

Running notes from executing the stages. Future-Claude should skim this before picking up work — it captures decisions that aren't derivable from the diff.

### 10.1 Stage 1 — `cc31ad2` · `style(ui): add :root design tokens, swap palette to muted terminal`

**Landed:** 2026-04-18.

Executed as specified: `:root` block inserted at top of the main `<style>`; every literal in §3's mapping table swapped to a token reference *inside that style block only*. Post-swap grep over the main style block returns zero hits for `#1E1E1E` / `#2D2D2D` / `#00FF00` / `#FF0000` / `#00D084` / `#FF4D4D` / `#333` / `#444`. AST parses clean.

Deviations and things deliberately left for later:

- **`.tv-ohlc-tooltip .tt-dn { color: #FF4444; }`** stayed untouched. `#FF4444` isn't in §3's mapping table nor in §8's forbidden-literals list. A follow-up pass may want `var(--put)` for consistency with `.tt-up` which is already tokenized.
- **`#00D084` and `#FF4D4D` also appear in inline JS** at ~line 8700–8701 (`renderKeyLevels` Call-Wall / Put-Wall series colors). Only the style-block occurrences were swapped here — the JS strings belong to Stage 6 (Key Levels tab rewrite) and were left alone on purpose.
- **Two other `<style>` blocks** exist in the file (popout-chart error pages at ~line 6562, ~6955). They carry the old palette but sit outside the "main inline `<style>` block (lines 4853–5895)" scope given in §4. Not touched.

Gotcha worth remembering: the file has a pre-existing UTF-8 BOM. Raw `python3 -c "ast.parse(open(path).read())"` errors with `invalid non-printable character U+FEFF`. Use `encoding='utf-8-sig'` for AST checks. The Python interpreter handles the BOM natively when running the script.

### 10.2 Stage 2 — `ee20f4d` · `refactor(plotly): centralize PLOT_THEME and CALL/PUT color constants`

**Landed:** 2026-04-18.

Introduced `PLOT_THEME` (paper_bgcolor / plot_bgcolor / font / xaxis / yaxis / margin), `CALL_COLOR = '#10B981'`, `PUT_COLOR = '#EF4444'` immediately above `create_exposure_chart`. Every Plotly `plot_bgcolor=` / `paper_bgcolor=` / nested `bgcolor=` kwarg now dereferences `PLOT_THEME`. Every chart-builder default `call_color='#00FF00'` / `put_color='#FF0000'` now references `CALL_COLOR` / `PUT_COLOR`. `create_exposure_chart`'s local `grid_color` / `text_color` / `background_color` initialize from `PLOT_THEME`.

**Deviation from §4 spec — value-dereference instead of `**PLOT_THEME` unpack.** §4 prescribes `fig.update_layout(**PLOT_THEME)` across every builder. We didn't do that. Reason:

- `PLOT_THEME` as specified includes `xaxis=…`, `yaxis=…`, `margin=…`. Every chart builder already passes its own detailed `xaxis=xaxis_config`, `yaxis=yaxis_config`, `margin=…` in the same `update_layout` call.
- Put both in one call and Python raises `TypeError: got multiple values for keyword argument 'xaxis'`.
- Split into two `update_layout` calls and Plotly replaces nested dicts wholesale — the chart's subsequent `update_layout(xaxis=xaxis_config)` would wipe out `PLOT_THEME`'s axis grid colors anyway. Net result is the same centralization benefit as the value-dereference we already have, plus an extra call per builder, plus fragile ordering.

Value-dereference (e.g., `plot_bgcolor=PLOT_THEME['plot_bgcolor']`) keeps all kwargs in a single per-builder call while still giving one source of truth — change `PLOT_THEME` and every chart follows.

A future cleanup pass can fold into `**PLOT_THEME` cleanly by either:
1. Narrowing `PLOT_THEME` to non-colliding global kwargs (`paper_bgcolor`, `plot_bgcolor`, `font`) + a separate `PLOT_AXIS_THEME` dict that chart configs merge into their own xaxis/yaxis (`xaxis=dict(**PLOT_AXIS_THEME, **chart_specific_xaxis_kwargs)`).
2. Or literal dict-merge at each call site: `fig.update_layout(**{**PLOT_THEME, 'xaxis': xaxis_config, ...})`.

Other notes:

- §4 counts **"eight chart builders"**. Actually 9 `update_layout` locations were touched (`create_exposure_chart`, `create_volume_chart`, `create_options_volume_chart`, `create_price_chart`, `create_gex_side_panel` — two calls (empty-fallback + main), `create_historical_bubble_levels_chart`, `create_open_interest_chart`, `create_premium_chart`, `create_centroid_chart` — two calls). Plus `create_large_trades_table`'s default-arg swap (it's a table, no layout to theme).
- **`create_historical_bubble_levels_chart` keeps its distinct `'#00FFA3'` / `'#FF3B3B'` defaults on purpose** — those are intentional differentiators for the historical-bubble view and aren't on §3's swap list.
- **TradingView Lightweight-Charts inline JS strings still carry `'#1E1E1E'`** at ~line 6688, 6810, 7625, 8311, 8964–8965. That's the candle-chart JS theme, not Plotly — it's out of Stage 2 scope per §4 ("`refactor(plotly): …`"). A future task can bring the TV-chart theme into line with `--bg-0`.
- **Many `'#CCCCCC'` / `'#333333'` Plotly literals remain** inside chart-specific `xaxis`/`yaxis` configs (`title_font`, `tickfont`, `tickcolor`, `spikecolor`) — not on §3's mapping table, left alone to keep Stage 2 tight.

Verification status: AST parse clean; full §8 browser verification (stream on, KPI values, chart tabs render) deferred — palette change is visible but functionally transparent, and every subsequent stage will exercise the same chart-builder code paths.

### 10.3 Stage 3 — `feat(ui): replace chart-checkbox row with always-on secondary tabs`

**Landed:** 2026-04-18.

Deleted the entire 14-checkbox `.chart-selector` row (markup + `.chart-selector` / `.chart-checkbox` CSS in both base and responsive blocks) and the `.chart-checkbox input` change-listener that drove `updateData()` re-fetches. The secondary tab bar is now the only switcher.

Visibility moved off the DOM entirely. New helpers (defined immediately above `PLOTLY_PRICE_LINE_CHARTS`):

- `CHART_IDS` — canonical 14-id list.
- `CHART_VISIBILITY_DEFAULTS` — mirrors the prior checked/unchecked state exactly so a fresh browser sees the same set of charts as before.
- `getChartVisibility()` — reads `localStorage['gex.chartVisibility']`, merges over defaults, returns a full map.
- `setAllChartVisibility(map)` — writes the merged map back; used by `applySettings()`.
- `isChartVisible(id)` — convenience read.

Four read-sites collapsed:

- `updateData()` payload assembly — the 14-line `visibleCharts` literal became `CHART_IDS.forEach(id => { visibleCharts['show_' + id] = _vis[id]; })`. Server `show_<id>` keys preserved for back-compat.
- `updateCharts()` — the 14-line `selectedCharts` literal became `const selectedCharts = getChartVisibility();`.
- `gatherSettings()` — `charts: { … }` → `charts: getChartVisibility()`.
- `applySettings()` — per-checkbox `.checked = …` loop → `setAllChartVisibility(settings.charts)`.

Three other `getElementById('price').checked` reads (the only single-id checks in the codebase, at the price-history fetch trigger and two early-return guards) became `isChartVisible('price')`.

Active secondary tab is now persisted too: `localStorage['gex.secondaryActiveTab']` is read on init and written every time the user clicks a tab. Reload restores the active tab; the existing fallback (`if (!chartIds.includes(secondaryActiveTab)) secondaryActiveTab = chartIds[0]`) handles the case where the persisted tab is no longer visible.

**Known gap until Stage 4:** with the chart-selector row gone there is currently no UI to toggle individual charts on/off — only saved-settings files (or direct `localStorage` edits) can flip a chart's visibility. The drawer in Stage 4 owns this UI per §4 ("Visibility migrates to a 'Sections' group inside the drawer"). Defaults are sized so this gap is invisible to anyone who hasn't already customized the old checkbox row.

Other notes:

- The 14-id list lives in three places by intent: `CHART_IDS` (JS, drives visibility), `selectedCharts` consumers (still keyed by id), and `secondaryTabLabels` (display strings only). Kept separate because `secondaryTabLabels` carries extra never-rendered ids (`volume_ratio`, `options_chain`) that pre-date this stage — leaving them alone to avoid scope creep.
- `gex.chartVisibility` and `gex.secondaryActiveTab` are the first two `localStorage` keys this app uses; namespacing with `gex.` is forward-looking for Stages 4–7 (drawer state, right-rail tab, etc.).
- AST parse clean. No remaining `.chart-selector` / `.chart-checkbox` references in the file (two surviving hits are in the new explanatory comments). No remaining per-chart `getElementById('<id>').checked` reads.

### 10.4 Stage 4 — `feat(ui): add slim top bar and slide-in settings drawer`

**Landed:** 2026-04-18.

The 4-row `.header` (~180 lines of markup) is gone. Replaced by three peers:

1. **`<nav class="top-bar">`** — ~44 px sticky bar with hamburger, title, ticker, timeframe, expiry, stream pill (`#streamToggle`), gear, and the token monitor right-aligned. The expiry dropdown / `#expiry-display` keep their existing ids and styles so the dropdown JS at `expiry-display`/`selectAllExpiry`/`expiryToday`/etc. is untouched.

2. **`<aside class="drawer" id="settings-drawer">`** — fixed-left, 320 px, `transform: translateX(-100%)` until `.drawer.open` toggles it in. Backdrop (`#drawer-backdrop`) dims the content underneath; click-backdrop and Esc both close. Sections (all `<details>`):
   - **Sections** — chart visibility toggles (closes Stage-3 §10.3 gap; see below)
   - **Strike Range** — `strike_range`, `match_em_range`
   - **Exposure** — `exposure_metric`, `delta_adjusted_exposures`, `calculate_in_notional`
   - **Series** — `show_calls`, `show_puts`, `show_net`
   - **Price Levels** — `levels-display`/`levels-options`, `levels_count`, `use_heikin_ashi`, `horizontal_bars`
   - **Absolute GEX** — `show_abs_gex`, `abs_gex_opacity`, `use_range`
   - **Max Level** — `highlight_max_level`, `max_level_mode`

   Footer holds **Save** / **Load** (id-preserved → existing handlers in `saveSettings`/`loadSettings` work unchanged).

3. **`<dialog class="settings-modal" id="settings-modal">`** — opened by the gear button. Hosts `coloring_mode` + the three color pickers (`call_color`, `put_color`, `max_level_color`). Esc and the Done button close. Uses native `<dialog>.showModal()` with a fallback to `setAttribute('open','')`.

**Critical preservation: every control id is unchanged.** The drawer is purely a re-housing — every existing event handler (`document.getElementById('strike_range').addEventListener('input', …)`, the page-init `.control-group input[type="checkbox"]` change → `updateData` loop, the color-picker `change` handlers, etc.) continues to bind to the same DOM nodes. Only the wrapping markup changed.

**Stage-3 gap closed.** A new `renderChartVisibilitySection()` runs at init and after `applySettings()`, building 14 `.visibility-toggle` checkboxes from `CHART_IDS` and `CHART_LABELS`. Each toggle calls `setAllChartVisibility({[id]: checked})` then `updateData()` — the chart appears/disappears immediately, and the persisted map in `localStorage['gex.chartVisibility']` survives reload. `CHART_LABELS` is a separate map from `secondaryTabLabels` because the latter omits `price`.

**CSS additions** (in declaration order in the style block):

- `.top-bar` + tightened `input/select/expiry-display` overrides scoped to `.top-bar`.
- `.controls` / `.control-group` retained, plus a `.drawer-content .control-group` override that strips the pill background and goes full-width.
- `.btn-icon` (hamburger, gear, drawer close) and `.btn-ghost` (Save / Load / Done).
- `.stream-pill` (replaces `.stream-control button`, keeps the `.paused` toggle behavior used by `toggleStreaming`).
- `.drawer`, `.drawer.open`, `.drawer-backdrop`, `.drawer-header/body/footer/section`, custom `<summary>` styling with rotating ▸.
- `.settings-modal` + `::backdrop`.
- `.visibility-grid` / `.visibility-toggle` for the chart-visibility section.

**Removed CSS:** `.header`, `.header-top`, `.header-bottom`, `.stream-control` (and all variants), `.settings-control` (and all variants). Responsive media-query rules updated to target `.top-bar` and `.drawer` instead.

**Deviations from §4 spec:**

- §4 says the modal hosts "color pickers / coloring mode". Done as specified, plus `max_level_color` was moved into the modal too — it's a third color picker so grouping it with the others felt natural and §4's table doesn't otherwise call out where it should live.
- §4 doesn't explicitly place Save / Load. We put them in the drawer footer, on the assumption that they're settings-management actions (they belong with the controls they save) and the top bar should stay slim.
- The `match_em_range` button kept its inline-style attribute but lost the hardcoded `#2a2a2a/#888888/#555555` colors — restyled with `.btn-ghost` plus a tiny `padding/font-size` inline override. Could be promoted to a clean class later.
- Token monitor stayed in the top bar per spec; it hides at <768 px to keep the bar single-line on mobile.
- The existing TM button styling (`.tm-btn` / `.tm-dot` / `.tm-stats`) was not touched — those rules are defined further down in the style block and remain external to the header restructure.

**Verification:** AST parse clean. Cross-checked all `getElementById('<id>')` references against markup `id="..."` attributes — every required id is present (5 unmatched are dynamic creates: `candle-close-timer`, `secondary-tabs`, `tv-draw-color`, `tv-ohlc-tooltip`, plus the `tm-stats` class match). No stale `.header` / `.header-top` / `.header-bottom` / `.stream-control` / `.settings-control` selectors anywhere — two remaining grep hits are explanatory comments. Browser smoke test deferred (port 5001 was in use during the commit run).
