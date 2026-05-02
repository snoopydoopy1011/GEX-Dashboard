# GEX Dashboard — Options Trading Rail Implementation Plan

**Status:** Journal persistence/editor added; Journal toggle/open bug queued; live bracket support later
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

### 2026-05-01 Progress Update

Stage 1 is complete on branch `codex/options-trading-rail-plan`.

Accomplished:

- Added a fourth `.chart-grid` column for a dedicated trading rail using `--trade-rail-w`.
- Added initial read-only trading rail DOM in the server-rendered HTML:
  - `#trade-rail-header`
  - `#trade-rail-collapse-toggle`
  - `#trade-rail-resize-handle`
  - `#trade-rail`
- Added a dense placeholder rail shell for contract picker, selected contract, order ticket, and disabled preview state.
- Added independent localStorage-backed collapse and resize state:
  - `gex.tradeRailCollapsed`
  - `gex.tradeRailWidthPx`
- Mirrored the new rail in the `ensurePriceChartDom()` rebuild path with `buildTradeRailHtml()` and `ensureTradeRailDom()`.
- Updated `showPriceChartUI()` so ticker/timeframe refresh paths do not leave trade rail elements hidden.
- Kept the rail collapsed by default.
- Added responsive placement so the trade rail stacks below the analytics rail on narrow screens.
- Did not add account, order preview, order placement, or live trading endpoints.

Tricky parts / implementation notes:

- `ensurePriceChartDom()` is a defensive rebuild path. Any trade rail element that exists in initial HTML also had to be recreated there or future chart rebuilds could drop the rail.
- The analytics rail collapse button is absolutely positioned. Its `right` offset now accounts for `--trade-rail-w` so it remains reachable when both rails are open.
- Existing strike rail and analytics rail width clamps needed to subtract `--trade-rail-w`; otherwise resizing one rail could squeeze the chart more than intended.
- Port `5001` was briefly restarted for verification, then released so local terminal workflows can own it again.

Verification completed:

- `python3 -m py_compile ezoptionsschwab.py` passed. The existing `render_template_string` invalid escape `SyntaxWarning` remains unchanged.
- `git diff --check` passed.
- Local HTTP smoke test confirmed the page served the new trade rail markup and default collapsed state.

### 2026-05-01 Stage 2 Progress Update

Stage 2 is complete on branch `codex/options-trading-rail-plan`.

Accomplished:

- Added `build_trading_chain_payload(...)`, a read-only normalizer for cached `_options_cache[ticker]` calls/puts.
- Added `POST /trade_chain`, which returns cached contracts without account lookup, order preview, or live trading behavior.
- Preserved Schwab-returned `contractSymbol` exactly as `contract_symbol`; no symbol reconstruction was introduced.
- Added trading rail call/put controls, expiry filtering, strike-range filtering, and a compact selectable contract table.
- Defaulted the backend payload to the nearest cached expiry when no requested expiry is available, and to strikes near spot using the requested range.
- Added a selected contract summary with bid, ask, mid/mark, last, spread, volume, OI, IV, delta, quote time, and trade time.
- Added stale quote and wide spread warnings from the cached quote fields.
- Mirrored the new trade rail DOM in `buildTradeRailHtml()` so `ensurePriceChartDom()` rebuilds keep the picker intact.

Tricky parts / implementation notes:

- `/trade_chain` intentionally returns `409` when the normal `/update` chain path has not populated `_options_cache`; it does not create a new Schwab chain fetch loop.
- The rail fetches the cached payload only when opened or after a successful normal chain update while the rail is open.
- The UI keeps Stage 2 read-only: ticket presets remain disabled and no account, preview, placement, or live trading endpoints were added.

Verification completed:

- `python3 -m py_compile ezoptionsschwab.py` passed. The existing `render_template_string` invalid escape `SyntaxWarning` remains unchanged.
- Flask test-client smoke test populated `_options_cache` with sample SPY calls/puts and confirmed `/trade_chain` returned status `200`, two contracts, and the exact sample Schwab contract symbol.

Next:

- Stage 4 should add preview-only single-leg limit order plumbing with strict server-side validation. Do not add live placement yet.

### 2026-05-01 Stage 3 Progress Update

Stage 3 is complete on branch `codex/options-trading-rail-plan`.

Accomplished:

- Added `GET /trade/accounts`, using `client.linked_accounts()` and returning only account hashes plus masked/display labels.
- Added `POST /trade/account_details`, using `client.account_details(accountHash, fields='positions')`.
- Added conservative account detail normalization that omits plain account numbers and returns selected balance fields plus positions relevant to the selected ticker/contract.
- Added an Account panel to the trading rail with account refresh/selection, masked selected-account context, and buying power display.
- Added a Position panel that refreshes for the selected account and current option contract/ticker.
- Mirrored all new trading rail DOM in `buildTradeRailHtml()` so `ensurePriceChartDom()` rebuilds preserve the account and position panels.
- Kept Stage 2 behavior intact: `/trade_chain` still reads from cached `_options_cache` only and does not create a new Schwab chain fetch loop.
- Added no order preview, place order, cancel, replace, or live trading endpoints.

Tricky parts / implementation notes:

- Schwab linked-account responses can include plain `accountNumber`, so the normalizer never returns it to the browser; it uses `hashValue` for API calls and a masked label for display.
- Account details responses can include full account structures, so the endpoint builds a narrow response instead of forwarding Schwab JSON.
- Position matching prioritizes exact selected contract symbol matches, then ticker/underlying matches, so selected contract positions float to the top while related underlying positions can still be seen.

Verification completed:

- `python3 -m py_compile ezoptionsschwab.py` passed. The existing `render_template_string` invalid escape `SyntaxWarning` remains unchanged.
- `git diff --check` passed.
- Flask test-client smoke tests mocked Schwab linked accounts/account details and confirmed masked account labels, no raw account number leakage, relevant position filtering, and clear token/client error handling.

### 2026-05-01 Stage 4 Progress Update

Stage 4 is complete on branch `codex/options-trading-rail-plan`.

Accomplished:

- Added a server-side single-leg option DAY LIMIT order builder for preview-only orders.
- Added `POST /trade/preview_order`, with no live placement endpoint and no cancel/replace behavior.
- Restricted preview inputs to cached OPTION contracts, `LIMIT`, `DAY`, `NORMAL`, `SINGLE`, `BUY_TO_OPEN`, and `SELL_TO_CLOSE`.
- Required selected contract symbols to exist exactly in the latest cached `_options_cache` trading-chain data.
- Preserved Schwab-returned `contractSymbol`; no option symbol reconstruction was introduced.
- Added preview token/order hash generation that binds account hash, ticker, and the exact order JSON for Stage 5.
- Added the order ticket UI with Buy Ask / Sell Bid action buttons, quantity, limit price, bid/mid/ask/mark quick fills, debit/credit estimate, max-risk estimate, and Schwab preview response display.
- Invalidated the prior preview when contract, account, action, quantity, limit price, expiry, strike range, or option type changes.
- Added `SELL_TO_CLOSE` server validation against the selected-contract long position by reading account positions before preview.
- Redacted plain account-number fields from Schwab preview response bodies before returning them to the browser.

Tricky parts / implementation notes:

- The trading rail DOM exists in both initial HTML and `buildTradeRailHtml()` because `ensurePriceChartDom()` can rebuild the rail after chart DOM churn.
- The UI defaults to the user's directional workflow: `BUY_TO_OPEN` quick-fills ask and `SELL_TO_CLOSE` quick-fills bid, while still exposing bid/mid/ask/mark presets.
- Preview failures from Schwab are surfaced as preview responses; they still do not enable any live order path.

Verification completed:

- `python3 -m py_compile ezoptionsschwab.py` passed. The existing `render_template_string` invalid escape `SyntaxWarning` remains unchanged.
- `git diff --check` passed.
- Flask test-client smoke tests for `/trade/preview_order` confirmed missing account rejection, missing/unknown contract rejection, invalid quantity/price/action rejection, expected Schwab preview payload for `BUY_TO_OPEN`, `SELL_TO_CLOSE` position validation, and account-number redaction.
- `python3 -m unittest tests.test_session_levels tests.test_trade_preview` passed.

### 2026-05-01 Stage 5 Progress Update

Stage 5 is complete on branch `codex/options-trading-rail-plan`.

Accomplished:

- Added guarded live single-leg placement via `POST /trade/place_order`.
- Kept live placement behind `ENABLE_LIVE_TRADING=1`; without the flag, placement returns a hard rejection and never calls Schwab.
- Added an in-memory successful-preview record store with a short TTL so placement requires a recent successful Schwab preview.
- Bound placement to the exact previewed account hash, ticker, contract symbol, action, quantity, limit price, preview token/order hash, and order JSON.
- Required explicit frontend final confirmation before posting a live placement request.
- Handled Schwab `place_order(accountHash, order)` responses, including `201 Created` with a `Location` header and no JSON body.
- Returned safe placement metadata only: status, location, token hash, ticker, contract symbol, instruction, quantity, limit price, and redacted Schwab response body.
- Added clear trading rail success/error state plus a disabled-until-preview `Place Live Order` button.
- Preserved the directional quick-fill workflow: `BUY_TO_OPEN` fills ask and `SELL_TO_CLOSE` fills bid.

Tricky parts / implementation notes:

- Stage 4's preview token was deterministic, so Stage 5 adds server-side memory for successful previews. A token alone is not enough to prove Schwab accepted a preview recently.
- The place endpoint rebuilds the order from current request fields, compares it to the saved preview order, and also rejects a submitted `order` JSON body if it differs from preview.
- The endpoint remains registered so the UI and tests receive a clear feature-flag rejection, but it is inert unless `ENABLE_LIVE_TRADING=1`.
- This stage still does not add cancel/replace, order lists, spread tickets, bracket orders, alert-triggered trading, chart-click trading, or any analytical formula changes.

Verification completed:

- `python3 -m py_compile ezoptionsschwab.py` passed. The existing `render_template_string` invalid escape `SyntaxWarning` remains unchanged.
- `git diff --check` passed.
- `python3 -m unittest tests.test_session_levels tests.test_trade_preview` passed.
- Flask test-client smoke tests for `/trade/place_order` confirmed feature-flag rejection, missing preview-token rejection, stale preview rejection, changed account/contract/action/quantity/price rejection, changed order JSON rejection, missing final confirmation rejection, exact previewed-order placement, `201 Created` plus `Location` handling, and no plain account-number exposure.

### 2026-05-01 Stage 6 Progress Update

Stage 6 is complete on branch `codex/options-trading-rail-plan`.

Accomplished:

- Added `POST /trade/orders`, using `client.account_orders(accountHash, fromEnteredTime, toEnteredTime, maxResults=None, status=None)` for read-only open/recent order lookup.
- Added conservative order normalization that returns safe metadata only: order id, status, entered/close time, order type/session/duration/price, quantity fields, cancelability, and leg summaries.
- Filtered returned orders by selected contract when possible, falling back to ticker/underlying matching.
- Added an Orders panel to the trading rail with refresh, open/recent order display, and cancel buttons only for cancelable statuses.
- Added `POST /trade/cancel_order`, gated by explicit confirmation and scoped to the selected account hash plus selected order id. No replace support was added.
- Refreshed positions and orders after successful placement, after successful cancel, and when the user clicks the rail refresh controls.
- Preserved Stage 5 live placement guards and the Buy Ask / Sell Bid quick-fill behavior.

Tricky parts / implementation notes:

- Schwab order responses may include plain account numbers or full order structures, so the endpoint never forwards raw order JSON and redacts before normalizing.
- Cancel is live account state, so the UI requires `window.confirm(...)` and the backend requires `confirmed: true`; the backend calls only `cancel_order(accountHash, orderId)`.
- The new Orders DOM is mirrored in both initial HTML and `buildTradeRailHtml()` so chart rebuilds keep the panel.

Verification completed:

- `python3 -m py_compile ezoptionsschwab.py` passed.
- `git diff --check` passed.
- `python3 -m unittest tests.test_session_levels tests.test_trade_preview` passed.
- Flask test-client smoke tests for `/trade/orders` and `/trade/cancel_order` confirmed missing account rejection, safe Schwab-client errors, no plain account-number exposure, selected contract filtering, explicit cancel confirmation, and cancel scoping to selected account hash/order id.

### 2026-05-01 Stage 7 Progress Update

Stage 7 planning helpers are complete on branch `codex/options-trading-rail-plan`.

Accomplished:

- Added a planning-only `Bracket Plan` panel to the dedicated fourth trading rail.
- Mirrored the new bracket planning DOM in both static HTML and `buildTradeRailHtml()` so `ensurePriceChartDom()` rebuilds keep the panel.
- Added Thinkorswim-style template choices:
  - `Single`
  - `OCO`
  - `TRG w/ bracket`
  - `TRG w/ 2 brackets`
  - `TRG w/ 3 brackets`
  - simple preset offset templates like `+1.00/-1.00`, `+2.00/-2.00`, and `Scalp`
- Added localStorage-backed default template saving with `gex.tradeBracketDefault`.
- Added bracket planning rows that show enabled row number, target limit, stop, `DAY` TIF, and quantity-link style allocation.
- Added premium-based target/stop helpers using the selected option premium or current ticket limit.
- Added underlying-reference helpers that can use the selected contract delta as a rough premium estimate from a reference level.
- Added risk-budget sizing with a manual `Use Risk Size` button.
- Added opt-in chart-click reference behavior: chart clicks only fill the helper underlying reference when `Chart click sets helper reference` is checked.

Tricky parts / implementation notes:

- The bracket panel is intentionally planning-only. It does not alter `/trade/preview_order`, `/trade/place_order`, `build_single_option_limit_order()`, Schwab order JSON, live trading gates, or final confirmation behavior.
- Chart clicks are routed through the existing `tvHandleChartClick()` path. The helper reference is only captured when no drawing mode is active and the explicit chart-reference checkbox is enabled, so drawing tools and indicator/AVWAP click behavior keep priority.
- Cheap contracts can clamp the stop helper to `0.01` when the stop offset exceeds the option premium. This is expected for the current helper math, but later UX may want per-template defaults that scale down for low-premium contracts.
- Bracket quantity rows currently split the visible ticket quantity across planned brackets for display only; no child orders are created.
- Default template saving is local to the browser via localStorage. Server-side/user-profile template persistence is still deferred.

Verification completed:

- `python3 -m py_compile ezoptionsschwab.py` passed. The existing `render_template_string` invalid escape `SyntaxWarning` remains unchanged.
- `git diff --check` passed.
- `python3 -m unittest tests.test_session_levels tests.test_trade_preview` passed.
- Manual screenshot review confirmed the bracket planning UI renders in the trading rail, shows the planning-only warning, and keeps Preview/Place separate.

### 2026-05-01 Position Card Polish Update

Position card polish is complete on branch `codex/options-trading-rail-plan`.

Accomplished:

- Reworked the trading rail Position rows to reduce vertical space and remove the duplicated full contract-name display.
- Made the parsed contract identifier primary with compact option-side pills such as `724C` / `724P`.
- Styled call pills with `var(--call)` and put pills with `var(--put)` while keeping color usage on existing tokens.
- Converted `Qty`, `Mkt`, and `Day` values into compact inline metric pills.
- Added positive/negative coloring for Day P/L while preserving the existing position data source and calculations.
- Preserved the full Schwab/OCC contract symbol as a row hover title instead of showing it as a third visible line.

Tricky parts / implementation notes:

- Position rows are rendered dynamically by `renderTradePositions()`, so no static HTML or `buildTradeRailHtml()` markup change was required. Static/rebuild parity was still checked before editing.
- The change is visual only. It does not alter account lookup, position normalization, selected-contract matching, `SELL_TO_CLOSE` caps, order preview, order placement, or Schwab payloads.
- The row layout wraps metric pills when the rail is narrow so long values do not overflow the dedicated fourth trading rail.

### 2026-05-01 Bracket Template + Journal Starter Update

Bracket template customization and the first local journal slice are complete on branch `codex/options-trading-rail-plan`.

Accomplished:

- Refined the planning-only Bracket Plan helper for cheap contracts so premium-mode stops do not silently pin at `0.01`; display offsets are scaled for low-premium planning while Schwab preview/place JSON remains unchanged.
- Added a manual `Scale Cheap` action that derives tighter target/stop offsets from the selected option premium.
- Added localStorage-only custom bracket templates with create/update/delete controls:
  - custom template store: `gex.tradeBracketTemplates`
  - default template store remains: `gex.tradeBracketDefault`
- Added a dashboard `Journal` button/view inside the dedicated fourth trading rail.
- Added a local SQLite `trade_events` table and index for deterministic rail trade events.
- Auto-recorded successful preview and successful placement events with safe rail metadata, order hash, selected contract context, and a planning-only bracket snapshot when provided.
- Added `GET /trade/journal` for read-only retrieval of recent local rail trade events.
- Extended `tests/test_trade_preview.py` with local temporary-DB coverage for preview and placement journal writes.
- Did not add screenshot or screen-recording capture.

Tricky parts / implementation notes:

- `sqlite_connect()` now resolves `DB_PATH` at call time instead of as a default argument. This lets tests swap in a temporary DB and avoids writing journal test rows to the real `options_data.db`.
- The journal records only successful Schwab preview responses and successful live placement responses. Failed previews/placements are not persisted yet, so the first journal slice stays deterministic and avoids clutter.
- Bracket snapshots are sent beside the preview/place requests as `bracket_plan`, but the backend ignores them for Schwab order construction. They are journal metadata only.
- Static trading rail HTML and `buildTradeRailHtml()` were both updated for the Journal panel and bracket-template controls so `ensurePriceChartDom()` rebuilds preserve the UI.
- Custom bracket templates are browser-local only. They are not in SQLite and are not synced across browsers/users.
- Cheap-contract scaling is a display/planning helper. It adjusts helper output rows and risk-size math, not the ticket limit, preview payload, live payload, or Schwab child orders.

Safety notes:

- Bracket Plan remains planning-only and does not alter `/trade/preview_order`, `/trade/place_order`, `build_single_option_limit_order()`, live-trading gates, final confirmation behavior, or Schwab order JSON.
- The Journal records deterministic order-entry events only. It does not trigger orders, automate chart clicks, or start media capture.

Still left to do:

- Polish the Journal view into a fuller in-dashboard workflow: filters, selected-event detail drawer, notes/tags, edit/delete for journal notes, and clearer entry/exit grouping.
- Decide whether failed previews/place attempts should be journaled as rejected events, and if so add explicit visual separation from successful trade events.
- Add journal capture for order cancels if useful, with the same explicit-confirmation and safe metadata rules.
- Add realized/marked P/L enrichment from positions/orders when deterministic enough.
- Add screenshot/screen-recording attachment only after explicit opt-in UI, clear storage controls, and no automatic capture.
- Keep live Schwab bracket/OCO child orders, SPX-specific validation, multi-leg spreads, and alert/chart-click automated trading out of scope until explicitly approved.

Verification completed:

- `python3 -m py_compile ezoptionsschwab.py` passed. The existing `render_template_string` invalid escape `SyntaxWarning` remains unchanged.
- `git diff --check` passed.
- `python3 -m unittest tests.test_session_levels tests.test_trade_preview` passed.

### 2026-05-02 Trading Journal Build-out Update

The second journal slice is complete on branch `codex/options-trading-rail-plan`.

Accomplished:

- Extended the existing local SQLite `trade_events` table with editable journal annotation fields:
  - `journal_status`
  - `journal_tags`
  - `journal_setup`
  - `journal_thesis`
  - `journal_notes`
  - `journal_outcome`
  - `updated_at`
- Added safe schema migration via `ALTER TABLE ... ADD COLUMN` inside `init_db()` so existing `options_data.db` files keep prior journal rows.
- Added `POST /trade/journal/update` for editing annotations on persisted local rail events.
- Kept automatic journal writes attached to successful Schwab previews and successful live placements only.
- Defaulted successful preview events to `planned` and successful placement events to `open`.
- Upgraded the fourth trading rail Journal panel from a starter list into a compact event list with status pills, setup/tags display, and an `Edit` action.
- Added a journal editor dialog/modal with status, tags, setup, thesis, notes, and outcome fields.
- Mirrored the Journal list/editor markup in both static `#trade-rail` HTML and `buildTradeRailHtml()` so `ensurePriceChartDom()` rebuilds keep the UI.
- Extended `tests/test_trade_preview.py` with editable journal annotation coverage and missing-event rejection coverage.
- Did not add screenshot/screen-recording capture, delete behavior, imports, exports, or any automated trading behavior.

Tricky parts / implementation notes:

- The journal is intentionally built on the existing `trade_events` table rather than introducing a second database or a TradeNote-style architecture.
- Journal annotations are local-only SQLite fields. The Schwab preview/place order payloads remain unchanged.
- `bracket_plan` remains journal metadata only. It still does not alter `build_single_option_limit_order()`, `/trade/preview_order`, `/trade/place_order`, live-trading gates, or Schwab order JSON.
- The editor state is held in the existing vanilla JS `tradeRailState`; no framework was introduced.
- Static/rebuild parity remains critical: any future journal controls under the fourth rail must exist in both initial Flask-rendered HTML and `buildTradeRailHtml()`.

Known issue / next-session bug:

- The user reported that clicking the `Journal` button in the in-app browser did not open anything. That is not expected behavior.
- Do not assume the latest smoke result proves the bug fixed. Next session should reproduce from the user's current `http://127.0.0.1:5014/` browser state, verify whether the rail is collapsed, whether `[data-trade-journal-toggle]` is visible/clickable, whether `wireTradeRailPickerControls()` bound the listener, and whether `renderTradeJournal()` toggles `.trade-journal-panel.visible`.
- Also verify the server on port `5014` is serving the current working tree. A stale Flask process can serve older rail DOM even when the file has been patched.

Still left to do:

- Fix the Journal button/toggle open behavior if reproducible.
- Add richer journal filtering/search by status, ticker, contract, tags, and event type.
- Add grouped lifecycle views that connect previewed, placed, canceled, closed/reviewed, and manually annotated events when deterministic.
- Consider adding cancel-order journal events with safe metadata and explicit-confirmation semantics.
- Add realized/marked P/L enrichment from positions/orders only when deterministic enough.
- Add screenshot/screen-recording attachments only after explicit opt-in UI and clear storage controls; do not start capture automatically.
- Deferred/non-goals remain unchanged: no live Schwab bracket/OCO child orders, no SPX-specific validation, no multi-leg spreads, no automated trading from chart clicks/alerts/flow, and no analytical formula changes.

Verification completed:

- `python3 -m py_compile ezoptionsschwab.py` passed. The existing `render_template_string` invalid escape `SyntaxWarning` remains unchanged.
- `git diff --check` passed.
- `python3 -m unittest tests.test_session_levels tests.test_trade_preview` passed.
- Browser smoke on `http://127.0.0.1:5014/` confirmed the served current tree contains the Journal modal/editor DOM after restarting a stale `5014` process. No screenshots or screen recordings were taken.

### 2026-05-01 Order Rail Annotation Polish Update

Trading rail annotation polish is complete on branch `codex/options-trading-rail-plan`.

Accomplished:

- Moved the Contract Helper out of the price chart toolbar and into the top of the trading rail Contract Picker card.
- Added a compact/expanded Contract Helper toggle stored locally under `gex.tradeHelperCompact`; compact mode keeps the helper visible but reduces it to a small summary.
- Removed duplicated selected-contract identity text from Selected Contract. The visible identity is now the call/put strike pill plus a DTE pill; the full Schwab/OCC symbol is retained as hover context, not as another visible line.
- Added a Position panel collapse/expand toggle stored locally under `gex.tradePositionCollapsed`.
- Added a `Use` pill on each relevant Position row. Clicking it selects that exact cached contract into Selected Contract and the Order Ticket, invalidates preview, and does not preview, stage, submit, or otherwise automate an order.
- Kept Contract Helper, Position, and Selected Contract static HTML in parity with `buildTradeRailHtml()` where applicable.

Tricky parts / implementation notes:

- The Contract Helper now lives inside Contract Picker, but it still uses global `data-met="contract_*"` rendering via `renderContractHelper()`. Static HTML and `buildTradeRailHtml()` both need the same helper nodes or chart/ticker rebuilds can drop the moved helper.
- Position row `Use` buttons must select only exact cached Schwab `contract_symbol` rows. If the held position is not currently in the cached picker payload, the UI should ask for a chain refresh or wider range instead of reconstructing symbols.
- Selected Contract intentionally has one visible identity path only: the side-colored strike pill plus DTE pill. The full OCC/Schwab symbol remains available via hover title and internal state.
- The annotation pass intentionally did not touch `build_single_option_limit_order()`, `/trade/preview_order`, `/trade/place_order`, or Schwab preview/place payload construction.

Safety notes:

- Position-row `Use` is selection-only. It does not bypass preview-required/live-trading/final-confirmation guards and does not change Schwab order JSON.
- Selected Contract remains backed by the exact Schwab-returned cached `contract_symbol`; no option symbol reconstruction was introduced.
- Use `http://127.0.0.1:5014/` for browser smoke checks in this branch. Avoid spending time probing or replacing an existing `5001` process unless the user explicitly asks.

Still left to do:

- Polish the Contract Picker option-chain rows into a cleaner/sleeker rail table.
- Add IV and delta columns/fields to the Contract Picker rows while keeping bid/mid/ask and liquidity visible at narrow rail widths.
- Keep option-chain row click behavior selection-only and preserve preview invalidation when the selected contract changes.
- Re-check horizontal overflow after adding IV/delta because the rail is narrow and native scrollbars consume width.

Verification completed:

- `python3 -m py_compile ezoptionsschwab.py` passed. The existing `render_template_string` invalid escape `SyntaxWarning` remains unchanged.
- `git diff --check` passed.
- `python3 -m unittest tests.test_session_levels tests.test_trade_preview` passed.

Continuation prompt for next Codex session:

```text
We are in /Users/scottmunger/Desktop/Trading/Dashboards/GEX-Dashboard on branch codex/options-trading-rail-plan.

Read AGENTS.md first, then read docs/OPTIONS_TRADING_RAIL_IMPLEMENTATION_PLAN.md and docs/OPTIONS_TRADING_RAIL_UI_POLISH_PLAN.md. Before editing, run:
git branch -a
git log --oneline main..HEAD
git status --short

Continue only the dedicated fourth order-entry trading rail:
- #trade-rail-header
- #trade-rail
- .trade-rail-shell
- Position / Contract Picker / Selected Contract / Order Ticket / Bracket Plan / Preview / Orders / Journal panels

Current state:
- Trading rail has preview-only and guarded live single-leg DAY LIMIT option orders.
- Bracket Plan is planning-only and must not alter Schwab preview/place payloads.
- Custom bracket templates are localStorage-only under gex.tradeBracketTemplates; default template is gex.tradeBracketDefault.
- Cheap-contract helper scaling exists and is display/planning-only.
- Contract Helper lives at the top of Contract Picker, has a compact/expanded localStorage toggle, and no longer appears in the price chart toolbar.
- Selected Contract should show one visible identity path only: call/put strike pill plus DTE pill.
- Position panel has a hide/show localStorage toggle, and each position row has a selection-only Use pill that can populate Selected Contract / Order Ticket from a cached exact contract.
- Journal button/view exists inside the trading rail.
- SQLite trade_events table stores deterministic successful previewed_order and placed_order events.
- GET /trade/journal returns recent local journal events.
- tests/test_trade_preview.py covers preview/place guards plus journal persistence through a temporary DB.

Next sensible scope:
1. Polish the Contract Picker option-chain rows into a cleaner/sleeker rail table and add IV + delta display alongside bid/mid/ask and liquidity. This is UI-only: keep row clicks selection-only, preserve exact cached Schwab contract symbols, and invalidate preview on selection changes.
2. Polish the Journal panel UI: add event detail display, filters, and compact rail-friendly rows.
3. Add optional manual journal notes/tags stored locally in SQLite, without recording screenshots/video.
4. Consider adding cancel_order journal events, preserving explicit confirmation and safe metadata.
5. Add tests in tests/test_trade_preview.py or a new focused test file if journal behavior grows beyond order preview/place.

Do not implement without explicit approval:
- Live Schwab bracket/OCO child orders.
- SPX-specific validation.
- Multi-leg spreads.
- Automated trading from chart clicks, alerts, or flow.
- Automatic screenshots/screen recordings without explicit opt-in and storage controls.

Safety constraints:
- Do not make chart-click submit, preview, stage, or select an order automatically.
- Do not bypass preview-required/live-trading/final-confirmation guards.
- Do not change existing single-leg Schwab order JSON unless explicitly required and covered by tests.
- Preserve ENABLE_LIVE_TRADING=1 gating and final confirmation behavior.
- Preserve SELL_TO_CLOSE position caps.
- Static HTML and buildTradeRailHtml() must stay in parity.
```

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
- Live bracket orders.
- Live OCO / trigger orders.
- Bracket/OCO templates beyond planning-only UI.
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

### Stage 7 — Planning Helpers Complete / Later Live Support

Implemented planning-only work:

- Bracket/OCO planning UI.
  - Model the Thinkorswim workflow visually: a compact template picker with `Single`, `OCO`, `TRG w/ bracket`, `TRG w/ 2 brackets`, and `TRG w/ 3 brackets`.
  - User normally uses one bracket, sometimes two; three brackets should remain possible but not be the primary/default workflow.
  - Bracket rows should show enabled state, quantity link, target limit offset, stop offset, and TIF in a dense table-like layout.
  - Until Schwab bracket/OCO behavior is validated, this must remain clearly planning-only or preview-only and must not alter the existing single-leg Schwab order JSON.
- Bracket template management.
  - Allow named bracket templates such as `+1.00/-1.00`, `+2.00/-2.00`, scalp/trail-style presets, and user-defined templates.
  - Let the user choose one default template for the trading rail.
  - Template persistence can be localStorage first; move server-side only if templates need account/device portability.
  - Template application should populate planning fields only. It must not preview, stage, or place an order automatically.
- Stop/target helpers based on option premium or underlying level.
  - Premium helpers should support dollar offsets and percent moves from the selected option premium.
  - Underlying helpers should use a chart-click reference level only for calculation context, likely with selected-contract delta as a rough estimate when available.
- Risk-budget sizing.
  - Calculate suggested contract count from risk budget and debit/stop assumptions.
  - Preserve `SELL_TO_CLOSE` position caps and existing max quantity validation.
- Chart click sets underlying reference for helper calculations only.
  - No chart click should select a contract, preview an order, stage an order, place an order, or change Schwab order payloads.

Still later:

- Validate Schwab bracket/OCO preview behavior before adding any live child-order support.
- Add user-defined template creation/edit/delete beyond the current built-in presets and saved default.
- Refine cheap-premium template defaults so the stop helper does not default to `0.01` for low-premium contracts unless intended.
- SPX-specific validation.
- Multi-leg spreads.
- In-dashboard order journal.
  - Capture orders placed/used from this rail in a dashboard journal later.
  - Target direction: a built-in journal surfaced from the dashboard, similar to a bottom-left `Journal` app button/view.
  - Auto-record trade events where safe and deterministic: previewed order, placed order metadata, selected contract, account display label/hash reference, ticker, timestamps, entry/exit context, bracket plan snapshot, and realized/marked P/L when available.
  - Add screenshot/screen-recording capture as a later explicit capability, saved to a local database/media store and displayed with each journal entry. Do not start browser/desktop recording automatically without clear user opt-in and storage controls.
  - Do not prioritize CSV/export-only behavior; the desired direction is an in-app journal view with searchable entries and attached screenshots/recordings.

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
PORT=5014 FLASK_DEBUG=0 FLASK_USE_RELOADER=0 python3 ezoptionsschwab.py
```

Health:

```bash
curl -s http://127.0.0.1:5014/token_health
```

Existing expiration path:

```bash
curl -s http://127.0.0.1:5014/expirations/SPY
```

Representative `/update_price` smoke payload:

```bash
python3 -c "import json, urllib.request; payload={'ticker':'SPY','timeframe':'1','lookback_days':2,'levels_types':[]}; req=urllib.request.Request('http://127.0.0.1:5014/update_price',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'}); d=json.load(urllib.request.urlopen(req,timeout=60)); print(sorted(d.keys()))"
```

For future `/trade_chain`:

```bash
python3 -c "import json, urllib.request; payload={'ticker':'SPY','expiry':['YYYY-MM-DD'],'strike_range':0.02}; req=urllib.request.Request('http://127.0.0.1:5014/trade_chain',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'}); d=json.load(urllib.request.urlopen(req,timeout=60)); print(d.keys()); print(len(d.get('contracts',[])))"
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

- [x] Confirm branch and worktree state.
- [x] Read this plan.
- [ ] Read `docs/UI_MODERNIZATION_PLAN.md` sections on layout/tokens.
- [x] Read current right rail anchors in `ezoptionsschwab.py`.
- [x] Decide whether trade rail starts collapsed by default.

Stage 1:

- [x] Add fourth grid column.
- [x] Add trade rail shell markup.
- [x] Add collapse state.
- [x] Add resize state.
- [x] Mirror DOM in `ensurePriceChartDom()`.
- [x] Update `showPriceChartUI()`.
- [x] Test existing rails.

Stage 2:

- [x] Add structured chain payload helper.
- [x] Preserve `contractSymbol`.
- [x] Add read-only contract picker.
- [x] Add selected contract summary.
- [x] Add stale/spread warnings.

Stage 3:

- [x] Add linked account lookup.
- [x] Add account details/positions lookup.
- [x] Mask account display.
- [x] Handle token/account errors.

Stage 4:

- [x] Add server-side order builder.
- [x] Add preview endpoint.
- [x] Add strict validation.
- [x] Add preview UI.
- [x] Keep live order disabled.

Stage 5:

- [x] Add env-gated place endpoint.
- [x] Bind place to preview token/order hash.
- [x] Add final confirmation.
- [x] Handle Schwab response status/header.

Stage 6:

- [x] Add open orders.
- [x] Add cancel support.
- [x] Add position refresh.
- [x] Polish Position card with compact contract and metric pills.

Stage 7:

- [x] Add planning-only bracket panel.
- [x] Add Thinkorswim-style bracket template choices.
- [x] Add local default template saving.
- [x] Add premium target/stop helpers.
- [x] Add underlying-reference helper mode.
- [x] Add opt-in chart-click helper reference.
- [x] Add risk-budget sizing helper.
- [x] Keep Schwab single-leg order JSON unchanged.
- [ ] Validate Schwab bracket/OCO preview/place behavior before live support.
- [ ] Add user-defined template editing beyond built-in presets.
- [x] Add in-dashboard order journal.
  - [x] Add journal button/view in the dashboard.
  - [x] Auto-record rail trade events into local storage/SQLite.
  - [x] Add editable local notes/tags/setup/thesis/outcome fields.
  - [ ] Fix reported Journal button/toggle open issue in the in-app browser if reproducible.
  - [ ] Add screenshot/screen-recording attachments with explicit opt-in and storage controls.

---

## 16. Non-Goals For Initial Build

- No automated trading.
- No orders triggered by alerts, flow pulse, chart levels, or GEX changes.
- No market orders.
- No spread orders.
- No bracket/OCO orders in the first live stage.
- Bracket/OCO template work should start as planning-only UI until Schwab preview/place behavior is validated.
- Future journaling should be an in-dashboard order journal for orders placed or used from this rail, not an export-only workflow.
- Future screenshot/screen-recording capture for journal entries should require explicit user opt-in and clear storage controls before any recording starts.
- No changes to GEX/DEX/Vanna/Charm/Flow formulas.
- No migration to a JS framework.
- No splitting `ezoptionsschwab.py` into modules.

---

## 17. Historical Prompt For A Fresh Codex Session

Historical prompt from an earlier stage. For the current next-session prompt, use `docs/OPTIONS_TRADING_RAIL_UI_POLISH_PLAN.md` section `16. 2026-05-02 Trading Journal Build-out Update`.

Earlier prompt:

```text
We are in /Users/scottmunger/Desktop/Trading/Dashboards/GEX-Dashboard.

Read AGENTS.md first, then read:
- docs/OPTIONS_TRADING_RAIL_IMPLEMENTATION_PLAN.md
- docs/OPTIONS_TRADING_RAIL_UI_POLISH_PLAN.md

Before editing, confirm branch/status with:
git branch -a
git log --oneline main..HEAD
git status --short

We are continuing the dedicated fourth order-entry trading rail only:
- #trade-rail-header
- #trade-rail
- .trade-rail-shell
- Position / Contract Picker / Selected Contract / Order Ticket / Bracket Plan / Preview / Orders panels

Do not change analytics formulas, chart behavior outside the explicit trade-helper reference path, existing analytics right rail, account redaction, Schwab endpoint safety behavior, or Schwab single-leg order JSON shape. Keep ezoptionsschwab.py as a single file and use vanilla JS/CSS tokens only.

Current state:
- Trading rail is implemented through Stage 7 planning helpers.
- Account selection is in the trading rail header.
- Position is directly below the header/account context and uses compact contract/metric pills instead of repeating full OCC symbols in-row.
- Contract Picker, Selected Contract, Order Ticket, Preview, Orders, live-order guards, and account redaction are working.
- Bracket Plan is planning-only and does not alter preview/place payloads.
- Built-in bracket templates include Single, OCO, TRG w/ bracket, TRG w/ 2 brackets, TRG w/ 3 brackets, +1.00/-1.00, +2.00/-2.00, and Scalp.
- Default bracket template is saved in localStorage as gex.tradeBracketDefault.
- Chart click only sets an underlying helper reference when the Bracket Plan checkbox is enabled and no drawing mode is active.
- Future journal direction is a built-in dashboard Journal view/button that can auto-record rail trade events and later attach screenshots/screen recordings with explicit opt-in and storage controls.
- Static HTML and buildTradeRailHtml() must stay in parity.

Recently passed:
- python3 -m py_compile ezoptionsschwab.py
- git diff --check
- python3 -m unittest tests.test_session_levels tests.test_trade_preview

Next sensible scope:
1. Refine bracket helper UX for cheap contracts so default stop/target presets scale better when the option premium is below the stop offset.
2. Add user-defined bracket template create/edit/delete in the rail, still localStorage-only and planning-only.
3. Add an in-dashboard order journal for orders placed/used from this rail.
   - Start with a `Journal` button/view and local database schema for trade events.
   - Auto-record deterministic rail events first: preview/place metadata, selected contract, ticker, account display context, bracket plan snapshot, timestamps, and P/L fields when available.
   - Treat screenshots/screen recordings as a later opt-in media attachment layer, not something that starts automatically without controls.

Do not implement yet without explicit approval:
- Live Schwab bracket/OCO child orders.
- SPX-specific validation.
- Multi-leg spreads.
- Automated trading from chart clicks, alerts, or flow.

Important safety constraints:
- Do not make chart-click submit, preview, stage, or select an order automatically.
- Do not bypass preview-required/live-trading/final-confirmation guards.
- Do not change existing single-leg order JSON unless explicitly required and covered by tests.
- Preserve ENABLE_LIVE_TRADING=1 gating and final confirmation behavior.
- Preserve SELL_TO_CLOSE position caps.

Useful anchors:
- buildTradeRailHtml
- ensureTradeRailDom
- renderTradeRail
- renderTradeTicket
- renderTradeBracketPlan
- getTradeBracketPlan
- getTradeRiskSize
- tvHandleChartClick
- requestTradePreview
- placeTradeOrder
- build_single_option_limit_order
- /trade/preview_order
- /trade/place_order
- data-trade-bracket-template
- data-trade-target-offset
- data-trade-stop-offset
- data-trade-risk-budget
- data-trade-underlying-reference
- data-trade-chart-reference-enabled

Start by inspecting the current rail code and confirming static HTML/buildTradeRailHtml parity before editing.
```
