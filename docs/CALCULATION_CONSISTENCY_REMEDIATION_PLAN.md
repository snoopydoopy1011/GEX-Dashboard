# GEX Dashboard — Calculation Consistency + Alert Semantics Remediation Plan

**Status:** Implemented on branch; local regression sweep is reproducible; optional live-session verification remains
**Owner:** Codex
**Created:** 2026-04-22
**Updated:** 2026-04-22
**Target branch:** `codex/calc-consistency-stage1`
**Base:** `main`
**Source:** Review findings captured on 2026-04-22
**Implementation commit:** `477ac3d` (`fix calculation consistency and alert scope`)

---

## 0. Read This First

This document exists to remediate five concrete review findings in `ezoptionsschwab.py` without changing the underlying Greek formulas or the single-file app structure.

As of 2026-04-22 after implementation:

- The remediation work has been implemented on branch `codex/calc-consistency-stage1`.
- The code changes were committed as `477ac3d`.
- The original five calculation / alert-consistency findings in this document have been addressed in `ezoptionsschwab.py`.
- Remaining work from this document is limited to optional live-session / UI verification, plus any follow-up tweaks discovered during that verification.

Line numbers in this document are a snapshot from `main` on 2026-04-22. They will drift. Grep by anchor name first.

Recommended first commands in a new session:

```bash
git branch -a
git log --oneline main..HEAD
rg -n "calculate_expected_move_snapshot|compute_flow_alerts|compute_trader_stats|_compute_session_deltas|_compute_level_session_deltas|store_interval_data|build_flow_pulse_snapshot" ezoptionsschwab.py
```

If reviewing this work after the fact, also run:

```bash
git show --stat 477ac3d
git diff main...HEAD -- ezoptionsschwab.py docs/CALCULATION_CONSISTENCY_REMEDIATION_PLAN.md
```

---

## 0.1 Current State

### Implemented

1. Vol-spike alerts now derive interval deltas from cumulative `interval_data.net_volume` snapshots at read time.
2. Expected-move selection is deterministic and shared across the main EM helper, `compute_key_levels()`, `/update`, and `/update_price`.
3. Session baseline dictionaries are scoped by explicit analytics scope instead of only `(ticker, date)`.
4. `chain_activity` now uses the same strike-window / expiry scope as the surrounding stats cards.
5. IV-surge buffers and alert ids are separated by side and expiry.
6. A repo-local regression script now exercises the stage-6 risk areas: `python3 scripts/check_calc_consistency_stage6.py`.

### Still worth checking manually

1. Live-session behavior for vol-spike alerts on a genuinely active ticker.
2. Multi-expiry EM behavior in the rendered UI, especially agreement between chart overlays and rail cards.
3. Intraday strike-range / expiry changes in the live dashboard to confirm the scoped baseline reset is intuitive.

### Out of scope for this document

1. Reordering right-rail cards.
2. Removing static regime copy from Live Alerts.
3. Adding style/color controls for built-in chart indicators.

---

## 1. Scope

This remediation phase addresses only these five issues.

Implementation status: all five are now addressed on `codex/calc-consistency-stage1`.

The issues were:

1. Volume-spike alerts are using cumulative `net_volume` snapshots as if they were interval volume.
2. Expected-move selection is nondeterministic when multiple expiries are present.
3. "Since open" deltas are not scoped to strike range / expiry scope.
4. `chain_activity` is full-chain while the adjacent analytics cards are spot-windowed.
5. IV-surge alerts collapse calls and puts into a shared strike buffer.

This plan does **not** cover:

- Reordering right-rail cards.
- Adding indicator style/color controls to the chart toolbar.
- Any formula rewrite for GEX / DEX / Vanna / Charm / EM.
- Splitting `ezoptionsschwab.py`.

---

## 2. Ground Rules

- No analytical-formula changes.
- No new framework.
- Keep the single-file Flask app structure.
- Prefer additive helpers over broad rewrites.
- Avoid changing existing SQLite column meanings unless there is no safe alternative.
- Reuse one helper in multiple call sites rather than duplicating logic again.

---

## 3. Review Findings to Fix

### Finding A — Vol-spike alert uses cumulative snapshots incorrectly

**Status:** Fixed

**Review reference:** `ezoptionsschwab.py:4443-4455`

**Current behavior:**

- `store_interval_data()` stores `net_volume` per strike in `interval_data`.
- That stored value is the current signed session volume snapshot at the strike:
  - calls add volume
  - puts subtract volume
- `_fetch_vol_spike_data()` reads the last 20 rows per strike and treats those stored snapshots as if each row were a one-minute interval volume sample.

**Why it is wrong:**

- The alert compares cumulative signed state against a rolling average of cumulative signed state.
- Late-session cumulative totals can look "spiky" even when minute-over-minute flow is normal.
- Real short bursts can be muted because the code is not measuring minute deltas.

### Finding B — Expected move can come from an arbitrary expiry

**Status:** Fixed

**Review reference:** `ezoptionsschwab.py:192-214`

**Current behavior:**

- `calculate_expected_move_snapshot()` finds an ATM strike across the whole DataFrame.
- It then selects the first row at that strike in calls and puts.
- The `/update_price` price-info block duplicates this same selection logic separately.

**Why it is wrong:**

- With multiple expiries selected, the EM straddle can depend on row ordering, not deterministic contract selection.
- `compute_key_levels()` also filters expiries for wall/flip logic but then calls the EM helper with the unfiltered input, which lets EM drift from the rest of the selected scope.

### Finding C — Session deltas are not scope-aware

**Status:** Fixed

**Review reference:** `ezoptionsschwab.py:4153-4186`

**Current behavior:**

- `_SESSION_BASELINE` and `_SESSION_LEVEL_BASELINE` are keyed only by `(ticker, date)`.
- `compute_trader_stats()` is called for both the full bundle and the 0DTE bundle.
- The user can also change strike range and expiry selection intraday.

**Why it is wrong:**

- "Since open" becomes apples-to-oranges after scope changes.
- The 0DTE bundle and the full bundle can inherit each other's baseline.
- Level drift can reflect a different expiry mix than the currently visible stats.

### Finding D — Chain Activity is inconsistent with the analytics window

**Status:** Fixed

**Review reference:** `ezoptionsschwab.py:4626-4649`

**Current behavior:**

- `net_gex`, `net_dex`, dealer-impact rows, and scenarios are computed inside the current strike window.
- `chain_activity` sums `openInterest` and `volume` across the full chain with no matching window filter.

**Why it is wrong:**

- The rail mixes scoped and unscoped numbers in adjacent cards.
- Narrowing strike range or expiries can change the top cards while leaving Chain Activity effectively on "all contracts".

### Finding E — IV-surge alert path collapses sides at the same strike

**Status:** Fixed

**Review reference:** `ezoptionsschwab.py:4484-4512`

**Current behavior:**

- `_IV_BUFFER` is keyed by `(ticker, strike)`.
- `compute_flow_alerts()` uses `seen_strikes` to skip any later contract at the same strike.

**Why it is wrong:**

- Calls and puts at the same strike share one rolling IV buffer.
- The first side processed wins; the second side is ignored.
- The Skew / IV card already treats call and put wings separately, so the alert engine should not collapse them.

---

## 4. Stable Anchors

Use these anchors instead of trusting line numbers:

- `store_interval_data`
- `_fetch_vol_spike_data`
- `compute_flow_alerts`
- `calculate_expected_move_snapshot`
- `compute_key_levels`
- `compute_trader_stats`
- `_compute_session_deltas`
- `_compute_level_session_deltas`
- `_SESSION_BASELINE`
- `_SESSION_LEVEL_BASELINE`
- `_IV_BUFFER`
- `build_flow_pulse_snapshot`
- `/update_price`

Also inspect these data stores:

- SQLite table `interval_data`
- SQLite table `interval_session_data`

---

## 5. Implementation Strategy

### 5.1 Fix vol-spike alerts without redefining `interval_data.net_volume`

**Decision:**

Do **not** change the meaning of `interval_data.net_volume` in this phase. Existing historical views may already rely on its current semantics. Instead, compute minute-over-minute deltas at read time inside the alert path.

**Implementation details:**

- Keep `store_interval_data()` as-is for the schema and write path.
- Update `_fetch_vol_spike_data()` so it:
  - reads ordered per-strike `net_volume` snapshots from SQLite
  - converts the cumulative signed series into interval deltas
  - uses the absolute value of each interval delta as the spike sample
- For each strike:
  - `delta_i = snapshot_i - snapshot_(i-1)`
  - `curr = abs(last_delta)`
  - `avg20 = mean(abs(previous_deltas))`
- Handle sparse history:
  - if there is only one snapshot for a strike, there is no delta yet
  - do not emit a spike until at least 2 samples exist
- Preserve the existing cooldown and key-level gate logic in `compute_flow_alerts()`.

**Why this design:**

- Minimal surface area.
- No migration required.
- Keeps historical overlay semantics stable.
- Fixes the actual alert logic rather than reinterpreting stored data globally.

**Code areas to update:**

- [ezoptionsschwab.py](/Users/scottmunger/Desktop/Trading/Dashboards/GEX-Dashboard/ezoptionsschwab.py:435)
- [ezoptionsschwab.py](/Users/scottmunger/Desktop/Trading/Dashboards/GEX-Dashboard/ezoptionsschwab.py:4345)
- [ezoptionsschwab.py](/Users/scottmunger/Desktop/Trading/Dashboards/GEX-Dashboard/ezoptionsschwab.py:4443)

### 5.2 Make expected-move selection deterministic and shared

**Decision:**

Create one shared helper for expected-move contract selection and reuse it everywhere EM is calculated.

**Implementation details:**

- Add a helper near `calculate_expected_move_snapshot()`, for example:
  - `_select_expected_move_contracts(calls, puts, spot_price, selected_expiries=None)`
- The helper must:
  - choose one expiry first
  - then choose the ATM strike within that expiry
  - then select the call and put from that same expiry and strike
- Expiry selection rule:
  - if `selected_expiries` is provided, choose the nearest selected expiry present in both chains
  - otherwise choose the nearest future expiry present in both chains
- Return enough context to avoid duplicate logic:
  - `expiry`
  - `atm_strike`
  - `call_mid`
  - `put_mid`
- Refactor `calculate_expected_move_snapshot()` to use that helper.
- Refactor the `/update_price` `expected_move_range` block to call the same helper instead of repeating its own row-picking logic.
- Update `compute_key_levels()` so EM uses the same filtered scope as walls and gamma flip:
  - either pass filtered DataFrames into the helper
  - or pass `selected_expiries` through explicitly

**Why this design:**

- Removes row-order dependence.
- Removes duplicated EM logic.
- Keeps EM consistent between chart levels and the rail card.

**Code areas to update:**

- [ezoptionsschwab.py](/Users/scottmunger/Desktop/Trading/Dashboards/GEX-Dashboard/ezoptionsschwab.py:187)
- [ezoptionsschwab.py](/Users/scottmunger/Desktop/Trading/Dashboards/GEX-Dashboard/ezoptionsschwab.py:3927)
- [ezoptionsschwab.py](/Users/scottmunger/Desktop/Trading/Dashboards/GEX-Dashboard/ezoptionsschwab.py:16629)

### 5.3 Scope baselines by actual analytics scope

**Decision:**

Do not infer baseline scope from the raw DataFrames. Pass an explicit `scope_id` into the stats and baseline helpers.

**Implementation details:**

- Add a lightweight scope-id helper, for example:
  - `_build_stats_scope_id(strike_range, selected_expiries=None, scope_label='all')`
- Baseline key should include:
  - `ticker`
  - session date
  - explicit scope id
- Scope id should distinguish at minimum:
  - full bundle
  - 0DTE bundle
  - strike range
  - selected expiry set when the main chain is filtered by user choice
- Recommended explicit call pattern:
  - full bundle: `scope_label='all'`
  - 0DTE bundle: `scope_label=f'expiry:{nearest_exp}'`
- Update:
  - `_compute_session_deltas(...)`
  - `_compute_level_session_deltas(...)`
  - `compute_trader_stats(...)`
- Add `scope_id` as an optional argument to `compute_trader_stats(...)`.
- Pass `scope_id` from both stats call sites in `/update_price`.

**Why this design:**

- Prevents full-chain and 0DTE baselines from leaking into each other.
- Makes "since open" meaningful after intraday scope changes.
- Avoids fragile attempts to reconstruct scope after the DataFrames have already been filtered upstream.

**Code areas to update:**

- [ezoptionsschwab.py](/Users/scottmunger/Desktop/Trading/Dashboards/GEX-Dashboard/ezoptionsschwab.py:4153)
- [ezoptionsschwab.py](/Users/scottmunger/Desktop/Trading/Dashboards/GEX-Dashboard/ezoptionsschwab.py:4524)
- [ezoptionsschwab.py](/Users/scottmunger/Desktop/Trading/Dashboards/GEX-Dashboard/ezoptionsschwab.py:16825)
- [ezoptionsschwab.py](/Users/scottmunger/Desktop/Trading/Dashboards/GEX-Dashboard/ezoptionsschwab.py:16846)

### 5.4 Align Chain Activity with the active analytics window

**Decision:**

Bring `chain_activity` onto the same spot window and expiry scope as the rest of `compute_trader_stats()`.

**Implementation details:**

- Replace the current full-chain `_chain_sum()` logic with a windowed version that mirrors `_window_sum()`.
- Apply the same filters used by the main stats:
  - selected expiries when present
  - strike range centered on `S`
- Compute these from the filtered rows:
  - `call_oi`
  - `put_oi`
  - `call_vol`
  - `put_vol`
  - `oi_call_share`
  - `vol_call_share`
  - `oi_cp_ratio`
  - `vol_cp_ratio`
  - `sentiment`
- Do **not** change the UI copy in this phase unless the card still labels itself too broadly after the data is scoped.

**Why this design:**

- Makes adjacent rail cards refer to the same population.
- Avoids misleading comparison when the user narrows range or expiries.

**Code areas to update:**

- [ezoptionsschwab.py](/Users/scottmunger/Desktop/Trading/Dashboards/GEX-Dashboard/ezoptionsschwab.py:4570)
- [ezoptionsschwab.py](/Users/scottmunger/Desktop/Trading/Dashboards/GEX-Dashboard/ezoptionsschwab.py:4626)

### 5.5 Separate IV-surge buffers by side and expiry

**Decision:**

Key the IV surge buffer by contract family, not just ticker+strike.

**Implementation details:**

- Change `_IV_BUFFER` to use a key like:
  - `(ticker, option_type, expiry_iso, strike)`
- Remove the `seen_strikes` dedupe that collapses sides at the same strike.
- When iterating rows in `compute_flow_alerts()`:
  - preserve side information
  - normalize expiry using the same date-normalization helpers already in the file
- Alert ids should distinguish side and expiry, for example:
  - `iv_surge:call:2026-04-22:710`
  - `iv_surge:put:2026-04-22:710`
- If alert text would otherwise be ambiguous, include side in the message or detail.
- Keep existing cooldown logic, but cooldown should apply per alert id, not per raw strike.

**Why this design:**

- Prevents a call series from suppressing a put series.
- Prevents different expiries at the same strike from sharing one rolling buffer.
- Matches how the Skew / IV card already thinks about side-specific IV.

**Code areas to update:**

- [ezoptionsschwab.py](/Users/scottmunger/Desktop/Trading/Dashboards/GEX-Dashboard/ezoptionsschwab.py:4328)
- [ezoptionsschwab.py](/Users/scottmunger/Desktop/Trading/Dashboards/GEX-Dashboard/ezoptionsschwab.py:4484)

---

## 6. Recommended Stage Order

Land these in order so each step reduces ambiguity for the next:

### Stage 1

**Status:** Completed

`fix(alerts): compute vol spikes from interval deltas`

Deliverables:

- `_fetch_vol_spike_data()` computes interval deltas from cumulative snapshots
- `compute_flow_alerts()` consumes the corrected `curr` and `avg20`

### Stage 2

**Status:** Completed

`fix(em): make expected-move selection deterministic`

Deliverables:

- shared EM contract-selection helper
- `calculate_expected_move_snapshot()` refactored
- `/update_price` EM block refactored
- `compute_key_levels()` EM path aligned to filtered scope

### Stage 3

**Status:** Completed

`fix(stats): scope since-open baselines by active analytics scope`

Deliverables:

- explicit `scope_id` support
- baseline dictionaries keyed by scope
- full-bundle and 0DTE call sites updated

### Stage 4

**Status:** Completed

`fix(rail): align chain activity to the active strike window`

Deliverables:

- windowed chain activity aggregation
- no full-chain leakage inside `compute_trader_stats()`

### Stage 5

**Status:** Completed

`fix(alerts): separate iv-surge buffers by side and expiry`

Deliverables:

- buffer keys updated
- strike dedupe removed
- alert ids and text disambiguated

### Stage 6

**Status:** Completed locally; optional live-session verification remains open

`chore(review): regression sweep for scoped stats and alerts`

Deliverables:

- verification pass across EM, alerts, scoped deltas, and right-rail consistency
- syntax and synthetic checks completed locally
- reproducible regression command added to the repo
- optional live-session verification still open

---

## 7. Verification Checklist

Implementation status summary:

- `python3 scripts/check_calc_consistency_stage6.py` passed on 2026-04-22 and now serves as the reproducible local regression sweep for this phase.
- `python3 -m py_compile ezoptionsschwab.py` passed after the remediation changes.
- Flask startup reached app initialization; the only observed blocker was that port `5001` was already in use during the smoke check.
- The scripted sweep covers deterministic EM selection, scope-separated baselines, scoped Chain Activity aggregation, IV-buffer separation, and vol-spike interval-delta interpretation.
- SQLite spot checks confirmed that the vol-spike fix now interprets stored cumulative `net_volume` snapshots as interval deltas instead of raw rolling values.
- IV buffer keys were verified to separate call and put state at the same strike / expiry.

### 7.1 Syntax and startup

Run:

```bash
python3 scripts/check_calc_consistency_stage6.py
python3 -m py_compile ezoptionsschwab.py
python3 ezoptionsschwab.py
```

Pass criteria:

- no syntax errors
- no startup tracebacks in the first 30 seconds

### 7.2 Vol-spike regression

Manual expectations:

- A quiet late-session strike should not repeatedly trigger just because cumulative signed volume is large.
- A genuine one-minute burst should be able to trigger even if cumulative signed volume is still modest.

Suggested checks:

- log the last few raw snapshots and derived deltas for one strike during a live session
- confirm `curr` reflects minute delta, not cumulative level

### 7.3 Expected-move determinism

Manual expectations:

- With multiple expiries selected, EM should not change across refreshes unless the actual chosen ATM straddle changes.
- The chart EM lines and right-rail EM card should agree.

Suggested checks:

- run the same request twice with multiple expiries selected and compare EM output
- reorder the DataFrame upstream if needed during local debugging and confirm EM does not change

### 7.4 Scoped baselines

Manual expectations:

- Full-bundle `Δ since open` should stay stable when the 0DTE pill is toggled away and back.
- 0DTE deltas should not inherit the full-bundle open baseline.
- Changing strike range should start a separate scoped baseline rather than silently reusing the old one.

Suggested checks:

- compare `stats` vs `stats_0dte` after several ticks
- change strike range intraday and verify the delta labels are recomputed from a scope-matching baseline

### 7.5 Chain Activity consistency

Manual expectations:

- Narrow strike ranges should visibly change Chain Activity in the same direction as nearby scoped cards.
- Multi-expiry narrowing should not leave Chain Activity on the old whole-chain numbers.

Suggested checks:

- compare the card before and after reducing strike range from a wide window to a narrow window
- compare the card under full expiries vs a narrow expiry selection

### 7.6 IV-surge separation

Manual expectations:

- A put IV move at a strike should not be blocked by the call at the same strike.
- If multiple expiries at the same strike are present, buffers should not contaminate each other.

Suggested checks:

- log the `_IV_BUFFER` keys while processing both calls and puts
- confirm alert ids differ by side and expiry

---

## 8. Open Questions

These were resolved during implementation:

1. EM uses the nearest expiry within the active selected set when a selected set is provided.

2. Scope-aware baseline changes reset immediately for the new scope.

3. IV-surge alerts keep the headline short and put expiry context in `detail`.

Remaining follow-up outside the implementation itself:

4. A live-session manual pass is still desirable before or after merge, but it is not a blocker for considering this remediation plan complete locally.

---

## 9. Non-Goals and Deferrals

The following came up in review but are intentionally not part of this document:

- Reordering cards in the Alerts rail
- Removing regime copy from the Live Alerts list
- Adding indicator line color/style controls for SMA / EMA / VWAP / BB
- Consolidating old popout-only chart code

Those can be handled in a separate UX / controls plan after these correctness fixes land.

---

## 10. Definition of Done

This remediation plan is complete locally when all five review findings are closed and the following are true:

- Vol-spike alerts are based on interval deltas, not cumulative snapshot values.
- Expected move is deterministic and shared across all call sites.
- Session deltas are scoped correctly for full-chain vs 0DTE and for strike-range / expiry changes.
- Chain Activity reflects the same active scope as the surrounding rail analytics.
- IV-surge alerts distinguish calls vs puts and do not share strike-only buffers.
- `python3 scripts/check_calc_consistency_stage6.py` passes.
- `python3 -m py_compile ezoptionsschwab.py scripts/check_calc_consistency_stage6.py` passes.

Recommended but non-blocking follow-up:

- Live-session manual smoke confirms no new mismatches between chart overlays and right-rail cards.
