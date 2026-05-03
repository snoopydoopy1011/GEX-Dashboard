# Order Rail Scalp Targets Implementation Plan

Created: 2026-05-03
Status: initial implementation completed on 2026-05-03. Follow-up pass required for purchased-position-only basis behavior.

## Goal

Add two order-entry rail upgrades for fast 0 - 1 DTE SPY option scalping:

1. Add a Thinkorswim-style P/L column on the right edge of the Active Trader ladder.
2. Add a compact Scalp Targets card above Active Trader that shows:
   - the SPY underlying price where the selected contract reaches breakeven, using the current exit/bid assumption.
   - the SPY underlying price where the selected contract reaches a configurable profit per contract, defaulting to $100/contract.

These are display/planning features only. They must not change order preview, order placement, Schwab order JSON, journal behavior, or the existing GEX/DEX/Vanna/Charm/Flow analytical formulas.

## Follow-Up Required: Purchased Position Basis Only

The initial implementation added the Scalp Targets card and Active Trader P/L ladder as planning surfaces. A follow-up session should tighten the basis rule so both surfaces are driven only by an actual purchased/open selected-contract position.

Required follow-up behavior:

- The Active Trader ladder P/L column should stay blank or `—` unless the selected contract matches an active long position in `tradeRailState.accountDetails.positions`.
- When a matching position exists, ladder P/L should be based on the Schwab position `average_price` normalized to option premium units and multiplied by the actual selected-contract position quantity.
- The Scalp Targets card should not show B/E SPY or target-profit SPY unless the selected contract has a matching active long position.
- Scalp Targets math should use the position average purchase price as the entry basis. If multiple contracts are held, this should be the average contract purchase price from Schwab, not the staged rail limit, current ask, mid, mark, or any planning fallback.
- The target-profit input remains editable and locally persisted, but it only affects display when a matching active purchased position exists.
- If no active selected-contract position exists, show neutral empty values and an `Unavailable` method chip. Do not show planned basis, ask basis, or limit basis.
- Keep this display-only. Do not change preview/place order payloads, Schwab order construction, journal behavior, or any GEX/DEX/Vanna/Charm/Flow formulas.

## Current Session Context

- The repo is `/Users/scottmunger/Desktop/Trading/Dashboards/GEX-Dashboard`.
- Branch observed in this planning session: `main`.
- `git log --oneline main..HEAD` was empty.
- Existing unrelated untracked file observed: `Trading_from_dashboard.txt`. Leave it alone unless the user explicitly asks.
- A smoke Flask instance was opened on `http://127.0.0.1:5014/` for UI inspection during this planning session. Kill it before handing off so the next session can bind to the same port.

## Port 5014 Handoff

Use `5014` as the smoke port in the implementation session so the user can compare against this planning context without disturbing a normal `5001` dashboard.

Before starting work in the next session:

```bash
curl -s http://127.0.0.1:5014/
```

If it responds, find and stop the stale app process before starting a new one. Prefer checking for the app process by command:

```bash
ps aux | rg 'PORT=5014|ezoptionsschwab.py'
```

Then kill only the relevant `ezoptionsschwab.py` process for port `5014`.

Start the smoke server after implementation with:

```bash
PORT=5014 FLASK_DEBUG=0 FLASK_USE_RELOADER=0 python3 ezoptionsschwab.py
```

Open `http://127.0.0.1:5014/` in the in-app browser and verify the rail visually. Do not leave the server running when the implementation session ends unless the user asks to keep it up.

## Design Constraints

- Keep the single-file `ezoptionsschwab.py` structure.
- No JS framework.
- Use existing color tokens. Do not add hardcoded neon hex colors.
- Keep this a dense trading tool, not a marketing/card-heavy screen.
- Preserve the preview-first safety model for live orders.
- This feature should be read-only/planning only.
- Use client-side calculations for the card and ladder P/L so targets update quickly with quote/time changes and do not require new endpoints.
- Reuse existing trade rail state and cached chain payload. Add only the small fields needed for expiry timing.

## Existing Anchors

Grep anchors rather than trusting line numbers:

- Backend cached trade payload: `build_trading_chain_payload`
- Trade rail state: `const tradeRailState = {`
- Active Trader static markup: `<section class="trade-panel trade-active-panel" data-trade-active-panel>`
- Active Trader rebuild markup: `buildTradeActiveTraderPanelHtml`
- Whole trade rail rebuild: `buildTradeRailHtml`
- Rebuild guard: `ensurePriceChartDom`
- Active ladder CSS: `.trade-active-ladder-head, .trade-active-ladder-row`
- Ladder row renderer: `buildTradeActiveLadderHtml`
- Active Trader renderer: `renderTradeActiveTrader`
- Position lookup helpers: `getSelectedTradePosition`, `getSelectedTradePositionQuantity`
- Rail control wiring: `wireTradeRailPickerControls`
- Selected contract renderer: `renderTradeSelected`
- Main rail renderer: `renderTradeRail`

## UI Plan

### Scalp Targets Card

Placement: directly above Active Trader in the order-entry rail.

Reason: this is decision support for the selected order, so it belongs beside order entry. The right analytics rail is market context and should not own trade-specific basis/target math.

Suggested compact layout:

```text
SCALP TARGETS                              TARGET / CT  [100]
+-------------------------------------------------------------+
| B/E SPY                    +$100/CT SPY                     |
| 719.82                     720.44                           |
| -0.18 vs spot              +0.44 vs spot                    |
| Basis 2.43 pos avg         IV model | exit bid              |
+-------------------------------------------------------------+
```

Behavior:

- Header input defaults to `100` and means dollars per contract.
- The target input should be small, numeric, and persisted to localStorage.
- Breakeven should mean "where the selected contract can be exited around current bid for the entry basis," not expiration breakeven.
- If already in profit, the breakeven SPY value can be behind current spot. This is useful as a giveback line.
- Use a confidence/method chip:
  - `IV model` when Black-Scholes anchored to the live quote is available.
  - `Delta/gamma` when using the approximation fallback.
  - `Unavailable` when input quality is insufficient.
- Keep the card visually short. Do not add explanatory paragraphs in the app UI.

Suggested class/data names:

- `trade-scalp-target-panel`
- `data-trade-scalp-target-panel`
- `data-trade-scalp-target-profit`
- `data-trade-scalp-breakeven`
- `data-trade-scalp-breakeven-move`
- `data-trade-scalp-profit`
- `data-trade-scalp-profit-move`
- `data-trade-scalp-basis`
- `data-trade-scalp-method`

### Active Trader P/L Ladder Column

Current ladder columns:

```text
Buy | Bid | Price | Ask | Sell
```

Target ladder columns:

```text
Buy | Bid | Price | Ask | Sell | P/L
```

Behavior:

- The ladder rows are option premium levels, not SPY underlying levels.
- For a selected long position, P/L at a row is:

```text
(row premium - entry basis premium) * 100 * position quantity
```

- If no live position exists but a staged entry limit exists, show a muted "planned" P/L using quantity from the rail.
- If no basis exists, show blank or `-`.
- Positive values use `var(--call)`, negative values use `var(--put)`, zero/missing uses muted foreground.
- The P/L cell must not add buttons or intercept ladder row clicks.
- Keep the P/L column narrow and tabular. This is a glance column, not a second stats panel.

Suggested class/data names:

- `trade-active-pnl`
- `trade-active-pnl.pos`
- `trade-active-pnl.neg`
- `trade-active-pnl.plan`

## Data Additions

The cached trade payload already includes most needed inputs:

- `underlying_price`
- contract `strike`
- contract `option_type`
- contract `expiry`
- contract `bid`, `ask`, `mid`, `mark`, `last`
- contract `iv`
- contract `delta`, `gamma`, `theta`, `vega`
- contract `quote_time`, `quote_age_seconds`

Add one timing field to each contract in `build_trading_chain_payload`:

- `expiry_time`: epoch milliseconds for 16:00 ET on the contract expiration date.

Optional but useful:

- `time_to_expiry_seconds`: seconds from payload build time to `expiry_time`.

Prefer `expiry_time` as the durable client-side value, because the browser can recompute shrinking time to expiry without a new chain payload.

Use the same timing convention as existing `calculate_time_to_expiration`: expiration is 16:00 ET on the expiration date, floor time-to-expiry at the same practical minimum used by existing option calculations.

## State Additions

Add a localStorage key near the other trade rail keys:

```js
const TRADE_SCALP_TARGET_PROFIT_KEY = 'gex.trade.scalpTargetProfit';
```

Add state:

```js
scalpTargetProfitPerContract: getTradeStoredNumber(TRADE_SCALP_TARGET_PROFIT_KEY, 100),
```

If there is no numeric helper yet, add a small one near `getTradeStoredBool`:

```js
function getTradeStoredNumber(key, fallback) {
    try {
        const saved = Number(localStorage.getItem(key));
        if (Number.isFinite(saved)) return saved;
    } catch (e) {}
    return fallback;
}
```

Clamp target profit to a sane positive range in the input handler, for example `$1` to `$5000`.

## Markup Implementation

1. Add `buildTradeScalpTargetsPanelHtml()` near `buildTradeActiveTraderPanelHtml()`.
2. In `buildTradeRailHtml()`, insert `buildTradeScalpTargetsPanelHtml()` before `buildTradeActiveTraderPanelHtml()`.
3. Add matching static server-rendered markup before the existing Active Trader section in the initial HTML. The rebuild path already has a JS builder, but the first render still comes from Python's HTML string.
4. Update the `ensurePriceChartDom` rail rebuild guard so a missing `[data-trade-scalp-target-panel]` triggers a rail rebuild.
5. Add CSS near the trade rail CSS:

```css
.trade-scalp-target-panel {
    order: 8;
    border-color: color-mix(in srgb, var(--accent) 24%, var(--border));
}
.trade-scalp-target-head { ... }
.trade-scalp-target-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px; }
.trade-scalp-target-cell { ... }
.trade-scalp-target-value { font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
.trade-scalp-target-move.pos { color: var(--call); }
.trade-scalp-target-move.neg { color: var(--put); }
```

Keep border radius at the existing rail/card radius.

## Math Implementation

Add client-side helpers near the trade rail helper functions, close to `getTradePremiumBase` or the selected contract helpers.

### Entry Basis

Helper: `getTradeEntryBasis(selected)`.

Source priority:

1. selected live position `average_price`
2. staged `tradeRailState.limitPrice`
3. selected `ask` for planning fallback
4. selected `mid`/`mark` only if ask is unavailable

Return shape:

```js
{
    premium: 2.43,
    source: 'pos avg',
    quantity: 1,
    livePosition: true
}
```

Watchout: Schwab option `averagePrice` units must be verified with a live position. If it appears to be total dollars per contract instead of option premium, add a defensive normalization helper:

```js
function normalizeTradePositionAveragePrice(avg, selected) {
    const n = Number(avg);
    const ref = Number(selected && (selected.mid || selected.mark || selected.ask || selected.bid));
    if (!Number.isFinite(n) || n <= 0) return null;
    if (Number.isFinite(ref) && ref > 0 && n > ref * 20 && Math.abs((n / 100) - ref) < Math.max(2, ref * 3)) return n / 100;
    return n;
}
```

Do not silently change this if live data proves a different Schwab convention. Make it visible in the method/basis text if normalization is applied.

### Black-Scholes Anchor

Implement:

- `normalCdf(x)` using a small approximation.
- `priceTradeOptionBlackScholes(optionType, spot, strike, years, iv, r = 0.02, q = 0)`.
- `getTradeYearsToExpiry(selected)`.
- `estimateTradeExitBidAtUnderlying(selected, candidateSpot)`.

Use the existing app assumptions:

- `r = 0.02`
- `q = 0`
- IV is decimal (`0.12`, not `12`)
- expiration is 16:00 ET
- use a small positive time floor

Quote anchoring:

```text
liveRef = selected.mid || selected.mark || midpoint(bid, ask) || selected.last
modelAtSpot = BS(currentSpot)
anchor = liveRef - modelAtSpot
estimatedMid(candidate) = max(0.01, BS(candidate) + anchor)
estimatedBid(candidate) = max(0.01, estimatedMid(candidate) - halfSpread)
```

Use `estimatedBid` for exit target math because the rail's sell action is `Sell Bid`.

### Solver

Helper: `solveTradeUnderlyingForExitPremium(selected, targetPremium)`.

Inputs:

- current SPY from `tradeRailState.payload.underlying_price`
- selected contract
- target premium

Monotonic direction:

- Calls increase as SPY rises.
- Puts increase as SPY falls.
- If current estimated bid is already above breakeven, the breakeven solution is behind current SPY.

Approach:

1. Estimate current exit bid at current spot.
2. Decide solve direction based on target premium versus current estimated bid and option type.
3. Expand a bracket from current spot in that direction, starting around `$0.25` and expanding up to a cap such as `max(10, spot * 0.06)`.
4. Binary search for 35 - 45 iterations.
5. Return unavailable if no crossing is found within the cap.

Return shape:

```js
{
    spot: 720.44,
    move: 0.44,
    targetPremium: 3.43,
    method: 'iv_model',
    confidence: 'high'
}
```

### Delta/Gamma Fallback

Use only when Black-Scholes inputs are missing but delta is available.

Approximation:

```text
targetPremium - currentExitPremium ~= delta * dS + 0.5 * gamma * dS^2
```

If gamma is missing or too small:

```text
dS = premiumDelta / delta
```

For puts, delta is negative, so direction should naturally invert.

Label this as lower confidence in the UI with `Delta/gamma`.

### Target Calculations

In `getTradeScalpTargets(selected)`:

```js
const basis = getTradeEntryBasis(selected);
const targetProfit = Math.max(1, Number(tradeRailState.scalpTargetProfitPerContract) || 100);
const profitPremiumOffset = targetProfit / 100;

const breakevenPremium = basis.premium;
const profitPremium = basis.premium + profitPremiumOffset;
```

Then solve:

- B/E SPY from `breakevenPremium`
- `+$target/ct SPY` from `profitPremium`

## P/L Ladder Implementation

In `buildTradeActiveLadderHtml(selected)`:

1. Compute basis once before mapping rows:

```js
const basis = getTradeEntryBasis(selected);
```

2. For each row premium, compute P/L:

```js
const pnl = basis && Number.isFinite(basis.premium)
    ? (price - basis.premium) * 100 * Math.max(1, basis.quantity || Number(tradeRailState.quantity) || 1)
    : null;
```

3. Add a sixth grid cell:

```html
<span class="trade-active-ladder-cell trade-active-pnl ...">+$100</span>
```

4. Update ladder header in both:
   - static markup
   - `buildTradeActiveTraderPanelHtml()`

5. Update CSS grid template:

```css
grid-template-columns:
    minmax(38px, 0.68fr)
    minmax(38px, 0.68fr)
    minmax(48px, 0.72fr)
    minmax(38px, 0.68fr)
    minmax(38px, 0.68fr)
    minmax(54px, 0.78fr);
```

Tune after screenshot verification. The rail is dense; do not let the new column make the row text wrap.

## Render and Wire Flow

Add:

- `renderTradeScalpTargets()`
- input handler for `[data-trade-scalp-target-profit]` in `wireTradeRailPickerControls`

Call `renderTradeScalpTargets()` after anything that changes selected contract, basis, account position, quote, or target:

- at the end of `renderTradeActiveTrader()`
- after `renderTradePositions()` updates account positions
- after `renderTradeSelected(contract)` changes selected contract fields
- after target profit input changes

Avoid loops:

- `renderTradeScalpTargets()` must not call `renderTradeActiveTrader()`.
- It should update only the scalp target card DOM.
- When updating the input value, do not overwrite while the input is focused.

For theta/time drift even without a fresh quote, consider a cheap interval:

```js
setInterval(() => {
    if (document.querySelector('[data-trade-scalp-target-panel]') && getSelectedTradeContract()) {
        renderTradeScalpTargets();
    }
}, 5000);
```

This is acceptable because the helper calculations are lightweight. Do not rebuild the full ladder every 5 seconds solely for theta.

## Watchouts

- Static markup and rebuild markup can drift. Update both initial HTML and `buildTradeActiveTraderPanelHtml()` / `buildTradeRailHtml()`.
- `ensurePriceChartDom` must treat the scalp target panel as required DOM.
- Do not change `previewTradeOrder`, `placeTradeOrder`, `build_single_option_limit_order`, or Schwab payload construction except for read-only display data.
- Do not reuse GEX exposure formulas for trade target math. Keep option-pricing helpers separate.
- Do not modify `calculate_greeks`, `calculate_theta`, `compute_trader_stats`, or exposure math unless a syntax/import issue requires a strictly mechanical fix.
- IV in the cached payload is already decimal. Guard percent-looking values, but do not double-divide normal values.
- Time to expiry should use 16:00 ET. After market close, use the floor and mark confidence lower if needed.
- The target math is only as good as quote/IV freshness. Surface stale quote or missing IV via method/muted state, not verbose copy.
- Average price unit from Schwab positions needs live verification.
- For puts, target and giveback directions invert. Test both call and put paths.
- In-profit breakeven is a giveback line, not an error.
- Ladder row click handling must still work after adding the P/L cell.
- The target input should persist but should not invalidate preview or alter live order data.
- The rail is already vertically dense. Keep the card compact, and verify no important Active Trader controls are pushed below the first viewport at common desktop widths.

## Verification Checklist

Code checks:

```bash
python3 -m py_compile ezoptionsschwab.py
```

Smoke:

```bash
PORT=5014 FLASK_DEBUG=0 FLASK_USE_RELOADER=0 python3 ezoptionsschwab.py
```

Browser checks at `http://127.0.0.1:5014/`:

1. Initial load with no cached chain: Scalp Targets card appears above Active Trader with empty/muted values.
2. Load SPY 0DTE or nearest expiry: selected contract fills Active Trader and Scalp Targets.
3. Change target from 100 to 50: profit target SPY updates, preview state does not reset.
4. Change contract from call to put: target direction and signs are sane.
5. Change ladder price/limit: planned basis and ladder P/L update.
6. Select an account with a matching position: basis switches to position average, ladder P/L uses position quantity.
7. Stale or missing IV: card uses fallback or unavailable state without console errors.
8. Collapse/expand Active Trader: Scalp Targets remains stable.
9. Switch ticker/expiry to trigger rail rebuild: Scalp Targets and P/L header survive.
10. Resize/narrow the rail: no text overlaps, no horizontal scroll in the rail.
11. Confirm browser console has no errors.

## Suggested Commit Scope

One focused commit is enough:

```text
feat(trade): add scalp targets and ladder pnl
```

Do not include unrelated formatting churn.

## Prompt For Next Codex Session

Use this prompt in a fresh Codex session:

```text
Please continue the order-entry rail scalp targets implementation in docs/ORDER_RAIL_SCALP_TARGETS_PLAN.md.

Start by following AGENTS.md: confirm branch with `git branch -a` and `git log --oneline main..HEAD`, then grep anchors instead of trusting line numbers. Do not change GEX/DEX/Vanna/Charm/Flow formulas, do not introduce a JS framework, and keep ezoptionsschwab.py as a single-file app.

Implementation goals:
- Update the Scalp Targets card and Active Trader P/L ladder so they only use actual purchased/open selected-contract positions.
- If the selected contract does not match an active long position, the Scalp Targets values and ladder P/L cells should stay blank or `—` with an `Unavailable` method chip.
- When a matching selected-contract position exists, use Schwab `average_price` normalized to option premium units as the sole entry basis.
- If multiple contracts are held, use the Schwab average contract purchase price and actual position quantity.
- Keep the target-profit input editable and locally persisted, but it should only affect target display when a matching active purchased position exists.
- Keep everything display/planning-only. Do not alter preview/place order payloads or Schwab order behavior.

Use port 5014 for smoke testing. If 5014 is already occupied, identify whether it is an old ezoptionsschwab.py smoke process and stop only that process. Then run:
PORT=5014 FLASK_DEBUG=0 FLASK_USE_RELOADER=0 python3 ezoptionsschwab.py

Verify with py_compile and in-app browser screenshots at http://127.0.0.1:5014/. Pay special attention to no-position empty states, matching-position basis, average_price units, multiple-contract average basis, call vs put direction, in-profit breakeven/giveback, missing IV/stale quotes, rail rebuilds, and narrow rail layout.
```
