# GEX Dashboard — Alerts Rail Modernization + Flow Alerts (Phase 3)

**Status:** Complete — 4 of 4 stages landed
**Owner:** @snoopydoopy1011
**Created:** 2026-04-19
**Target branch:** `feat/analytics-phase3`
**Base:** `main` (once `feat/analytics-phase2` is merged); otherwise cut from `feat/analytics-phase2` head and rebase after merge.
**Prior initiatives (complete):**
- [`UI_MODERNIZATION_PLAN.md`](UI_MODERNIZATION_PLAN.md) — layout, palette, design tokens.
- [`ANALYTICS_CHART_PHASE2_PLAN.md`](ANALYTICS_CHART_PHASE2_PLAN.md) — Dealer Impact block, Scenarios tab, HVL/±2σ/secondary-wall chart lines, extended candle history.

Read §1–3 of the modernization doc and §3 (Reuse inventory) of the Phase 2 doc before editing markup in this effort.

---

## 0. Where are we? (read this first)

**Current state (as of 2026-04-19):** all 4 stages landed on `feat/analytics-phase3`. Ready for PR.

**Line numbers** in this document are a snapshot against `feat/analytics-phase2` HEAD (post-Stage-4 of Phase 2). They will drift as soon as any stage lands here. **Grep by anchor name** — function names, CSS class names, element IDs — rather than trusting line numbers. Stable anchors used throughout:

- **Python functions:** `create_gex_side_panel`, `compute_trader_stats`, `compute_key_levels`, `compute_greek_exposures`, `_recompute_gex_row`, `fetch_options_for_date`, `_ingest_interval_snapshot` (or equivalent — grep `interval_data` for the writer).
- **JS functions:** `updatePriceInfo`, `renderRailAlerts`, `renderDealerImpact`, `applyRightRailTab`, `wireRightRailTabs`, `renderGexSidePanel`, `_lastStats`.
- **CSS / HTML anchors:** `.right-rail-tabs`, `.right-rail-panels`, `.right-rail-panel`, `data-rail-tab`, `data-rail-panel`, `.dealer-impact`, `.rail-alerts-list`, `.rail-alert-item`, `.tab-badge`, `.price-info`.
- **SQLite tables:** `interval_data` (per-strike 1-min snapshots), `centroid_data`, `interval_session_data`.

To determine which stage is next:

```bash
git branch -a
git log --oneline main..feat/analytics-phase3   # or current branch base
```

Match commit subjects against the 4 stages in §7. Subjects follow §6.2 exactly. If N stages have landed, start at N+1.

---

## 1. Context

Phase 2 wrapped the dashboard up to analytical parity with reference trading desks: Dealer Hedge Impact block, Scenarios table, HVL and ±2σ EM on-chart, 20-session candle window. What's still weak: the **right-rail Alerts tab** reads as a flat wall of strings (current price block + 5-row dealer-impact grid + "No active alerts"), and the **alerts list itself** runs on 5 static rule checks (near-wall 0.3%, near gamma-flip 0.5%, long/short gamma regime tags). No live-flow signal surfacing; no card visual hierarchy; no volume spike detection despite having 1-minute per-strike history in SQLite.

The user flagged a reference UI (see session conversation, "Chain Activity" screenshot) whose visual grammar is worth borrowing: stacked card modules, uppercase tracked section headers, colored dots, dual-metric bars, a directional sentiment meter. This initiative adopts that grammar **without copying data fields** — we surface our own (Net GEX, Net DEX, gamma regime, OI C/P ratio, Vol C/P ratio, live flow alerts) inside a matching card system.

**Three independent goals:**

1. **Alerts rail card refresh.** Replace the flat `.price-info` block and the bare `.dealer-impact` grid with a stack of `.rail-card` modules: price header, market metrics, range scale, gamma profile, dealer impact (restyled, same data), chain activity meter, live alerts feed. Dense, glanceable, distinct per-module surface.

2. **GEX side-panel alpha-intensity gradient.** The horizontal red/green bars in the GEX tab currently use a single solid color per sign. Switch to per-bar rgba where alpha scales with `|value| / max(|values|)` — walls dominate, small bars recede. Sign hue (green/red) stays fixed.

3. **Live flow alerts engine.** Upgrade `compute_trader_stats`'s alerts builder from static rule checks to a live spike detector backed by existing SQLite `interval_data`. Four new alert types (volume spike, volume/OI ratio unusual, IV surge, wall shift), each with cooldowns and floors to avoid spam on illiquid strikes.

**No analytical-formula changes.** No new Greeks, no new data sources, no new endpoints. Every new signal comes from data the backend already computes or stores. The single-file `ezoptionsschwab.py` structure and vanilla-JS + CSS-tokens discipline stay in force.

---

## 2. Target layout

### Alerts rail (first tab in right-rail-panels)

```
┌──────────────────────────────┐
│  $710.75   +1.30%    0 DTE   │  price header card
├──────────────────────────────┤
│  NET GEX          NET DEX    │  market-metrics card
│  1.18B            143.6M     │
│  Δ +912K          Δ +75M     │
├──────────────────────────────┤
│  RANGE · EM ±0.64%           │  range scale card
│  $705 ▰▰▰◆▰▰ $715            │
├──────────────────────────────┤
│  ● Positive Gamma            │  gamma profile card
│  dealer hedging dampens moves│
├──────────────────────────────┤
│  DEALER IMPACT               │  existing block, restyled
│  Spot +1%     +$11.8M  ↑     │
│  Spot −1%     −$11.8M  ↓     │
│  Vol +1 pt    +$2.3M         │
│  Vol −1 pt    −$2.3M         │
│  Charm close  −$1.1M         │
├──────────────────────────────┤
│  CHAIN ACTIVITY              │  chain activity card
│  bearish ──◆────── bullish   │
│  OI   ▰▰▰▱▱   C/P 1.55       │
│  VOL  ▰▰▱▱▱   C/P 0.88       │
├──────────────────────────────┤
│  LIVE ALERTS                 │  flow alerts card
│  ⚡ Vol spike  715  · 2m     │
│  ⚠ Put Wall   701→700        │
│  📈 IV surge  710  · 4m      │
│  🔥 V/OI      712  · 1m      │
└──────────────────────────────┘
```

### GEX side panel (second column — unchanged structure)

Same horizontal bars keyed to strike, now with alpha-intensity gradient per bar.

### Levels / Scenarios tabs

Untouched in this phase.

---

## 3. Reuse inventory

Before writing anything new, locate these. They are load-bearing for the stages.

| What | Where (snapshot) | Why it matters |
|---|---|---|
| `CALL_COLOR`, `PUT_COLOR` hex constants | top of `ezoptionsschwab.py` (grep) | Stage 1 feeds these into the per-bar rgba builder |
| `create_gex_side_panel` colors assignment | `:3207` (line `colors = [call_color if v >= 0 else put_color ...`) | Stage 1's only edit point |
| `marker=dict(color=colors, ...)` on the Plotly `go.Bar` trace | `:3224` | Plotly accepts per-bar arrays here; no other change needed |
| `interval_data` schema | `:64–80` | Stage 3 reads 1-min history from here for rolling baselines |
| `interval_data` writer | `:325–472` (grep `_ingest_interval_snapshot` or similar) | Confirms what columns exist (net_volume, net_gamma, etc.); check whether per-strike IV is stored |
| `_options_cache` (live chain dict) | `:10758` | Stage 3 reads today's cumulative volume + openInterest from this for V/OI ratio |
| `compute_trader_stats` existing alerts builder | `:3574–3587` | Stage 3 appends flow alerts to the same `out['alerts']` list |
| `compute_trader_stats` scenarios builder + `chain_activity` insert point | `:3540–3572` | Stage 2 adds `chain_activity` / `profile` near here |
| `.dealer-impact` block + `renderDealerImpact` | HTML `:6773–6779`, CSS `:5506–5525`, JS ~`:9720` | Stage 2 keeps IDs and JS; just nests the block inside a `.rail-card` |
| `.rail-alert-item` base CSS | `:5548–5571` | Stage 3 extends with a `.flow` variant and a `.rail-alert-ago` stamp |
| `.tab-badge` unread mechanism | `:5491–5504`, JS `:9025–9078` | Stage 3 does not disturb — new alerts still increment the badge |
| `updatePriceInfo(info)` | `:10082` | Stage 2 refactors into a fan-out into the new card IDs |
| `renderRailAlerts(list)` | `:9661` | Stage 3 extends to render `ts` → "N m ago" and a `.flow` variant |
| Rail HTML rebuild path | `:8770–8776` | Every new card must be mirrored here (Phase 2 §7 Stage 2 explicitly noted this drops otherwise) |
| `applyRightRailTab` + `RAIL_TAB_KEY` localStorage | `:8854–8894` | Tab persistence across reloads — already includes `'alerts'` |
| CSS token set | `:root` definitions at `:5080–5088` | All new colors must pull from here. Zero neon hex literals. |

---

## 4. Data contract additions

All new backend data rides the existing `/price` (update) response through `compute_trader_stats`. **No new endpoints.**

### Stage 2 additions to `compute_trader_stats` output

```python
out['chain_activity'] = {
  'oi_cp_ratio':  float,   # sum(call.openInterest) / sum(put.openInterest), in-window
  'vol_cp_ratio': float,   # sum(call.volume) / sum(put.volume), in-window
  'sentiment':    float,   # -1..+1, derived from existing call_percentage/put_percentage
}

out['profile'] = {
  'regime':   'Long Gamma' | 'Short Gamma',       # mirrors existing out['regime']
  'headline': 'Positive Gamma' | 'Negative Gamma',
  'blurb':    'dealer hedging dampens moves' | 'dealer hedging amplifies moves',
}

out['session_deltas'] = {                         # optional; may be None if baseline unknown
  'net_gex_vs_open': float | None,
  'net_dex_vs_open': float | None,
}
```

`sentiment` is simply `(call_percentage - put_percentage) / 100.0`, already in `out` post Phase 2. Store it in the new structure for frontend ergonomics.

### Stage 3 additions — alert envelope

Alerts existed pre-Phase-3; shape is currently `{level, text}`. Extend — backwards-compatible — to:

```python
{
  'id':     str,                                  # stable dedup key, e.g. 'vol_spike:715'
  'level':  'warn' | 'info' | 'flow',             # 'flow' is new
  'text':   str,                                  # 'Vol spike @ 715 (4.2× avg)'
  'strike': float | None,                         # optional — enables future click-to-jump
  'ts':     str,                                  # ISO8601 UTC, new field
  'detail': str | None,                           # optional secondary line
}
```

The 5 existing rule-based alerts get `id` and `ts` fields added; `level` stays `'warn'` / `'info'`. Frontend handles missing `ts` gracefully (no "N m ago" rendered).

### Stage 1 — no payload changes

Stage 1 is purely a Plotly color-array change inside `create_gex_side_panel`. The `/price` JSON shape is identical before and after.

---

## 5. Ground rules (inherited from prior initiatives)

- **No analytical-formula changes.** Vanna, charm, gamma, delta, expected-move, key-level detection formulas stay exactly as implemented. Stage 3's "IV surge" uses the per-strike IV that Schwab already returns; no re-derivation.
- **No new data sources.** Everything rides existing `/price` response + `_options_cache` + `interval_data` SQLite history. No new API calls.
- **No JS framework.** Vanilla JS + CSS tokens only.
- **Single file.** `ezoptionsschwab.py` stays a single file. No new Python modules. No new JS bundles.
- **One commit per stage.** Follow subject convention in §6.2.
- **No pushes to `main`** during the effort. Merge via PR at the end.
- **Tokens only for colors.** Reuse `--bg-0/1/2/3`, `--fg-0/1/2`, `--call`, `--put`, `--warn`, `--info`, `--accent`, `--border`, `--border-strong`, `--ok`. Zero neon hex literals.
- **Tabular numerics** (`.tabular-nums`) on every dollar/percent column rendered in a grid.
- **Mirror rail HTML to the rebuild path.** Any new element added under `[data-rail-panel="alerts"]` at `:6770–6796` MUST also appear in the rebuild sequence at `:8770–8776`, or tick rebuilds (triggered by ticker/timeframe switches) will drop it. This bit Phase 2 Stage 2; do not repeat.

---

## 6. Git workflow

### 6.1 Branch

```bash
git checkout main && git pull           # after Phase 2 PR merges
git checkout -b feat/analytics-phase3
```

If `feat/analytics-phase2` hasn't merged yet, cut from its HEAD and plan to rebase after the merge lands.

Do **not** push or open a PR until all 4 stages land and have been eyeballed end-to-end by the user.

### 6.2 Commit subjects (stable — grep these to determine next stage)

```
style(gex): alpha-intensity gradient for side-panel bars        # Stage 1
feat(ui): modernize alerts rail with card modules               # Stage 2
feat(alerts): volume, IV, wall-shift, and V/OI flow alerts      # Stage 3
chore(alerts): polish + regression sweep                        # Stage 4
```

One commit per stage. No squashing mid-effort. Commit bodies list files touched and any deviations from the stage spec.

### 6.3 Stage gates (before each commit)

1. `python ezoptionsschwab.py` starts cleanly (no import errors, no tracebacks in the first 30 seconds of tick updates).
2. Run the stage's verification checklist from §7.
3. Browser-test the golden path: SPY, 5-min timeframe, near-ATM expiry, tick through a few `/price` cycles. No console errors.
4. Regression-spot-check the other modernization surfaces (drawer, settings modal, Levels tab, Scenarios tab, GEX side panel). They should be identical.

### 6.4 Merge

After Stage 4:

```bash
git push -u origin feat/analytics-phase3
gh pr create --base main --title "Alerts Rail Modernization + Flow Alerts (Phase 3)" --body "<per-stage summary>"
```

Body should be a 4-bullet per-stage summary plus a "What's new in the UI" section.

---

## 7. Stages

Each stage is self-contained and ships a working UI. If you stop mid-effort, the app still runs correctly.

### Stage 1 — GEX bar alpha-intensity gradient

**Why:** Make dealer walls pop. Currently every positive bar is the same green and every negative bar the same red, so a strike holding 10× the gamma of its neighbor doesn't read as more important at a glance. Alpha scaling solves this without changing any math, and without changing sign legibility (green stays green, red stays red).

**Files:**

- `ezoptionsschwab.py` — `create_gex_side_panel` (~`:3160–3240`)

**Backend change** (replace the `colors = [...]` list comprehension at `:3207`):

```python
def _hex_to_rgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

call_rgb = _hex_to_rgb(call_color)
put_rgb  = _hex_to_rgb(put_color)
max_abs = max((abs(v) for v in net), default=0) or 1.0

def _shade(v):
    alpha = 0.30 + 0.70 * (abs(v) / max_abs)
    r, g, b = call_rgb if v >= 0 else put_rgb
    return f'rgba({r},{g},{b},{alpha:.3f})'

colors = [_shade(v) for v in net]
```

Floor at 0.30 alpha so every bar is visible. Ceiling is 1.0 (at max_abs). `max_abs` guard prevents div-zero on all-zero panels.

No change to `marker=dict(color=colors, ...)` at `:3224` — Plotly accepts rgba-string arrays natively.

**Verification:**

1. Load SPY 5-min during market hours. The largest wall bar is fully saturated; a bar ~10% of its magnitude renders visibly transparent.
2. Sign distinction still unambiguous — positive bars green, negative bars red.
3. No Plotly console warnings.
4. On low-activity tickers with all-near-zero bars, no div-zero traceback.

**Commit:** `style(gex): alpha-intensity gradient for side-panel bars`

---

### Stage 2 — Alerts rail card refresh

**Why:** The current `.price-info` block is a flat text list. `.dealer-impact` is a bare 5-row grid with no surface definition. Seven visual modules stacked as cards give each metric group its own header, surface, and breathing room — matches the reference UI's visual grammar (uppercase tracked headers, colored dots, dual-metric bars, sentiment slider) without copying reference data fields.

**Files:**

- `ezoptionsschwab.py` — CSS block near `.dealer-impact` at `:5506–5525` (new `.rail-card` system, `.rail-range-track`, `.rail-sentiment-track`, `.rail-bar`, etc.)
- `ezoptionsschwab.py` — HTML at `:6770–6796` (replace contents of `[data-rail-panel="alerts"]`)
- `ezoptionsschwab.py` — rebuild path at `:8770–8776` (mirror the new HTML)
- `ezoptionsschwab.py` — `compute_trader_stats` (~`:3400–3590`): emit `chain_activity`, `profile`, `session_deltas` (see §4)
- `ezoptionsschwab.py` — JS `updatePriceInfo` (~`:10082`): refactor into card fan-out
- `ezoptionsschwab.py` — new JS renderers: `renderMarketMetrics`, `renderRangeScale`, `renderGammaProfile`, `renderChainActivity`. Call each from the `/price` handler where `updatePriceInfo` is currently called.

#### 7.2.1 Backend additions to `compute_trader_stats`

After the existing `out['net_gex']` / `out['regime']` / `out['call_percentage']` / `out['put_percentage']` computations, append:

```python
# Chain activity
def _safe_sum(df, col):
    if df is None or getattr(df, 'empty', True) or col not in df.columns:
        return 0.0
    return float(df[col].sum())

call_oi  = _safe_sum(calls, 'openInterest')
put_oi   = _safe_sum(puts,  'openInterest')
call_vol = _safe_sum(calls, 'volume')
put_vol  = _safe_sum(puts,  'volume')

out['chain_activity'] = {
    'oi_cp_ratio':  (call_oi / put_oi) if put_oi > 0 else None,
    'vol_cp_ratio': (call_vol / put_vol) if put_vol > 0 else None,
    'sentiment':    ((out.get('call_percentage', 0) - out.get('put_percentage', 0)) / 100.0),
}

# Gamma profile descriptor
is_long = (out['regime'] == 'Long Gamma')
out['profile'] = {
    'regime':   out['regime'],
    'headline': 'Positive Gamma' if is_long else 'Negative Gamma',
    'blurb':    'dealer hedging dampens moves' if is_long else 'dealer hedging amplifies moves',
}

# Session deltas (optional — None if baseline unknown)
out['session_deltas'] = _compute_session_deltas(ticker, out['net_gex'], out.get('net_dex'))
```

`_compute_session_deltas` reads from `interval_data` the first post-09:30 ET snapshot for the current session, or falls back to an in-process dict keyed by `(ticker, session_date)`:

```python
_SESSION_BASELINE = {}  # module-level

def _compute_session_deltas(ticker, net_gex, net_dex):
    import datetime as _dt
    today = _dt.datetime.now(pytz.timezone('US/Eastern')).date()
    key = (ticker, today)
    if key not in _SESSION_BASELINE:
        # try SQLite first
        baseline = _lookup_session_baseline_from_interval_data(ticker, today)
        if baseline is None:
            baseline = {'net_gex': net_gex, 'net_dex': net_dex}
        _SESSION_BASELINE[key] = baseline
    base = _SESSION_BASELINE[key]
    return {
        'net_gex_vs_open': (net_gex - base['net_gex']) if base['net_gex'] is not None else None,
        'net_dex_vs_open': (net_dex - base['net_dex']) if base['net_dex'] is not None else None,
    }
```

If the SQLite query proves expensive per tick, cache with a 30-second TTL.

#### 7.2.2 CSS (insert after the existing `.dealer-impact` block, ~`:5525`)

```css
/* ── Rail card system (Phase 3) ─────────────────────────────────── */
.rail-card {
  background: var(--bg-1);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 10px 12px;
  margin: 8px 10px 0 10px;
}
.rail-card:last-child { margin-bottom: 8px; }
.rail-card-header {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--fg-2);
  margin-bottom: 6px;
}

/* Price header card */
.rail-card-price-big {
  font-size: 22px;
  font-weight: 600;
  color: var(--fg-0);
  font-variant-numeric: tabular-nums;
  line-height: 1.1;
}
.rail-card-price-sub {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
  font-size: 11px;
}
.rail-card-price-sub .chg.pos { color: var(--call); }
.rail-card-price-sub .chg.neg { color: var(--put); }
.rail-card-chip {
  display: inline-block;
  padding: 2px 7px;
  border-radius: 999px;
  background: var(--bg-2);
  color: var(--fg-1);
  font-size: 10px;
  letter-spacing: 0.04em;
}

/* Metrics pair */
.rail-metric-pair { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.rail-metric .v { font-size: 18px; color: var(--fg-0); font-variant-numeric: tabular-nums; }
.rail-metric .d { font-size: 11px; color: var(--fg-2); font-variant-numeric: tabular-nums; }
.rail-metric .d.pos { color: var(--call); }
.rail-metric .d.neg { color: var(--put); }

/* Range scale */
.rail-range-track {
  position: relative;
  height: 6px;
  background: var(--bg-2);
  border-radius: 3px;
  margin: 6px 0;
}
.rail-range-em {
  position: absolute; height: 100%;
  background: linear-gradient(90deg, rgba(239,68,68,0.25), rgba(16,185,129,0.25));
  border-radius: 3px;
}
.rail-range-marker {
  position: absolute; top: -3px;
  width: 12px; height: 12px;
  border-radius: 50%;
  background: var(--fg-0);
  transform: translateX(-50%);
  box-shadow: 0 0 0 2px var(--bg-1);
}
.rail-range-labels {
  display: flex;
  justify-content: space-between;
  font-size: 10px;
  color: var(--fg-2);
  font-variant-numeric: tabular-nums;
}

/* Gamma profile */
.rail-profile-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; vertical-align: middle; }
.rail-profile-dot.pos { background: var(--call); }
.rail-profile-dot.neg { background: var(--put); }
.rail-profile-headline { font-size: 13px; color: var(--fg-0); font-weight: 500; }
.rail-profile-blurb { color: var(--fg-1); font-size: 11px; margin-top: 4px; line-height: 1.35; }

/* Chain activity */
.rail-sentiment-track {
  position: relative;
  height: 4px;
  background: linear-gradient(90deg, var(--put), var(--bg-2) 50%, var(--call));
  border-radius: 2px;
  margin: 10px 0;
}
.rail-sentiment-marker {
  position: absolute; top: -3px;
  width: 10px; height: 10px;
  border-radius: 50%;
  background: var(--fg-0);
  transform: translateX(-50%);
  box-shadow: 0 0 0 2px var(--bg-1);
}
.rail-sentiment-labels {
  display: flex;
  justify-content: space-between;
  font-size: 10px;
  color: var(--fg-2);
  letter-spacing: 0.04em;
  text-transform: uppercase;
  margin-bottom: 6px;
}
.rail-bar { display: grid; grid-template-columns: 32px 1fr 80px; gap: 8px; align-items: center; font-size: 11px; margin-top: 6px; color: var(--fg-1); }
.rail-bar-track { height: 4px; background: var(--bg-2); border-radius: 2px; overflow: hidden; }
.rail-bar-fill { height: 100%; background: var(--accent); transition: width 180ms ease; }
.rail-bar .num { text-align: right; color: var(--fg-0); font-variant-numeric: tabular-nums; }

/* Dealer-impact block nested in a card — remove its redundant padding */
.rail-card .dealer-impact { padding: 0; border-bottom: none; }

/* Live alerts card — use existing .rail-alerts-list, drop its padding since the card provides it */
.rail-card .rail-alerts-list { padding: 0; }

/* Flow-level alerts (new) */
.rail-alert-item.flow { border-left-color: var(--accent); }
.rail-alert-item.flow .rail-alert-dot { background: var(--accent); }
.rail-alert-ago {
  margin-left: auto;
  font-size: 10px;
  color: var(--fg-2);
  font-variant-numeric: tabular-nums;
  flex: 0 0 auto;
}
```

#### 7.2.3 HTML (replace contents of `[data-rail-panel="alerts"]` at `:6770–6796`)

```html
<div class="right-rail-panel active" data-rail-panel="alerts">
  <div class="rail-card" id="rail-card-price">
    <div class="rail-card-price-big" data-live-price>—</div>
    <div class="rail-card-price-sub">
      <span class="chg" data-met="price_change">—</span>
      <span class="rail-card-chip" data-met="expiry_chip">—</span>
    </div>
  </div>

  <div class="rail-card" id="rail-card-metrics">
    <div class="rail-metric-pair">
      <div class="rail-metric">
        <div class="rail-card-header">Net GEX</div>
        <div class="v" data-met="net_gex">—</div>
        <div class="d" data-met="net_gex_delta">—</div>
      </div>
      <div class="rail-metric">
        <div class="rail-card-header">Net DEX</div>
        <div class="v" data-met="net_dex">—</div>
        <div class="d" data-met="net_dex_delta">—</div>
      </div>
    </div>
  </div>

  <div class="rail-card" id="rail-card-range">
    <div class="rail-card-header">Range · EM <span data-met="em_pct">—</span></div>
    <div class="rail-range-track">
      <div class="rail-range-em" data-met="em_band"></div>
      <div class="rail-range-marker" data-met="price_marker"></div>
    </div>
    <div class="rail-range-labels">
      <span data-met="range_low">—</span>
      <span data-met="range_high">—</span>
    </div>
  </div>

  <div class="rail-card" id="rail-card-profile">
    <div class="rail-card-header">Gamma Profile</div>
    <div class="rail-profile-headline">
      <span class="rail-profile-dot" data-met="profile_dot"></span>
      <span data-met="profile_headline">—</span>
    </div>
    <div class="rail-profile-blurb" data-met="profile_blurb">—</div>
  </div>

  <div class="rail-card" id="rail-card-dealer">
    <div class="rail-card-header">Dealer Impact</div>
    <div class="dealer-impact" id="dealer-impact">
      <div class="label">Spot +1%<div class="sub">dealers buy/sell</div></div><div class="val" data-di="hedge_on_up_1pct">—</div>
      <div class="label">Spot −1%<div class="sub">dealers buy/sell</div></div><div class="val" data-di="hedge_on_down_1pct">—</div>
      <div class="label">Vol +1 pt<div class="sub">vanna delta shift</div></div><div class="val" data-di="vanna_up_1">—</div>
      <div class="label">Vol −1 pt<div class="sub">vanna delta shift</div></div><div class="val" data-di="vanna_down_1">—</div>
      <div class="label">Charm by close<div class="sub">intraday delta decay</div></div><div class="val" data-di="charm_by_close">—</div>
    </div>
  </div>

  <div class="rail-card" id="rail-card-activity">
    <div class="rail-card-header">Chain Activity</div>
    <div class="rail-sentiment-labels"><span>bearish</span><span>bullish</span></div>
    <div class="rail-sentiment-track">
      <div class="rail-sentiment-marker" data-met="sentiment_marker"></div>
    </div>
    <div class="rail-bar">
      <span>OI</span>
      <div class="rail-bar-track"><div class="rail-bar-fill" data-met="oi_fill"></div></div>
      <span class="num" data-met="oi_cp">—</span>
    </div>
    <div class="rail-bar">
      <span>VOL</span>
      <div class="rail-bar-track"><div class="rail-bar-fill" data-met="vol_fill"></div></div>
      <span class="num" data-met="vol_cp">—</span>
    </div>
  </div>

  <div class="rail-card" id="rail-card-alerts">
    <div class="rail-card-header">Live Alerts</div>
    <div class="rail-alerts-list" id="right-rail-alerts">
      <div class="rail-alerts-empty">No active alerts.</div>
    </div>
  </div>
</div>
```

Mirror the identical markup in the rebuild path at `:8770–8776`. Use a shared JS template string if feasible.

#### 7.2.4 JS renderers (insert near `updatePriceInfo` in the JS block)

```js
function setMet(key, text) {
  document.querySelectorAll(`[data-met="${key}"]`).forEach(n => { n.textContent = text; });
}

function renderPriceHeader(info) {
  const p = (livePrice !== null) ? livePrice : info.current_price;
  const priceEl = document.querySelector('#rail-card-price [data-live-price]');
  if (priceEl) priceEl.textContent = '$' + p.toFixed(2);
  const chgEl = document.querySelector('#rail-card-price [data-met="price_change"]');
  if (chgEl) {
    const pct = info.net_percent;
    chgEl.textContent = (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%';
    chgEl.classList.toggle('pos', pct >= 0);
    chgEl.classList.toggle('neg', pct < 0);
  }
  const expiries = lastData.selected_expiries || [];
  const chipText = expiries.length > 1 ? `${expiries.length} exp` : (expiries[0] || '—');
  setMet('expiry_chip', chipText);
}

function renderMarketMetrics(stats, info) {
  setMet('net_gex', fmtDollars(stats.net_gex));
  setMet('net_dex', fmtDollars(stats.net_dex));
  const dGex = stats.session_deltas && stats.session_deltas.net_gex_vs_open;
  const dDex = stats.session_deltas && stats.session_deltas.net_dex_vs_open;
  setMet('net_gex_delta', dGex == null ? '' : `Δ ${dGex > 0 ? '+' : ''}${fmtDollars(dGex)}`);
  setMet('net_dex_delta', dDex == null ? '' : `Δ ${dDex > 0 ? '+' : ''}${fmtDollars(dDex)}`);
  // sign coloring
  document.querySelectorAll('#rail-card-metrics .d').forEach((el, i) => {
    const v = i === 0 ? dGex : dDex;
    el.classList.toggle('pos', v != null && v > 0);
    el.classList.toggle('neg', v != null && v < 0);
  });
}

function renderRangeScale(info) {
  const low = info.low, high = info.high;
  const price = (livePrice !== null) ? livePrice : info.current_price;
  const range = high - low;
  if (range <= 0) return;
  const pct = Math.max(0, Math.min(1, (price - low) / range));
  const marker = document.querySelector('#rail-card-range [data-met="price_marker"]');
  if (marker) marker.style.left = (pct * 100).toFixed(2) + '%';
  setMet('range_low',  '$' + low.toFixed(2));
  setMet('range_high', '$' + high.toFixed(2));
  if (info.expected_move_range) {
    const emLo = info.expected_move_range.lower;
    const emHi = info.expected_move_range.upper;
    const a = Math.max(0, Math.min(1, (emLo - low) / range));
    const b = Math.max(0, Math.min(1, (emHi - low) / range));
    const band = document.querySelector('#rail-card-range [data-met="em_band"]');
    if (band) {
      band.style.left  = (a * 100).toFixed(2) + '%';
      band.style.width = ((b - a) * 100).toFixed(2) + '%';
    }
    const emPct = info.expected_move_range.upper_pct;
    setMet('em_pct', emPct != null ? `±${Math.abs(emPct).toFixed(2)}%` : '');
  }
}

function renderGammaProfile(stats) {
  if (!stats || !stats.profile) return;
  const dot = document.querySelector('#rail-card-profile [data-met="profile_dot"]');
  if (dot) {
    const pos = stats.profile.regime === 'Long Gamma';
    dot.classList.toggle('pos', pos);
    dot.classList.toggle('neg', !pos);
  }
  setMet('profile_headline', stats.profile.headline);
  setMet('profile_blurb',    stats.profile.blurb);
}

function renderChainActivity(stats) {
  if (!stats || !stats.chain_activity) return;
  const { oi_cp_ratio, vol_cp_ratio, sentiment } = stats.chain_activity;
  const pct = Math.max(0, Math.min(1, (sentiment + 1) / 2));
  const marker = document.querySelector('#rail-card-activity [data-met="sentiment_marker"]');
  if (marker) marker.style.left = (pct * 100).toFixed(2) + '%';
  setMet('oi_cp',  oi_cp_ratio  == null ? '—' : `C/P ${oi_cp_ratio.toFixed(2)}`);
  setMet('vol_cp', vol_cp_ratio == null ? '—' : `C/P ${vol_cp_ratio.toFixed(2)}`);
  // Fill proportional to ratio, clamped to 0..100% mapping 0.5..2.0 → 0..100%
  const fillPct = r => r == null ? 0 : Math.max(0, Math.min(100, ((r - 0.5) / 1.5) * 100));
  const oiFill  = document.querySelector('#rail-card-activity [data-met="oi_fill"]');
  const volFill = document.querySelector('#rail-card-activity [data-met="vol_fill"]');
  if (oiFill)  oiFill.style.width  = fillPct(oi_cp_ratio)  + '%';
  if (volFill) volFill.style.width = fillPct(vol_cp_ratio) + '%';
}
```

Call all four renderers wherever `updatePriceInfo` is currently called. Keep `updatePriceInfo` as a thin wrapper that invokes `renderPriceHeader`, `renderRangeScale`, and existing live-price machinery; delete the old innerHTML blob.

**Verification:**

1. Open Alerts tab → seven cards render top-to-bottom.
2. Price card updates every tick (or via streamer); change-% colors green/red correctly.
3. Metrics card Net GEX matches the KPI strip value; delta reads `Δ +xxx` format.
4. Range marker sits between low/high labels; EM band overlays; price marker updates with stream.
5. Profile dot is green (Long Gamma) / red (Short Gamma); headline + blurb match regime.
6. Sentiment marker position = `((call% - put%) / 100 + 1) / 2` mapped to 0–100% of track.
7. OI / VOL C/P bars fill proportionally; numeric label right-aligned.
8. Dealer Impact card shows existing 5 rows unchanged; data updates.
9. Live Alerts card shows existing rule-based alerts during transition (Stage 3 hasn't landed yet).
10. Scroll: if cards overflow the fixed `.right-rail-panels` height, inner scroll kicks in rather than pushing the layout.
11. Ticker / timeframe switch: cards regenerate correctly (rebuild path mirror works).
12. Console: zero errors.

**Commit:** `feat(ui): modernize alerts rail with card modules`

---

### Stage 3 — Live flow alerts engine

**Why:** Current alerts are 5 static rule checks (near-wall-0.3%, near-gamma-flip-0.5%, long/short gamma regime). No surfacing of spikes, unusual flow, or wall movement — despite having 1-minute per-strike history already stored in SQLite's `interval_data` table since Phase 1. Light up four alert types from existing data, each with cooldowns and floors so illiquid tickers don't spam.

**Files:**

- `ezoptionsschwab.py` — new function `compute_flow_alerts(ticker, calls, puts, now_utc, last_stats_cache)` near `compute_trader_stats` (~`:3400`)
- `ezoptionsschwab.py` — `compute_trader_stats`: call `compute_flow_alerts(...)` and merge results into `out['alerts']`
- `ezoptionsschwab.py` — extend existing rule-based alerts in `:3574–3587` with `id`, `ts` fields
- `ezoptionsschwab.py` — JS `renderRailAlerts` (~`:9661`): timestamp stamp + `.flow` variant + auto-hide >15min

#### 7.3.1 Alert specifications

**1. Volume spike at strike.**
- Per strike in the near-money window (already filtered by `strike_range` before `compute_trader_stats` is called), query `interval_data` for the last 20 minutes of `net_volume` for `(ticker, strike)`.
- Compute `avg20 = mean(last 20 net_volume values)`.
- Let `curr = net_volume for the current 1-min bar`.
- Emit if: `curr >= max(500, 3 * avg20)` (500 = absolute floor to suppress sleepy strikes).
- Text: `f"Vol spike @ {strike:.0f} ({curr/avg20:.1f}× avg)"`
- Cooldown: 5 min per `(ticker, strike)`.

**2. Volume/OI ratio unusual.** (User's explicit request — "OI doesn't change intraday, so compare volume to OI")
- Per strike: `today_vol = sum of net_volume for current session from interval_data` (or read from live `_options_cache` if that's the cheaper path).
- `ratio = today_vol / openInterest`.
- Look at the 15-min-ago ratio for the same `(ticker, strike)`; let `prev_ratio = ratio at now-15min`.
- Emit if `ratio > 0.25` AND `ratio > 2 * prev_ratio` AND `openInterest > 100` (floor).
- Text: `f"Heavy vol/OI @ {strike:.0f} ({ratio:.2f})"`
- Cooldown: 10 min per `(ticker, strike)`.

**3. IV surge at strike.**
- Check `interval_data` schema for per-strike IV storage. If stored:
  - Pull last 30 min of IV per `(ticker, strike)`; compute mean and stdev.
  - Emit if `curr_iv > mean + 2*stdev` AND stdev > 0.001 (avoid flat-IV false positives).
- If **not** stored, add a module-level ring buffer:
  ```python
  _IV_BUFFER = collections.defaultdict(lambda: collections.deque(maxlen=30))
  # Append curr_iv each tick before spike check
  ```
  Acceptable that this loses state on restart — live feed, not authoritative history.
- Text: `f"IV surge @ {strike:.0f} (+{z:.1f}σ)"`
- Cooldown: 10 min per `(ticker, strike)`.

**4. Wall shift.**
- Module-level dict: `_LAST_WALLS = {}` keyed by `ticker`. Stores `{'call_wall': prev_strike, 'put_wall': prev_strike}`.
- On each `compute_trader_stats` call, compare current `out['call_wall']` and `out['put_wall']` against the cached previous values.
- If either changes: emit `f"Call Wall {prev:.0f} → {curr:.0f}"` (or `"Put Wall ..."`). Update cache.
- Cooldown: 2 min per `(ticker, wall_type)` to prevent flapping when two strikes have nearly equal GEX.

#### 7.3.2 Alert envelope

```python
{
  'id':     'vol_spike:715',            # stable dedup key
  'level':  'flow',                     # 'flow' is the new variant; 'warn' / 'info' still used by rule-based alerts
  'text':   'Vol spike @ 715 (4.2× avg)',
  'strike': 715.0,                      # None if not strike-specific
  'ts':     '2026-04-20T14:32:05Z',
  'detail': None,                       # optional secondary line
}
```

Dedup in `compute_flow_alerts` by `id` — keep the newest entry; drop duplicates within the cooldown window.

#### 7.3.3 Merge with existing alerts

In `compute_trader_stats`, after the existing block at `:3574–3587` that builds `alerts = [...]`:

```python
# Stamp existing alerts with id + ts so frontend can dedupe
now_iso = datetime.utcnow().isoformat() + 'Z'
for a in alerts:
    a.setdefault('id', f"{a['level']}:{hash(a['text']) & 0xffff}")
    a.setdefault('ts', now_iso)

# Append flow alerts
try:
    flow = compute_flow_alerts(ticker, calls, puts, now_iso)
    alerts.extend(flow)
except Exception as e:
    print(f"[compute_flow_alerts] failed: {e}")

out['alerts'] = alerts
```

#### 7.3.4 Frontend — `renderRailAlerts` extension

Update the item template to include timestamp and support `.flow` variant:

```js
function relTime(iso) {
  if (!iso) return '';
  const t = new Date(iso).getTime();
  const now = Date.now();
  const mins = Math.max(0, Math.round((now - t) / 60000));
  if (mins < 1) return 'now';
  if (mins < 60) return mins + 'm';
  return Math.floor(mins / 60) + 'h';
}

function renderRailAlerts(list) {
  const container = document.getElementById('right-rail-alerts');
  if (!container) return;
  const MAX_AGE_MIN = 15;
  const now = Date.now();
  const filtered = (list || []).filter(a => {
    if (!a.ts) return true;
    return (now - new Date(a.ts).getTime()) / 60000 <= MAX_AGE_MIN;
  });
  if (!filtered.length) {
    container.innerHTML = '<div class="rail-alerts-empty">No active alerts.</div>';
    return;
  }
  container.innerHTML = filtered.map(a => {
    const lvl = ['warn', 'info', 'flow'].includes(a.level) ? a.level : 'info';
    const ago = relTime(a.ts);
    return `
      <div class="rail-alert-item ${lvl}">
        <span class="rail-alert-dot"></span>
        <span class="rail-alert-text">${escapeHtml(a.text)}</span>
        ${ago ? `<span class="rail-alert-ago">${ago}</span>` : ''}
      </div>
    `;
  }).join('');
}
```

Existing unread-badge mechanism at `:9025–9078` keeps working — new alerts still bump the badge when the Alerts tab is inactive.

**Verification:**

1. **Volume spike:** during a live trading session, leave SPY open for 10 min. At least one volume spike should fire on a 0DTE or near-ATM strike. Verify: the strike matches a bar that visibly jumped; the `×N avg` number is sensible.
2. **V/OI ratio:** on a 0DTE expiry, a strike near-money typically trades >25% of OI by midday. Verify the alert fires at crossing, not continuously.
3. **IV surge:** on a macro-event day (FOMC, CPI), at least one IV surge should fire. On sleepy days, fewer — acceptable.
4. **Wall shift:** spot-check by logging `call_wall` / `put_wall` every tick; when the value changes, a corresponding alert should appear within one tick.
5. **No duplicates:** run for 30 min, tail the alerts list. No identical `id` within its cooldown window.
6. **Auto-fade:** alerts older than 15 min disappear from the list (verify by timestamping one and waiting).
7. **Unread badge:** Switch to Levels tab, wait for a flow alert to fire, confirm the Alerts tab badge increments.
8. **Illiquid ticker (e.g., a thin single-name):** no alert spam. Vol floor + IV stdev floor should suppress false positives.
9. **Regression:** pre-existing rule-based alerts (near-wall, near-gamma-flip, regime) still fire with the same text.

**Commit:** `feat(alerts): volume, IV, wall-shift, and V/OI flow alerts`

---

### Stage 4 — Polish + regression pass

**Why:** Card-based layout + new live alerts engine touch both DOM structure and backend tick cadence. Defect sweep before PR.

1. **Panel height.** `.right-rail-panels { height: 680px; }` may be too short for seven cards. Measure at Stage 4; either raise to `height: auto; max-height: calc(100vh - 240px);` OR reduce card padding to 8px. Decide empirically; do not guess upfront.
2. **Rebuild-path drift.** Phase 2 Stage 2 explicitly hit this: rail HTML mirror at `:8770–8776`. Verify by switching tickers (SPY → QQQ → TSLA) and confirming all seven cards survive.
3. **Tab persistence.** `applyRightRailTab` + `RAIL_TAB_KEY` localStorage still round-trips. Reload with Alerts selected → Alerts comes back.
4. **Perf.** If Stage 3's SQLite queries (per-strike 20-minute lookups) run every tick × every near-money strike, that's 10–30 queries/second. Add a 30-second TTL cache keyed by `(ticker, strike)` if CPU profile shows hot. Don't prematurely optimize — measure.
5. **Smoke matrix.**
   - Tickers: SPY, QQQ, TSLA, and one illiquid single-name.
   - Timeframes: 1, 5, 15, 30, 60 min.
   - Tabs: Alerts, Levels, Scenarios, GEX (implicit).
   - Drawer: open/close, toggle each chart visibility control.
   - Settings modal: open/close, save/load.
   - Confirm: zero console errors; zero visual regressions from modernization or Phase 2.
6. **Doc updates.**
   - Flip §0 of this doc to `Complete — 4 of 4 stages landed`.
   - Update `CLAUDE.md` "Active initiative" → "Completed initiative (Phase 3)" and clear the active-initiative line.
   - Populate §10 progress log with per-stage commit SHAs and deviations.

**Commit:** `chore(alerts): polish + regression sweep`

---

## 8. After all stages land

1. Manual smoke per Stage 4 matrix.
2. Update `CLAUDE.md` and this doc's §0 status line.
3. Push branch and open PR per §6.4.
4. After merge, next initiative (if any) starts from fresh `main`.

---

## 9. Risks and open questions

- **`interval_data` IV column.** Unconfirmed whether implied volatility is stored per-strike per-minute. If absent, Stage 3's IV-surge uses an in-process ring buffer — acceptable, but loses state across restarts. Verify at schema `:64–80` before coding Stage 3.
- **Session baseline for Δ values.** The metric card's `Δ +912K` displays require a "session open" baseline. Cleanest: query `interval_data` for the first snapshot after 09:30 ET of the current session. If the query is expensive or the table doesn't have an efficient index, fall back to an in-process `_SESSION_BASELINE` dict that captures on first post-09:30 tick. Acceptable to leave these deltas `null` on cold start before the first baseline is set.
- **SQLite query hot-path.** Stage 3 volume-spike detection runs per near-money strike per tick. On SPY with ~40 near-money strikes at 1-sec tick cadence, that's 40 queries/sec. If the DB is in a separate file and the connection is unpooled, this could stall. Mitigation: (a) add `CREATE INDEX IF NOT EXISTS` on `(ticker, strike, timestamp)` if not already present; (b) cache per-strike rolling-mean with 15-sec TTL. Profile before optimizing.
- **Card vertical fit.** Seven cards × ~80px each ≈ 560px plus gutters. Should fit in the current 680px `.right-rail-panels` height, but decide at Stage 4 based on actual render — either raise the cap or tighten padding.
- **Rebuild-path drift.** Phase 2 Stage 2 specifically called this out. Any new element under `[data-rail-panel="alerts"]` MUST also appear in the rebuild sequence at `:8770–8776`, or tick-triggered rebuilds (ticker switch, timeframe switch) drop it. Do not copy-paste — use a shared JS template string.
- **Alert spam on illiquid names.** Floors and cooldowns prevent it, but specific thresholds (500-contract vol floor, 100-contract OI floor, 2σ IV threshold, 2–10 min cooldowns) are hand-tuned. Revisit after a week of production use if user flags noise.
- **Backfill.** No historical data needs migrating. `interval_data` already stores what this initiative reads.

---

## 10. Progress log

_(populate as stages land — one bullet each with commit SHA, notes on any deviation from spec)_

- **Stage 1 — `0ca238d`** `style(gex): alpha-intensity gradient for side-panel bars`. Per spec; no deviation.
- **Stage 2 — `ef75c4e`** `feat(ui): modernize alerts rail with card modules`. Rebuild-path mirror implemented via shared `buildAlertsPanelHtml()` template (avoids copy-paste drift called out in §5).
- **Stage 3 — `4b92847`** `feat(alerts): volume, IV, wall-shift, and V/OI flow alerts`. All four alert types wired with cooldowns/floors; volume-spike SQLite lookup is batched and 30-second TTL cached per ticker (`_VOL_SPIKE_CACHE`). IV buffer kept in-process per §9.
- **Stage 4 — _this commit_** `chore(alerts): polish + regression sweep`. Added `idx_interval_data_ticker_date_ts` composite index on `interval_data(ticker, date, timestamp, strike)` to back the per-tick rolling-20min volume query (§9 mitigation). Panel height left at 680px — matches `.gex-column` grid alignment, and the `.right-rail-panel` `overflow-y: auto` handles the rare case where Live Alerts card grows past the baseline ~620px stack. Rebuild-path mirror verified: `buildAlertsPanelHtml()` is the single source of truth used by both the initial HTML and `ensurePriceChartDom`.
