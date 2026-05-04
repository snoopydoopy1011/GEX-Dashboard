# GEX Dashboard - Order Entry Rail Scalping Repair Plan

**Status:** Implemented and locally verified on `codex/price-chart-window-controls`  
**Created:** 2026-05-04  
**Primary file:** `ezoptionsschwab.py`  
**Audit branch:** `codex/price-chart-window-controls`  
**Related docs:** `docs/OPTIONS_TRADING_RAIL_IMPLEMENTATION_PLAN.md`, `docs/OPTIONS_TRADING_RAIL_UI_POLISH_PLAN.md`, `docs/ORDER_RAIL_SCALP_TARGETS_PLAN.md`

---

## 0. Purpose

This document is a self-contained implementation handoff for repairing the dedicated Order Entry rail for fast 0-1 DTE SPY option scalping.

The user reported these live-market issues:

1. With `Auto` armed, clicking `Buy Ask` does not visibly create a working ladder order marker and may not actually send a live order.
2. With `Auto` off, previewed orders do not appear anywhere useful in the rail or ladder.
3. The Active Trader ladder recenters every refresh instead of keeping a stable ladder with a moving current-price marker.
4. The ladder colors whole rows green/red instead of shading only the buy/bid and ask/sell columns like TOS.
5. While the Active Trader ladder is open, the rail can become hard to scroll because ladder refreshes can pull attention/scroll back upward.

The goal is not a cosmetic tweak. The Order Entry rail needs a clearer order lifecycle model and a ladder render model designed for scalping.

---

## 0.1 Implementation Update - 2026-05-04

Implemented in `ezoptionsschwab.py`:

- Added in-memory order intent state for `staged`, `previewing`, `previewed`, `sending`, `working`, `rejected`, `canceling`, `canceled`, `filled`, and `expired` local lifecycle states.
- Added Active Trader ladder markers for local staged/previewing/previewed/sending/rejected/expired intents, separate from real Schwab working-order markers.
- Changed armed Auto Send so `Buy Ask`, `Sell Bid`, and `Flatten` lock the clicked/current bid or ask, create a visible local intent, preview that exact single-leg `DAY LIMIT` order, then place the exact previewed order in one armed fast flow.
- Preserved backend live-trading guards: `ENABLE_LIVE_TRADING=1`, exact preview-token binding, exact order JSON matching, cached-contract validation, and `SELL_TO_CLOSE` position caps.
- Changed quote movement after preview from a hard frontend placement blocker into a warning when the ticket fields still match the exact previewed limit order.
- Parsed Schwab order ids from placement `Location` headers via `_trade_order_id_from_location()` and returned/wrote `order_id` in placement results and journal details.
- Reconciled local intents with `/trade/orders` by order id first, then by selected contract, instruction, quantity, price, and active status.
- Added lightweight `/trade/orders` polling only while active intents exist or the Orders panel is open.
- Stabilized the Active Trader ladder viewport around a retained center, with recentering only on contract/init/edge drift instead of every tick.
- Added a gray current-price marker that moves within stable ladder rows.
- Changed bid/ask zones to cell/column shading instead of full-row green/red row backgrounds.
- Added scroll preservation and `overflow-anchor: none` around Active Trader ladder renders so streaming updates do not pull the order rail back to the top.

Tests updated in `tests/test_trade_preview.py`:

- Added coverage for parsing Schwab order ids from common `Location` header shapes.
- Added coverage that additional open Schwab statuses such as `PENDING_REVIEW` normalize as cancelable/open.
- Extended the placement test to assert `order_id` is returned and written into local journal details.

Tricky parts:

- The safe one-click behavior is still preview-mandatory. The frontend now chains preview then place for armed fast actions, but `/trade/place_order` still consumes and validates the successful preview token server-side.
- The frontend no longer treats quote movement as a hard blocker for exact previewed limit orders, but order field changes still invalidate preview and the backend still rejects mismatched order fields or JSON.
- Local intents are intentionally in-memory only. They are visible for scalping clarity, but the journal remains historical and is not used as active order state.
- Ladder markers must merge two sources without duplicating: real Schwab open/cancelable orders and unreconciled local intents.
- Polling is intentionally narrow: it runs while active intent states need reconciliation or while Orders is open, then stops.
- The existing dirty Contract Helper rank/price changes in `ezoptionsschwab.py` were preserved and not reverted.

Verification completed:

```bash
python3 -m py_compile ezoptionsschwab.py
git diff --check
python3 -m unittest tests.test_session_levels tests.test_trade_preview
python3 -c "import re, pathlib, ezoptionsschwab as m; html=m.app.test_client().get('/').get_data(as_text=True); scripts=re.findall(r'<script[^>]*>(.*?)</script>', html, re.S|re.I); pathlib.Path('/tmp/gex-inline-scripts.js').write_text('\n;\n'.join(scripts)); print('scripts', len(scripts))"
node --check /tmp/gex-inline-scripts.js
```

Browser smoke on `http://127.0.0.1:5016/` confirmed:

- Order Entry rail loaded with Auto Send unchecked.
- `Buy Ask` with Auto Send off staged a visible local ladder marker and did not place a live order.
- Local ladder marker clear worked.
- No row-level `bid-zone` / `ask-zone` classes remained; bid/ask shading appeared only on ladder cells.
- A single gray current-price marker rendered.
- Scrolling the trade rail lower stayed stable through streaming updates.

No real live order was sent during verification.

---

## 1. Current Workspace Notes

Before editing, run:

```bash
git branch -a
git log --oneline main..HEAD
git status --short
```

At audit time, the workspace was:

```text
* codex/price-chart-window-controls
main..HEAD: 4c3ac2f Move price chart window actions into toolbar menu
dirty:
  M ezoptionsschwab.py
  ?? Trading_from_dashboard.txt
```

The existing `ezoptionsschwab.py` diff was unrelated to this audit. It added compact Contract Helper rank/price display changes. Do not revert it. Work with it if still present.

Line numbers below are audit snapshots. Use `rg` by anchor name before editing.

---

## 2. Hard Constraints

Inherit all constraints from `AGENTS.md` and the existing trading rail docs:

- Do not change GEX, DEX, Vanna, Charm, Flow, expected-move, or dealer analytics formulas.
- No JS framework.
- Keep `ezoptionsschwab.py` single-file.
- Use existing CSS tokens for colors. Do not add raw neon palette literals.
- Do not reconstruct option symbols. Use Schwab-returned `contract_symbol` / `contractSymbol`.
- Do not weaken backend live-order safety gates:
  - `ENABLE_LIVE_TRADING=1` is still required for live placement.
  - A successful Schwab preview must still exist before a live order is placed.
  - Placement must still match the exact previewed order fields and order JSON.
  - `SELL_TO_CLOSE` must remain capped by selected-contract long position when positions are available.
  - Final live placement confirmation semantics in the app must remain explicit for non-auto-send actions.
- Do not implement live Schwab bracket/OCO child orders in this pass.
- Bracket Planner remains planning-only and must not change Schwab order JSON.
- Do not run a real live order as part of testing. Browser/test verification must use live trading disabled, mocks, or non-placement paths.
- Every static trading rail markup change must be mirrored in `buildTradeRailHtml()` because `ensureTradeRailDom()` can rebuild the rail.

---

## 3. Audit Summary

I started the app on `http://127.0.0.1:5016`, opened the in-app browser, inspected the live Order Entry rail, then stopped the temporary server.

Observed runtime behavior:

- The rail loaded with a linked account and buying power.
- Active Trader showed selected contract, fast buttons, quantity presets, bracket-template selector, quote age, preview TTL, and ladder.
- The ladder price range changed as quotes updated.
- The ladder had full-row green/red shading.
- Previewed journal entries existed from prior activity, but previewed orders did not appear as Active Trader ladder markers or in the Orders panel.
- The Orders panel is collapsed by default.
- Holding the trade rail scrolled lower for a few update cycles did not always reproduce the jump, but the render path is still vulnerable because Active Trader replaces ladder HTML on every render.

Important server log signal from the audit:

- `/update` and `/trade_chain` were called repeatedly during streaming.
- `/trade/orders` was called on initial account/selection load and when forced, not continuously every tick.

---

## 4. Current Code Behavior

### 4.1 Auto-send is not TOS-style auto-send

Relevant anchors:

- `handleTradeFastAction`
- `stageTradeFastTicket`
- `getTradeFastQuoteError`
- `placeTradeOrder`
- `/trade/place_order`

Current flow for `Buy Ask`:

```text
click Buy Ask
  -> stageTradeFastTicket("BUY_TO_OPEN", "ask")
  -> sets tradeRailState.action
  -> sets tradeRailState.limitPrice to the current ask
  -> if price/action changed, invalidateTradePreview()
  -> if Auto is off: stop after staging
  -> if Auto is on:
       - require no quote error
       - require staged.changed === false
       - require tradeRailState.previewToken
       - then call placeTradeOrder({ skipConfirm: true })
```

This means Auto Send can only place a previously previewed exact order if the click does not change the ticket. In a live SPY 0DTE option, the ask often changes between preview and click. So the fast button usually restages and invalidates the preview instead of placing.

This explains the user's symptom: "Auto-send is armed and I click Buy Ask, but I do not see a working order." The UI may never make it to `/trade/place_order`; it often stops at "this exact order needs a successful preview first."

### 4.2 Quote movement blocks placement too aggressively

Relevant anchors:

- `getTradeQuotePriceSignature`
- `getTradeQuoteMoveState`
- `renderTradeTicket`
- `placeTradeOrder`

`getTradeQuotePriceSignature(contract)` includes:

```text
contract symbol
bid
mid/mark
ask
mark
```

`renderTradeTicket()` disables `Place Live Order` if `quoteMove.previewMoved` is true.

`placeTradeOrder()` also blocks if `quoteMove.previewMoved` is true.

This is too strict for a limit order workflow. The backend already enforces that the submitted order fields and JSON match the previewed order. A quote update after preview should be a warning, not always a hard blocker, if the user is intentionally sending the exact previewed limit.

For Active Trader scalping, the price should be locked at click time:

```text
user clicks Buy Ask at 0.93
order intent is BUY_TO_OPEN xN @ 0.93 LIMIT
if ask moves to 0.94 while the request is in flight, the order is still a valid 0.93 limit order
```

### 4.3 Previewed orders do not have a UI lifecycle

Relevant anchors:

- `previewTradeOrder`
- `/trade/preview_order`
- `_write_trade_event('previewed_order', ...)`
- `renderTradeActiveTrader`
- `renderTradeOrders`
- `buildTradeActiveLadderHtml`

Successful preview currently:

- stores `tradeRailState.preview`
- stores `tradeRailState.previewToken`
- writes a local journal `previewed_order`
- updates Preview / Place response

It does not:

- create an Active Trader ladder marker
- create an Orders panel row
- create a cancel/clearable visible ticket marker
- show price/qty on the ladder like TOS

This explains the user's "I had an auto send order and a preview order and neither show up anywhere." A preview is not a Schwab working order, but for this rail it still needs a visible local marker so the user can see the order intent.

### 4.4 Ladder order markers only come from Schwab orders

Relevant anchors:

- `getTradeWorkingOrdersForSelected`
- `buildTradeLadderOrderMarkerHtml`
- `buildTradeActiveLadderHtml`
- `/trade/orders`
- `_normalize_trade_orders`

Current ladder markers are built from:

```text
tradeRailState.orders
  -> only cancelable orders
  -> only orders with order_id
  -> only orders with numeric price
```

Then matching orders are rendered as green/red pill markers with an `x` cancel button.

That is good for real working Schwab orders, but incomplete for scalping because there is no marker for:

- staged but not previewed ticket
- preview in progress
- successful preview waiting to place
- placement request in flight
- placement rejected/error
- local previewed order from journal

### 4.5 Orders panel is collapsed and not a live working-order monitor

Relevant anchors:

- `tradeRailState.ordersCollapsed`
- `requestTradeOrders`
- `renderTradeOrders`

`ordersCollapsed` defaults to true.

`requestTradeOrders()` uses a request key:

```text
accountHash | ticker | contractSymbol
```

If `options.force` is false and the key matches, it returns without making another request.

After successful live placement, the code forces:

```text
requestTradeAccountDetails({ force: true })
requestTradeOrders({ force: true })
```

But if placement is blocked before `/trade/place_order`, or if the order lookup does not return yet, there is no persistent pending marker. For scalping, the rail needs optimistic local intent state immediately on click, then reconciliation with Schwab orders.

### 4.6 Ladder recenters every render

Relevant anchors:

- `buildTradeActiveLadderHtml`
- `renderTradeActiveTrader`

Current ladder center:

```js
const center = [mid, mark, last, limit, values[0]].find(...)
```

Then rows are regenerated around that center every render:

```js
ladder.innerHTML = buildTradeActiveLadderHtml(selected);
```

This makes the visible price ladder shift whenever mid/mark/last/limit moves. The TOS behavior the user wants is different:

- price rows stay fixed for a while
- the current trading price marker moves up/down within the ladder
- the ladder recenters only when the marker drifts near the edge or when the user explicitly recenters

### 4.7 Bid/ask shading is row-level instead of column-level

Relevant anchors:

- `.trade-active-ladder-row.bid-zone`
- `.trade-active-ladder-row.ask-zone`
- `buildTradeActiveLadderHtml`

Current CSS:

```css
.trade-active-ladder-row.bid-zone { background: ...call...; }
.trade-active-ladder-row.ask-zone { background: ...put...; }
```

This paints the whole row. TOS paints only the buy/bid side green and the ask/sell side red. The fix should move zone classes to individual cells.

### 4.8 Scroll jump risk is real even if intermittent

Relevant anchors:

- `rememberTradeRailScroll`
- `restoreTradeRailScroll`
- `preserveTradeRailScroll`
- `renderTradeActiveTrader`
- `.trade-active-ladder`

There is scroll preservation around `renderTradeRail()` and `renderTradeOrders()`, but `renderTradeActiveTrader()` replaces the ladder HTML directly and is called from many paths.

Full ladder replacement can trigger browser scroll anchoring. If the user is scrolled below Active Trader, changes above the viewport can pull the scroll position upward. This becomes more likely while the ladder is open and streaming updates change row content every couple seconds.

---

## 5. Target Behavior

### 5.1 Fast entry modes

There should be two clear user modes:

#### Auto off

Fast buttons stage a local order intent only:

```text
Buy Ask
  -> create/update local intent: staged BUY_TO_OPEN xN @ current ask
  -> show marker on ladder immediately
  -> update Order Ticket
  -> user clicks Preview Order
  -> intent becomes previewing, then previewed or rejected
  -> Place Live Order remains explicit and confirmed
```

#### Auto on

Fast buttons should submit with one click:

```text
Buy Ask
  -> lock current ask at click time
  -> create local intent: previewing/sending BUY_TO_OPEN xN @ locked ask
  -> call Schwab preview
  -> if preview succeeds, immediately call Schwab place using returned preview token/order
  -> reconcile with /trade/orders
  -> marker becomes working or rejected
```

This preserves the preview requirement but makes it internal to the armed fast workflow. The user gets one-click behavior and immediate visual feedback.

Do not silently enable Auto. Auto must remain off by default and not persisted.

### 5.2 Order lifecycle in UI

The ladder and rail should understand these states:

```text
staged      local ticket set, no Schwab preview yet
previewing  preview request in flight
previewed   successful Schwab preview, not placed
sending     live placement request in flight
working     Schwab returned a cancelable/open order
filled      Schwab order filled
canceling   cancel request in flight
canceled    Schwab order canceled
rejected    preview/place/cancel failed or Schwab rejected
expired     local preview TTL expired
```

Do not rely on the local journal as the active order lifecycle. The journal is historical. The Active Trader ladder needs current in-memory intent state.

### 5.3 Ladder display

The ladder should approximate the TOS mental model:

- stable price rows
- gray current-price marker moves within the price column
- recenter only when current price drifts near the top/bottom threshold
- Buy/Bid side shading only in left columns
- Ask/Sell side shading only in right columns
- staged/previewed/sending/working markers appear at their limit price
- real working markers include cancel `x`
- local staged/previewed markers can include `x` to clear, not cancel
- rejected markers should show error until cleared or superseded

---

## 6. Implementation Plan

Implement in focused stages. Do not jump straight to the full UI before the lifecycle is modeled.

### Stage 1 - Add local order intent state

Add to `tradeRailState`:

```js
orderIntents: [],
orderIntentSeq: 0,
orderPollTimer: null,
lastOrderPollAt: 0,
```

Define an intent shape:

```js
{
  local_id: 'local-1',
  schwab_order_id: '',
  state: 'staged',
  source: 'active_trader' | 'ticket' | 'ladder',
  account_hash: '',
  ticker: '',
  contract_symbol: '',
  instruction: 'BUY_TO_OPEN' | 'SELL_TO_CLOSE',
  quantity: 1,
  limit_price: '0.93',
  preview_token: '',
  order_hash: '',
  schwab_status: '',
  error: '',
  created_at: Date.now(),
  updated_at: Date.now(),
}
```

Add helpers:

- `createTradeOrderIntent(fields)`
- `updateTradeOrderIntent(localId, patch)`
- `getActiveTradeOrderIntentsForSelected(selected)`
- `clearTradeOrderIntent(localId)`
- `expireStaleTradeOrderIntents()`
- `findTradeOrderIntentForTicket(selected, instruction, quantity, limitPrice)`
- `upsertTradeOrderIntentFromPreview(previewData, selected)`
- `reconcileTradeOrderIntentsWithOrders(orders)`

Rules:

- Keep this in memory. Do not persist active intents to localStorage in this pass.
- Clear stale `staged`, `previewed`, and `rejected` intents on contract/account change or after a reasonable timeout.
- Do not clear `sending` until it resolves or times out to `rejected`.
- Keep `working` until Schwab order status is terminal or the user changes context.

### Stage 2 - Make preview and staging visible

Modify `stageTradeFastTicket()` and `handleTradeFastLadderPrice()`:

- When a fast button or ladder price stages a ticket, create/update a local `staged` intent.
- The intent should render immediately on the ladder at the selected limit price.
- If a new staged intent supersedes an old staged/previewed intent for the same contract/action, mark the old one expired or remove it.

Modify `previewTradeOrder()`:

- Before fetch, create/update intent state to `previewing`.
- On success, update intent to `previewed`, attach `preview_token`, `order_hash`, and any summary.
- On failure, update intent to `rejected` with the error.

Important: previewed order markers should not look identical to real working Schwab orders. Use a distinct state style such as neutral/blue/amber and text like `PREVIEW x1` or `BUY x1 PREV`.

### Stage 3 - Fix armed Auto Send semantics

Modify `handleTradeFastAction(instruction)`:

Current behavior blocks if `staged.changed` or preview is missing. Replace the armed branch with:

```text
if Auto off:
  stage only

if Auto on:
  validate account/contract/qty/quote
  lock preset price at click time
  create intent with state previewing/sending
  call preview for exact locked order
  if preview succeeds:
    call place for exact returned preview token/order
  if place succeeds:
    mark intent sending/working, attach result fields
    force order refresh/poll
  if any step fails:
    mark intent rejected and show error
```

Suggested function names:

- `sendTradeFastOrder(instruction, preset, source)`
- `previewTradeOrderForIntent(intent)`
- `placeTradeOrderForIntent(intent, previewData)`

Do not reuse `placeTradeOrder()` directly if its current quote movement checks would block the just-previewed exact order. Either:

1. refactor `placeTradeOrder()` to accept `{ skipQuoteMoveBlock: true, intentId }`, or
2. create a separate internal helper that posts to `/trade/place_order` with the exact preview response.

Preferred: refactor shared request code so ticket Place and active Auto Send use the same fetch body builder, but Active Auto Send does not block on `quoteMove.previewMoved` after it has locked and previewed a specific limit.

Keep non-auto `Place Live Order` confirmation:

```js
if (!skipConfirm && !window.confirm(...)) return;
```

Auto Send may continue to use `skipConfirm: true` because the user explicitly armed it in the UI.

### Stage 4 - Relax quote movement from hard blocker to warning where appropriate

Do not remove quote awareness. Change how it is used:

- If the user is trying to place a previewed exact limit order, quote movement should show a warning but should not automatically disable placement.
- If the user is trying to auto-send at current ask/bid, lock the price at click time and operate on that price.
- If the selected contract changes, quantity changes, instruction changes, account changes, or limit changes, preview remains invalid.

Candidate refactor:

```js
function getTradeTicketChangeState(contract) {
  return {
    orderFieldsChanged: ...,
    quoteMovedSinceLimit: ...,
    quoteMovedSincePreview: ...,
    hardBlock: orderFieldsChanged || missing account/contract/limit || expired preview,
    warning: quoteMovedSincePreview ? 'Quote moved since preview...' : ''
  };
}
```

Do not change backend `/trade/place_order` exact-preview validation. The backend already checks:

- account
- ticker
- contract symbol
- instruction
- quantity
- limit price
- preview token
- exact order JSON

### Stage 5 - Parse and expose Schwab order id from placement

Relevant backend anchor:

- `_trade_order_location`
- `/trade/place_order`

If Schwab returns a `Location` header, parse the order id from the final path segment.

Add:

```python
def _trade_order_id_from_location(location):
    ...
```

Return it from `/trade/place_order`:

```python
result['order_id'] = parsed_order_id
```

Write it to journal metadata too.

This helps the client immediately convert a `sending` marker to a `working` marker with a cancelable id, even before the next `/trade/orders` response.

### Stage 6 - Improve order polling and reconciliation

Add a lightweight order polling loop that runs only when needed:

Start polling when:

- any local intent is `sending`, `working`, or `canceling`
- Orders panel is open
- a live placement just succeeded

Stop polling when:

- no active local intents remain
- Orders panel is collapsed
- no account/selected contract exists

Suggested cadence:

- every 1-2 seconds while an active intent exists
- every 3-5 seconds while Orders panel is open but no active intent exists

Make polling call `requestTradeOrders({ force: true, silent: true })` so the existing request-key dedupe does not block refresh.

Add `silent` option so the UI does not flash "Loading Schwab orders..." every poll.

After each `/trade/orders` response:

- update `tradeRailState.orders`
- reconcile local intents by `order_id` first
- then by `contract_symbol`, `instruction`, `quantity`, `limit_price`, and recent timestamp
- update intent states from Schwab status

Terminal statuses should clear or downgrade markers:

```text
FILLED -> filled
CANCELED/CANCELLED -> canceled
REJECTED -> rejected
EXPIRED -> expired
```

Open/cancelable statuses include at least:

```text
ACCEPTED
PENDING_ACTIVATION
QUEUED
WORKING
PENDING_REVIEW
AWAITING_PARENT_ORDER
AWAITING_CONDITION
```

Also update `_normalize_trade_orders()` if needed to mark additional Schwab statuses as cancelable/open. Current code only treats `{ACCEPTED, PENDING_ACTIVATION, QUEUED, WORKING}` as cancelable.

### Stage 7 - Render local intents and real orders on the ladder

Modify:

- `getTradeWorkingOrdersForSelected`
- `buildTradeLadderOrderMarkerHtml`
- `buildTradeActiveLadderHtml`

Do not make the ladder depend only on `tradeRailState.orders`.

Add a combined marker source:

```js
function getTradeLadderMarkersForSelected(selected) {
  return [
    ...normalized Schwab working orders,
    ...local intents not reconciled to those orders
  ];
}
```

Marker fields should be normalized:

```js
{
  marker_id,
  source: 'schwab' | 'local',
  side: 'buy' | 'sell',
  label: 'BUY x1' | 'PREVIEW x1' | 'SENDING x1',
  state,
  price,
  quantity,
  instruction,
  order_id,
  local_id,
  cancelable,
  clearable,
  error,
}
```

Cancel/clear rules:

- Real Schwab `cancelable` marker: `x` calls `cancelTradeOrder(orderId)`.
- Local `staged`, `previewed`, `rejected`, `expired`: `x` clears the local intent only.
- Local `sending`: disable `x` or show non-clickable pending state.
- Local `working` with `order_id`: cancel via Schwab.

### Stage 8 - Stabilize ladder viewport and current-price marker

Add ladder viewport state to `tradeRailState`:

```js
ladderSymbol: '',
ladderCenterPrice: null,
ladderTick: null,
ladderRows: 25,
ladderLastRecenterAt: 0,
```

Add helpers:

- `getTradeCurrentLadderPrice(selected)`
- `getTradeLadderTick(price)`
- `ensureTradeLadderViewport(selected, markers)`
- `shouldRecenterTradeLadder(currentPrice, centerPrice, tick, visibleRows)`
- `recenterTradeLadder(selected, reason)`

Current-price basis:

Use `last` if valid and recent enough. Fallback to `mark`, then `mid`.

Recenter rules:

- always recenter on selected contract change
- recenter if no center exists
- recenter if current price is within 4 rows of top/bottom
- optionally recenter if active working order price is outside visible rows and the user has not manually interacted
- do not recenter merely because bid/ask/mid changes by one tick

Add a small "recenter" button if useful, but keep the Active Trader surface compact.

Important: do not center on `limitPrice` by default. A staged limit should show as a marker at its price. The current trading price marker should remain the ladder's movement reference.

### Stage 9 - Change ladder row/cell CSS

Current row-level zone classes should be removed or neutralized:

```css
.trade-active-ladder-row.bid-zone
.trade-active-ladder-row.ask-zone
```

Replace with cell-level classes:

```css
.trade-active-marker-cell.buy-zone,
.trade-active-bid.bid-zone {
  background: color-mix(in srgb, var(--call) 9%, transparent);
}

.trade-active-ask.ask-zone,
.trade-active-marker-cell.sell-zone {
  background: color-mix(in srgb, var(--put) 9%, transparent);
}

.trade-active-price.current-market {
  background: color-mix(in srgb, var(--fg-0) 18%, var(--bg-1));
  color: var(--fg-0);
  border-radius: 3px;
}
```

Use existing tokens only.

Render row HTML so cell classes are specific:

```html
<span class="trade-active-ladder-cell trade-active-marker-cell buy-zone">...</span>
<span class="trade-active-ladder-cell trade-active-bid bid-zone">BID</span>
<span class="trade-active-ladder-cell trade-active-price current-market">0.93</span>
<span class="trade-active-ladder-cell trade-active-ask ask-zone">ASK</span>
<span class="trade-active-ladder-cell trade-active-marker-cell sell-zone">...</span>
```

Only apply `current-market` to the row nearest current trading price. Do not use the existing `.current` row background for this.

### Stage 10 - Reduce scroll jumps

Apply all of these:

1. Stop recenters caused by normal quote updates via Stage 8.
2. Preserve trade rail scroll inside `renderTradeActiveTrader()` when it replaces ladder HTML.
3. Add `overflow-anchor: none` to the ladder container and possibly the Active Trader panel:

```css
.trade-active-panel,
.trade-active-ladder {
  overflow-anchor: none;
}
```

4. Avoid rebuilding ladder DOM if the structural signature is unchanged.

Suggested ladder render optimization:

```js
const html = buildTradeActiveLadderHtml(selected);
const signature = getTradeLadderRenderSignature(selected, markers);
if (ladder.dataset.tradeLadderSignature !== signature) {
  ladder.innerHTML = html;
  ladder.dataset.tradeLadderSignature = signature;
  wire ladder handlers
} else {
  update only current-price cell classes/marker text if feasible
}
```

If this is too much for the first pass, stable center plus scroll preservation should still address most of the user-facing jump.

---

## 7. Suggested Function Inventory

Add or refactor around these names:

```js
createTradeOrderIntent
updateTradeOrderIntent
clearTradeOrderIntent
expireStaleTradeOrderIntents
getTradeActiveOrderIntents
getActiveTradeOrderIntentsForSelected
findTradeOrderIntentForTicket
upsertTradeOrderIntentFromPreview
reconcileTradeOrderIntentsWithOrders
getTradeLadderMarkersForSelected
buildTradeLadderMarkerHtml
sendTradeFastOrder
previewTradeOrderForIntent
placeTradeOrderForIntent
scheduleTradeOrderPolling
stopTradeOrderPolling
requestTradeOrders
getTradeCurrentLadderPrice
ensureTradeLadderViewport
shouldRecenterTradeLadder
getTradeLadderRenderSignature
```

Backend:

```python
_trade_order_id_from_location
trade_place_order
_normalize_trade_orders
```

---

## 8. Specific Code Anchors

Use `rg` for these:

```bash
rg -n "tradeRailState = \\{" ezoptionsschwab.py
rg -n "function handleTradeFastAction" ezoptionsschwab.py
rg -n "function stageTradeFastTicket" ezoptionsschwab.py
rg -n "function previewTradeOrder" ezoptionsschwab.py
rg -n "function placeTradeOrder" ezoptionsschwab.py
rg -n "function requestTradeOrders" ezoptionsschwab.py
rg -n "function renderTradeOrders" ezoptionsschwab.py
rg -n "function buildTradeActiveLadderHtml" ezoptionsschwab.py
rg -n "function renderTradeActiveTrader" ezoptionsschwab.py
rg -n "function getTradeQuotePriceSignature" ezoptionsschwab.py
rg -n "function getTradeQuoteMoveState" ezoptionsschwab.py
rg -n "def trade_preview_order" ezoptionsschwab.py
rg -n "def trade_place_order" ezoptionsschwab.py
rg -n "def _normalize_trade_orders" ezoptionsschwab.py
rg -n "trade-active-ladder-row\\.bid-zone|trade-active-ladder-row\\.ask-zone" ezoptionsschwab.py
```

---

## 9. User-Facing Copy

Keep messages direct and action-oriented.

Examples:

```text
Staged BUY x1 @ 0.93. Preview before live placement.
Preview ready: BUY x1 @ 0.93.
Auto-send armed: previewing BUY x1 @ 0.93.
Sending live BUY x1 @ 0.93.
Working BUY x1 @ 0.93. Cancel from ladder marker.
Rejected: Live trading is disabled.
Quote moved since preview. Sending exact previewed limit.
```

Avoid implying a live order is working until either:

- `/trade/place_order` succeeded, or
- `/trade/orders` shows a working/cancelable order.

Use "staged", "preview", or "sending" for local-only states.

---

## 10. Verification Plan

Run after implementation:

```bash
python3 -m py_compile ezoptionsschwab.py
git diff --check
python3 -m unittest tests.test_session_levels tests.test_trade_preview
```

If frontend JS changed, syntax-check rendered inline JS:

```bash
python3 -c "import re, pathlib, ezoptionsschwab as m; html=m.app.test_client().get('/').get_data(as_text=True); scripts=re.findall(r'<script[^>]*>(.*?)</script>', html, re.S|re.I); pathlib.Path('/tmp/gex-inline-scripts.js').write_text('\n;\\n'.join(scripts)); print('scripts', len(scripts))"
node --check /tmp/gex-inline-scripts.js
```

Manual browser checks:

1. Start app on a non-conflicting port, for example:

   ```bash
   PORT=5016 FLASK_DEBUG=0 FLASK_USE_RELOADER=0 python3 ezoptionsschwab.py
   ```

2. Open the app in the in-app browser.
3. Confirm Order Entry rail opens and account/contract data loads.
4. With Auto off:
   - click `Buy Ask`
   - verify ladder shows staged marker at clicked ask
   - click `Preview Order`
   - verify marker changes to previewed
   - verify no live order is placed
5. With Auto on and `ENABLE_LIVE_TRADING` disabled:
   - click `Buy Ask`
   - verify marker goes previewing/sending then rejected with "Live trading is disabled"
   - verify this failure is visible on ladder and message area
6. Do not test a real live order unless the user explicitly asks and gives action-time confirmation.
7. Scroll the trade rail down to Contract Picker / Preview / Place while stream updates:
   - verify it does not jump back to Active Trader
8. Watch ladder through quote updates:
   - price rows should remain stable
   - gray current-price marker should move
   - ladder should recenter only when marker nears top/bottom
9. Verify bid/ask shading:
   - green only in buy/bid side cells
   - red only in ask/sell side cells
   - no full-row red/green wash
10. If a mocked or safe working order is available:
   - verify Schwab order marker has cancel `x`
   - cancel still requires confirm
   - after cancel, marker updates to canceled/clears

---

## 11. Tests To Add Or Extend

Backend:

- Extend `tests.test_trade_preview` if it exists:
  - preview route still creates exact order JSON
  - place route still rejects without `ENABLE_LIVE_TRADING=1`
  - place route still rejects changed account/ticker/contract/instruction/qty/limit
  - `_trade_order_id_from_location()` parses common Schwab location URLs
  - `_normalize_trade_orders()` marks expected open statuses correctly

Frontend logic is inline JS and not currently modular. At minimum, use rendered JS syntax checks. If practical, add small browser smoke notes or test harness snippets, but do not introduce a new JS framework.

---

## 12. Known Non-Goals

Do not implement these in this repair pass:

- automated strategy trading from chart/alerts/flow
- live bracket/OCO Schwab order payloads
- multi-leg spreads
- SPX-specific validation
- storing active local order intents across reloads
- changing analytics math
- changing chart/GEX/right-rail features outside the Order Entry rail

---

## 13. Implementation Priority

If time is limited, implement in this order:

1. Local order intents and ladder markers for staged/previewed/sending/rejected.
2. Armed Auto Send one-click preview-then-place flow with immediate visible marker.
3. Stable ladder viewport/current-price marker.
4. Column-only bid/ask shading.
5. Order polling/reconciliation.
6. Scroll jump hardening.

The first two items address the most dangerous user confusion: not knowing whether an order was sent, previewed, rejected, or working.

---

## 14. New Session Prompt

Use this prompt for a new implementation session:

```text
We need to implement the Order Entry rail scalping repair plan in docs/ORDER_ENTRY_RAIL_SCALPING_REPAIR_PLAN.md.

Please read AGENTS.md instructions from the repo root, then read docs/OPTIONS_TRADING_RAIL_IMPLEMENTATION_PLAN.md, docs/OPTIONS_TRADING_RAIL_UI_POLISH_PLAN.md, and docs/ORDER_ENTRY_RAIL_SCALPING_REPAIR_PLAN.md. Confirm the branch with `git branch -a`, `git log --oneline main..HEAD`, and `git status --short`. There may be unrelated dirty changes in ezoptionsschwab.py; do not revert them.

Focus only on the dedicated Order Entry trading rail in ezoptionsschwab.py. Do not change analytics formulas, the chart, the strike rail, or the analytics right rail.

Implement the plan in focused stages:
1. Add local order intent state for staged, previewing, previewed, sending, working, rejected, canceled, filled, and expired states.
2. Show staged/previewed/sending/rejected local markers on the Active Trader ladder immediately, separate from real Schwab working-order markers.
3. Fix armed Auto Send so Buy Ask/Sell Bid/Flatten lock the clicked price, create a visible local intent, preview the exact order, then place the exact previewed order in one armed fast flow while preserving backend live-trading guards.
4. Make quote movement a warning for exact previewed limit orders, not a hard blocker when order fields still match the preview.
5. Parse Schwab order id from placement Location when available and reconcile local intents with /trade/orders.
6. Add lightweight order polling while active intents exist or Orders is open.
7. Stabilize the ladder viewport so rows do not recenter every tick; use a gray current-price marker that moves and recenter only near the edge.
8. Change ladder bid/ask color zones to cell/column shading instead of full-row shading.
9. Harden trade rail scroll preservation so streaming ladder updates do not pull the rail back to Active Trader.

Keep Auto Send off by default and not persisted. Do not bypass ENABLE_LIVE_TRADING=1, exact preview binding, or SELL_TO_CLOSE position caps. Do not test by sending a real live order. Verify with py_compile, git diff --check, the existing trade/session tests, rendered inline JS node --check, and an in-app browser smoke test on a local Flask port.
```
