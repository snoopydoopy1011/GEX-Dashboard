# GEX Dashboard — Options Trading Rail Implementation Plan

**Status:** Draft / planning only  
**Created:** 2026-05-01  
**Primary goal:** Add a separate, independently hideable/resizable trading right rail for selecting and trading 0-1 DTE options contracts from the dashboard.  
**Initial scope:** SPY first; SPX after preview/order validation proves Schwab accepts the returned contract symbols.  
**Safety stance:** Preview-only before live order placement. Live order placement must be explicitly enabled in a later stage after contract selection, account lookup, order JSON, and Schwab preview responses are verified.

---

## 0. Read This First

This plan is written so a new Codex session can jump in without prior chat context.

The user wants a **new trading rail that is its own rail**, not just a fifth tab inside the current Overview / Levels / Scenarios / Flow right rail.

Current dashboard rails:

- **Strike rail:** right-side strike/GEX rail, hidden by default in some workflows because GEX is also overlaid on the price axis.
- **Analytics right rail:** one right-side grid column with tabs: Overview, Levels, Scenarios, Flow. It is independently collapsible/resizable.
- **Desired trading rail:** a new, separate right rail similar to the attached screenshot concept: order entry, contract picker/selection, risk/quantity/limit controls, preview/submit, positions/orders management.

The trading rail should use the existing dashboard option-chain data as much as possible. The app already fetches Schwab options chains for analytics, including contract symbols, bid/ask/mark/last, volume, OI, IV, Greeks, expiry, and strike.

**Do not start by placing live orders.** The implementation should progress from read-only contract selection to Schwab preview order responses to live placement only after explicit confirmation.

---

## 1. Standing Project Constraints

Inherit these from `AGENTS.md` and existing initiative docs:

- No analytical formula changes. GEX/DEX/Vanna/Charm/Flow math stays put.
- No JS framework introduction. Vanilla JS + CSS tokens only.
- Do not break the single-file `ezoptionsschwab.py` structure.
- Use design tokens for colors (`--bg-*`, `--fg-*`, `--call`, `--put`, `--warn`, `--info`, `--accent`, `--border`, etc.).
- Do not commit `.env`, `options_data.db`, token DBs, `terminal_while_running*.txt`, or `__pycache__/`.
- Any element that can be rebuilt by `ensurePriceChartDom()` must be mirrored in its rebuild path, or tick/ticker changes may drop it.
- Use Schwab-returned option `contractSymbol` for trading. Do not reconstruct symbols by hand unless debugging a verified Schwab SDK/API issue.

Before implementation work:

```bash
git branch -a
git log --oneline main..HEAD
git status --short
```

Recommended branch:

```bash
git checkout -b codex/options-trading-rail
```

---

## 2. Relevant Existing Files And Anchors

Primary file:

- `ezoptionsschwab.py` — single-file Flask + Plotly + TradingView-Lightweight-Charts dashboard.

Useful docs:

- `docs/UI_MODERNIZATION_PLAN.md`
- `docs/ANALYTICS_CHART_PHASE2_PLAN.md`
- `docs/ALERTS_RAIL_PHASE3_PLAN.md`
- `Trading_from_dashboard.txt` — Schwab trading/OAuth/order examples supplied by user. This may be untracked and should not be assumed committed.

Key anchors in `ezoptionsschwab.py`:

- Schwab client init: `client = schwabdev.Client(...)`
- Option chain fetcher: `fetch_options_for_date`
- Multiple expiry fetcher: `fetch_options_for_multiple_dates`
- Chain cache: `_options_cache`
- Main chain update endpoint: `@app.route('/update', methods=['POST'])`
- Price/chart refresh endpoint: `@app.route('/update_price', methods=['POST'])`
- Existing Flow tab rendering:
  - Backend: `create_large_trades_table`
  - Frontend: `renderRailFlowBlotter`
  - Frontend: `initRailFlowBlotter`
- Current analytics right rail:
  - CSS: `.right-rail-tabs`, `.right-rail-panels`, `.right-rail-panel`
  - JS: `wireRightRailTabs`, `applyRightRailTab`, `syncRightRailWidthForTab`, `wireRightRailCollapseToggle`, `wireRightRailResizeHandle`
  - HTML/rebuild: `buildAlertsPanelHtml`, `ensurePriceChartDom`
- Strike rail:
  - CSS/HTML: `.gex-col-header`, `.gex-resize-handle`, `.gex-column`, `.gex-side-panel-wrap`
  - JS: `applyStrikeRailTabs`, `renderStrikeRailPanel`, `wireGexColumnToggle`
- Token/account health:
  - `_get_token_db_path`
  - `_read_token_db`
  - `/token_health`
  - `/token_delete`

Installed `schwabdev.Client` methods verified locally:

- `linked_accounts()`
- `account_details(accountHash, fields=None)`
- `account_orders(accountHash, fromEnteredTime, toEnteredTime, maxResults=None, status=None)`
- `preview_order(accountHash, orderObject)`
- `place_order(accountHash, order)`
- `replace_order(accountHash, orderId, order)`
- `cancel_order(accountHash, orderId)`
- `order_details(accountHash, orderId)`

---

## 3. Current Data Reality

The existing chain fetch already captures most fields needed for a contract picker:

```python
option_data = {
    'contractSymbol': option['symbol'],
    'strike': K,
    'lastPrice': float(option['last']),
    'bid': float(option['bid']),
    'ask': float(option['ask']),
    'mark': float(option.get('mark', 0) or 0),
    'volume': int(option['totalVolume']),
    'openInterest': int(option['openInterest']),
    'impliedVolatility': vol,
    'inTheMoney': option['inTheMoney'],
    'expiration': ...,
    'quoteTimeInLong': ...,
    'tradeTimeInLong': ...,
    'delta': delta,
    'gamma': gamma,
    'theta': theta,
    'vega': vega,
    'rho': rho,
}
```

Important issue:

- The existing rail Flow tab parses HTML rows and renders a compact list, but it does **not currently preserve `contractSymbol` in the rail row dataset**. A trading rail needs the exact `contractSymbol`.

Recommended fix when implementation starts:

- Add a structured contract-picker payload instead of scraping/parsing flow table HTML.
- Keep it separate from analytical formulas.
- Use cached `calls`/`puts` from `_options_cache[ticker]`.

Candidate new backend helper:

```python
def build_trading_chain_payload(ticker, calls, puts, S, selected_expiries=None, strike_range=0.02):
    ...
```

Candidate output:

```json
{
  "ticker": "SPY",
  "underlying_price": 660.07,
  "as_of": "2026-05-01T15:42:10Z",
  "selected_expiries": ["2026-05-01"],
  "contracts": [
    {
      "contract_symbol": "SPY   260501C00660000",
      "underlying": "SPY",
      "option_type": "CALL",
      "instruction_open": "BUY_TO_OPEN",
      "instruction_close": "SELL_TO_CLOSE",
      "expiry": "2026-05-01",
      "dte": 0,
      "strike": 660.0,
      "bid": 1.22,
      "ask": 1.26,
      "mark": 1.24,
      "last": 1.23,
      "mid": 1.24,
      "spread": 0.04,
      "spread_pct": 3.23,
      "volume": 12345,
      "open_interest": 67890,
      "iv": 0.142,
      "delta": 0.51,
      "gamma": 0.08,
      "theta": -0.31,
      "in_the_money": false,
      "quote_time": 1777668130000,
      "trade_time": 1777668125000
    }
  ]
}
```

---

## 4. Schwab Order Constraints From Supplied Docs

The attached Schwab trading docs state:

- OAuth access token is valid for 30 minutes.
- Refresh token is valid for 7 days.
- Order entry is available for `EQUITY` and `OPTION`.
- Option instructions:
  - `BUY_TO_OPEN`
  - `BUY_TO_CLOSE`
  - `SELL_TO_OPEN`
  - `SELL_TO_CLOSE`
- Single option limit order example:

```json
{
  "complexOrderStrategyType": "NONE",
  "orderType": "LIMIT",
  "session": "NORMAL",
  "price": "6.45",
  "duration": "DAY",
  "orderStrategyType": "SINGLE",
  "orderLegCollection": [
    {
      "instruction": "BUY_TO_OPEN",
      "quantity": 10,
      "instrument": {
        "symbol": "XYZ   240315C00500000",
        "assetType": "OPTION"
      }
    }
  ]
}
```

Initial dashboard implementation should support only:

- Single-leg option orders.
- `LIMIT` orders.
- `DAY` duration.
- `NORMAL` session.
- `BUY_TO_OPEN` and `SELL_TO_CLOSE` first.

Defer:

- Market orders.
- Multi-leg spreads.
- Bracket orders.
- OCO / trigger orders.
- Trailing stops.
- `SELL_TO_OPEN`.
- Auto-trading from alerts.

Rationale: 0-1 DTE option order entry has high execution risk; narrow scope keeps validation tractable.

---

## 5. Target UX

The trading rail should look like a trading tool, not a marketing panel and not an analytics card stack.

Desired surface:

- Dedicated right-side rail beside the existing analytics rail.
- Independently collapsible.
- Independently resizable.
- Can be toggled open/closed without losing the existing analytics right rail state.
- Dense layout, low decoration, fast scanning.
- Contract picker visible enough to select 0-1 DTE SPY/SPX calls/puts quickly.
- Order ticket always shows exactly what contract/order is selected.

Suggested top-level rail sections:

1. **Header**
   - “Order Entry”
   - Ticker / expiry context
   - account selector or masked account label
   - preview/live status badge
   - collapse button

2. **Contract Picker**
   - CALL / PUT segmented control
   - expiry selector, default nearest 0DTE/1DTE
   - moneyness/strike window around spot
   - row list or compact chain table
   - key columns: strike, bid, ask, mid/mark, spread, vol, OI, delta, IV
   - selected row state

3. **Selected Contract**
   - contract symbol
   - expiry, strike, type
   - bid/mid/ask/last
   - spread warning
   - quote age warning

4. **Order Ticket**
   - Action: Buy to Open / Sell to Close
   - Quantity
   - Limit price
   - quick price buttons: bid, mid, ask, last/mark
   - estimated debit/credit
   - estimated max risk for long options: `limit * quantity * 100`
   - optional risk budget quantity helper

5. **Preview / Submit**
   - Preview button
   - preview response panel
   - Place Order disabled until preview succeeds
   - final confirmation dialog or inline confirm step

6. **Position / Order Management**
   - Current position for selected contract if available
   - open orders for selected contract
   - cancel button for open orders
   - replace order later, not stage 1

---

## 6. Layout Architecture

The current `.chart-grid` has 3 columns:

```css
grid-template-columns: minmax(0, 1fr) var(--gex-col-w) var(--rail-col-w);
```

Current column roles:

- Column 1: chart
- Column 2: strike/GEX rail
- Column 3: analytics right rail

Trading rail should add a fourth column:

```css
--trade-rail-w: clamp(360px, 24vw, 460px);
grid-template-columns:
    minmax(0, 1fr)
    var(--gex-col-w)
    var(--rail-col-w)
    var(--trade-rail-w);
```

Candidate new DOM siblings inside `#chart-grid`:

```html
<div class="trade-rail-header" id="trade-rail-header">...</div>
<button type="button" class="trade-rail-collapse-toggle" id="trade-rail-collapse-toggle">...</button>
<div class="trade-rail-resize-handle" id="trade-rail-resize-handle"></div>
<aside class="trade-rail" id="trade-rail">...</aside>
```

Candidate placement:

```css
.chart-grid > .trade-rail-header { grid-column: 4; grid-row: 1; }
.chart-grid > .trade-rail        { grid-column: 4; grid-row: 2; }
.chart-grid > .trade-rail-resize-handle {
    grid-column: 4;
    grid-row: 1 / span 2;
    justify-self: start;
}
```

Collapse class:

```css
.chart-grid.trade-rail-collapsed { --trade-rail-w: 0px; }
.chart-grid.trade-rail-collapsed > .trade-rail-header,
.chart-grid.trade-rail-collapsed > .trade-rail,
.chart-grid.trade-rail-collapsed > .trade-rail-resize-handle {
    display: none;
}
```

LocalStorage keys:

```js
const TRADE_RAIL_COLLAPSE_KEY = 'gex.tradeRailCollapsed';
const TRADE_RAIL_WIDTH_KEY = 'gex.tradeRailWidthPx';
```

Implementation warning:

- `ensurePriceChartDom()` can rebuild missing grid children. Any new trade rail DOM must be created both in the initial Python HTML and the JS rebuild path.
- `showPriceChartUI()` currently lists grid child IDs to display. Add trade rail IDs there.
- Responsive CSS sections around current right rail/strike rail must account for the fourth column.
- When both analytics rail and trading rail are open, chart width will shrink. Add minimum chart width logic or auto-collapse trading rail on narrow screens.

Suggested responsive rule:

- Desktop wide: chart + strike rail + analytics rail + trade rail may all show.
- Medium desktop: allow either analytics rail or trading rail open; if trade rail opens, optionally collapse analytics rail.
- Mobile/narrow: trade rail becomes a full-width lower panel or modal-like drawer, not a fourth column.

Do not implement automatic collapse without confirming UX, but design the CSS so narrow widths do not break.

---

## 7. Backend Endpoint Design

Add endpoints only after read-only payload helper exists.

### 7.1 Trading Chain Snapshot

Candidate:

```python
@app.route('/trade_chain', methods=['POST'])
def trade_chain():
    ...
```

Inputs:

```json
{
  "ticker": "SPY",
  "expiry": ["2026-05-01"],
  "strike_range": 0.02,
  "contract_type": "ALL"
}
```

Behavior:

- Prefer `_options_cache[ticker]` if it matches the selected ticker/expiry enough for current UI.
- If cache missing/stale, return a clear error or optionally fetch chain using `fetch_options_for_date`.
- Do not place orders here.

Output:

- `contracts` list as described above.
- `as_of`
- `underlying_price`
- `selected_expiries`
- `warnings`

### 7.2 Account List

Candidate:

```python
@app.route('/trade/accounts', methods=['GET'])
def trade_accounts():
    ...
```

Use:

```python
client.linked_accounts()
```

Return masked account info only. Store/use account hash from Schwab response. Do not expose plain account numbers if Schwab provides hashed/encrypted IDs.

### 7.3 Account Details / Positions

Candidate:

```python
@app.route('/trade/account_details', methods=['POST'])
def trade_account_details():
    ...
```

Use:

```python
client.account_details(accountHash, fields='positions')
```

Return:

- balances needed for buying-power display
- positions relevant to selected ticker/contract

Be conservative about what is displayed.

### 7.4 Preview Order

Candidate:

```python
@app.route('/trade/preview_order', methods=['POST'])
def trade_preview_order():
    ...
```

Validate request before calling Schwab:

- account hash present
- contract symbol exists in latest cached chain
- asset type is `OPTION`
- order type is `LIMIT`
- duration is `DAY`
- session is `NORMAL`
- quantity is positive integer
- price is positive decimal
- instruction is one of allowed initial instructions

Build Schwab order JSON server-side. Do not trust raw frontend JSON for final Schwab order construction.

Initial order builder:

```python
def build_single_option_limit_order(contract_symbol, instruction, quantity, limit_price):
    return {
        "complexOrderStrategyType": "NONE",
        "orderType": "LIMIT",
        "session": "NORMAL",
        "price": f"{float(limit_price):.2f}",
        "duration": "DAY",
        "orderStrategyType": "SINGLE",
        "orderLegCollection": [
            {
                "instruction": instruction,
                "quantity": int(quantity),
                "instrument": {
                    "symbol": contract_symbol,
                    "assetType": "OPTION"
                }
            }
        ]
    }
```

Use:

```python
client.preview_order(accountHash, order)
```

Return:

- preview success/failure
- Schwab response status code
- Schwab response body if JSON
- normalized warning/error summary
- a server-generated preview token/hash to bind future `place_order` to this exact order

### 7.5 Place Order

Candidate:

```python
@app.route('/trade/place_order', methods=['POST'])
def trade_place_order():
    ...
```

Do not build this until preview-only flow is verified.

Hard guards:

- require `ENABLE_LIVE_TRADING=1` env var
- require successful preview token for exact same order
- require frontend final confirmation flag
- reject order if cached quote is stale beyond threshold
- reject if price moved too far from preview inputs, unless user previews again
- log order request/response metadata locally, without secrets

Use:

```python
client.place_order(accountHash, order)
```

Handle likely success status:

- Schwab may return `201 Created` with `Location` header rather than a rich JSON body. Preserve headers/status in response.

### 7.6 Orders

Candidate:

```python
@app.route('/trade/orders', methods=['POST'])
def trade_orders():
    ...
```

Use:

```python
client.account_orders(accountHash, fromEnteredTime, toEnteredTime, ...)
client.cancel_order(accountHash, orderId)
client.order_details(accountHash, orderId)
```

Defer replace order until after cancel/order details are reliable.

---

## 8. Frontend State Model

Suggested JS state:

```js
const tradeRailState = {
    enabled: false,
    accountHash: null,
    accountLabel: null,
    ticker: 'SPY',
    selectedExpiry: null,
    selectedType: 'CALL',
    selectedContractSymbol: null,
    selectedContract: null,
    action: 'BUY_TO_OPEN',
    quantity: 1,
    limitPriceMode: 'mid',
    limitPrice: null,
    preview: null,
    previewToken: null,
    liveTradingEnabled: false,
    lastChainAsOf: null,
    error: null
};
```

Key frontend functions:

- `buildTradeRailHtml()`
- `ensureTradeRailDom()`
- `applyTradeRailCollapse(collapsed)`
- `wireTradeRailCollapseToggle()`
- `wireTradeRailResizeHandle()`
- `fetchTradeChainSnapshot()`
- `renderTradeContractPicker(payload)`
- `selectTradeContract(contractSymbol)`
- `renderTradeTicket()`
- `computeTradeEstimate()`
- `previewTradeOrder()`
- `placeTradeOrder()` later only
- `renderTradePositions()`
- `renderTradeOrders()`

Interaction rules:

- Selecting a contract populates ticket fields.
- Changing quantity/price/action invalidates prior preview.
- Changing selected contract invalidates prior preview.
- Place Order stays disabled until preview succeeds.
- A preview response should show Schwab warnings/errors plainly.
- If quote/chain snapshot becomes stale, require refresh or re-preview.

---

## 9. Validation And Safety Rules

Initial validation:

- Only `OPTION`.
- Only `LIMIT`.
- Only `DAY`.
- Only `NORMAL`.
- Only single-leg.
- Only quantity `1..N`, configurable max.
- Only positive limit price.
- Price must be near bid/ask/mark unless user explicitly overrides in a later stage.
- Contract symbol must be found in latest cached chain payload.
- For `BUY_TO_OPEN`, estimated debit = `limit * qty * 100`.
- For `SELL_TO_CLOSE`, position quantity should be checked before preview if positions are available.

Warnings:

- Bid/ask spread too wide.
- Contract quote stale.
- Selected expiry is not 0DTE/1DTE.
- Underlying ticker changed after contract selection.
- Token invalid/refresh expired.
- No account selected.
- Buying power unavailable.
- Preview response contains Schwab warning.

Live-order hard stops:

- `ENABLE_LIVE_TRADING` not set.
- No successful preview for exact order hash.
- Preview older than threshold, e.g. 15-30 seconds.
- Selected contract no longer matches current chain snapshot.
- Requested order changed since preview.
- User has not final-confirmed.

Do not add any auto-submit behavior from alerts, flow pulses, levels, or chart clicks.

---

## 10. Stage Plan

### Stage 1 — Read-Only Trade Rail Shell

Goal:

- Add the fourth grid column and dedicated trading rail shell.
- No account access.
- No Schwab trading endpoint calls.
- No preview/place order.

Tasks:

- Add CSS variables: `--trade-rail-w`.
- Extend `.chart-grid` to four columns.
- Add `trade-rail-collapsed` class behavior.
- Add initial Python HTML for:
  - `#trade-rail-header`
  - `#trade-rail-collapse-toggle`
  - `#trade-rail-resize-handle`
  - `#trade-rail`
- Add JS rebuild support in `ensurePriceChartDom()`.
- Add `showPriceChartUI()` IDs.
- Add localStorage collapse/width state.
- Add resize/collapse wiring.
- Keep rail hidden/collapsed by default if preferred.

Verification:

- App starts.
- Chart still renders.
- Strike rail collapse still works.
- Analytics right rail collapse/resize still works.
- Trade rail collapse/resize works independently.
- Ticker/timeframe refresh does not drop trade rail DOM.
- Narrow screen does not overlap text or destroy chart usability.

### Stage 2 — Contract Picker Payload And UI

Goal:

- Populate trade rail with selectable contracts from existing cached chain data.
- No account access.
- No preview/place order.

Tasks:

- Add `build_trading_chain_payload(...)`.
- Add `/trade_chain` endpoint or include payload in existing `/update_price` response only if payload size is acceptable.
- Preserve exact `contractSymbol`.
- Render contract picker with calls/puts/expiry filters.
- Default to nearest expiry and strikes near spot.
- Add selected contract panel.
- Add quote age/spread warnings.

Verification:

- SPY 0DTE contracts show exact symbols.
- Calls/puts filters work.
- Contract row selection persists until ticker/expiry changes.
- Selected contract bid/mid/ask/last match backend payload.
- No new chain fetch loop or API spam.

### Stage 3 — Account Read / Positions Read

Goal:

- Show available accounts and selected-account context.
- Show relevant positions for selected contract/ticker.
- Still no preview/place order until account plumbing is stable.

Tasks:

- Add `/trade/accounts`.
- Add `/trade/account_details`.
- Use `client.linked_accounts()`.
- Use `client.account_details(accountHash, fields='positions')`.
- Mask account labels.
- Store selected account hash in localStorage only if acceptable; otherwise require selecting per session.
- Render buying power if available.
- Render selected contract position if present.

Verification:

- Token health failures surface clearly.
- Account hash is used for downstream calls.
- No plain account number leakage in UI/logs.
- Positions display for held contracts.

### Stage 4 — Preview-Only Single-Leg Limit Orders

Goal:

- Build and preview Schwab order JSON for single-leg option limit orders.
- Still no live order placement.

Tasks:

- Add server-side order builder.
- Add `/trade/preview_order`.
- Add strict validation.
- Support:
  - `BUY_TO_OPEN`
  - `SELL_TO_CLOSE`
  - quantity
  - limit price
- Add ticket controls.
- Add price quick-fill buttons: bid/mid/ask/mark.
- Render Schwab preview response.
- Generate preview token/order hash.

Verification:

- Preview for SPY option returns expected Schwab response or clear API error.
- Invalid quantity/price/action is rejected locally before Schwab.
- Changing any order field invalidates preview.
- Preview token changes when order changes.
- No live order endpoint exists or it is disabled.

### Stage 5 — Live Place Order Behind Feature Flag

Goal:

- Enable live single-leg limit orders only after preview succeeds.

Tasks:

- Add `ENABLE_LIVE_TRADING=1` gate.
- Add `/trade/place_order`.
- Require preview token.
- Require exact order hash match.
- Require final UI confirmation.
- Handle Schwab status/header response.
- Log order metadata safely.
- Add clear success/error state in UI.

Verification:

- With env var off, live placement is impossible.
- With stale/missing preview, placement is impossible.
- With changed quantity/price/contract after preview, placement is impossible.
- Successful order returns Schwab order id/location if available.
- UI does not double-submit.

### Stage 6 — Order And Position Management

Goal:

- Add open order list and cancel support.
- Add position status for selected contract.

Tasks:

- Add `/trade/orders`.
- Add `/trade/cancel_order`.
- Show open orders for selected account/ticker.
- Show selected contract position.
- Add cancel confirmation.
- Defer replace order unless cancel/details are stable.

Verification:

- Open orders refresh.
- Cancel works for a known paper/small live test only after user confirmation.
- Position display updates after order fills or refresh.

### Stage 7 — Later Enhancements

Possible later work:

- Bracket/OCO order builder.
- Stop/target helpers based on option premium or underlying level.
- Risk-budget sizing.
- Chart click sets underlying reference, not order submission.
- SPX-specific validation.
- Multi-leg spreads.
- Order journal export.

---

## 11. Data And API Decisions To Confirm

Open questions before live trading:

- Should trade rail default collapsed or visible?
- Should opening trade rail auto-collapse analytics rail on medium screens?
- Should selected account hash persist in localStorage?
- Max default order quantity?
- Should `SELL_TO_CLOSE` be visible only when position exists?
- Should SPX be enabled immediately or only after a successful preview test?
- Should `SELL_TO_OPEN` ever be supported? Recommended: no initially.
- Should order placement require typing a confirmation word or just a final button?

Recommended defaults:

- Trade rail collapsed by default.
- Opening trade rail does not automatically collapse analytics rail on wide screens.
- Do not persist account hash until user approves.
- Max default quantity 10, configurable later.
- SPY first.
- `BUY_TO_OPEN` first, `SELL_TO_CLOSE` once positions read is working.

---

## 12. Known Risks

### Schwab API Behavior

- `preview_order` and `place_order` response shapes may differ from examples.
- Successful place order may return status/header without JSON body.
- Account hash must come from `linked_accounts`; do not use plain account numbers in URL path.
- Token refresh behavior is handled by `schwabdev`, but expired refresh tokens still require full OAuth re-auth.

### Contract Symbol Risk

- The exact Schwab `contractSymbol` should be used.
- Manual symbol construction can fail on spacing, roots, weeklies, SPX/SPXW, adjusted contracts, or special classes.

### UI Rebuild Risk

- Current chart DOM can be rebuilt by `ensurePriceChartDom()`.
- Any new trading rail markup must exist in both initial HTML and rebuild logic.

### Layout Risk

- Four columns can squeeze the chart.
- Existing responsive CSS references `.right-rail-tabs`/`.right-rail-panels` and may need updates.
- Check desktop and narrow widths visually.

### Trading Safety Risk

- A stale 0DTE option quote can become dangerous quickly.
- Require re-preview after material changes.
- Keep live placement behind an env flag and explicit confirmation.

---

## 13. Suggested Verification Commands

Basic syntax:

```bash
python3 -m py_compile ezoptionsschwab.py
```

Run app:

```bash
PORT=5001 FLASK_DEBUG=0 FLASK_USE_RELOADER=0 python3 ezoptionsschwab.py
```

Health:

```bash
curl -s http://127.0.0.1:5001/token_health
```

Existing expiration path:

```bash
curl -s http://127.0.0.1:5001/expirations/SPY
```

Representative `/update_price` smoke payload:

```bash
python3 -c "import json, urllib.request; payload={'ticker':'SPY','timeframe':'1','lookback_days':2,'levels_types':[]}; req=urllib.request.Request('http://127.0.0.1:5001/update_price',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'}); d=json.load(urllib.request.urlopen(req,timeout=60)); print(sorted(d.keys()))"
```

For future `/trade_chain`:

```bash
python3 -c "import json, urllib.request; payload={'ticker':'SPY','expiry':['YYYY-MM-DD'],'strike_range':0.02}; req=urllib.request.Request('http://127.0.0.1:5001/trade_chain',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'}); d=json.load(urllib.request.urlopen(req,timeout=60)); print(d.keys()); print(len(d.get('contracts',[])))"
```

Use the in-app browser or Playwright/browser-use to visually verify:

- chart not blank
- no overlapping rails
- trade rail collapse/resize
- analytics rail still works
- contract picker scrolls
- order ticket buttons do not overflow

---

## 14. Suggested Commit Breakdown

Use one commit per stage.

```text
feat(trading): add independent trade rail shell
feat(trading): add contract picker from cached chain
feat(trading): show linked accounts and positions
feat(trading): add preview-only option limit orders
feat(trading): gate live single-leg option placement
feat(trading): add order status and cancel controls
```

Do not squash while the feature is being reviewed. The safety progression should remain auditable.

---

## 15. Initial Implementation Checklist

Before Stage 1:

- [ ] Confirm branch and worktree state.
- [ ] Read this plan.
- [ ] Read `docs/UI_MODERNIZATION_PLAN.md` sections on layout/tokens.
- [ ] Read current right rail anchors in `ezoptionsschwab.py`.
- [ ] Decide whether trade rail starts collapsed by default.

Stage 1:

- [ ] Add fourth grid column.
- [ ] Add trade rail shell markup.
- [ ] Add collapse state.
- [ ] Add resize state.
- [ ] Mirror DOM in `ensurePriceChartDom()`.
- [ ] Update `showPriceChartUI()`.
- [ ] Test existing rails.

Stage 2:

- [ ] Add structured chain payload helper.
- [ ] Preserve `contractSymbol`.
- [ ] Add read-only contract picker.
- [ ] Add selected contract summary.
- [ ] Add stale/spread warnings.

Stage 3:

- [ ] Add linked account lookup.
- [ ] Add account details/positions lookup.
- [ ] Mask account display.
- [ ] Handle token/account errors.

Stage 4:

- [ ] Add server-side order builder.
- [ ] Add preview endpoint.
- [ ] Add strict validation.
- [ ] Add preview UI.
- [ ] Keep live order disabled.

Stage 5:

- [ ] Add env-gated place endpoint.
- [ ] Bind place to preview token/order hash.
- [ ] Add final confirmation.
- [ ] Handle Schwab response status/header.

Stage 6:

- [ ] Add open orders.
- [ ] Add cancel support.
- [ ] Add position refresh.

---

## 16. Non-Goals For Initial Build

- No automated trading.
- No orders triggered by alerts, flow pulse, chart levels, or GEX changes.
- No market orders.
- No spread orders.
- No bracket/OCO orders in the first live stage.
- No changes to GEX/DEX/Vanna/Charm/Flow formulas.
- No migration to a JS framework.
- No splitting `ezoptionsschwab.py` into modules.

