# GEX Dashboard - Options Trading Rail Implementation Plan

**Status:** Active Trader fast surface is live with preview-mandatory armed auto-send; helper/quick contract selection and journal media are in place; next pass should finish TOS-style ladder/template ergonomics and get the branch PR-ready.
**Last updated:** 2026-05-02
**Branch:** `codex/options-trading-rail-plan`
**Primary file:** `ezoptionsschwab.py`

---

## 0. Current State

The dashboard now has a dedicated fourth order-entry rail, separate from the strike rail and analytics rail. It is independently collapsible/resizable and mirrors its markup in both the server-rendered HTML and `buildTradeRailHtml()` because `ensurePriceChartDom()` can rebuild chart-grid children.

Current order-entry surfaces:

- `#trade-rail-header`
- `#trade-rail`
- `.trade-rail-shell`
- `Active Trader`
- `Contract Helper` / quick contract buttons
- `Position`
- `Selected Contract`
- `Order Ticket`
- `Preview / Place`
- `Contract Picker`
- `Orders`
- `Bracket Plan`
- `Journal`
- `#trade-journal-workspace`

The visual rail order is optimized for 0-1 DTE SPY scalping:

1. Active Trader
2. Contract Helper / quick contract buttons
3. Position
4. Selected Contract
5. Order Ticket
6. Preview / Place
7. Contract Picker
8. Orders
9. Bracket Plan
10. Journal

Active Trader v1 includes:

- selected contract header
- Buy Ask, Sell Bid, Flatten
- quantity input and `1` / `2` / `5` / `10` presets
- bracket-template selector for planning context only
- explicit `Auto-send` arm checkbox
- position, preview, and open/recent order summary
- compact bid/ask/price ladder

The current one-click safety model is conservative:

- Auto-send is off by default.
- Auto-send is not persisted.
- With Auto-send off, fast buttons stage the rich ticket only.
- With Auto-send armed, fast buttons can live-send only the already-previewed exact single-leg `DAY LIMIT` order.
- `/trade/place_order` still requires `ENABLE_LIVE_TRADING=1`, `confirmed=true`, exact recent preview token, exact cached Schwab contract, unchanged order JSON, and `SELL_TO_CLOSE` position caps.
- `/trade/place_order` consumes successful previews, so each new live send requires a fresh preview.

Contract Helper and quick buttons are selection-only:

- Helper Call / Put candidate boxes select the exact cached Schwab/OCC `contract_symbol` if it exists in the current `/trade_chain` payload.
- Quick buttons select exact cached contracts for `ATM Call`, `ATM Put`, `+1 OTM Call`, `+1 OTM Put`, `+2 OTM Call`, and `+2 OTM Put`.
- Calls anchor at the nearest strike at/above spot; puts anchor at the nearest strike at/below spot.
- No option symbols are reconstructed by hand. If a candidate is not in cached `/trade_chain`, the UI asks for refresh/widen-range instead.

Journal state:

- Local SQLite `trade_events` stores successful previews, successful live placements, successful confirmed cancels, and manual notes.
- Local SQLite `trade_event_media` stores media metadata linked to `trade_events`.
- Screenshot files live under `Screenshots/trade_journal`.
- On successful live sidebar placement only, the browser best-effort captures chart canvas layers and attaches a PNG to the matching placed-order event.
- Screenshot failure logs to the console and must not make a live order appear failed.
- Rail Journal and full `#trade-journal-workspace` show attachments, local paths, open links, delete controls, and cleanup controls.

---

## 1. Safety Rules

Do not weaken these without explicit approval:

- Do not silently enable Auto-send.
- Do not add previewless live placement.
- Do not bypass `ENABLE_LIVE_TRADING=1`.
- Do not bypass exact cached contract validation.
- Do not bypass successful preview-token binding for live placement.
- Preserve `SELL_TO_CLOSE` position caps.
- Do not let Bracket Plan alter Schwab preview/place payloads.
- Do not implement live Schwab bracket/OCO child orders.
- Do not implement SPX-specific validation.
- Do not implement multi-leg spreads.
- Do not implement chart/alert/flow automated trading.
- Do not implement automatic screen recordings.
- Do not change GEX/DEX/Vanna/Charm/Flow math.
- Do not introduce a JS framework or split the single-file app.

Bracket Plan remains planning-only. It may be saved to journal metadata, but it must not change `build_single_option_limit_order()`, `/trade/preview_order`, `/trade/place_order`, live-trading gates, final-confirmation behavior, or Schwab order JSON.

---

## 2. Anchors

Use `rg` by anchor name rather than relying on line numbers.

HTML/CSS:

- `#trade-rail-header`
- `#trade-rail`
- `.trade-rail-shell`
- `.trade-active-panel`
- `.trade-helper-panel`
- `.trade-quick-contracts`
- `.trade-picker-panel`
- `.trade-selected-panel`
- `.trade-ticket-panel`
- `.trade-submit-panel`
- `.trade-orders-panel`
- `.trade-bracket-panel`
- `.trade-journal-panel`
- `#trade-journal-workspace`

JavaScript:

- `buildTradeActiveTraderPanelHtml`
- `buildTradeHelperPanelHtml`
- `buildTradeRailHtml`
- `buildTradeJournalWorkspaceHtml`
- `ensureTradeRailDom`
- `ensureTradeJournalWorkspace`
- `renderContractHelper`
- `renderTradeQuickContracts`
- `getTradeContractAnchorRow`
- `getTradeQuickContract`
- `selectTradeContractSymbol`
- `renderTradeActiveTrader`
- `renderTradeSelected`
- `renderTradeTicket`
- `renderTradeRail`
- `wireTradeRailPickerControls`
- `requestTradeChain`
- `requestTradeAccountDetails`
- `requestTradeOrders`
- `requestTradeJournal`
- `placeTradeOrder`

Python/backend:

- `build_trading_chain_payload`
- `_find_cached_trade_contract`
- `_selected_contract_position_quantity`
- `build_single_option_limit_order`
- `_record_trade_event`
- `_get_trade_media_storage_path`
- `/trade_chain`
- `/trade/accounts`
- `/trade/account_details`
- `/trade/orders`
- `/trade/cancel_order`
- `/trade/preview_order`
- `/trade/place_order`
- `/trade/journal`
- `/trade/journal/update`
- `/trade/journal/create`
- `/trade/journal/attach_screenshot`
- `/trade/journal/media/<id>`
- `/trade/journal/media/delete`
- `/trade/journal/media/cleanup`

SQLite:

- `trade_events`
- `trade_event_media`
- `idx_trade_events_created`
- `idx_trade_event_media_event`

---

## 3. Condensed Completed History

Stage 1 added the dedicated fourth rail column, collapse/resize state, static markup, and `ensurePriceChartDom()` rebuild parity.

Stage 2 added `/trade_chain` and `build_trading_chain_payload(...)` to normalize cached Schwab chain contracts without creating a new fetch loop or reconstructing symbols.

Stage 3 added read-only linked accounts and relevant positions with masked labels and no raw account-number leakage.

Stage 4 added preview-only single-leg option `DAY LIMIT` orders with strict cached-contract validation, server-built Schwab order JSON, preview tokens, and `SELL_TO_CLOSE` position validation.

Stage 5 added guarded live placement behind `ENABLE_LIVE_TRADING=1`, exact successful-preview binding, final confirmation, safe Schwab response handling, and no bracket/OCO behavior.

Stage 6 added read-only orders, selected-contract filtering, and confirmed cancel support.

Stage 7 added planning-only Bracket Plan helpers, custom localStorage templates, premium/underlying helper math, risk sizing, and opt-in chart-click reference capture for helper calculations only.

Journal slices added local `trade_events`, editable notes/tags/setup/thesis/outcome, manual notes, cancel journal events, rail/workspace review UI, conservative P/L display, and local screenshot attachment support for successful sidebar live placements only.

Active Trader added a collapsible fast scalping surface and the preview-mandatory armed auto-send workflow while preserving all backend live-order guards.

The latest UI pass made Contract Helper candidate boxes actionable, added quick exact-contract buttons, and reordered the rail visually for fast SPY scalping without changing Schwab preview/place payloads.

---

## 4. Verification

Run after trading rail or journal changes:

```bash
python3 -m py_compile ezoptionsschwab.py
git diff --check
python3 -m unittest tests.test_session_levels tests.test_trade_preview
```

If frontend JS changes, also syntax-check rendered inline JS:

```bash
python3 -c "import re, pathlib, ezoptionsschwab as m; html=m.app.test_client().get('/').get_data(as_text=True); scripts=re.findall(r'<script[^>]*>(.*?)</script>', html, re.S|re.I); pathlib.Path('/tmp/gex-inline-scripts.js').write_text('\n;\\n'.join(scripts)); print('scripts', len(scripts))"
node --check /tmp/gex-inline-scripts.js
```

Use `http://127.0.0.1:5014/` for browser smoke checks on this branch. Avoid replacing an existing `5001` process unless explicitly requested.

Manual smoke targets:

- Active Trader collapse/expand.
- Buy Ask / Sell Bid stage ticket when Auto-send is off.
- Armed Auto-send blocks when the ticket no longer matches preview.
- Helper Call / Put candidate boxes select only exact cached contracts.
- Quick buttons select ATM and +1/+2 OTM contracts from cached `/trade_chain`.
- Selected Contract, Order Ticket, and Active Trader update together after selection.
- Position and order refresh still work for selected account/contract.
- Screenshot attachment remains async after successful live placement and does not affect live-order success display.
- Rail rebuild through ticker/timeframe refresh does not drop helper, quick buttons, Active Trader, or Journal controls.

---

## 5. 2026-05-02 Fast Selection Update

Accomplished:

- Moved Contract Helper into its own fast-selection panel directly below Active Trader.
- Made the helper Call and Put candidate boxes actionable.
- Added quick exact-contract buttons for `ATM Call`, `ATM Put`, `+1 OTM Call`, `+1 OTM Put`, `+2 OTM Call`, and `+2 OTM Put`.
- Reordered the visible rail for faster 0-1 DTE scalping: Active Trader, helper/quick contracts, Position, Selected Contract, Order Ticket, Preview/Place, Contract Picker, Orders, Bracket Plan, Journal.
- Preserved static HTML and `buildTradeRailHtml()` parity for every new rail element.
- Preserved exact Schwab/OCC `contract_symbol` selection only. No option symbols are reconstructed.
- Preserved preview invalidation when helper/quick selection changes the selected contract.
- Browser-smoked against real cached SPY chain/account/order data on `http://127.0.0.1:5014/`:
  - quick `+1 OTM Put` selected the cached `719P`;
  - helper Call selected the cached `722C`;
  - account refresh returned masked `Account *8805`;
  - Position and Orders summaries refreshed;
  - Buy Ask staged the ticket with Auto-send off;
  - armed Auto-send displayed the armed state and blocked live send because the selected quote was stale.

Tricky parts:

- The helper candidates come from `compute_contract_helper(...)`, while the trading rail can only trade contracts present in the cached `/trade_chain` payload. Candidate clicks therefore check that exact symbol is present in the cached picker before selecting it.
- Quick contracts use the same side-aware anchor as the contract ladder: calls anchor at the nearest strike at/above spot; puts anchor at the nearest strike at/below spot. Offsets are then taken from that side's ordered cached rows.
- The Active Trader ladder currently displays price rows and best-effort working-order counts, but it does not yet render a TOS-style order marker with an inline cancel `x`.
- The Active Trader template selector currently chooses the planning template, but the editable bracket rows still live lower in Bracket Plan. The TOS screenshots show template rows editable directly in the Active Trader area, which is the next UX gap.
- Browser smoke proved the armed-state stale-quote block. A true successful armed live-send path still needs either a carefully controlled live test or a mocked browser path, because live Schwab placement is intentionally guarded.

Still left to do before PR/merge:

- Finish a TOS-style Active Trader ladder experience:
  - show the user's working order directly on the ladder at its price level;
  - make the marker visually distinct from bid/ask/limit rows;
  - add an inline `x` on that ladder marker to cancel the selected Schwab order;
  - keep cancel explicitly confirmed unless the user approves a lower-friction cancel model;
  - refresh positions/orders after ladder cancel.
- Let the ladder itself stage trades:
  - clicking a ladder price should set the ticket limit at that price;
  - with Auto-send off, ladder clicks should stage only and require preview;
  - with Auto-send armed, ladder clicks must still obey the current preview-mandatory safety model unless a different model is explicitly approved later.
- Move or mirror bracket-template editing into Active Trader:
  - expose enabled rows, quantity link, TRG/LIMIT/STOP labels, offsets, and TIF close to the template selector;
  - keep the existing Bracket Plan as the richer lower-panel editor if useful;
  - keep all bracket/template behavior planning-only and out of Schwab preview/place payloads.
- Run one final PR-readiness sweep:
  - check static HTML / `buildTradeRailHtml()` parity;
  - check `buildTradeJournalWorkspaceHtml()` parity;
  - check no unrelated analytics/chart behavior changed;
  - browser-smoke open/close/resize/rebuild paths;
  - rerun compile, diff check, unit tests, and rendered inline JS node check;
  - prepare the branch for a PR and merge to `main`.

---

## 6. Latest Continuation Prompt

```text
We are in /Users/scottmunger/Desktop/Trading/Dashboards/GEX-Dashboard on branch codex/options-trading-rail-plan.

Read AGENTS.md first, then read:
- docs/OPTIONS_TRADING_RAIL_IMPLEMENTATION_PLAN.md
- docs/OPTIONS_TRADING_RAIL_UI_POLISH_PLAN.md

Before editing, run:
git branch -a
git log --oneline main..HEAD
git status --short

Continue only the dedicated fourth order-entry trading rail and Journal surfaces:
- #trade-rail-header
- #trade-rail
- .trade-rail-shell
- #trade-journal-workspace
- Active Trader
- Contract Helper / quick contract buttons
- Position / Contract Picker / Selected Contract / Order Ticket / Bracket Plan / Preview / Orders / Journal panels

Current state:
- Active Trader v1 exists as a collapsible fast surface at the top of the fourth rail.
- Auto-send is off by default, not persisted, and still preview-mandatory.
- Armed fast buttons can live-send only the already-previewed exact single-leg DAY LIMIT order.
- /trade/place_order still requires ENABLE_LIVE_TRADING=1, confirmed=true, exact recent preview token, exact cached Schwab contract, unchanged order JSON, and SELL_TO_CLOSE position caps.
- Bracket Plan remains planning-only and must not alter Schwab preview/place payloads.
- Screenshot attachments and Journal media controls exist; screenshot failure must not make live order appear failed.
- Helper candidate boxes and quick contract buttons select exact cached Schwab/OCC contracts only.
- Browser smoke with real cached SPY chain/account/order data confirmed quick +1 OTM Put selection, helper Call selection, masked account refresh, position/order summaries, Buy Ask staging, and armed stale-quote blocking.
- Static HTML and JS rebuild helpers must stay in parity.

Main next work:
1. Finish the TOS-style Active Trader ladder so this branch can become PR-ready:
   - show working orders directly on the ladder at their price level;
   - add a clear order marker with an inline x to cancel from the ladder;
   - keep cancel explicitly confirmed unless the user explicitly approves lower-friction cancel behavior;
   - refresh positions/orders after ladder cancel;
   - do not make the marker or cancel path alter preview/place payloads.
2. Let the ladder itself stage trades:
   - clicking a ladder price sets the ticket limit at that price;
   - with Auto-send off, ladder clicks stage only and require preview;
   - with Auto-send armed, ladder clicks still must obey the current preview-mandatory live-send model unless a different model is explicitly approved.
3. Move or mirror bracket-template editing into Active Trader, inspired by the TOS screenshots:
   - expose enabled bracket rows, quantity link, TRG/LIMIT/STOP labels, offset values, and TIF near the Active Trader template selector;
   - keep the lower Bracket Plan as a richer editor if useful;
   - keep Bracket Plan/template behavior planning-only and do not alter Schwab preview/place payloads.
4. Do one final PR-readiness sweep:
   - verify static HTML and buildTradeRailHtml() parity;
   - verify buildTradeJournalWorkspaceHtml() parity;
   - verify rail collapse/resize/rebuild behavior;
   - browser-smoke Active Trader, quick contract selection, account/position/orders, Preview, armed blocking, Journal, and screenshot attachment failure isolation;
   - rerun compile, diff check, unit tests, and rendered inline JS node check;
   - get the branch into shape to open a PR and merge with main.
5. Improve deterministic closed-trade P/L only if order/position lifecycle data proves entry, exit, quantities, and prices reliably.

Safety constraints:
- Do not silently enable Auto-send.
- Do not add previewless live placement unless explicitly approved.
- Do not bypass ENABLE_LIVE_TRADING=1, cached contract validation, preview-token binding, or SELL_TO_CLOSE caps.
- Do not make Bracket Plan alter Schwab payloads.
- Do not implement live Schwab bracket/OCO child orders, SPX-specific validation, multi-leg spreads, chart/alert/flow automated trading, or automatic screen recordings.

Run after changes:
python3 -m py_compile ezoptionsschwab.py
git diff --check
python3 -m unittest tests.test_session_levels tests.test_trade_preview

If frontend JS changes, also run the rendered inline JS node check from this plan.
```
