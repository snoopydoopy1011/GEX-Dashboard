# GEX Dashboard — Analytics + Chart Phase 2

**Status:** Not started — 0 of 4 stages landed
**Owner:** @snoopydoopy1011
**Created:** 2026-04-18
**Target branch:** `feat/analytics-phase2`
**Base:** `main` @ `e276e63` (Merge pull request #1 from snoopydoopy1011/feat/ui-modernization)
**Prior initiative (complete):** [`UI_MODERNIZATION_PLAN.md`](UI_MODERNIZATION_PLAN.md) — read §1 and §3 for layout + design tokens before editing any markup in this effort.

---

## 0. Where are we? (read this first)

**Current state (as of 2026-04-18):** branch not cut, no stages landed.

Line numbers in this doc are a snapshot as of `main` @ `e276e63`. They drift as soon as Stage 1 lands. **Grep by anchor name** (function names, CSS class names, element IDs) instead of trusting line numbers. Stable anchors used throughout this doc:

- Python functions: `fetch_price_data`, `filter_market_hours`, `aggregate_to_hourly`, `compute_trader_stats`, `compute_key_levels`, `compute_greek_exposures`, `calculate_expected_move_snapshot`, `_window_sum` (private helper inside `compute_trader_stats`).
- JS functions: `applyRightRailTab`, `wireRightRailTabs`, `renderRailAlerts`, `renderGexSidePanel`, `syncGexPanelYAxisToTV`.
- CSS / HTML anchors: `.right-rail-tabs`, `.right-rail-panels`, `.right-rail-panel`, `data-rail-tab`, `data-rail-panel`, `.gex-side-panel-wrap`, `.kpi-card`, `.top-bar`, `.drawer`, `.secondary-tabs`.

To determine which stage is next:

```bash
git branch -a
git log --oneline main..feat/analytics-phase2
```

Match commit subjects against the 4 stages in §7. Subjects follow §6.2 exactly. If the branch doesn't exist yet, cut it from `main` and start at Stage 1. If N stages have landed, start at N+1.

---

## 1. Context

The post-modernization dashboard is visually clean but analytically shallow relative to desks that display Dealer Hedge Impact and Scenario GEX tables alongside the chart. Three concrete complaints from the user:

1. **Chart looks like one session.** Backend pulls 5 days of RTH candles from Schwab `priceHistory`, `timeScale().fitContent()` zooms to the full range, and the last session dominates the view. Users expect ~20–30 trading days available by default.
2. **Alerts are four strings.** `compute_trader_stats()` emits `{level, text}` for Near Call/Put Wall, Approaching Gamma Flip, and Long/Short gamma regime. No dealer-hedge panel, no scenario stress table, even though Vanna and Charm are already computed by `compute_greek_exposures` and never surfaced.
3. **GEX side panel placement — settled.** The user considered moving GEX to an on-chart histogram overlay (like TV's volume histogram). **Decision: keep the Plotly side panel** (strike fidelity matters) and instead enrich the main chart with more `createPriceLine` overlays. This avoids a fidelity compromise and keeps the rail's tab architecture.

**Goal:** add a Dealer Hedge Impact block + a Scenario GEX table to the right rail, extend candle history, and enrich the main chart with more key-level price lines. **No analytical formulas change** — vanna, charm, gamma, expected-move, and key-level math stay exactly as they are today. This initiative only reuses or aggregates what `compute_greek_exposures` already emits (no new Greeks), with one exception: scenario GEX requires shifting spot and IV inside existing formulas (no new formula — just parameterized re-use).

---

## 2. Target additions

```
┌──────────────────────────────────────────────────────────────────────┐
│ [☰]  SPY  5 min  2026-04-20  ●Auto-Update  ⚙                         │   (unchanged top bar)
├────────┬──────────────────────────────────────────────┬──────────────┤
│        │ KPI strip (unchanged)                        │  G  A  L  S  │   + new "Scenarios" tab
│        ├──────────────────────────────────────────────┤ ┌──────────┐ │
│        │   Candles +                                  │ │ Dealer   │ │   new: Dealer
│ Left   │   Call/Put walls, Γ-flip, ±1σ EM (existing)  │ │ Impact   │ │   Impact block
│ drawer │   **+ HVL, ±2σ EM, secondary walls (new)**   │ ├──────────┤ │   at top of GEX
│        │                                              │ │ GEX bars │ │   rail panel
│        │                                              │ └──────────┘ │
│        ├──────────────────────────────────────────────┤              │
│        │ Gamma/Delta/Vanna/... tabs (unchanged)       │              │
└────────┴──────────────────────────────────────────────┴──────────────┘
```

- **Candle history**: 5-min default view shows ~20 RTH sessions instead of ~5.
- **Right rail**: GEX tab gains a Dealer Impact block above the Plotly bars. A fourth tab "Scenarios" is added.
- **Chart**: three new price lines (HVL, ±2σ EM, secondary wall) join the existing five.

---

## 3. Reuse inventory

Stage design leans on code that already exists. Before writing anything new, locate these:

| What | Where (as of `e276e63`) | Why it matters |
|---|---|---|
| Black-Scholes greeks recompute | `compute_greek_exposures` (~line 1261–1440) | Stage 3 extracts a pure `_recompute_greeks(df, S, iv_override)` helper from inside this function. No formula edits. |
| VEX (vanna exposure) per row | written at ~`:1408`, `VEX` column on calls/puts DataFrames | Stage 2 sums this in-window for the vanna-delta-shift metric. |
| Charm exposure per row | `:1412`, `Charm` column | Stage 2 sums this and scales by time-to-close for Charm-by-close. |
| `_window_sum` strike-window sum | private to `compute_trader_stats`, ~`:3341` | Stage 2 generalizes it to accept a column name (currently hard-codes `'GEX'`). |
| Key-level ranking (walls) | `compute_key_levels` in `ezoptionsschwab.py` (~`:3230`+) | Today only the top wall is surfaced. Stage 4 reads rank `[1]` for secondary wall. |
| Expected move math | `calculate_expected_move_snapshot` (~`:181–217`) | Already returns ±1σ. Stage 4 multiplies `move` by 2 for ±2σ — no new math. |
| TV `createPriceLine` pattern | in the TV setup block, ~`:9200–9225` | Copy exactly for HVL / ±2σ / secondary-wall lines. |
| Rail tab wiring | `applyRightRailTab()` (~`:8854–8894`), HTML at `:6395–6427`, rebuild path at `:8770–8776` | Stage 3 adds a 4th tab; touch all three locations. |
| Unread badge mechanism | `:9025–9078` | Stage 3 does **not** give Scenarios an unread badge. |

---

## 4. Data contract additions

All new backend data rides the existing `/update_price` response. No new endpoints. `compute_trader_stats()` gets four new scalar fields and one new list:

```python
# existing fields unchanged
{
  'net_gex': ..., 'hedge_per_1pct': ..., 'regime': ...,
  'em_move': ..., 'em_upper': ..., 'em_lower': ..., 'em_pct': ...,
  'call_wall': ..., 'put_wall': ..., 'gamma_flip': ...,
  'spot': ..., 'alerts': [...],

  # Stage 2 additions — all in dollars, signed, same convention as hedge_per_1pct
  'hedge_on_up_1pct':   float,   # dealer $-flow on +1% spot (sign: + means dealers buy)
  'hedge_on_down_1pct': float,   # dealer $-flow on -1% spot
  'vanna_delta_shift_per_1volpt': float,  # delta $-shift per +1 IV point (negate for -1pt)
  'charm_by_close':     float,   # delta $-decay from now to 16:00 ET

  # Stage 3 additions
  'scenarios': [                 # 7 rows, see §7 Stage 3 for full spec
    {'label':'Current', 'net_gex':..., 'regime':'Long Gamma', 'magnitude':'high'},
    {'label':'+2% spot', ...},
    {'label':'-2% spot', ...},
    {'label':'+5 vol',   ...},
    {'label':'-5 vol',   ...},
    {'label':'+2%/-5 vol', ...},
    {'label':'-2%/+5 vol', ...},
  ],
}
```

Stage 4 does not touch this payload — it adds new fields to the `levels` object already built by `compute_key_levels` (secondary wall rank-`[1]`, HVL strike). `em_upper_2s` / `em_lower_2s` can be derived on the frontend from the existing `em_move` field (multiply by 2 from spot).

---

## 5. Ground rules

From `CLAUDE.md` and the original modernization plan — all still in force:

- **No analytical-formula changes.** Vanna, charm, gamma, delta, expected move, key-level detection formulas stay exactly as implemented. Stage 3 parameterizes inputs to existing formulas; it does not introduce new Greeks.
- **No JS framework.** Vanilla JS + CSS tokens only. No React, no Vue, no build step.
- **Single-file app.** `ezoptionsschwab.py` stays a single file. No new Python modules. No new JS bundles.
- **One commit per stage.** Follow the subject convention in §6.2.
- **No pushes to `main`** during the effort. Merge via PR at the end.
- **Tokens only for colors.** Use existing `:root` design tokens (`--call`, `--put`, `--warn`, `--info`, `--fg-0/1/2`, `--bg-0/1/2/3`). Zero neon hex literals in new code — this was explicitly retired in the modernization effort (commit `9923320`).
- **Tabular numerics** for any dollar/percentage displayed in a column (Dealer Impact block, Scenarios table). Reuse the class `.tabular-nums` (already defined from commit `d457190`), not a new class.

---

## 6. Git workflow

### 6.1 Branch

```bash
git checkout main && git pull
git checkout -b feat/analytics-phase2
```

Do **not** push or open a PR until all 4 stages land and have been eyeballed end-to-end by the user.

### 6.2 Commit subjects (stable — grep these to determine next stage)

```
feat(chart): extend candle history window per-timeframe             # Stage 1
feat(analytics): dealer hedge impact panel in GEX rail              # Stage 2
feat(analytics): scenario GEX table as right-rail tab               # Stage 3
style(chart): add HVL, ±2σ EM, and secondary walls to price lines   # Stage 4
```

One commit per stage. No squashing mid-effort. Commit bodies should list the files touched and any non-obvious reasoning.

### 6.3 Stage gates

Before committing a stage:

1. `python ezoptionsschwab.py` starts cleanly (no import errors, no tracebacks in the first 30 seconds of tick updates).
2. Run the stage's verification checklist from §7.
3. Browser-test the golden path: SPY, 5-min timeframe, near-ATM expiry, tick through a few `/update_price` cycles. Confirm no console errors.
4. Regression-spot-check the other modernization surfaces (drawer, settings modal, Alerts tab badge, Levels tab). They should be identical.

### 6.4 Merge

After Stage 4:

```bash
git push -u origin feat/analytics-phase2
gh pr create --base main --title "Analytics + Chart Phase 2" --body "<per-stage summary>"
```

Body should be a 4-bullet per-stage summary plus a short "What's new in the UI" section for the user.

---

## 7. Stages

Each stage is self-contained and ships a working UI. If you stop mid-effort, the app still runs correctly.

### Stage 1 — Extend candle history per-timeframe

**Branch state entering:** `feat/analytics-phase2` cut from `main @ e276e63`.

**Why:** `fetch_price_data` pulls `period=5` days regardless of timeframe, so 1-min / 5-min / 15-min / 60-min all return the same 5-day window. At finer timeframes this is too short; at coarser it's wasteful. Map `period` to timeframe.

**Files:**

- `ezoptionsschwab.py` — `fetch_price_data` (~`:2395–2462`)

**Change:** replace the static `period=5` with a lookup. Insert above the `client.price_history(...)` call:

```python
PERIOD_BY_TF = {1: 5, 5: 20, 15: 30, 30: 30, 60: 90}   # trading days per timeframe
period_days = PERIOD_BY_TF.get(timeframe, 20)
```

Then:

- Replace `period=5,` with `period=period_days,`.
- Replace `start_date = datetime.combine(current_date - timedelta(days=5), ...)` with `timedelta(days=period_days + 5)` (the `+5` cushions for weekends/holidays that `filter_market_hours` will later discard).
- Leave `filter_market_hours` alone — extended-hours bars stay excluded.
- Leave `aggregate_to_hourly` alone.

**Frontend:** no change. `tvChart.timeScale().fitContent()` at `:7064` / `:7249` auto-fits the new range. Pan/zoom already works.

**Verification:**

1. Start app, load SPY at 5-min → chart should show ~20 RTH sessions (four weeks of candles).
2. Switch to 1-min → ~5 sessions.
3. Switch to 30-min → ~30 sessions.
4. Switch to 60-min → ~90 sessions (after `aggregate_to_hourly` runs).
5. No gaps at night (filter still strips overnight bars).
6. Weekend appears as a clean break, not a flatline.

**Commit:** `feat(chart): extend candle history window per-timeframe`

---

### Stage 2 — Dealer Hedge Impact block in GEX rail

**Why:** Give the GEX tab parity with the reference dashboard's top section: Spot +1% / Spot -1% / Vol -1pt vanna shift / Charm-by-close. All the inputs already exist; this stage is 100% surfacing work plus one small scalar calculation.

**Files:**

- `ezoptionsschwab.py` — `compute_trader_stats` (~`:3312–3378`)
- `ezoptionsschwab.py` — CSS `<style>` block (~line 4853+ range)
- `ezoptionsschwab.py` — HTML at `:6412–6415` (the `data-rail-panel="gex"` div)
- `ezoptionsschwab.py` — JS inside `/update_price` response handler

**Backend:**

1. Generalize `_window_sum` (currently private to `compute_trader_stats`, `:3341`) to accept a column name: `_window_sum(df, col='GEX')`. Update its two existing callers.
2. After the existing `call_gex = _window_sum(calls)` / `put_gex = _window_sum(puts)` block, add:

```python
out['hedge_on_up_1pct']   = +0.01 * net_gex
out['hedge_on_down_1pct'] = -0.01 * net_gex

vex_call = _window_sum(calls, col='VEX')
vex_put  = _window_sum(puts,  col='VEX')
out['vanna_delta_shift_per_1volpt'] = vex_call + vex_put

charm_call = _window_sum(calls, col='Charm')
charm_put  = _window_sum(puts,  col='Charm')
# hours to 16:00 ET / 6.5 regular-session hours → fraction of intraday charm still to bleed
now_et = datetime.now(pytz.timezone('US/Eastern'))
close_et = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
hours_left = max(0.0, (close_et - now_et).total_seconds() / 3600.0)
out['charm_by_close'] = (charm_call + charm_put) * (hours_left / 6.5)
```

The magnitudes produced by `compute_greek_exposures` for VEX and Charm are already weighted correctly (see `:1408`, `:1412`) — do not apply extra scaling.

**Frontend:**

1. CSS: add a `.dealer-impact` block. Two-column grid, label left / value right. Reuse existing tokens. Reuse `.tabular-nums` on the values.

```css
.dealer-impact {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 4px 12px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--border);
  font-size: 12px;
}
.dealer-impact .label { color: var(--fg-1); }
.dealer-impact .sub   { color: var(--fg-2); font-size: 11px; margin-top: -2px; }
.dealer-impact .val   { font-variant-numeric: tabular-nums; text-align: right; }
.dealer-impact .val.pos { color: var(--call); }
.dealer-impact .val.neg { color: var(--put); }
```

2. HTML: inside `<div class="right-rail-panel active" data-rail-panel="gex">` at `:6412`, insert a new block **before** the existing GEX panel wrap:

```html
<div class="dealer-impact" id="dealer-impact">
  <div class="label">Spot +1% <div class="sub">dealers buy/sell</div></div><div class="val" data-di="hedge_on_up_1pct">—</div>
  <div class="label">Spot −1% <div class="sub">dealers buy/sell</div></div><div class="val" data-di="hedge_on_down_1pct">—</div>
  <div class="label">Vol +1 pt<div class="sub">vanna delta shift</div></div><div class="val" data-di="vanna_up_1">—</div>
  <div class="label">Vol −1 pt<div class="sub">vanna delta shift</div></div><div class="val" data-di="vanna_down_1">—</div>
  <div class="label">Charm by close<div class="sub">intraday decay remaining</div></div><div class="val" data-di="charm_by_close">—</div>
</div>
```

Also add the same block to the rebuild path at `:8770–8776` so rail regenerations don't drop it.

3. JS: after the existing `renderTraderStats(stats)` call in the `/update_price` handler, add:

```js
function fmtDollars(v) {
  if (v == null) return '—';
  const a = Math.abs(v);
  if (a >= 1e9) return (v/1e9).toFixed(2) + 'B';
  if (a >= 1e6) return (v/1e6).toFixed(2) + 'M';
  if (a >= 1e3) return (v/1e3).toFixed(1) + 'K';
  return v.toFixed(0);
}
function renderDealerImpact(stats) {
  const el = document.getElementById('dealer-impact');
  if (!el || !stats) return;
  const set = (key, v) => {
    const n = el.querySelector(`[data-di="${key}"]`);
    if (!n) return;
    n.textContent = (v == null ? '—' : (v > 0 ? '+' : '') + fmtDollars(v));
    n.classList.remove('pos','neg');
    if (v != null && v !== 0) n.classList.add(v > 0 ? 'pos' : 'neg');
  };
  set('hedge_on_up_1pct',   stats.hedge_on_up_1pct);
  set('hedge_on_down_1pct', stats.hedge_on_down_1pct);
  set('vanna_up_1',         stats.vanna_delta_shift_per_1volpt);
  set('vanna_down_1',       stats.vanna_delta_shift_per_1volpt == null ? null : -stats.vanna_delta_shift_per_1volpt);
  set('charm_by_close',     stats.charm_by_close);
}
```

Call `renderDealerImpact(stats)` every place `renderTraderStats(stats)` is called.

**Verification:**

1. Open GEX tab → 5-row block renders above the Plotly chart.
2. Values update per tick.
3. Sign/color: long-gamma SPY → Spot +1% should be green (dealers buy rallies? actually the sign convention matches existing `hedge_per_1pct`; reference the KPI strip to sanity-check).
4. Charm-by-close ticks toward 0 as 16:00 ET approaches; near-zero after market close.
5. Formatting: no NaN, no `$undefined`, tabular alignment holds.

**Commit:** `feat(analytics): dealer hedge impact panel in GEX rail`

---

### Stage 3 — Scenario GEX table as right-rail tab

**Why:** Stress-test net GEX under ±2% spot and ±5 IV-point shifts, plus two combined scenarios. Seven rows, glanceable, same table aesthetic as Dealer Impact.

**Files:**

- `ezoptionsschwab.py` — `compute_greek_exposures` (~`:1261–1440`) — **refactor** to extract pure helper.
- `ezoptionsschwab.py` — new function `compute_scenario_gex` (insert near `compute_trader_stats`, ~`:3310`).
- `ezoptionsschwab.py` — `compute_trader_stats` — call `compute_scenario_gex`, add `out['scenarios']`.
- `ezoptionsschwab.py` — HTML at `:6395–6427` and rebuild path at `:8770–8776`.
- `ezoptionsschwab.py` — JS `applyRightRailTab` at `:8854–8894`, new `renderScenarioTable`.

**Backend:**

1. Extract a pure helper from `compute_greek_exposures`:

```python
def _recompute_gex_row(row, S, iv_override=None):
    """Return GEX for a single option row given spot S and optional IV override.
    Formulas match compute_greek_exposures exactly — no changes, only parameterization."""
    # Move the existing gamma + weighting math from compute_greek_exposures into here.
    # Accept iv_override; if None, use row['volatility'].
    # Return the dollar GEX value for that row.
```

Important: copy the formula lines verbatim from `compute_greek_exposures`. Do not alter constants, do not round differently, do not reorder multiplications. If there's drift between this helper and the original, scenarios will disagree with the KPI strip at `spot_shift=0, iv_shift=0`, which is the first regression check.

Optionally refactor `compute_greek_exposures` to loop-call `_recompute_gex_row` instead of keeping duplicated math. Skip that refactor if it risks touching hot-path code; acceptable to have the formula duplicated across the two functions for this initiative.

2. New function:

```python
def compute_scenario_gex(calls, puts, S, spot_shift=0.0, iv_shift=0.0,
                         strike_range=0.02, selected_expiries=None):
    """Re-sum net GEX under a spot-shift and/or IV-shift.
    No new math — reuses _recompute_gex_row against shifted inputs.
    Returns {'net_gex': float, 'regime': 'Long Gamma'|'Short Gamma'}."""
    S_new = S * (1.0 + spot_shift)
    lo, hi = S_new * (1 - strike_range), S_new * (1 + strike_range)
    def _sum(df):
        if df is None or df.empty: return 0.0
        if selected_expiries and 'expiration_date' in df.columns:
            df = df[df['expiration_date'].isin(selected_expiries)]
        f = df[(df['strike'] >= lo) & (df['strike'] <= hi)]
        if f.empty: return 0.0
        return sum(_recompute_gex_row(r, S_new,
                                      iv_override=(r['volatility'] + iv_shift))
                   for _, r in f.iterrows())
    net = _sum(calls) - _sum(puts)
    return {'net_gex': net, 'regime': 'Long Gamma' if net >= 0 else 'Short Gamma'}
```

3. In `compute_trader_stats`, after existing calculations:

```python
base_abs = abs(out['net_gex']) if out['net_gex'] else 1.0
def _mag(v):
    if v is None: return 'low'
    r = abs(v) / max(base_abs, 1.0)
    return 'high' if r >= 0.75 else ('med' if r >= 0.35 else 'low')
scenarios = [
    ('Current',      0.0,   0.0),
    ('+2% spot',    +0.02,  0.0),
    ('−2% spot',    -0.02,  0.0),
    ('+5 vol',       0.0,  +0.05),
    ('−5 vol',       0.0,  -0.05),
    ('+2%/−5 vol',  +0.02, -0.05),
    ('−2%/+5 vol',  -0.02, +0.05),
]
out['scenarios'] = []
for label, ss, ivs in scenarios:
    r = compute_scenario_gex(calls, puts, S, spot_shift=ss, iv_shift=ivs,
                             strike_range=strike_range, selected_expiries=selected_expiries)
    out['scenarios'].append({
        'label': label,
        'net_gex': r['net_gex'],
        'regime': r['regime'],
        'magnitude': _mag(r['net_gex']),
    })
```

IV is stored as a decimal (0.20 = 20%), so `iv_shift=0.05` ≡ 5 vol points. Double-check by logging one row of `row['volatility']` before merging.

**Frontend:**

1. HTML — add fourth tab. At `:6395–6398`, after `levels`:

```html
<button type="button" class="right-rail-tab" data-rail-tab="scenarios">Scenarios</button>
```

At `:6411–6427`, after the levels panel:

```html
<div class="right-rail-panel" data-rail-panel="scenarios">
  <table class="scenario-table" id="scenario-table">
    <thead><tr><th>Scenario</th><th>Net GEX</th><th>Regime</th></tr></thead>
    <tbody></tbody>
  </table>
</div>
```

Mirror the same additions in the rebuild path at `:8770–8776`.

2. CSS:

```css
.scenario-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.scenario-table th {
  text-align: left;
  font-weight: 500;
  color: var(--fg-2);
  padding: 6px 10px;
  border-bottom: 1px solid var(--border);
}
.scenario-table td {
  padding: 6px 10px;
  border-bottom: 1px solid var(--border);
}
.scenario-table td.num { text-align: right; }
.scenario-table td.num.pos { color: var(--call); }
.scenario-table td.num.neg { color: var(--put); }
.scenario-table td .mag { color: var(--fg-2); font-size: 11px; margin-left: 6px; }
```

3. JS — new renderer plus dispatch:

```js
function renderScenarioTable(rows) {
  const tbl = document.getElementById('scenario-table');
  if (!tbl) return;
  const tbody = tbl.querySelector('tbody');
  tbody.innerHTML = (rows || []).map(r => {
    const sign = (r.net_gex != null && r.net_gex < 0) ? 'neg' : 'pos';
    return `<tr>
      <td>${r.label}</td>
      <td class="num ${sign}">${fmtDollars(r.net_gex)}</td>
      <td>${r.regime} <span class="mag">(${r.magnitude})</span></td>
    </tr>`;
  }).join('');
}
```

In `applyRightRailTab` at `:8854+`, add a branch:

```js
if (which === 'scenarios') {
  renderScenarioTable(_lastStats && _lastStats.scenarios);
}
```

Also call `renderScenarioTable(stats.scenarios)` inside the `/update_price` response handler whenever the scenarios tab is active (gate with `if (activeRailTab === 'scenarios')` to avoid DOM work on background ticks).

Stash `_lastStats` as a module-level variable if it doesn't already exist (check — `renderRailAlerts` already caches an equivalent via `_lastRailAlerts`; follow the same pattern).

**No unread badge** for Scenarios.

**Verification:**

1. Click Scenarios tab → table renders with 7 rows.
2. `Current` row value === KPI strip's Net GEX (within floating-point tolerance). This is the critical regression check — a mismatch means `_recompute_gex_row` drifted from `compute_greek_exposures`.
3. Monotonicity spot-check: long-gamma regime → `+2% spot` row magnitude lower than current (dealers delta-hedge out), `−2% spot` higher; short-gamma → opposite.
4. `±5 vol` shifts change Net GEX materially (tens of percent), not marginally.
5. Regime column flips at least once across the 7 rows for most tickers (confirms spot shift crosses gamma flip).
6. Tab persistence: reload with `?rail=scenarios` style behavior — `applyRightRailTab`'s `localStorage` should remember the tab.

**Commit:** `feat(analytics): scenario GEX table as right-rail tab`

---

### Stage 4 — On-chart key-level enrichment

**Why:** Now that GEX stays in the rail, let the chart carry more of the level story. Three additions, all using the existing `createPriceLine` pattern.

**Files:**

- `ezoptionsschwab.py` — `compute_key_levels` (~`:3230`+): expose secondary wall + HVL.
- `ezoptionsschwab.py` — TV setup block ~`:9200–9225`: draw new price lines.

**Backend:**

`compute_key_levels` already ranks walls internally. Locate the block that picks the top call wall / put wall and also emit the second-ranked entries:

```python
levels['call_wall_2'] = {'price': float(ranked_calls[1][1])} if len(ranked_calls) > 1 else None
levels['put_wall_2']  = {'price': float(ranked_puts[1][1])}  if len(ranked_puts) > 1 else None
```

(Replace `ranked_calls` / `ranked_puts` with the actual variable names used in the existing function.)

HVL (highest-volume strike) — sum `openInterest` or `volume` per strike across calls+puts inside the window, take argmax:

```python
if calls is not None and puts is not None and not calls.empty and not puts.empty:
    combined = pd.concat([calls[['strike','volume']], puts[['strike','volume']]])
    grouped = combined.groupby('strike')['volume'].sum()
    # limit to strike window
    S_ = S
    grouped = grouped[(grouped.index >= S_*(1-strike_range)) & (grouped.index <= S_*(1+strike_range))]
    if not grouped.empty:
        levels['hvl'] = {'price': float(grouped.idxmax())}
```

Use `volume` (not `openInterest`) to match the HVL semantic convention of "high-volume strike magnet." If a ticker is illiquid and `volume` is zero, fall back to `openInterest`.

**Frontend:**

In the TV setup block at ~`:9200–9225`, right after the existing 5 `createPriceLine` calls, add:

```js
if (levels.hvl && levels.hvl.price != null) {
  tvCandleSeries.createPriceLine({
    price: levels.hvl.price,
    color: 'rgba(156, 163, 175, 0.8)',   // var(--fg-1)
    lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted,
    axisLabelVisible: true, title: 'HVL',
  });
}
if (em && em.move != null && stats.spot != null) {
  const up2 = stats.spot + 2 * em.move;
  const dn2 = stats.spot - 2 * em.move;
  tvCandleSeries.createPriceLine({ price: up2, color: 'rgba(156,163,175,0.4)',
    lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted,
    axisLabelVisible: true, title: '+2σ EM' });
  tvCandleSeries.createPriceLine({ price: dn2, color: 'rgba(156,163,175,0.4)',
    lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted,
    axisLabelVisible: true, title: '−2σ EM' });
}
if (levels.call_wall_2 && levels.call_wall_2.price != null) {
  tvCandleSeries.createPriceLine({
    price: levels.call_wall_2.price,
    color: 'rgba(16, 185, 129, 0.5)',    // --call at 50% alpha
    lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed,
    axisLabelVisible: true, title: 'Call Wall 2',
  });
}
if (levels.put_wall_2 && levels.put_wall_2.price != null) {
  tvCandleSeries.createPriceLine({
    price: levels.put_wall_2.price,
    color: 'rgba(239, 68, 68, 0.5)',
    lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed,
    axisLabelVisible: true, title: 'Put Wall 2',
  });
}
```

On tick, the existing code at `:9200–9225` removes stale lines before drawing new ones. Locate that removal loop and extend it to cover the new titles too — otherwise lines will stack every tick. Grep for `removePriceLine` or `_priceLineRefs` in the surrounding code to find the cleanup mechanism.

Make all four new overlays respect the existing chart-visibility controls in the drawer (`renderChartVisibilitySection`, `getChartVisibility`). Add toggle entries for HVL, 2σ EM, Secondary Walls — reuse the existing pattern verbatim; if the toggle structure is a simple keyed object, add three keys. Default all three to `true`.

**Verification:**

1. Open chart → four new lines visible alongside the existing five (9 price lines total).
2. Toggle each off in the drawer → line disappears, others untouched.
3. On tick, lines update in place (not stacked).
4. 2σ EM lines sit exactly 2× as far from spot as the existing 1σ lines.
5. `Call Wall 2` price < `Call Wall 1` price (secondary wall is ranked below primary by definition).
6. Illiquid ticker (e.g., a thin single-name) still renders without JS errors — HVL may be absent, others may be absent.

**Commit:** `style(chart): add HVL, ±2σ EM, and secondary walls to price lines`

---

## 8. After all stages land

1. Manual smoke: SPY, QQQ, TSLA, one illiquid single-name. 1/5/15/30/60 min. Click each rail tab. Toggle each chart visibility control. Open drawer, open settings modal. Confirm no console errors and no visual regressions from the modernization effort.
2. Update §0 "Current state" in this doc to `Complete — 4 of 4 stages landed`.
3. Push the branch and open a PR per §6.4.
4. After merge, update `CLAUDE.md` "Active initiative" → "Completed initiative (Phase 2)" and clear the active-initiative line.

---

## 9. Risks and open questions

- **`_recompute_gex_row` drift.** If the extracted helper disagrees with `compute_greek_exposures` by even a small constant, the Scenarios `Current` row will mismatch the KPI strip. Stage 3 verification step 2 exists specifically to catch this. If mismatch: do not ship Stage 3 until fixed.
- **Charm-by-close unit convention.** The existing `Charm` column is already pre-weighted (`:1412` divides by 365). The `hours_left / 6.5` factor assumes the stored quantity is "charm per day of calendar time" — verify by logging a raw row before deploying. If units are per-year, scale adjustment needed.
- **IV-shift direction on vanna-delta.** `VEX` in the codebase is `vanna * weight * contract_size * spot_multiplier * 0.01` at `:1408`. Confirm whether that 0.01 already makes it "per 1 vol point." If it represents per-100%-vol instead, divide by 100 in Stage 2.
- **Backfill:** no historical data needs migrating. SQLite (`options_data.db`) is untouched by this initiative.

---

## 10. Progress log

_(populate as stages land — one bullet each with commit SHA, notes on any deviation from spec)_

- Stage 1 — not started
- Stage 2 — not started
- Stage 3 — not started
- Stage 4 — not started
