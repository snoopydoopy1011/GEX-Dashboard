# GEX Dashboard - Options Trading Rail UI Polish Plan

**Status:** Full journal workspace, local screenshot attachments, and Active Trader ladder/template polish are implemented; branch is functionally PR-ready pending final review.
**Created:** 2026-05-01  
**Branch at draft time:** `codex/options-trading-rail-plan`  
**Primary file:** `ezoptionsschwab.py`  
**Related plan:** [`docs/OPTIONS_TRADING_RAIL_IMPLEMENTATION_PLAN.md`](OPTIONS_TRADING_RAIL_IMPLEMENTATION_PLAN.md)

---

## 0. Read This First

This document is written as a handoff for a new Codex session with no prior chat context.

The order-entry rail is already functionally strong: it has a dedicated fourth rail, cached contract picker, selected contract details, account lookup, positions, order ticket, preview, guarded live placement, orders, and cancel support. This effort is **UI polish only** for the fourth order-entry rail.

Focus only on the dedicated trading rail:

- `#trade-rail-header`
- `#trade-rail`
- `.trade-rail-shell`
- `.trade-panel`
- account, position, contract picker, selected contract, order ticket, preview/place, and orders panels

Do not redesign the analytics right rail, GEX/strike rail, chart, toolbar, drawer, alerts rail, or calculations.

### User Feedback Driving This Plan

The user reviewed screenshots of the current fourth rail and called out:

1. The top **Account** card takes too much vertical space and has wasted layout.
2. The **Contract Picker** `Range` box overflows past the card edge.
3. The contract picker information is useful, and the clickable chain is good, but the display can be tightened.
4. The put side appears to choose the same ATM anchor as the call side. Example: current price around `719.4`; calls show `720`, and puts also show `720`, while the user expects the put-side ATM/top row to respect put-side behavior.
5. **Position** should be near the top, directly below Account, and the details should be easier to read.
6. The **Order Ticket** `Limit` input overflows past the card boundary.
7. Keep the robust order-entry behavior. This is a polish pass, not a rebuild.

---

## 1. Current Branch / Code State

At plan creation, the current branch is:

```bash
codex/options-trading-rail-plan
```

Recent commits on top of `main` include:

```text
4c5c935 fix(trading): harden rail order guards
325eb80 fix(trading): refine rail order workflow
3cf390c feat(trading): add order management panel
8322294 feat(trading): add guarded live option placement
d0a92f7 feat(trading): add preview-only option orders
1f03600 fix(trading): keep position context stable on chain refresh
5c35ed3 feat(trading): add read-only account context
5bd7383 feat(trading): add contract picker from cached chain
36de6c9 feat(trading): add independent trade rail shell
956f344 docs(trading): plan independent options trade rail
```

Before starting implementation, run:

```bash
git branch -a
git log --oneline main..HEAD
git status --short
```

There may be unrelated local/untracked files. Do not revert or delete user files.

---

## 2. Hard Constraints

Inherit these from `AGENTS.md` and the existing trading rail plan:

- No analytical formula changes.
- No JS framework introduction.
- Keep the single-file `ezoptionsschwab.py` structure.
- Use existing CSS tokens for colors. No new neon/raw palette literals for rail UI.
- Preserve Schwab-returned `contractSymbol`; do not reconstruct option symbols by hand.
- Keep order-entry safety guards intact:
  - Preview required before live placement.
  - `ENABLE_LIVE_TRADING=1` required for live placement.
  - Placement must match the exact previewed order.
  - `SELL_TO_CLOSE` remains capped by selected-contract long position when positions are available.
  - Cancel remains explicitly confirmed.
- Do not change order math, order JSON shape, Schwab endpoints, account redaction, or order placement behavior unless a UI polish bug exposes an existing defect.
- Every static trading rail markup change must be mirrored in `buildTradeRailHtml()` because `ensurePriceChartDom()` can rebuild the rail.

---

## 3. Important Anchors

Use grep by anchor name rather than relying on these line numbers, because this file moves fast.

### Python / backend

- `build_trading_chain_payload`
- `_normalize_trade_positions`
- `build_trade_account_details_payload`
- `build_single_option_limit_order`
- `_find_cached_trade_contract`
- `_selected_contract_position_quantity`
- `_normalize_trade_orders`
- Routes:
  - `/trade_chain`
  - `/trade/accounts`
  - `/trade/account_details`
  - `/trade/orders`
  - `/trade/cancel_order`
  - `/trade/preview_order`
  - `/trade/place_order`

### HTML / static rail

- `#trade-rail-header`
- `#trade-rail`
- `.trade-rail-shell`
- `.trade-panel`
- `.trade-account-row`
- `.trade-filter-grid`
- `.trade-chain-table`
- `.trade-chain-list`
- `.trade-selected-summary`
- `.trade-ticket-grid`
- `.trade-price-presets`
- `[data-trade-account-select]`
- `[data-trade-buying-power]`
- `[data-trade-position-list]`
- `[data-trade-chain-list]`
- `[data-trade-strike-range]`
- `[data-trade-limit]`

### JS

- `buildTradeRailHtml`
- `ensureTradeRailDom`
- `renderTradeAccounts`
- `renderTradePositions`
- `getTradeContractsForView`
- `renderTradeExpiryOptions`
- `renderTradeSelected`
- `renderTradeRail`
- `renderTradeTicket`
- `wireTradeRailPickerControls`
- `requestTradeChain`
- `requestTradeAccountDetails`
- `requestTradeOrders`
- `requestTradeJournal`
- `renderTradeJournal`
- `openTradeJournalEditor`
- `saveTradeJournalEvent`

### CSS

- `--trade-rail-w`
- `.trade-rail-header`
- `.trade-rail`
- `.trade-rail-shell`
- `.trade-panel`
- `.trade-panel-head`
- `.trade-filter-grid`
- `.trade-chain-head`
- `.trade-contract-row`
- `.trade-account-row`
- `.trade-position-row`
- `.trade-ticket-grid`
- `.trade-ticket-input`
- `.trade-journal-panel`
- `.trade-journal-row`
- `.trade-journal-modal`

---

## 4. Current Findings

### 4.1 Account Card Is Too Tall

Current Account markup uses:

- panel title row
- `Selected` label
- full-width account select
- refresh button
- buying power line
- warning line

This is too much vertical space for one selected masked account and one buying-power value. The account label also appears in the rail header, creating duplicated emphasis.

Desired direction:

- Make Account a compact summary panel.
- Keep the select available, but reduce label noise.
- Show account and buying power as primary data, not as a large form block.
- Keep refresh accessible as an icon button.
- Preserve account error/warning display.

Possible UI shape:

```text
ACCOUNT              Selected
Account *8805    BP $4.99    refresh
[compact select only if needed / always compact]
warning text if any
```

Alternative:

```text
Account *8805                 refresh
Buying power $4.99            Preview only
[select dropdown, compact]
```

Do not remove account selection. Just compress it.

### 4.2 Position Belongs Near The Top

Current Position panel is below Order Ticket and preview/place actions. The user wants it directly below Account.

Desired order:

1. Account
2. Position
3. Contract Picker
4. Selected Contract
5. Order Ticket
6. Preview/place actions
7. Orders

Position is decision-critical: if the user owns the selected contract, it affects sell-to-close behavior and sizing. It should be visible before the ticket.

### 4.3 Position Rows Are Hard To Scan

Current position row shape is roughly:

```text
SPY 260501C00724000                 Qty 1
OPTION · CALL                       $0.5 · Day $-31.5
```

Problems:

- Full OCC-like symbol dominates the row.
- Strike/type/expiry are not visually parsed.
- Qty, market value, and day P/L sit in one low-contrast text block.
- Selected-contract match is not visually distinguished.

Desired direction:

- Preserve full symbol somewhere, but make parsed details primary.
- Use compact chips or aligned metrics:
  - `Qty`
  - `Mkt`
  - `Day`
- Highlight selected-contract match.
- Keep rows dense, not card-heavy.

Possible row shape:

```text
720C  2026-05-01        Qty 4
SPY 260501C00720000     Mkt $2   Day -$24
```

or:

```text
720 CALL · 0DTE         Qty 4
Mkt $2                  Day -$24
SPY 260501C00720000
```

If parsing symbol client-side feels fragile, use existing normalized fields where available. Backend position normalization currently returns `symbol`, `asset_type`, `put_call`, `quantity`, `average_price`, `market_value`, `day_pnl`, `day_pnl_pct`, and `selected_contract_match`. If more display fields are needed, add safe normalized fields server-side without returning raw account data.

### 4.4 Contract Picker Control Layout Overflows

The `Range` input currently lives in a fixed-width grid column:

```css
.trade-filter-grid {
    grid-template-columns: minmax(0, 1fr) 78px;
}
```

In screenshots, the range number box extends past the card edge. This is likely caused by a combination of:

- narrow rail width
- fixed `78px` column
- input intrinsic width / spinner controls
- default box sizing from the surrounding page context

Desired direction:

- Make inputs impossible to overflow.
- Use `min-width: 0`, `box-sizing: border-box`, and stable responsive constraints.
- Consider replacing the labeled `Range` box with compact segmented/chip controls or inline stepper.

Possible control layout:

```text
Calls | Puts
Expiry 2026-05-01        Range 2%
```

or:

```text
Expiry [2026-05-01                  ]
Range  [-] 2% [+]
```

Keep range editable. Do not remove it.

### 4.5 Contract Picker Data Is Useful But Too Tabular For The Space

Current columns:

```text
Strike | Bid | Ask | Mid | Vol/OI
```

Problems:

- `Vol/OI` truncates heavily.
- All non-strike columns share equal width.
- The table is technically correct but visually cramped.
- The active row is useful; keep row click behavior.

Desired direction:

- Keep clickable option chain rows.
- Keep bid/ask/mid and liquidity visible.
- Improve hierarchy for narrow rail:
  - strike and contract side are primary
  - bid/ask/mid can be compact quote group
  - volume/OI can be a secondary line or right-aligned smaller metric

Possible row shape:

```text
720C          B 0.43  M 0.45  A 0.47
Vol 101,684   OI 17,237
```

or:

```text
720C      0.43 / 0.45 / 0.47
Vol 101,684 · OI 17,237
```

The header could be reduced or removed if row labels are self-explanatory.

### 4.6 Put-Side ATM Sorting Needs A Trading-Side Rule

Current `getTradeContractsForView()`:

1. Filters contracts by option type and expiry.
2. Finds the nearest strike to spot.
3. Pins that strike first for both calls and puts.
4. For puts, sorts lower strikes first after the pinned ATM.
5. For calls, sorts higher strikes first after the pinned ATM.

The user saw spot around `719.4` and put list starting at `720`, because the shared nearest-strike ATM behavior can make put-side top row match call-side ATM. The expected trading UX is different:

- Calls should prefer the nearest strike at or above spot when available.
- Puts should prefer the nearest strike at or below spot when available.
- If that side-specific candidate does not exist, fall back to nearest strike.

Desired behavior:

```text
spot = 719.4
call top candidate = 720C
put top candidate = 719P
```

Keep this as a UI sorting/selection rule. Do not change backend option-chain math or analytics calculations.

Implementation watch-out:

- The backend `build_trading_chain_payload()` sorts all contracts by expiry, type, distance, strike. The frontend then filters/sorts for visible view. The put-side issue is most likely in frontend `getTradeContractsForView()`, not Schwab data.
- When the option type changes, `selectedSymbol` is cleared and the first visible row becomes selected. Therefore the sort rule affects default selected contract and default ticket limit price.
- Make sure existing selected contract remains selected if it is still in the visible rows.

### 4.7 Order Ticket Limit Input Overflows

The Order Ticket uses:

```css
.trade-ticket-grid {
    grid-template-columns: 1fr 1fr;
    gap: 8px;
}

.trade-ticket-input {
    width: 100%;
}
```

In screenshots, the `Limit` input extends past the card edge. This likely has the same root cause as Range: grid children need `min-width: 0` and inputs need explicit `box-sizing: border-box` / stable layout.

Desired direction:

- Prevent overflow at all rail widths.
- Keep quantity and limit visible together.
- Make the price presets feel attached to the limit workflow.

Possible UI shape:

```text
BUY ASK | SELL BID
Qty [1]        Limit [0.47]
Bid | Mid | Ask | Mark
Debit $47      Max Risk $47
```

or tighter:

```text
Qty 1       Limit 0.47
Debit $47  Risk $47
[Bid] [Mid] [Ask] [Mark]
```

### 4.8 Selected Contract May Be Too Tall

Current Selected Contract uses six boxed fields:

- Bid / Mid / Ask
- Last / Mark
- Spread
- Vol / OI
- IV / Delta
- Quote / Trade

The information is useful, but the boxed grid consumes significant vertical height before the user reaches the ticket.

Desired direction:

- Preserve the details.
- Make it more compact, possibly as:
  - a one-line selected contract header
  - quote group
  - liquidity/greeks as compact rows
  - warning line

Possible layout:

```text
SPY 260501C00720000       2026-05-01 720C
Bid 0.43  Mid 0.45  Ask 0.47
Vol 101,684  OI 17,237  IV 11.1%  Delta .995
Stale quote
```

Do not hide stale quote / wide spread warnings.

---

## 5. Recommended Implementation Stages

Keep this polish split into small commits if possible. The rail is live-order-adjacent UI, so small reviewable changes are safer.

### Stage 1 - Layout Safety And Overflow Fixes

Goal: stop Range and Limit overflow before broader redesign.

Tasks:

- Add local box-sizing protection for trading rail controls if needed.
- Ensure `.trade-filter-grid`, `.trade-ticket-grid`, `.trade-field`, labels, inputs, and selects cannot exceed panel width.
- Add `min-width: 0` to grid children where needed.
- Consider changing fixed `78px` range column to a responsive `minmax(...)` or moving range to a compact inline control.
- Validate at minimum/default rail width and resized widths.
- Keep behavior unchanged.

Acceptance:

- Range input stays inside Contract Picker card.
- Limit input stays inside Order Ticket card.
- No horizontal scroll appears inside the rail.
- Quantity, limit, expiry, and range are still usable.

Suggested verification:

```bash
python3 -m py_compile ezoptionsschwab.py
git diff --check
```

Manual browser verification:

- Open rail at default width.
- Resize rail narrower/wider.
- Check call side and put side.
- Check focused inputs, spinner controls, and long account labels.

### Stage 2 - Account + Position Reorder And Compression

Goal: reduce wasted space and put position context near the top.

Tasks:

- Move Position panel directly below Account in both static HTML and `buildTradeRailHtml()`.
- Compress Account UI.
- Improve `renderTradeAccounts()` text formatting if needed.
- Improve `renderTradePositions()` row markup for scanability.
- Add selected-contract visual emphasis if `selected_contract_match` is true.
- Keep account refresh and account select available.
- Keep account warnings/errors visible.

Acceptance:

- Account is visibly shorter.
- Position appears immediately below Account.
- Position rows expose symbol/contract identity, qty, market value, day P/L clearly.
- Selected contract match is visually obvious.
- Account/position refresh flows still work when account or selected contract changes.

Watch-outs:

- `requestTradeAccountDetails()` refreshes positions when selection changes. Do not break this dependency.
- `renderTradePositions()` is also called during account loading/error states.
- Empty states should remain useful but compact.

Suggested tests:

```bash
python3 -m py_compile ezoptionsschwab.py
python3 -m unittest tests.test_session_levels tests.test_trade_preview
git diff --check
```

If there are existing account/order tests beyond `tests.test_trade_preview`, run them too.

### Stage 3 - Contract Picker UX And Put-Side ATM Sorting

Goal: make contract picking easier to scan and fix side-specific default ordering.

Tasks:

- Update `getTradeContractsForView()` to choose a side-specific display anchor:
  - call anchor: nearest strike `>= spot`, fallback nearest
  - put anchor: nearest strike `<= spot`, fallback nearest
- Preserve existing selected symbol if still visible.
- Keep calls sorted upward from call anchor and puts sorted downward from put anchor.
- Redesign chain row markup to reduce truncation.
- Keep row click behavior and active row state.
- Keep bid, ask, mid/mark, volume, and OI visible.
- Keep chain meta line (`SPY @ price · N shown`) compact.

Acceptance:

- With spot `719.4`, calls default/top near `720C`; puts default/top near `719P` when available.
- Clicking rows still selects the contract and updates Selected Contract + ticket limit.
- Chain rows do not overflow and are easier to read.
- `Vol/OI` no longer appears as a mostly truncated field.

Suggested unit/smoke thought:

- If feasible, add a small frontend-neutral helper function for sorting logic, but avoid introducing a JS build system.
- If not adding tests, manually verify with cached sample contracts around spot.

### Stage 4 - Selected Contract + Order Ticket Tightening

Goal: reduce vertical height while keeping all decision-critical fields.

Tasks:

- Compact Selected Contract display.
- Consider merging selected contract summary visually closer to Order Ticket, but do not remove the selected contract state.
- Keep stale quote and wide spread warnings visible.
- Tighten Order Ticket spacing and align quantity/limit/debit/risk.
- Keep bid/mid/ask/mark presets.
- Keep Buy Ask / Sell Bid behavior and active styles.
- Keep preview invalidation behavior unchanged.

Acceptance:

- User can see account, position, contract picker, selected contract, and order ticket with less scrolling.
- Ticket controls are aligned and professional at rail widths.
- Limit quick-fill presets still set limit price and invalidate preview.
- Preview/place buttons still reflect correct enabled/disabled state.

Suggested tests:

```bash
python3 -m py_compile ezoptionsschwab.py
python3 -m unittest tests.test_session_levels tests.test_trade_preview
git diff --check
```

### Stage 5 - Full Rail Regression Sweep

Goal: verify polish did not regress safety or rebuild behavior.

Tasks:

- Confirm static HTML and `buildTradeRailHtml()` match.
- Exercise `ensurePriceChartDom()` rebuild path by changing ticker/timeframe or refreshing chart data.
- Confirm rail collapse/resize still works.
- Confirm account refresh, selected contract change, position refresh, orders collapsed/expanded, preview invalidation, and place disabled/enabled state.
- Confirm responsive/narrow layout.
- Capture screenshots if using browser automation.

Acceptance:

- No horizontal overflow.
- No dropped trade rail elements after ticker/timeframe refresh.
- No safety guard regression.
- No unrelated rail/chart styling changed.

---

## 6. Suggested CSS / Markup Direction

Do not treat this as exact required code. It is a design direction.

### 6.1 Rail-Level Sizing

The rail width is controlled by:

```css
--trade-rail-w: clamp(360px, 24vw, 460px);
```

Any design must work at `360px`. Test at:

- collapsed/open
- 360px minimum
- default width
- manually resized wider

### 6.2 Prefer Dense Data Rows Over Nested Cards

Do not put cards inside cards. The rail already uses `.trade-panel` as the card container. Inside a panel, prefer:

- compact rows
- chips
- two-column metric strips
- small table-like rows

Avoid adding large nested bordered boxes for every metric.

### 6.3 Controls

For inputs inside grid/flex layouts:

- Set `min-width: 0` on grid/flex children.
- Set `box-sizing: border-box` on local inputs/selects/buttons if global CSS is not reliable.
- Avoid fixed widths that are too tight for browser number inputs.
- Test native number spinners.

### 6.4 Accessibility / Usability

Maintain:

- button labels or `aria-label`
- active states with `aria-pressed`
- title/tooltips for icon-only refresh buttons
- readable focus states if existing ones apply

---

## 7. Data / Behavior Notes

### 7.1 Contract Payload

`build_trading_chain_payload()` returns normalized contracts with:

- `contract_symbol`
- `underlying`
- `option_type`
- `expiry`
- `dte`
- `strike`
- `bid`
- `ask`
- `mark`
- `last`
- `mid`
- `spread`
- `spread_pct`
- `volume`
- `open_interest`
- `iv`
- `delta`
- `quote_time`
- `trade_time`
- `quote_age_seconds`
- `warnings`

Use these fields for display. Do not request new Schwab data for UI polish.

### 7.2 Position Payload

Position normalization currently exposes safe fields:

- `symbol`
- `asset_type`
- `put_call`
- `quantity`
- `average_price`
- `market_value`
- `day_pnl`
- `day_pnl_pct`
- `selected_contract_match`

If adding display fields, keep them normalized and safe. Do not forward raw account JSON.

### 7.3 Preview / Placement State

The UI uses:

- `tradeRailState.action`
- `tradeRailState.quantity`
- `tradeRailState.limitPrice`
- `tradeRailState.preview`
- `tradeRailState.previewToken`
- `tradeRailState.previewError`
- `tradeRailState.placement`
- `tradeRailState.placementError`

Preview invalidates on:

- account change
- contract change
- option type change
- expiry change
- strike range change
- action change
- quantity change
- limit price change

Do not weaken this behavior.

---

## 8. Files Expected To Change

Likely:

- `ezoptionsschwab.py`
- this plan doc only if updating status/checklists

Possibly:

- existing tests under `tests/` if adding coverage for sort behavior or normalized position display fields

Do not create frontend build files or new framework structure.

---

## 9. Detailed To-Do Checklist

### Setup

- [ ] Read this plan.
- [ ] Read `docs/OPTIONS_TRADING_RAIL_IMPLEMENTATION_PLAN.md`.
- [ ] Confirm branch and commits with `git branch -a` and `git log --oneline main..HEAD`.
- [ ] Check `git status --short` and preserve unrelated user changes.

### Stage 1

- [ ] Inspect current CSS around `.trade-filter-grid`, `.trade-ticket-grid`, `.trade-ticket-input`, `.trade-field`, `.trade-panel`.
- [ ] Fix Range overflow.
- [ ] Fix Limit overflow.
- [ ] Verify no horizontal rail overflow.
- [ ] Run py compile and diff check.

### Stage 2

- [ ] Move Position below Account in static HTML.
- [ ] Move Position below Account in `buildTradeRailHtml()`.
- [ ] Compact Account panel.
- [ ] Improve Position row markup.
- [ ] Add selected-contract match visual state.
- [ ] Verify account/position refresh flows.

### Stage 3

- [ ] Update put/call side-specific ATM anchor logic in `getTradeContractsForView()`.
- [ ] Redesign chain row markup for readability.
- [ ] Preserve click/active behavior.
- [ ] Verify default call/put top row near spot.
- [ ] Verify selected contract and limit price update.

### Stage 4

- [ ] Compact Selected Contract panel.
- [ ] Tighten Order Ticket layout.
- [ ] Keep presets and warnings.
- [ ] Verify preview/place button states.

### Stage 5

- [ ] Compare static HTML and `buildTradeRailHtml()` for parity.
- [ ] Run tests.
- [ ] Run manual browser check.
- [ ] Verify rail collapse/resize.
- [ ] Verify chart/ticker refresh rebuild path.
- [ ] Update this doc status if desired.

---

## 10. Verification Commands

Baseline:

```bash
python3 -m py_compile ezoptionsschwab.py
git diff --check
```

Existing test suite subset used during trading rail work:

```bash
python3 -m unittest tests.test_session_levels tests.test_trade_preview
```

Optional local server:

```bash
PORT=5001 FLASK_DEBUG=0 FLASK_USE_RELOADER=0 python3 ezoptionsschwab.py
```

Open:

```text
http://localhost:5001
```

Manual checks:

- Open fourth order-entry rail.
- Account compact display.
- Position directly below Account.
- Calls view around spot.
- Puts view around spot.
- Range input inside card.
- Limit input inside card.
- Select contract updates selected summary.
- Ticket quick-fill buttons update limit price.
- Preview invalidates when fields change.
- Orders panel still expands/collapses.
- Rail resizes without overflow.
- Ticker/timeframe refresh does not drop any trade rail DOM.

---

## 11. Non-Goals

Do not include in this polish pass:

- Multi-leg spreads.
- Bracket/OCO orders.
- Stop orders.
- Alert-triggered order entry.
- Chart-click order placement.
- New Schwab data fetch loops.
- Account performance analytics.
- Rewriting the dashboard into components/modules.
- Moving code out of `ezoptionsschwab.py`.
- Changing GEX/DEX/Vanna/Charm/Flow calculations.
- Redesigning the analytics right rail or chart controls.

---

## 12. Commit Guidance

Recommended commit subjects:

```text
fix(trading-ui): contain order rail controls
style(trading-ui): compact account and position panels
fix(trading-ui): align contract picker atm sorting
style(trading-ui): tighten selected contract and ticket
chore(trading-ui): regression sweep
```

Small commits are preferred because the rail touches live-order-adjacent UI.

---

## 13. Prompt For A Fresh Codex Session

Use this prompt to start implementation in a new session:

```text
We are in /Users/scottmunger/Desktop/Trading/Dashboards/GEX-Dashboard.

Read AGENTS.md first, then read docs/OPTIONS_TRADING_RAIL_UI_POLISH_PLAN.md and docs/OPTIONS_TRADING_RAIL_IMPLEMENTATION_PLAN.md.

We are only polishing the dedicated fourth order-entry trading rail. Do not change analytics formulas, the chart, the existing analytics right rail, or Schwab order safety behavior. Keep ezoptionsschwab.py as a single file and use vanilla JS/CSS tokens only.

Implement the UI polish plan in stages:
1. Fix Range and Limit overflow in the trading rail.
2. Compact Account and move/improve Position directly below Account.
3. Improve Contract Picker readability and fix side-specific ATM sorting so calls prefer nearest strike at/above spot and puts prefer nearest strike at/below spot.
4. Tighten Selected Contract and Order Ticket layout without removing useful fields or warnings.
5. Verify rebuild parity between static HTML and buildTradeRailHtml(), run tests, and manually check the rail.

Before editing, confirm branch/status with:
git branch -a
git log --oneline main..HEAD
git status --short

Important anchors: buildTradeRailHtml, renderTradeAccounts, renderTradePositions, getTradeContractsForView, renderTradeSelected, renderTradeRail, renderTradeTicket, .trade-filter-grid, .trade-ticket-grid, [data-trade-strike-range], [data-trade-limit].

Any static HTML change under #trade-rail must also be mirrored in buildTradeRailHtml(), or chart DOM rebuilds can drop it.
```

---

## 14. 2026-05-02 Contract Picker Column Polish Update

Accomplished:

- Reworked the dedicated fourth trading rail Contract Picker rows into a tighter rail table.
- Replaced the broad `Market / Greeks / Liquidity` header with explicit columns: `Contract`, `B`, `M`, `A`, `IV`, `Δ`, `Vol`, and `OI`.
- Removed repeated `B`, `M`, `A`, `IV`, `Δ`, `Vol`, and `OI` labels from every contract row so the rows scan cleaner and fit better in the narrow rail.
- Kept the contract identity visible as strike-side plus DTE, with call/put color styling still using existing tokens.
- Added compact row formatting for large `Vol` and `OI` values while keeping full values in the row hover title.
- Preserved exact cached Schwab/OCC `contract_symbol` values in `data-trade-symbol` and in the selected-contract flow.
- Preserved row-click behavior as selection-only. Clicking a row still invalidates preview with `Contract changed. Preview again.`
- Mirrored the header markup in both static `#trade-rail` HTML and `buildTradeRailHtml()` so chart DOM rebuilds keep the picker intact.

Tricky parts:

- The rail is narrow, so showing eight table columns required compact integer formatting for row-level volume/open-interest values.
- The table is built from `<button>` rows, not a native `<table>`, because each contract row is selectable. CSS grid is used to preserve table-like column alignment while keeping the existing click/active behavior.
- The visible row can be compact, but the exact Schwab symbol and full values still need to be accessible for verification. The row title now carries the exact symbol plus full market/Greek/liquidity context.
- Static/rebuild parity matters here: the header exists in the initial Flask-rendered rail and in the JS rebuild path.

Still left to do:

- Re-check extreme rail resize widths after more live data shapes, especially very high IV, million-plus volume, and wider OI values.
- Consider whether selected-row active state should also highlight the `Contract` cell more strongly without adding visual noise.
- Continue broader order rail polish only if requested: account compacting, selected-contract density, order ticket density, and orders/journal polish.
- Deferred/non-goals remain unchanged: no Schwab bracket/OCO child orders, no SPX-specific validation, no multi-leg spreads, no chart/alert/flow automated trading, and no analytical formula changes.

### Prompt For Next Session

```text
We are in /Users/scottmunger/Desktop/Trading/Dashboards/GEX-Dashboard on branch codex/options-trading-rail-plan.

Read AGENTS.md first, then read docs/OPTIONS_TRADING_RAIL_IMPLEMENTATION_PLAN.md and docs/OPTIONS_TRADING_RAIL_UI_POLISH_PLAN.md.

Before editing, run:
git branch -a
git log --oneline main..HEAD
git status --short

Continue only the dedicated fourth order-entry trading rail:
- #trade-rail-header
- #trade-rail
- .trade-rail-shell
- Position / Contract Picker / Selected Contract / Order Ticket / Bracket Plan / Preview / Orders / Journal panels

Current latest state:
- Trading rail has preview-only and guarded live single-leg DAY LIMIT option orders.
- Contract Helper lives at the top of Contract Picker, with compact/expanded localStorage state.
- Selected Contract shows one visible identity path: call/put strike pill plus DTE pill.
- Position panel has hide/show state and row-level Use pills that select exact cached contracts only.
- Bracket Plan is planning-only and must not alter Schwab preview/place payloads.
- Contract Picker rows now use explicit columns: Contract, B, M, A, IV, Δ, Vol, OI. Row clicks are selection-only and exact cached Schwab/OCC contract symbols remain in data-trade-symbol and hover title.
- Use http://127.0.0.1:5014/ for browser smoke tests. Do not take automatic screenshots or screen recordings unless the user explicitly asks.

Potential next scope:
- Re-check the Contract Picker table at very narrow rail widths and with larger live volume/OI values.
- Continue visual polish of Selected Contract, Order Ticket, Orders, or Journal panels if requested.
- Keep preview invalidation on selection changes and preserve exact Schwab order payload behavior.

Do not implement without explicit approval:
- Live Schwab bracket/OCO child orders.
- SPX-specific validation.
- Multi-leg spreads.
- Automated trading from chart clicks, alerts, or flow.
- Automatic screenshots/screen recordings.
```

---

## 15. 2026-05-02 Contract Picker ATM Ladder Update

Accomplished:

- Fixed Contract Picker row ordering so calls and puts behave like a strike ladder instead of grouping ATM, then OTM, then ITM.
- Calls now sort low-to-high by strike. The selected ATM call is auto-scrolled to the top of the visible chain list, with ITM calls above it and OTM calls below it.
- Puts now sort high-to-low by strike. The selected ATM put is auto-scrolled to the top of the visible chain list, with ITM puts above it and OTM puts below it.
- Kept row clicks selection-only. Manual row selection no longer forces the picker scroll position.
- Preserved exact cached Schwab/OCC `contract_symbol` values in `data-trade-symbol`, row hover titles, selected-contract state, preview invalidation, and order payload behavior.
- Restarted the `5014` smoke-test process and confirmed the served page includes the new picker scroll/anchor code. Also confirmed `5001` can serve the same patched code when started from the current working tree, then released port `5001` back to the user's terminal workflow.

Tricky parts:

- The old sorter intentionally made ATM first, then OTM strikes, then ITM strikes. That visually looked useful at first, but it broke the trader's expected ladder model because ITM calls were buried below OTM calls.
- The fix splits ordering from initial viewport position: rows remain in true ladder order, while a one-shot `pendingContractScrollSymbol` flag scrolls the selected ATM row to the top after the rows are rendered.
- Calls and puts need opposite ladder direction. Calls use ascending strikes; puts use descending strikes.
- The selected ATM anchor still needs side-specific behavior: calls prefer the nearest strike at/above spot, while puts prefer the nearest strike at/below spot.
- Two local Flask processes can serve different code if only one port is restarted. For browser smoke tests use `http://127.0.0.1:5014/`; for the user's local terminal workflow, let their terminal own `5001`.

Verification completed:

- `python3 -m py_compile ezoptionsschwab.py` passed. The existing `render_template_string` invalid escape `SyntaxWarning` remains unchanged.
- `git diff --check` passed.
- `python3 -m unittest tests.test_session_levels tests.test_trade_preview` passed.
- In-app browser smoke loaded `http://127.0.0.1:5014/` and found the Contract Picker without taking screenshots or recordings.

Still left to do:

- Re-check the Contract Picker table at very narrow rail widths and with larger live volume/OI values.
- Continue visual polish of Selected Contract, Order Ticket, Orders, and Journal panels.
- Build out a functional in-dashboard trading journal. The current Journal button/panel is only a starter shell and should become a useful, modern journal tied directly to trades placed or previewed from this platform.
- Use the existing journal at `/Users/scottmunger/Desktop/Trading/Options_Trading_Journal` as a reference for ideas only. It appears to be TradeNote, a Vue/Vite + Node/Express + MongoDB/Parse app that imports trades through CSV-style workflows. Do not port that whole architecture into this dashboard; this app should stay single-file Flask + vanilla JS, with a simpler journal directly connected to the trading rail and Schwab order lifecycle.
- Decide the journal persistence shape before implementation. A conservative next step is a local SQLite table keyed by order/preview/contract/account/timestamps with editable fields for thesis, setup, tags, screenshots/links later, planned target/stop, realized outcome, mistakes, and review notes.
- Deferred/non-goals remain unchanged: no Schwab bracket/OCO child orders, no SPX-specific validation, no multi-leg spreads, no automated trading from chart clicks/alerts/flow, and no analytical formula changes.

### Prompt For Next Session

```text
We are in /Users/scottmunger/Desktop/Trading/Dashboards/GEX-Dashboard on branch codex/options-trading-rail-plan.

Read AGENTS.md first, then read docs/OPTIONS_TRADING_RAIL_IMPLEMENTATION_PLAN.md and docs/OPTIONS_TRADING_RAIL_UI_POLISH_PLAN.md.

Before editing, run:
git branch -a
git log --oneline main..HEAD
git status --short

Continue only the dedicated fourth order-entry trading rail:
- #trade-rail-header
- #trade-rail
- .trade-rail-shell
- Position / Contract Picker / Selected Contract / Order Ticket / Bracket Plan / Preview / Orders / Journal panels

Current latest state:
- Trading rail has preview-only and guarded live single-leg DAY LIMIT option orders.
- Contract Helper lives at the top of Contract Picker, with compact/expanded localStorage state.
- Position panel has hide/show state and row-level Use pills that select exact cached contracts only.
- Bracket Plan is planning-only and must not alter Schwab preview/place payloads.
- Contract Picker rows use explicit columns: Contract, B, M, A, IV, Δ, Vol, OI. Row clicks are selection-only and exact cached Schwab/OCC contract symbols remain in data-trade-symbol and hover title.
- Contract Picker now behaves like a strike ladder: calls sort low-to-high and puts sort high-to-low. On side/expiry/range changes, the side-specific ATM anchor is selected and auto-scrolled to the top of the chain list. Do not reintroduce ATM/OTM/ITM grouping.
- Use http://127.0.0.1:5014/ for browser smoke tests. Do not take automatic screenshots or screen recordings unless explicitly asked. Leave port 5001 available for the user's local terminal unless they ask you to start it.

Potential next scope:
- Build out the trading journal. The current Journal button/panel is only a starter and should become a simple, modern, functional journal attached directly to trades from this platform.
- Reference /Users/scottmunger/Desktop/Trading/Options_Trading_Journal for ideas only. It appears to be TradeNote, built with Vue/Vite + Node/Express + MongoDB/Parse and CSV imports. Do not port its full architecture; keep this dashboard single-file Flask + vanilla JS.
- Suggested journal direction: local SQLite persistence, automatic entries from successful previews/live placements/order refreshes where possible, editable notes/tags/setup/thesis/outcome fields, compact journal list in the rail, and a detailed entry drawer/modal that does not interfere with order entry.
- Re-check the Contract Picker table at very narrow rail widths and with larger live volume/OI values.
- Continue visual polish of Selected Contract, Order Ticket, Orders, or Journal panels as needed.
- Keep preview invalidation on selection changes and preserve exact Schwab order payload behavior.

Do not implement without explicit approval:
- Live Schwab bracket/OCO child orders.
- SPX-specific validation.
- Multi-leg spreads.
- Automated trading from chart clicks, alerts, or flow.
- Automatic screenshots/screen recordings.
```

---

## 16. 2026-05-02 Trading Journal Build-out Update

Accomplished:

- Built on the existing local SQLite `trade_events` journal table instead of porting the external TradeNote-style app architecture.
- Added editable journal fields to `trade_events`: status, tags, setup, thesis, notes, outcome, and updated timestamp.
- Added local schema migration in `init_db()` so existing `options_data.db` files keep old journal rows.
- Added `POST /trade/journal/update` for saving journal annotations.
- Kept automatic journal entries attached to successful previews and successful live placements.
- Default journal statuses are `planned` for preview events and `open` for placed events.
- Fixed the rail Journal button/open path so clicking `Journal` opens the trade rail if needed, toggles `tradeRailState.journalVisible`, refreshes journal data, and scrolls the Journal panel into view.
- Upgraded the rail Journal panel with compact event rows, status pills, setup/tags display, status/event filters, text search, selected-entry detail, and edit actions.
- Added a journal editor dialog/modal for status, tags, setup, thesis, notes, and outcome.
- Added `POST /trade/journal/create` for local-only manual notes from the current rail context.
- Added successful cancel-order journal events through the existing explicitly confirmed cancel path.
- Mirrored the Journal list/editor/filter/detail markup in static `#trade-rail` HTML and `buildTradeRailHtml()`.
- Added focused tests for editable journal annotations, missing journal-event rejection, manual journal creation, and cancel journal events.

Tricky parts:

- The journal editor is local-only and annotation-only. It does not change Schwab preview/place payloads, final confirmation behavior, live-trading gates, or bracket planning behavior.
- Static HTML and `buildTradeRailHtml()` must stay in parity. The Journal panel and editor are under the fourth trading rail and can be dropped by chart DOM rebuilds if future markup is added in only one path.
- Port `5014` can be stale. During smoke testing, the old process served older DOM until it was restarted; use this as a first check before assuming frontend code is missing.
- The user-facing Journal bug was likely visibility/scroll-state confusion rather than a missing route: the panel lives low in the scrollable rail. The open path now scrolls the panel into view.
- Header Journal controls are outside `#trade-rail`; panel/editor/filter controls are inside it. `wireTradeRailPickerControls()` has to bind both global and rail-scoped controls.
- Cancel journaling records only after Schwab confirms cancellation and preserves the explicit cancel confirmation requirement.

Verification completed:

- `python3 -m py_compile ezoptionsschwab.py` passed. The existing `render_template_string` invalid escape `SyntaxWarning` remains unchanged.
- `git diff --check` passed.
- `python3 -m unittest tests.test_session_levels tests.test_trade_preview` passed.
- Browser smoke on `http://127.0.0.1:5014/` confirmed the Journal button opens the panel, the panel gets `.visible`, and status filter / New / detail controls render. No screenshots or screen recordings were taken.
- Port `5014` was stopped after verification.

Still left to do:

- Build a full journal workspace/page with meaningful stats and review ergonomics. A likely direction is a new full-width section below the existing Live Alerts and Flow Pulse lane so it behaves like a new page after scrolling down, without crowding the order-entry rail.
- Journal workspace should include trade/event table filters, selected-entry detail, lifecycle grouping, daily/weekly counts, event-type/status summaries, ticker/contract summaries, setup/tag summaries, and deterministic P/L only when safe.
- Decide whether the below-alerts/flow-pulse Journal workspace is always rendered, collapsed behind a section header, opened by the existing rail `Journal` button, or paired with the rail quick-annotation panel.
- Explore desktop-app feasibility before implementing:
  - PySide6/PyQt6 + `QWebEngineView` wrapper around the local Flask dashboard
  - in-process/threaded Flask server vs managed subprocess
  - Schwab OAuth/token callback implications
  - SQLite/file path handling
  - macOS packaging and whether this should be a lightweight launcher instead of a full packaged app
- Add clearer lifecycle grouping across preview, placed, canceled, closed/reviewed, and manually annotated entries.
- Add realized/marked P/L enrichment only when deterministic enough from positions/orders.
- Keep screenshot/screen-recording attachments deferred until there is explicit opt-in UI and storage controls.
- Keep deferred/non-goals unchanged: no live Schwab bracket/OCO child orders, no SPX-specific validation, no multi-leg spreads, no automated trading from chart clicks/alerts/flow, and no analytical formula changes.

### Prompt For Next Session

```text
We are in /Users/scottmunger/Desktop/Trading/Dashboards/GEX-Dashboard on branch codex/options-trading-rail-plan.

Read AGENTS.md first, then read:
- docs/OPTIONS_TRADING_RAIL_IMPLEMENTATION_PLAN.md
- docs/OPTIONS_TRADING_RAIL_UI_POLISH_PLAN.md

Before editing, run:
git branch -a
git log --oneline main..HEAD
git status --short

Continue only the dedicated fourth order-entry trading rail:
- #trade-rail-header
- #trade-rail
- .trade-rail-shell
- Position / Contract Picker / Selected Contract / Order Ticket / Bracket Plan / Preview / Orders / Journal panels

Current latest state:
- Trading rail has preview-only and guarded live single-leg DAY LIMIT option orders.
- Contract Helper lives at the top of Contract Picker, with compact/expanded localStorage state.
- Position panel has hide/show state and row-level Use pills that select exact cached contracts only.
- Bracket Plan is planning-only and must not alter Schwab preview/place payloads.
- Contract Picker rows use explicit columns: Contract, B, M, A, IV, Δ, Vol, OI.
- Contract Picker behaves like a strike ladder: calls sort low-to-high and puts sort high-to-low. On side/expiry/range changes, the side-specific ATM anchor is selected and auto-scrolled to the top of the chain list. Do not reintroduce ATM/OTM/ITM grouping.
- Journal persistence exists in local SQLite `trade_events`.
- Successful previews, successful live placements, and successful confirmed cancels auto-record local journal events.
- Journal entries have editable local annotation fields: status, tags, setup, thesis, notes, outcome.
- `/trade/journal` returns recent events, `/trade/journal/update` saves annotations, and `/trade/journal/create` creates local-only manual notes.
- The rail Journal button now opens the trade rail if collapsed, toggles `tradeRailState.journalVisible`, requests fresh journal data, and scrolls the Journal panel into view.
- The rail Journal panel now has status/event filters, text search, selected-entry detail, manual New entry, and edit modal controls.
- Static HTML and `buildTradeRailHtml()` must stay in parity.

Important next scope:
- Design and build a full Journal workspace/page for actually reviewing journal entries and stats. The current rail panel is good for quick annotations, but it is not enough for serious review.
- Strong candidate placement: a full-width Journal section below the existing Live Alerts and Flow Pulse lane. That area is effectively a new page after scrolling down, so it can expose journal tables/stats without crowding the fourth order-entry rail.
- Decide whether the existing rail `Journal` button should scroll to/open that workspace, keep opening the rail quick panel, or offer both quick panel and full workspace behavior.
- Journal workspace should include:
  - event/trade table with filters/search
  - selected-entry detail
  - lifecycle grouping across previewed, placed, canceled, closed/reviewed, and manual notes
  - daily/weekly event counts
  - status and event-type summaries
  - ticker/contract summaries
  - setup/tag summaries
  - deterministic P/L enrichment from positions/orders only when safe and reliable
  - clear empty states for days with no local journal events
- Explore how hard it would be to convert or wrap this localhost dashboard as a lightweight desktop app before implementing. Evaluate PySide6/PyQt6 + `QWebEngineView`, running Flask in-process vs subprocess, Schwab OAuth/token callback implications, SQLite/file paths, macOS packaging, and whether a local launcher is enough.
- Re-check the Contract Picker table at very narrow rail widths and with larger live volume/OI values.
- Continue visual polish of Selected Contract, Order Ticket, Orders, or Journal panels as needed.

Do not implement without explicit approval:
- Live Schwab bracket/OCO child orders.
- SPX-specific validation.
- Multi-leg spreads.
- Automated trading from chart clicks, alerts, or flow.
- Automatic screenshots/screen recordings.

Verification commands to run after changes:
python3 -m py_compile ezoptionsschwab.py
git diff --check
python3 -m unittest tests.test_session_levels tests.test_trade_preview
```

---

## 17. 2026-05-02 Full Journal Workspace Update

Accomplished:

- Added a full-width `#trade-journal-workspace` below the existing Live Alerts / Flow Pulse lane.
- Added journal workspace filters/search, event table, selected-entry detail, lifecycle grouping, daily/weekly event counts, status/event-type summaries, ticker/contract summaries, setup/tag summaries, and conservative deterministic P/L display.
- Added a rail Journal `Review` button that refreshes journal data and scrolls to the workspace.
- Added rebuild-path support through `buildTradeJournalWorkspaceHtml()` and `ensureTradeJournalWorkspace()`.
- Explored desktop-app wrapping. A PySide6/PyQt6 + `QWebEngineView` wrapper appears feasible, but PySide6/PyQt6 and packagers are not installed here; a lightweight launcher or managed Flask subprocess is likely the lower-risk path.

Tricky parts:

- The workspace is outside `#trade-rail`, but the journal editor still lives inside the rail markup. Workspace edit/new actions ensure the rail/editor surface exists and expands the rail if needed.
- `ensurePriceChartDom()` can run before `tradeRailState` is initialized, so workspace rebuild logic must avoid touching state too early.
- Static HTML and rebuild helpers both need parity for the rail and workspace surfaces.
- P/L enrichment intentionally avoids guessing realized P/L. It only uses explicit event P/L or exact current-position day P/L when account/contract match.
- Rendered inline JavaScript was checked with `node --check` because the workspace added substantial frontend logic.

Still left to do:

- Do a fresh human diff review and create the PR next session.
- A real ladder-marker cancel smoke still depends on having a cancelable selected-contract Schwab order in the account.
- A true successful armed live-send path still needs a carefully controlled live test or mocked browser path. The stale-quote block and preview-mandatory model were browser-smoked.
- Use `/Users/scottmunger/Desktop/Trading/Options_Trading_Journal` only for product/data-shape ideas such as fields, review flows, setup/tag/outcome concepts, stats, and lifecycle views. Do not port its Vue/Vite + Node/Express + MongoDB/Parse/Docker architecture.
- Improve deterministic closed-trade P/L only when order/position lifecycle data is reliable.
- Keep monitoring workspace/rail visual polish with larger live journal/order datasets.

### Prompt For Next Session

Use `docs/OPTIONS_TRADING_RAIL_IMPLEMENTATION_PLAN.md` section `6. Latest Handoff` for the current handoff prompt.
