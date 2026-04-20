# GEX Dashboard — OI Visibility, 0DTE Isolation, Alert Focus (Phase 4)

**Status:** In progress — 4 of 5 stages landed
**Owner:** @snoopydoopy1011
**Created:** 2026-04-19
**Target branch:** `feat/analytics-phase4`
**Base:** `main` (after `feat/analytics-phase3` merges); otherwise cut from `feat/analytics-phase3` head and rebase after merge.
**Prior initiatives (complete):**
- [`UI_MODERNIZATION_PLAN.md`](UI_MODERNIZATION_PLAN.md) — layout, palette, design tokens.
- [`ANALYTICS_CHART_PHASE2_PLAN.md`](ANALYTICS_CHART_PHASE2_PLAN.md) — Dealer Impact, Scenarios, HVL/±2σ/secondary walls.
- [`ALERTS_RAIL_PHASE3_PLAN.md`](ALERTS_RAIL_PHASE3_PLAN.md) — 7-card alerts rail, alpha-intensity GEX bars, live flow-alerts engine.

Read §2 (Target layout) and §3 (Reuse inventory) of the Phase 3 doc before touching the alerts rail or the side panel.

---

## 0. Where are we? (read this first)

**Current state (as of 2026-04-20):** Stages 1-4 have landed on `feat/analytics-phase4` (`b88383b`, `1d4491a`, `310d872`, `f3752f8`). Stage 5 — flow-alert key-level gating — is next.

**Line numbers** in this document are a snapshot against `feat/analytics-phase3` HEAD. They will drift as soon as any stage lands. **Grep by anchor name** — function names, CSS class names, element IDs — rather than trusting line numbers. Stable anchors:

- **Python:** `compute_key_levels`, `compute_trader_stats`, `compute_flow_alerts`, `compute_greek_exposures`, `create_open_interest_chart`, `create_exposure_chart`, `fetch_options_for_date`, `_options_cache`.
- **JS:** `applyIndicators`, `activeInds`, `tvKeyLevelLines`, `renderKeyLevels`, `tvCandleSeries.createPriceLine`, `updateSecondaryTabs`, `applySecondaryTabVisibility`, `renderGexSidePanel`, `renderRailAlerts`, `buildAlertsPanelHtml`, `_lastStats`.
- **CSS / HTML:** CSS token block at line ~5349 (`--call`, `--put`, `--accent`, `--warn`, `--info`, `--ok`), `.secondary-tabs`, `.chart-container`, `.price-info`, `[data-rail-panel="alerts"]`, `.rail-card`.

To determine which stage is next:

```bash
git branch -a
git log --oneline main..feat/analytics-phase4
```

Match commit subjects against the 5 stages in §7. Subjects follow §6.2 exactly. If N stages have landed, start at N+1.

---

## 1. Context

Phase 3 wrapped the alerts rail into 7 glanceable cards and added a live flow-alerts engine backed by SQLite `interval_data`. Reviewing the current UI (screenshots 2026-04-19) against Gemini's external critique and the user's own wish list, five gaps emerge:

1. **Open Interest has no visual surface.** `create_open_interest_chart()` is fully implemented (line 4557) and already returned from `/update` (line 11471), but the OI tab is hidden by default (`open_interest: false`, line 7335). Users get no OI context alongside Gamma/Delta/Vanna/Charm.
2. **No price-chart context for the dominant OI strikes.** At dealer close and expiration, SPY pins toward the top-OI strikes of the nearest expiration. Currently there's no overlay for them — users have to eyeball the GEX bars, which are sorted by notional, not OI.
3. **Net GEX/DEX blends all expirations.** SPY intraday price action is dominated by 0DTE flow; showing a blended Net GEX hides whether same-day speculators are fighting longer-dated dealer positioning.
4. **Strike/exposure tabs are palette-inconsistent.** Each `create_*_chart()` writes its own Plotly styling (colors, gridlines, margins, hover format). Switching tabs feels visually jumpy.
5. **Flow alerts fire on any strike.** As Gemini noted, vol-spike / IV-surge / V-OI alerts are noisy away from structural levels. Gating them to key-level proximity (HVL, ±σ EM, Walls, Gamma Flip) keeps signal density high.

**Note on Black-Scholes (addresses user question):** Schwab's options-chain response returns `volatility` (IV) per contract but **not** greeks. The app already uses Schwab's IV as input (see lines 1152, 1188) and plugs it into Black-Scholes to compute delta/gamma/vanna/charm/vomma/speed. Second-order greeks are never available from any vendor API — they must be computed. The current pipeline is correct; no change in this phase.

**Three independent goals:**

1. **OI visibility.** Default-on the existing OI tab + one shared Plotly theme helper so every strike/exposure tab looks identical. Add a Price Chart toolbar `OI` button that renders the top-5 call-OI strikes (green) and top-5 put-OI strikes (red) at the nearest expiration, with overlap strikes drawn as a single gold line.
2. **0DTE isolation.** Precompute a second stats/key-levels bundle on the backend filtered to the nearest expiration; add a `All | 0DTE` pill to the KPI card that swaps displayed numbers and chart price lines instantly.
3. **Alert focus.** Gate strike-origin flow alerts (vol spike, IV surge, V/OI, sweep) to within 0.25% of spot of any key level. Wall-shift and gamma-flip-move alerts are themselves key-level events — they remain ungated. A settings toggle lets power users turn the gate off.
4. **Forward layout compatibility.** The `All | 0DTE` bundle introduced here should not be wired only to the current GEX side panel. The later UX/layout phase will promote strike-aligned tabs into a shared center strike rail, and this phase's 0DTE state needs to feed that future surface too.

**No analytical-formula changes.** No new Greeks, no new data sources, no new endpoints. Every new signal reuses data the backend already has. Single-file `ezoptionsschwab.py`, vanilla JS, CSS tokens only.

---

## 2. Target layout

### Price Chart toolbar (new button)

```
Price Chart  SMA20  SMA50  SMA200  EMA9  EMA21  VWAP  BB  RSI  MACD  ATR  OI  — H-Line  ↗ Trend  …
                                                                        ↑ new
```

Single click on `OI` toggles all ≤10 top-OI price lines on/off. Lines are dotted, width 1, so they visually subordinate to the solid Call/Put Walls and the dashed Gamma Flip.

### Top-5 OI overlay on price chart (button ON)

```
── 714.00  C OI          (green dotted)
── 712.50  C OI          (green dotted)
── 711.00  ★ 711.00      (gold dotted, overlap — in both top-5 calls AND top-5 puts)
── 710.00  C OI          (green dotted)   ← Call Wall label still drawn on top (solid green)
── 708.00  C OI          (green dotted)
── 705.00  P OI          (red dotted)
── 704.00  P OI          (red dotted)
── 701.00  ★ 701.00      (gold dotted, overlap)    ← Put Wall label still drawn on top (solid red)
── 700.00  P OI          (red dotted)
── 698.00  P OI          (red dotted)
```

### Side-panel KPI card (new 0DTE pill)

```
┌──────────────────────────────────┐
│  $710.75                         │
│  +1.30% (+9.09)   2026-04-20     │
├──────────────────────────────────┤
│  NET GEX        NET DEX          │
│  $1.27B         $2.75B           │
│  Δ +$20.3K      Δ +$12.8K        │
│                                  │
│  [ All ][ 0DTE ]   ← new pill    │
└──────────────────────────────────┘
```

Clicking `0DTE` swaps Net GEX, Net DEX, Call Wall, Put Wall, Gamma Flip in-place from the `stats_0dte` / `key_levels_0dte` bundles. No network round-trip. The price-chart lines (Call Wall, Put Wall, Gamma Flip, HVL, ±σ EM) also redraw from the active bundle.

**Forward note:** in the follow-on UX/layout phase, the same `All | 0DTE` state should also drive the active strike-aligned rail tab (`GEX`, `Gamma`, `Delta`, `Vanna`, `Charm`, `OI`, `Options Vol`, `Premium`) so the center comparison surface stays in sync with the KPI card and price lines.

### Settings drawer (new row)

Under the existing **SECTIONS** and **CHART OVERLAYS** groups, add:

```
CHART OVERLAYS
  ☑ HVL line           ☑ ±2σ EM lines
  ☑ Secondary walls

ALERTS
  ☑ Only alert near key levels    ← new, default ON
```

---

## 3. Reuse inventory (do not reinvent)

### Button/overlay registry (reuse pattern from existing indicators)

- **`activeInds: Set<string>`** (~line 7800 region) — source of truth for which toolbar toggles are active.
- **Toolbar button factory** (~line 7864) with `inds = [{k, l, t}, ...]`. Add `{k:'oi', l:'OI', t:'Top 5 OI strikes (nearest exp)'}`.
- **`applyIndicators(candles)`** (~line 7796) — central per-tick re-render. Add `renderTopOI()` call guarded by `activeInds.has('oi')`.

### Price-line registry (reuse pattern from key levels)

- **`tvKeyLevelLines: PriceLine[]`** + **`renderKeyLevels()`** (~line 10301) — mirror exactly for `tvTopOILines` + `renderTopOI(topOi)`. Same idempotent shape: remove-all, clear, rebuild.
- **`tvCandleSeries.createPriceLine({...})`** — the only API needed. Pass `{ price, color, lineWidth: 1, lineStyle: LS.Dotted, axisLabelVisible: true, title: 'C OI 710' }`.

### Chart builders (reuse, do not rewrite)

- **`create_open_interest_chart()`** (line 4557) — already implemented. Just default-visible and apply theme helper.
- **`create_exposure_chart()`**, `create_volume_chart`, `create_options_volume_chart`, `create_premium_chart`, `create_centroid_chart`, `create_large_trades_table` — append `apply_plotly_theme(fig)` before return. `create_large_trades_table` returns HTML (skip theme).

### Backend analytics (reuse, parameterize)

- **`compute_key_levels(calls, puts, S)`** (line 3281) — call twice: once with full chain (existing), once with chain filtered to nearest expiration.
- **`compute_trader_stats(...)`** — same: call twice.
- **`compute_flow_alerts(...)`** — extend signature with `key_levels, spot, gate_strike_alerts=True`. Gate inside the emit loop.
- **`_options_cache[ticker]`** (line 615) — `{calls, puts, S}` DataFrames with `expiration_date` column already populated.

### Tab system (reuse)

- **`CHART_IDS`** (line 7327) and the default visibility map (line 7335) — flip `open_interest: true`.
- **`updateSecondaryTabs()`** (line 10241) + **`applySecondaryTabVisibility()`** (line 10271) — no changes needed; OI is already wired.

### CSS tokens

- Existing: `--call:#10B981`, `--put:#EF4444`, `--accent:#3B82F6`, `--warn:#F59E0B`, `--info:#3B82F6`, `--ok:#10B981` (line 5349 block).
- **New:** `--gold: #D4AF37` — for overlap strikes.

---

## 4. Design decisions (locked)

- **0DTE bundle strategy:** backend precomputes BOTH full-chain and 0DTE-filtered bundles every tick. Cost is one extra DataFrame filter + one `compute_key_levels` + one `compute_trader_stats` on the filtered rows — negligible. Benefit: instant client toggle, no fetch round-trip, no stale state.
- **Future strike-rail compatibility:** the `activeStatsKey` / `stats_0dte` state introduced here should be treated as the source of truth for any future strike-aligned rail tab set, not just the current GEX panel.
- **OI overlay transport:** extend `/update` response with a `top_oi` key. The route has `calls`/`puts` DataFrames in hand; a separate `/top_oi` endpoint would duplicate chain loading and double network chatter.
- **Overlap rendering:** one gold line, not two stacked. Build `Map<strike, {side, color}>`. Process call top-5 first (side=`call`, color=`--call`). Process put top-5: if strike already present, upgrade to side=`both`, color=`--gold`; else add as `put`. Flatten to ≤10 unique lines.
- **Overlap label:** `★ {strike}` (terse, visually distinctive). Plain labels: `C OI {strike}`, `P OI {strike}`.
- **OI line style:** dotted, width 1. Visually subordinate to Call Wall / Put Wall (solid width 2) and Gamma Flip (dashed width 2).
- **Key-level proximity:** **0.25% of spot** — scales across tickers (SPY $710 → ±$1.78; GME $30 → ±$0.075). Dollar band would need ticker-specific tuning.
- **Gated alerts:** only strike-origin alerts (vol spike, IV surge, V/OI, sweep). Wall-shift and gamma-flip-move alerts are themselves key-level events → never gated.
- **0DTE fallback:** if no today-expiry exists (weekends / market-closed sessions), backend silently picks nearest-future expiration. Pill label stays `0DTE`. Acceptable UX drift rather than hiding the pill.
- **Plotly theme:** one `apply_plotly_theme(fig)` helper called at the end of every `create_*_chart()`. Reads a module-level `PLOTLY_THEME` dict resolved once at import time from the same color constants used in the CSS token block.

---

## 5. Response schema additions

`/update` response gains three keys (additive; existing keys unchanged):

```json
{
  "top_oi": {
    "calls": [{"strike": 714.0, "oi": 12453}, ...],
    "puts":  [{"strike": 705.0, "oi": 9812}, ...],
    "both":  [711.0, 701.0]
  },
  "stats_0dte":      { /* same shape as existing "stats"      */ },
  "key_levels_0dte": { /* same shape as existing "key_levels" */ }
}
```

Clients that don't read these keys see no change. Existing integration tests and the current frontend tolerate the additions.

---

## 6. Engineering conventions

### 6.1 Code style

- Python: follow existing style in `ezoptionsschwab.py`; type hints only where they aid readability (module is untyped elsewhere — don't add annotations just because).
- JS: vanilla ES6; no frameworks, no new build step. Match indentation and naming of the surrounding code.
- CSS: tokens only for color. No neon hex literals. New token goes in the existing `:root { --call: ...; ... }` block.

### 6.2 Commit subject format

```
<type>(<scope>): <subject>
```

Types: `feat`, `fix`, `refactor`, `style`, `chore`. Scopes for this phase: `oi`, `0dte`, `theme`, `alerts`, `chart`. One stage = one commit (or tightly-related pair). Examples:

- `feat(theme): shared Plotly theme helper for strike/exposure tabs`
- `feat(oi): top-5 OI overlay button on price chart`
- `feat(0dte): precompute 0DTE stats + key_levels bundles`
- `feat(alerts): gate strike-origin alerts to key-level proximity`
- `chore(oi): default open_interest tab visible`

### 6.3 Do / don't

**Do:**
- Reuse `createPriceLine` for the OI overlay — do not roll a custom canvas overlay.
- Reuse `compute_key_levels` / `compute_trader_stats` verbatim for the 0DTE bundle — pass a filtered DataFrame, don't fork the function.
- Apply `apply_plotly_theme(fig)` at the END of each builder so it overrides any per-chart overrides predictably.

**Don't:**
- Don't touch the Black-Scholes pipeline, Greek formulas, or the IV input path (lines 1152, 1188, 1268+). The premise "use Schwab IV instead of Black-Scholes" is a category error — Schwab provides IV, not greeks; the app already uses Schwab's IV as input to Black-Scholes.
- Don't introduce a new endpoint for OI. Extend `/update`.
- Don't add the 0DTE pill to the Price Chart toolbar — it belongs on the KPI card where the Net GEX number lives.
- Don't change per-session Δ logic in alerts when gating — gate the emit, leave the computation alone.

---

## 7. Stages

Land in this order — each stage is independently committable and reviewable.

### Stage 1 — Plotly theme helper + default-on OI tab

**Goal:** one shared theme applied to every strike/exposure tab, with OI visible by default.

**Changes:**
- Add `PLOTLY_THEME` dict at module scope (near the color constants / CSS token resolution region).
- Add `apply_plotly_theme(fig) -> None`. Sets:
  - `paper_bgcolor` + `plot_bgcolor` = panel bg
  - `font.family / size / color` = UI font
  - `xaxis.gridcolor` + `yaxis.gridcolor` = `--border`
  - `xaxis.zerolinecolor` + `yaxis.zerolinecolor` = `--border` (slightly stronger)
  - `hoverlabel.bgcolor / bordercolor / font`
  - `margin` = compact `(l=48, r=24, t=32, b=40)`
  - `xaxis.nticks = 10`
  - `hovermode = 'x unified'`
- Call `apply_plotly_theme(fig)` at the end of every `create_*_chart()` builder (gamma/delta/vanna/charm/options_vol/volume/premium/centroid/open_interest). Skip `create_large_trades_table` (returns HTML, not Plotly).
- Flip `open_interest: false` → `true` at line 7335.

**Verification:**
- Run the app, cycle through every strike/exposure tab. Confirm consistent background, grid color, tick density, hover format.
- Confirm OI tab appears default-enabled after a cache-clear load.

### Stage 2 — Top-5 OI overlay backend

**Goal:** `/update` response exposes `top_oi`.

**Changes:**
- Add `compute_top_oi_strikes(calls_df, puts_df, n=5) -> dict`. Algorithm:
  1. Filter both DFs to the nearest expiration (min of `expiration_date` ≥ today, falls back to min overall if none in the future).
  2. `calls.groupby('strike')['openInterest'].sum().nlargest(n).reset_index()` → list of `{strike, oi}`.
  3. Same for puts.
  4. `overlap = set(top_calls.strike) & set(top_puts.strike)` → list.
- Call it in `/update` route; attach as `top_oi` key on the response.
- Tolerate `calls` or `puts` being empty (return `{calls: [], puts: [], both: []}`).

**Verification:**
- `curl localhost:5001/update -X POST ...` and inspect `top_oi` — 5 entries per side (or fewer if sparse), `both` is the intersection of strike values.

### Stage 3 — Top-5 OI overlay frontend

**Goal:** toolbar button + dotted price lines.

**Changes:**
- Add `{k:'oi', l:'OI', t:'Top 5 OI strikes (nearest exp)'}` to the indicator button array (~line 7864).
- Add `--gold: #D4AF37` CSS token to the root block (~line 5349).
- Declare `let tvTopOILines = []` near `tvKeyLevelLines`.
- Add `renderTopOI(topOi)` mirroring `renderKeyLevels()`:
  1. Remove-all from `tvTopOILines`, clear.
  2. If `!activeInds.has('oi') || !topOi` → return.
  3. Build `Map<strike, {color, title}>`. Seed with call top-5 (`color: --call`, `title: 'C OI ' + strike`). For each put top-5 strike: if Map has it → upgrade to `{color: --gold, title: '★ ' + strike}`; else add `{color: --put, title: 'P OI ' + strike}`.
  4. Iterate Map → `tvCandleSeries.createPriceLine({ price, color, lineWidth: 1, lineStyle: LS.Dotted, axisLabelVisible: true, title })` → push into `tvTopOILines`.
- Call `renderTopOI(lastUpdate.top_oi)` inside `applyIndicators(candles)` (which already fires on tick refresh).

**Verification:**
- Click `OI` button — ≤10 dotted lines appear. Green for call-only, red for put-only, gold `★`-labeled for overlap.
- Wait 2s (tick refresh) — no flicker. Composition adapts if OI shifts.
- Click again — all vanish.

### Stage 4 — 0DTE stats + key-levels bundle (backend) and pill (frontend)

**Goal:** client toggle swaps KPIs and chart lines without a fetch.

**Changes (backend):**
- In `/update` route, after computing existing `stats` and `key_levels`:
  ```python
  nearest_exp = nearest_future_expiration(calls)  # helper; min(exp) >= today else min(exp)
  calls_0dte = calls[calls['expiration_date'] == nearest_exp]
  puts_0dte  = puts [puts ['expiration_date'] == nearest_exp]
  stats_0dte      = compute_trader_stats(calls_0dte, puts_0dte, S, ...)
  key_levels_0dte = compute_key_levels (calls_0dte, puts_0dte, S)
  ```
- Attach as `stats_0dte` and `key_levels_0dte` response keys.

**Changes (frontend):**
- Add `gexScope: 'all' | '0dte'` state var (persist in localStorage).
- Add pill `[ All ][ 0DTE ]` segmented control into the KPI card region inside `buildAlertsPanelHtml` (and any side-panel render call that rebuilds this card).
- On pill click: toggle `gexScope`, call a new `redrawGexScope()` that:
  1. Reads `bundle = gexScope === '0dte' ? lastUpdate.stats_0dte : lastUpdate.stats`.
  2. Re-renders KPI card numbers (Net GEX, Net DEX, deltas).
  3. Swaps `tvKeyLevelLines` source bundle to `lastUpdate.key_levels_0dte` (or full) and re-invokes `renderKeyLevels()`.
- Call `redrawGexScope()` in `applyIndicators` / the per-tick render loop so it persists across refreshes.

**Verification:**
- Click `0DTE` pill — Net GEX, Net DEX, deltas, Call Wall, Put Wall, Gamma Flip, HVL, ±σ EM all swap without a network request (Dev Tools Network panel idle).
- Click `All` — values restore.
- Weekend load: pill still works; backend returns nearest-future-expiration data.

### Stage 5 — Flow-alert key-level gating

**Goal:** strike-origin alerts only fire near key levels; wall-shift alerts unaffected; setting toggle.

**Changes (backend):**
- Extend `compute_flow_alerts` signature with `key_levels: dict, spot: float, gate_strike_alerts: bool = True`.
- Inside the emit loop, for each candidate alert:
  - If type is `wall_shift` or `gamma_flip_move` → emit unconditionally.
  - Else, compute `proximity = min(|alert.strike - kl| for kl in active_levels) / spot`. If `gate_strike_alerts and proximity > 0.0025` → skip.
- `active_levels` = `{call_wall, call_wall_2, put_wall, put_wall_2, gamma_flip, hvl, em_upper, em_upper_2, em_lower, em_lower_2}` filtered to non-null.
- Route reads `gate_alerts` bool from POST body (or query), default True; threads to `compute_flow_alerts`.

**Changes (frontend):**
- Add `☑ Only alert near key levels` checkbox in the settings drawer under a new `ALERTS` group (or the existing `CHART OVERLAYS` group — whichever the settings panel structure permits cleanly). Default checked.
- Persist via localStorage alongside existing settings.
- Send `gate_alerts` in the `/update` POST body.

**Verification:**
- Default ON: alerts fire only for strikes within ±0.25% of spot of any key level. Wall-shift alerts always fire.
- Uncheck setting → previously-suppressed strike-origin alerts reappear.
- Confirm the alerts rail visual grammar (card module, rail alerts list, cooldowns) is unchanged — only the set of emitted alerts shrinks.

---

## 8. Out of scope (explicit)

Deferred to a later phase so this one stays reviewable:

- **Strike-level GEX rate-of-change** (Gemini suggestion: show 15-min Δ per wall strike). Data is in SQLite `interval_data`; build it next.
- **0DTE toggle for the future strike rail.** This phase only toggles the KPI card and chart lines. The follow-on UX/layout phase should carry the same state into the promoted strike-aligned rail tabs so they stop behaving as full-chain-only views.
- **Fixed N-strikes-around-spot window.** Bottom tabs stay percentage-range-based.
- **Vomma / Speed / Color bottom tabs.** Already computed server-side; tabs are hidden. Not in this phase.
- **Moving the "Exposure Metric" dropdown** (OI vs Volume weighting for greek aggregation). Works as designed.

## 9. Hand-off notes

- The OI overlay button is the lowest-risk, highest-visibility feature. If time pressure forces a cut, land Stages 1–3 and ship. Stages 4–5 can follow in a minor PR.
- The 0DTE bundle doubles backend work per `/update` tick but the extra work is cheap (one filter + two analytics calls on a subset DF). If latency regresses measurably, add a TTL cache keyed on `(ticker, minute, nearest_exp)`.
- The flow-alert gate is the one change with semantic risk. A/B it by leaving the setting exposed with a clear default ON; monitor over a week whether users opt out. If >50% disable, retune the proximity band or move the gate logic server-side behind a per-alert-type config.
