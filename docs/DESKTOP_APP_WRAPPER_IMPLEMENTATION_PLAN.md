# Desktop App Wrapper Implementation Plan

**Created:** 2026-05-04
**Status:** Phase 4 packaging handoff complete; desktop wrapper phases implemented; local PySide6 launch/OAuth validated
**Recommended first prototype:** pywebview launcher
**Likely long-term wrapper:** PySide6 + `QWebEngineView`
**Current app target:** `ezoptionsschwab.py` stays the dashboard runtime

This document lays out how to move the GEX dashboard from "open a localhost page in a browser" to a desktop-app wrapper without changing analytics, trading logic, Schwab payloads, or the current UI. The wrapper should hide localhost from the daily workflow, add native app/window behavior, and keep the existing Flask + vanilla JS dashboard as the source of truth.

## 1. Goal

Create a lightweight desktop app shell for the existing local dashboard.

The intended user workflow becomes:

1. Open `GEX Dashboard.app` or run a desktop launcher.
2. The launcher starts the existing Flask app on `127.0.0.1` using an available port.
3. The launcher opens a native desktop window pointed at the local dashboard URL.
4. Schwab data, SQLite persistence, local settings, journal screenshots, SSE streaming, order preview/place safety gates, and all calculations continue to run through the same existing backend and frontend code.
5. Later phases add native multi-window behavior for charts, journal review, order entry, and other workspace surfaces.

## 2. Non-Goals

- Do not change GEX, DEX, Vanna, Charm, Speed, Vomma, Color, Flow, expected-move, key-level, or alert formulas.
- Do not alter Schwab API request/response logic unless a narrow launcher compatibility issue forces it.
- Do not change Schwab order JSON, live trading gates, preview-token logic, cancel confirmation, journal behavior, or account redaction.
- Do not introduce a JS framework.
- Do not split or rewrite the dashboard UI as a native Qt UI.
- Do not remove the Flask server. Schwab OAuth and local API calls still need a local callback/API surface.
- Do not force packaging in the first prototype.

The first implementation should feel like the same dashboard in a desktop window.

## 3. Current App Facts

Grep anchors instead of trusting line numbers:

- Flask app object: `app = Flask(__name__)`
- Main dashboard route: `@app.route('/')`
- Schwab client init: `schwabdev.Client`
- Schwab callback env: `SCHWAB_CALLBACK_URL`
- Runtime startup: `app.run(debug=debug_enabled, port=port, threaded=True, use_reloader=use_reloader)`
- SSE endpoint: `@app.route('/price_stream/<path:ticker>')`
- SSE frontend connect: `new EventSource('/price_stream/' + encodeURIComponent(upperTicker))`
- Price popout: `function openPopoutChart(chartId)`
- Existing browser popout call: `window.open('', 'popout_' + chartId`
- Existing popout dependency: `window.opener`
- Token health route: `@app.route('/token_health')`
- Token DB helper: `_get_token_db_path`
- Settings routes: `/save_settings`, `/load_settings`
- Journal media directory: `TRADE_JOURNAL_MEDIA_DIR`

Observed state during planning:

- Branch: `main`
- `git log --oneline main..HEAD`: empty
- Existing unrelated untracked file: `Trading_from_dashboard.txt`; leave it alone.
- `requirements.txt` currently does not include `pywebview`, `PySide6`, `PyQt6`, `pyinstaller`, or `briefcase`.
- Local import check in this workspace showed `flask` installed and `webview`, `PySide6`, `PyQt6`, and `electron` not installed.

## 4. Architecture Decision

### Recommended Sequence

Start with **pywebview** for a small proof of concept, then move to **PySide6** if the wrapper needs stronger native-window control.

Reasoning:

- pywebview can create a desktop window around a local URL or WSGI app with minimal code.
- pywebview supports multiple windows, so it can validate the workflow quickly.
- It keeps the first prototype close to the existing Python-only stack.
- PySide6 is the better long-term desktop shell if we need robust window management, custom menus, profile/cookie control, download/open-url handling, lifecycle hooks, and better control over `window.open`.
- Electron and Tauri remain viable, but both introduce larger non-Python toolchains for a project that is currently single-file Python + vanilla JS.

### Final Direction

Use pywebview to prove:

- the dashboard loads correctly in a native window,
- Schwab auth/token health still works,
- SSE streaming stays connected,
- popouts do not break core dashboard behavior,
- shutdown/restart behavior is clean.

Then graduate to PySide6 if the wrapper becomes part of daily trading.

## 5. Wrapper Model

The wrapper should be a separate file, not a dashboard rewrite.

Suggested first file:

- `desktop_launcher.py`

Later PySide6 files, if needed:

- `desktop_app.py`
- `desktop/` package only if the launcher grows beyond one file.

The dashboard remains:

- `ezoptionsschwab.py`

### Server Startup Options

#### Option A: In-Process Server

Import `app` from `ezoptionsschwab.py`, start it in a background thread with Werkzeug `make_server`, then point the desktop webview at the chosen URL.

Pros:

- One process.
- Easy to share logs and lifecycle.
- Uses Flask/Werkzeug already installed with Flask.
- No shell process management.

Cons:

- GUI loop and Flask live in the same Python process.
- A hard backend crash can take down the desktop shell.
- Need a clean shutdown hook.

This is the best first prototype if the goal is speed and minimum moving parts.

#### Option B: Managed Subprocess

Start the existing command in the background:

```bash
PORT=<port> FLASK_DEBUG=0 FLASK_USE_RELOADER=0 python3 ezoptionsschwab.py
```

Pros:

- Closest to the current workflow.
- Keeps Flask isolated from the GUI process.
- Easier to restart the backend if needed.

Cons:

- More process management.
- Need log capture.
- Need shutdown cleanup.
- More awkward for packaged apps.

This is a good fallback if in-process Flask and the GUI loop interfere.

### Required Discovery: In-Process vs Managed Subprocess

The one area that still needs hands-on discovery in Phase 1 is the exact launcher mechanics. A future implementation session should not assume the first server model is final until both startup and shutdown have been smoke-tested locally.

Start with Option A, the in-process server, because it is the smallest prototype and avoids shell process management. Use Werkzeug's server object rather than calling `app.run()` directly, so the launcher can stop the server cleanly when the desktop window closes. Confirm that importing `ezoptionsschwab.py` only initializes the app/client/database once and does not accidentally start the debug reloader.

Evaluate Option A against this checklist:

- The desktop window opens after the server is ready.
- `GET /`, `/token_health`, `/update_price`, and `/price_stream/<ticker>` respond normally.
- The GUI remains responsive while the dashboard polls and streams data.
- Closing the final desktop window shuts down the Flask server without leaving a listener on the chosen port.
- A backend exception does not leave the user with a silent blank desktop window.
- Restarting the launcher immediately can bind a fresh port or reuse the previous port if it is free.

Switch to Option B, the managed subprocess, if any of these happen:

- pywebview or PySide6 requires the main thread in a way that makes the in-process server brittle.
- Flask shutdown from the GUI close event is unreliable.
- Schwab stream threads, SQLite connections, or app globals behave poorly when hosted in the same process as the GUI loop.
- Capturing backend logs separately is more useful than sharing one process.
- Packaging proves easier when the server runs as a child process.

If Option B is used, the launcher must own the child process lifecycle:

- set `PORT`, `FLASK_DEBUG=0`, and `FLASK_USE_RELOADER=0`,
- capture stdout/stderr to a log file or launcher console,
- wait for the local URL before showing the window,
- terminate only the launcher-owned child process on close,
- never kill an unrelated user-run `ezoptionsschwab.py` process.

Document the decision in this file after the prototype smoke test. The final choice should be based on local behavior, not preference.

## 6. Phase Plan

### Phase 0: Preparation / Safety Baseline

Purpose: establish a baseline before adding the wrapper.

Tasks:

- Confirm branch and worktree:

```bash
git branch -a
git log --oneline main..HEAD
git status --short
```

- Read:
  - `AGENTS.md`
  - `README.md`
  - this doc
  - `docs/OPTIONS_TRADING_RAIL_IMPLEMENTATION_PLAN.md` if touching order-entry surfaces
  - `docs/OPTIONS_TRADING_RAIL_UI_POLISH_PLAN.md` if touching trade rail/window behavior

- Run syntax checks:

```bash
python3 -m py_compile ezoptionsschwab.py
```

- Run the normal browser baseline on a smoke port:

```bash
PORT=5014 FLASK_DEBUG=0 FLASK_USE_RELOADER=0 python3 ezoptionsschwab.py
```

- Open `http://127.0.0.1:5014/` and verify the normal browser workflow still works before wrapper work starts.

Exit criteria:

- Existing browser app runs.
- Token monitor behaves the same as before.
- Price chart renders or fails only for known Schwab/session reasons.
- No code changes yet.

### Phase 1: pywebview Proof of Concept

Purpose: prove the dashboard can run inside a desktop window with minimal app changes.

Expected changes:

- Add `pywebview` to requirements or a separate optional requirements file.
- Add `desktop_launcher.py`.
- Do not modify analytics functions.
- Do not modify Schwab order endpoints.
- Do not change current HTML/CSS/JS except for a tiny optional desktop-detection hook if needed.

Suggested launcher behavior:

1. Find an available port, preferring `5001` only if free.
2. Start Flask with `debug=False`, `use_reloader=False`, `threaded=True`.
3. Wait for `GET /` to respond.
4. Create a native window titled `GEX Dashboard`.
5. Load `http://127.0.0.1:<port>/`.
6. On last window close, shut down the Flask server cleanly.

Validation:

- App opens without manually typing localhost.
- `/token_health` works from the drawer.
- `/update`, `/update_price`, `/price_stream/<ticker>`, `/trade/*`, `/trade/journal`, and journal media routes continue to work.
- `localStorage` persists between launcher restarts if the chosen webview backend supports a stable profile.
- If localStorage does not persist, document it and defer profile handling to PySide6.
- Existing `window.open` chart popout either works or fails gracefully without breaking the main window.

Expected limitation:

- The first pywebview prototype may still treat chart popouts as browser-like webview popups. That is acceptable for Phase 1.

Exit criteria:

- A user can launch the dashboard from the desktop wrapper and trade/read the dashboard the same way as in the browser.
- No calculation or trading logic changed.

### Phase 1 Handoff - 2026-05-04

Accomplished:

- Added `desktop_launcher.py` as a separate pywebview desktop launcher. It imports the existing Flask app, starts an in-process Werkzeug server bound to `127.0.0.1`, forces Flask debug and the reloader off, waits for `/` to respond, opens a native `GEX Dashboard` window, and shuts down only the wrapper-owned server when pywebview exits.
- Added optional desktop dependency handling through `requirements-desktop.txt`, which layers `pywebview` on top of the existing browser/server requirements without changing `requirements.txt`.
- Kept `ezoptionsschwab.py` directly runnable with `python3 ezoptionsschwab.py`.
- Did not change analytics formulas, chart calculations, flow/alert logic, Schwab order endpoints, order JSON, preview/place/cancel gates, trading behavior, dashboard UI, or `Trading_from_dashboard.txt`.
- Verified local browser launch on `http://127.0.0.1:5017/` and wrapper launch on `http://127.0.0.1:5018/`.
- Verification passed: `python3 -m py_compile ezoptionsschwab.py`, `python3 -m py_compile desktop_launcher.py`, `git diff --check`, and `python3 -m unittest tests.test_session_levels tests.test_trade_preview`.

Tricky parts / findings:

- The first smoke port, `5014`, was already in use, so smoke testing moved to `5017` and `5018`.
- The sandbox blocked binding a local Flask listener until the smoke command was approved outside the sandbox.
- Local `pywebview` was not installed at first; installing `requirements-desktop.txt` installed pywebview 6.2.1 and its macOS dependencies.
- Local smoke testing did not show a need to fall back to a managed subprocess. The in-process Werkzeug model served `/`, `/load_settings`, `/trade/journal`, `/token_health`, `/expirations/SPY`, `/update_price`, `/update`, and `/price_stream/SPY`, then stopped without leaving a listener on the wrapper smoke port after the final pywebview window closed.
- pywebview 6.2.1 on macOS supports `private_mode=False` and `storage_path`; with those enabled, a two-run same-origin probe showed `localStorage` round-tripped during a run and persisted across wrapper restart.
- The existing price popout pattern is limited in this Phase 1 wrapper. A probe of `window.open('', 'gex_desktop_probe_popup', ...)` returned no window in pywebview, so the current `window.open` + `window.opener` price popout should be treated as unsupported in the pywebview prototype and addressed by the Phase 2 desktop window route/bridge work. The main dashboard continued to work.
- `ezoptionsschwab.py` still emits a pre-existing compile-time `SyntaxWarning` at line `10438` for an invalid escape sequence inside a large template string; compilation still succeeds.

Suggested next-session prompt:

```text
We are in /Users/scottmunger/Desktop/Trading/Dashboards/GEX-Dashboard.

Read AGENTS.md first, then read docs/DESKTOP_APP_WRAPPER_IMPLEMENTATION_PLAN.md.

Continue from the Phase 1 Handoff dated 2026-05-04. Phase 1 added desktop_launcher.py and requirements-desktop.txt, proved the in-process Werkzeug + pywebview wrapper, documented localStorage persistence, and found that pywebview does not support the existing window.open('', ...) + window.opener price-popout pattern.

Goal: start Phase 2 only, Desktop-Aware Window Seams.

Constraints:
- Wrapper/window-seam work only. Do not change analytics formulas, chart calculations, flow/alert logic, Schwab order endpoints, order JSON, preview/place/cancel safety gates, or trading behavior.
- Keep ezoptionsschwab.py runnable directly with python3 ezoptionsschwab.py.
- Do not redesign the UI.
- No JS framework introduction.
- Leave Trading_from_dashboard.txt alone unless explicitly asked.
- Keep existing browser window.open behavior working until a desktop route/bridge is proven.

Start by confirming:
git branch -a
git log --oneline main..HEAD
git status --short

Recommended Phase 2 target:
- Add a minimal desktop window abstraction, likely openDashboardWindow(kind, params), without making core rendering depend on desktop mode.
- Add the smallest passive desktop detection needed, such as a query param or server-injected flag from the launcher.
- Add first-class Flask route(s) for the price chart desktop window, starting with /desktop/window/price or /desktop/window/chart/<chart_id>.
- Move the critical price-popout state away from window.opener by passing explicit query params and/or a small server-side window-state endpoint.
- Keep the main dashboard browser behavior unchanged.
- Smoke test normal browser launch and wrapper launch.

Verification:
python3 -m py_compile ezoptionsschwab.py
python3 -m py_compile desktop_launcher.py
git diff --check
python3 -m unittest tests.test_session_levels tests.test_trade_preview if tests exist
Smoke normal browser launch and wrapper launch.
```

### Phase 2: Desktop-Aware Window Seams

Purpose: make multi-window behavior deliberate instead of relying on browser `window.open` and `window.opener`.

Problem today:

- The current price popout writes a full HTML document into `window.open`.
- The popout reads settings from `window.opener.document`.
- That is workable in a browser tab model, but fragile in a native app model.

Preferred direction:

- Add first-class Flask routes for window surfaces:
  - `/desktop/window/price`
  - `/desktop/window/chart/<chart_id>`
  - `/desktop/window/journal`
  - `/desktop/window/order-entry` only if useful later
- Pass state through query params or a small server-side window-state endpoint instead of reading from `window.opener`.
- Keep existing browser `window.open` support until the desktop route proves stable.
- Add a tiny frontend abstraction:

```text
openDashboardWindow(kind, params)
```

Behavior:

- In normal browser mode, it can keep using `window.open`.
- In desktop mode, it can call the wrapper bridge or open a desktop route.

Desktop detection should be passive and low-risk:

- server injects a small `window.GEX_DESKTOP = true`, or
- launcher adds a query param like `?desktop=1`, or
- wrapper bridge exposes a minimal JS API.

Do not make core dashboard rendering depend on this flag.

Exit criteria:

- Main dashboard works in browser and wrapper.
- Price chart window can be opened as an independent desktop window.
- Secondary chart windows no longer require `window.opener` for critical data.

### Phase 2 Handoff - 2026-05-04

Accomplished:

- Added passive desktop detection to the main dashboard. `desktop_launcher.py` now opens the dashboard at `/?desktop=1`, and `ezoptionsschwab.py` injects `window.GEX_DESKTOP` only for that desktop launch URL. Direct browser launches remain unchanged.
- Added a minimal route-backed desktop window seam:
  - `POST /desktop/window_state` stores a short-lived in-memory window payload.
  - `GET /desktop/window_state/<state_id>` retrieves the payload for a child window.
  - `GET /desktop/window/price` serves a first-class desktop price chart surface.
  - `GET /desktop/window/chart/<chart_id>` routes `price` / `price-chart` to the same price surface.
- Added `openDashboardWindow(kind, params)` in the dashboard JavaScript. In normal browser mode it does nothing and the existing `window.open('', ...)` popout path remains active. In desktop mode, the price chart popout stores explicit state and asks the wrapper bridge to open `/desktop/window/price`.
- Added `DashboardWindowBridge` to `desktop_launcher.py`. The bridge exposes a narrow pywebview JS API that can open route-backed native windows without relying on browser `window.open` / `window.opener`.
- Kept `ezoptionsschwab.py` directly runnable with `python3 ezoptionsschwab.py`.
- Did not change analytics formulas, chart calculations, flow/alert logic, Schwab order endpoints, order JSON, preview/place/cancel gates, trading behavior, dashboard layout, or `Trading_from_dashboard.txt`.
- Verification passed: `python3 -m py_compile ezoptionsschwab.py`, `python3 -m py_compile desktop_launcher.py`, `git diff --check`, and `python3 -m unittest tests.test_session_levels tests.test_trade_preview`.
- Flask test-client verification passed for `/?desktop=1`, `/desktop/window_state`, `/desktop/window_state/<state_id>`, and `/desktop/window/price`.
- Normal browser smoke passed on `http://127.0.0.1:5019/` with HTTP 200.
- pywebview wrapper smoke passed on `http://127.0.0.1:5020/`; it loaded `/?desktop=1`, `/load_settings`, `/trade/journal`, `/expirations/SPY`, and `/token_health`, then auto-closed and left no listener on the smoke port.

Tricky parts / findings:

- The desktop route intentionally starts as a minimal independent price chart surface rather than a full copy of the legacy popup document. It proves the important seam: the child window can fetch explicit state from the server and request `/update_price` directly without `window.opener`.
- The existing browser price popout still uses the legacy blank-document `window.open` path. That is intentional until the desktop route and future shell behavior are mature enough to replace it.
- The in-memory window state is short-lived and process-local. That is enough for the current in-process wrapper model, but Phase 3 should preserve the same route contract if it moves to a stronger native shell.
- Local Flask listener smoke required sandbox approval again. The first sandboxed normal launch failed with `Operation not permitted`; the approved launch on port `5019` worked.
- `ezoptionsschwab.py` still emits the pre-existing compile-time `SyntaxWarning` for an invalid escape sequence inside the large dashboard template; compilation succeeds.

Suggested next-session prompt:

```text
We are in /Users/scottmunger/Desktop/Trading/Dashboards/GEX-Dashboard.

Read AGENTS.md first, then read docs/DESKTOP_APP_WRAPPER_IMPLEMENTATION_PLAN.md.

Continue from the Phase 2 Handoff dated 2026-05-04. Phase 2 added passive desktop detection, the /desktop/window_state endpoint, first-class /desktop/window/price and /desktop/window/chart/<chart_id> routes, openDashboardWindow(kind, params), and a narrow pywebview DashboardWindowBridge. The browser window.open('', ...) + window.opener popout path was intentionally kept working for normal browser launches.

Goal: start Phase 3 only, PySide6 Shell. Do not do a sanity check of completed Phase 1 or Phase 2 beyond the normal branch/worktree confirmation and whatever local context you need for Phase 3 implementation.

Constraints:
- Wrapper/shell/window-seam work only. Do not change analytics formulas, chart calculations, flow/alert logic, Schwab order endpoints, order JSON, preview/place/cancel safety gates, or trading behavior.
- Keep ezoptionsschwab.py runnable directly with python3 ezoptionsschwab.py.
- Keep desktop_launcher.py working unless the new PySide6 launcher deliberately supplements it.
- Do not redesign the UI.
- No JS framework introduction.
- Leave Trading_from_dashboard.txt alone unless explicitly asked.
- Preserve the existing browser window.open behavior while the desktop shell matures.

Start by confirming:
git branch -a
git log --oneline main..HEAD
git status --short

Recommended Phase 3 target:
- Add optional PySide6 dependency handling, preferably in requirements-desktop.txt.
- Add a PySide6 launcher, likely desktop_app.py, that reuses the existing in-process Werkzeug server model first.
- Use QWebEngineView / QWebEngineProfile with persistent local storage/cookies/cache under the existing desktop app support directory.
- Load the main dashboard as /?desktop=1.
- Route new-window requests through QWebEngineView.createWindow or a custom QWebEnginePage so desktop windows load Flask routes, especially /desktop/window/price.
- Add only minimal native app/window controls needed for Phase 3, such as New Price Chart Window, Reload, and Quit/Stop Server.
- Keep the route-backed price chart state contract from Phase 2: pass explicit state through /desktop/window_state rather than window.opener.
- Smoke test normal browser launch, pywebview launcher launch, and PySide6 launcher launch.

Verification:
python3 -m py_compile ezoptionsschwab.py
python3 -m py_compile desktop_launcher.py
python3 -m py_compile desktop_app.py if added
git diff --check
python3 -m unittest tests.test_session_levels tests.test_trade_preview if tests exist
Smoke normal browser launch, pywebview wrapper launch, and PySide6 wrapper launch.
```

### Phase 3: PySide6 Shell

Purpose: replace or supplement pywebview with a more capable long-term desktop shell.

Expected changes:

- Add PySide6 dependency in optional desktop requirements.
- Add a PySide6 launcher using `QWebEngineView`.
- Use `QWebEngineProfile` for persistent local storage/cookies/cache.
- Use `QWebEngineView.createWindow` or a custom page class to route `window.open` requests into native windows.
- Add native menu actions:
  - New Dashboard Window
  - New Price Chart Window
  - Reload
  - Toggle Full Screen
  - Open Token Health
  - Open Journal Workspace
  - Quit and Stop Server

Server model decision:

- Re-test in-process server first.
- Use managed subprocess if PySide6 event loop and Flask lifecycle become brittle.

Exit criteria:

- Multiple native windows are stable.
- Local storage persists.
- Closing the app stops only the wrapper-owned Flask server.
- Existing browser launch still works with `python3 ezoptionsschwab.py`.

### Phase 3 Handoff - 2026-05-04

Accomplished:

- Added optional PySide6 dependency handling to `requirements-desktop.txt` while keeping `pywebview` and the existing browser/server requirements unchanged.
- Added `desktop_app.py` as a separate PySide6 shell. It imports the existing Flask app, starts the same in-process Werkzeug server model used by `desktop_launcher.py`, loads the main dashboard at `/?desktop=1`, and shuts down only the wrapper-owned server when the final native window closes.
- Added a persistent `QWebEngineProfile` for local storage, cookies, and cache under `~/Library/Application Support/GEX Dashboard/pyside6`, with overrides via `GEX_DESKTOP_APP_SUPPORT_DIR` or `GEX_QTWEBENGINE_STORAGE_DIR`.
- Added a PySide6-injected bridge compatible with the Phase 2 `window.pywebview.api.open_window` contract. The dashboard still posts explicit state through `/desktop/window_state`; the bridge opens route-backed windows with `window.open('/desktop/window/price?...')`, and Qt routes those requests through `QWebEngineView.createWindow` into native windows.
- Added minimal native menus: New Dashboard Window, New Price Chart Window, Reload, Toggle Full Screen, and Quit and Stop Server.
- Kept `ezoptionsschwab.py` directly runnable with `python3 ezoptionsschwab.py` and kept `desktop_launcher.py` working.
- Did not change analytics formulas, chart calculations, flow/alert logic, Schwab order endpoints, order JSON, preview/place/cancel gates, trading behavior, dashboard layout, or `Trading_from_dashboard.txt`.

Verification passed:

- `python3 -m py_compile ezoptionsschwab.py`
- `python3 -m py_compile desktop_launcher.py`
- `python3 -m py_compile desktop_app.py`
- `git diff --check`
- `python3 -m unittest tests.test_session_levels tests.test_trade_preview`
- Normal browser smoke on `http://127.0.0.1:5021/` returned HTTP 200 for `/` and `/token_health`, then the smoke server was stopped.
- pywebview wrapper smoke on `http://127.0.0.1:5022/` loaded `/?desktop=1`, `/load_settings`, `/trade/journal`, `/token_health`, and `/expirations/SPY`, then auto-closed and left no listener on the smoke port.
- PySide6 shell smoke on `http://127.0.0.1:5024/` loaded `/?desktop=1`, opened a route-backed price window via `/desktop/window_state` and `/desktop/window/price`, fetched the child state, hit `/update_price`, connected `/price_stream/SPY`, then auto-closed and left no listener on the smoke port.

Tricky parts / findings:

- PySide6 was not installed locally at the start of Phase 3. Installing `requirements-desktop.txt` installed PySide6 6.11.0 plus its Addons, Essentials, and shiboken packages.
- The PySide6 shell preserves the Phase 2 bridge contract by injecting a pywebview-compatible JS shim rather than adding another frontend branch. This keeps normal browser `window.open('', ...)` behavior untouched and keeps the pywebview launcher path intact.
- The native New Price Chart Window action asks the current dashboard page to call `openDashboardWindow('price', buildPricePayload())`, so state still flows through `/desktop/window_state` rather than `window.opener`.
- `ezoptionsschwab.py` still emits the pre-existing compile-time `SyntaxWarning` for an invalid escape sequence inside the large dashboard template; compilation succeeds.

Suggested next-session prompt:

```text
We are in /Users/scottmunger/Desktop/Trading/Dashboards/GEX-Dashboard.

Read AGENTS.md first, then read docs/DESKTOP_APP_WRAPPER_IMPLEMENTATION_PLAN.md.

Continue from the Phase 3 Handoff dated 2026-05-04. Phase 3 added optional PySide6 dependency handling, desktop_app.py, a persistent QWebEngineProfile under the desktop app support directory, a pywebview-compatible injected bridge for route-backed windows, Qt createWindow routing for /desktop/window/price, and minimal native shell menus. Browser window.open('', ...) behavior and the pywebview launcher were intentionally kept working.

Goal: knock out all remaining desktop-wrapper phases in one go, starting with Phase 4 Packaging and any narrow path-policy work required to make the packaged macOS app usable. Do not do a sanity check of completed Phase 1, Phase 2, or Phase 3 beyond normal branch/worktree confirmation and whatever local context is needed for packaging.

Constraints:
- Wrapper/shell/window-seam/packaging work only. Do not change analytics formulas, chart calculations, flow/alert logic, Schwab order endpoints, order JSON, preview/place/cancel safety gates, or trading behavior.
- Keep ezoptionsschwab.py runnable directly with python3 ezoptionsschwab.py.
- Keep desktop_launcher.py and desktop_app.py working unless a packaging-specific supplement is deliberately added.
- Do not redesign the UI.
- No JS framework introduction.
- Leave Trading_from_dashboard.txt alone unless explicitly asked.
- Preserve existing browser window.open behavior while the desktop shell matures.
- Do not move existing user data automatically without a clear migration step.

Start by confirming:
git branch -a
git log --oneline main..HEAD
git status --short

Recommended remaining target:
- Add optional packaging dependency handling, preferably without bloating the normal browser requirements.
- Add the narrow data/path policy needed for packaged desktop mode, preserving dev-mode behavior and supporting env overrides such as GEX_DASHBOARD_DATA_DIR and SCHWAB_TOKENS_DB.
- Build a macOS .app with PyInstaller --onedir --windowed for the PySide6 shell.
- Ensure Qt WebEngine resources/plugins are included or document the exact required PyInstaller adjustment.
- Keep .env, options_data.db, settings.json, screenshots, and Schwab tokens out of git and in predictable user-controlled locations.
- Launch the packaged app from Finder or command line, confirm it starts the local Flask server, loads /?desktop=1, opens a route-backed price chart window, and shuts down its owned server.
- Smoke normal browser launch, pywebview launcher launch, PySide6 launcher launch, and packaged app launch.

Verification:
python3 -m py_compile ezoptionsschwab.py
python3 -m py_compile desktop_launcher.py
python3 -m py_compile desktop_app.py
git diff --check
python3 -m unittest tests.test_session_levels tests.test_trade_preview if tests exist
Smoke normal browser launch, pywebview wrapper launch, PySide6 wrapper launch, and packaged .app launch.
```

### Phase 4: Packaging

Purpose: package the wrapper as a macOS app for daily use.

Likely packager:

- PyInstaller `--onedir --windowed` first.

Reasoning:

- PyInstaller documents macOS `.app` output with `--windowed`.
- PyInstaller notes onefile + windowed app bundles are inefficient and problematic for signed/notarized sandboxed apps. Prefer `--onedir`.
- Qt/PySide6 packaging may require extra attention to Qt WebEngine resources, plugins, and profiles.

Packaging must preserve user data:

- `.env`
- `options_data.db`
- `settings.json`
- `Screenshots/trade_journal`
- `~/.schwabdev/tokens.db`

Important path issue:

- `DB_PATH` currently uses `BASE_DIR = os.path.dirname(os.path.abspath(__file__))`.
- `settings.json` currently uses the process working directory.
- In a packaged app, `__file__` and CWD may not be the desired user-data directory.

Before packaging, add a narrow path policy:

- Dev mode: keep current paths.
- Desktop mode: use a user data directory, probably under `~/Library/Application Support/GEX Dashboard/`.
- Allow env overrides:
  - `GEX_DASHBOARD_DATA_DIR`
  - `SCHWAB_TOKENS_DB`

Do not move existing user data automatically without a clear migration step.

Exit criteria:

- `.app` launches from Finder.
- App can find `.env` or has a documented config location.
- Existing data is not lost.
- Tokens remain readable.
- Journal screenshots still save and open.

### Phase 4 Handoff - 2026-05-05

Accomplished:

- Added a narrow path policy in `ezoptionsschwab.py`:
  - Direct dev runs still use repo-local `.env`, `options_data.db`, `settings.json`, and `Screenshots/trade_journal`.
  - Packaged/frozen runs use `~/Library/Application Support/GEX Dashboard/`.
  - `GEX_DASHBOARD_DATA_DIR` overrides dashboard data in both modes.
  - `SCHWAB_TOKENS_DB` still overrides Schwab token DB location; default remains `~/.schwabdev/tokens.db`.
  - Existing repo-local user data is not moved automatically.
- Added optional packaging dependency handling via `requirements-packaging.txt`; normal browser `requirements.txt` remains unchanged.
- Added `packaging/gex_dashboard_macos.spec` for PyInstaller `--onedir --windowed` packaging of the PySide6 shell.
- The spec explicitly collects PySide6 Qt WebEngine resources, translations, and key plugins needed by `QWebEngineView`.
- Added `packaging/README.md` with build commands, data-location policy, and the exact broader PyInstaller fallback (`--collect-all PySide6`) if a future Qt WebEngine build misses resources.
- Added `build/` and `dist/` to `.gitignore`.
- Tightened packaged PySide6 shutdown by forcing the frozen process to exit after the normal controller cleanup path returns. This avoids leaving a packaged shell process resident after its windows and owned Flask server are already stopped.
- Built `dist/GEX Dashboard.app` with PyInstaller 6.20.0 on macOS using the spec.

Verification passed:

- `python3 -m py_compile ezoptionsschwab.py`
- `python3 -m py_compile desktop_launcher.py`
- `python3 -m py_compile desktop_app.py`
- `git diff --check`
- `python3 -m unittest tests.test_session_levels tests.test_trade_preview`
- Normal Flask/browser smoke on `http://127.0.0.1:5021/` returned HTTP 200 for `/` and `/token_health`, then the smoke server was stopped.
- pywebview wrapper smoke on `http://127.0.0.1:5022/` loaded `/?desktop=1`, `/load_settings`, `/trade/journal`, `/token_health`, and `/expirations/SPY`, then auto-closed and left no listener.
- PySide6 shell smoke on `http://127.0.0.1:5024/` loaded `/?desktop=1`, opened `/desktop/window/price` through `/desktop/window_state`, fetched child state, attempted `/update_price`, then auto-closed and left no listener.
- Packaged app command-line smoke on `http://127.0.0.1:5025/` loaded `/?desktop=1`, opened the route-backed price window, fetched child state, attempted `/update_price`, exited cleanly, and left no listener.

Tricky parts / findings:

- Direct normal launch with the repo `.env` prompted for Schwab OAuth because the local refresh token was expired. Smoke runs used temporary `GEX_DASHBOARD_DATA_DIR` values to avoid touching live credentials or moving user data.
- Temp-data smoke runs intentionally had no Schwab app credentials, so `/expirations/SPY` returned 400 and `/update_price` returned 500 after proving the route-backed window path. That is expected for no-client smoke.
- PyInstaller built the app bundle, but signing directly in the local Desktop/File Provider-backed `dist/` path can fail with `resource fork, Finder information, or similar detritus not allowed` when macOS attaches protected extended attributes. Follow-up added `packaging/sign_macos_app.sh`, which clean-copies the bundle with `ditto --norsrc --noextattr`, signs the clean copy, and passes strict `codesign --verify --deep --strict` for local validation. Gatekeeper distribution still requires a Developer ID certificate and notarization.
- `ezoptionsschwab.py` still emits the pre-existing compile-time `SyntaxWarning` for an invalid escape sequence inside the large dashboard template; compilation succeeds.

User validation after handoff:

- On 2026-05-05, the user launched the PySide6 wrapper with `python3 desktop_app.py`.
- The desktop shell appeared to work locally.
- The user completed the Schwab OAuth prompt during that run, and OAuth/token behavior was working afterward.
- Practical local launch recommendation is now `python3 desktop_app.py` for the desktop shell, while `python3 ezoptionsschwab.py` remains the supported browser fallback.

Suggested next-session audit prompt:

```text
We are in /Users/scottmunger/Desktop/Trading/Dashboards/GEX-Dashboard.

Read AGENTS.md first, then read docs/DESKTOP_APP_WRAPPER_IMPLEMENTATION_PLAN.md.

Continue from the Phase 4 Handoff dated 2026-05-05 and the user validation note that `python3 desktop_app.py` launched successfully and Schwab OAuth was completed successfully.

Goal: audit the completed desktop-wrapper implementation end to end. Do not start a new feature phase. Inspect the code and run focused smoke tests to confirm the browser app, pywebview launcher, PySide6 launcher, route-backed price window, data-path policy, OAuth/token behavior, and packaged app path are correct. If you find narrow defects in wrapper/shell/window-seam/packaging behavior, fix them and re-run the relevant checks.

Constraints:
- Wrapper/shell/window-seam/packaging verification and narrow fixes only.
- Do not change analytics formulas, chart calculations, flow/alert logic, Schwab order endpoints, order JSON, preview/place/cancel safety gates, or trading behavior.
- Keep `ezoptionsschwab.py` runnable directly with `python3 ezoptionsschwab.py`.
- Keep `desktop_launcher.py` and `desktop_app.py` working.
- Do not redesign the UI.
- No JS framework introduction.
- Leave `Trading_from_dashboard.txt` alone unless explicitly asked.
- Preserve existing browser `window.open('', ...)` behavior while validating the desktop route-backed windows.
- Do not move existing user data automatically.

Start by confirming:
git branch -a
git log --oneline main..HEAD
git status --short

Recommended audit targets:
- Review `ezoptionsschwab.py` path policy for dev mode, packaged/frozen mode, `GEX_DASHBOARD_DATA_DIR`, `SCHWAB_TOKENS_DB`, `.env`, `options_data.db`, `settings.json`, and `Screenshots/trade_journal`.
- Confirm direct browser launch still serves `/` without `window.GEX_DESKTOP = true`.
- Confirm `desktop_launcher.py` still loads `/?desktop=1` and can auto-close without leaving a listener.
- Confirm `desktop_app.py` loads `/?desktop=1`, uses persistent Qt WebEngine storage, opens `/desktop/window/price` through `/desktop/window_state`, and shuts down its owned server.
- Confirm OAuth/token behavior works with the normal repo `.env` and does not require any code-secret changes.
- Confirm packaged app build docs and spec are accurate; rebuild only if needed.
- Check whether the local macOS signing/provenance issue is only a distribution/signing problem or blocks Finder launch.
- Inspect for accidental inclusion of `.env`, `options_data.db`, `settings.json`, screenshots, token DBs, `build/`, or `dist/` in git.

Verification:
python3 -m py_compile ezoptionsschwab.py
python3 -m py_compile desktop_launcher.py
python3 -m py_compile desktop_app.py
git diff --check
python3 -m unittest tests.test_session_levels tests.test_trade_preview if tests exist
Smoke normal browser launch, pywebview wrapper launch, PySide6 wrapper launch, route-backed price window, and packaged app launch if the local build/signing state allows it.
```

## 7. Schwab OAuth / Token Handling

The wrapper should not fight Schwab OAuth.

Current behavior:

- Credentials come from `.env`.
- Schwab client uses `SCHWAB_APP_KEY`, `SCHWAB_APP_SECRET`, and `SCHWAB_CALLBACK_URL`.
- Token health reads `~/.schwabdev/tokens.db` unless `SCHWAB_TOKENS_DB` overrides it.

Wrapper requirements:

- Keep using `127.0.0.1` or `localhost` callback URLs that match the Schwab developer app.
- Keep token DB readable by the existing `schwabdev` client.
- Do not embed secrets into packaged app code.
- Do not copy `.env` into git.
- Do not alter live trading gates.

Future enhancement:

- Add a desktop menu item that opens the token-health drawer or route.
- Add a guided "refresh token" workflow only after understanding the current `schwabdev` OAuth flow end to end.

## 8. Multi-Window Roadmap

Start simple. Do not redesign the dashboard into many windows immediately.

Suggested order:

1. Main dashboard native window.
2. Price chart native popout.
3. Journal workspace native window.
4. Secondary Plotly chart popouts.
5. Optional order-entry companion window.

Each window should load a Flask route and use existing endpoint data. Avoid building native Qt widgets for trading data.

For price chart windows:

- reuse existing Lightweight Charts renderer where possible,
- avoid duplicating calculations,
- use `/update_price` and `/price_stream/<ticker>`,
- pass selected ticker/timeframe/settings explicitly,
- remove dependency on `window.opener` once the desktop route exists.

## 9. Security / Localhost Surface

The current browser app assumes same-machine use. A desktop wrapper makes that assumption stronger, but the local HTTP server still exists.

Future hardening:

- Bind only to `127.0.0.1`, not all interfaces.
- Randomize the wrapper port unless `PORT` is explicitly set.
- Consider a per-launch local token header for mutating routes if the app becomes distributed.
- Be careful with CSRF if exposing a local web server to a webview and browser at the same time.
- Keep live trading guarded by `ENABLE_LIVE_TRADING=1`, preview token, and final confirmation.

Do not block the first prototype on these unless the server binds beyond localhost.

## 10. Testing Matrix

### Required after any wrapper code

```bash
python3 -m py_compile ezoptionsschwab.py
python3 -m py_compile desktop_launcher.py
git diff --check
```

If tests are available in the checkout:

```bash
python3 -m unittest tests.test_session_levels tests.test_trade_preview
```

### Browser Baseline

- `python3 ezoptionsschwab.py`
- Open `http://127.0.0.1:5001/`
- Verify dashboard still works outside the wrapper.

### Wrapper Smoke

- Launch wrapper.
- Verify main window loads.
- Verify token monitor.
- Select ticker/expiry.
- Verify `/update` and `/update_price` responses.
- Verify SSE stream by checking live candle/quote updates when market data is available.
- Open price popout.
- Open journal and any screenshot media.
- Preview an order only if the normal trading safety context is available.
- Do not place live orders as part of wrapper testing unless explicitly requested.

### Shutdown

- Close the desktop window.
- Confirm the wrapper-owned Flask process/thread stops.
- Confirm no stale `ezoptionsschwab.py` smoke process is left on the test port.

## 11. Risks

- `window.open` / `window.opener` behavior may vary by wrapper backend.
- Browser `localStorage` persistence may differ between Safari/Chrome and pywebview.
- Packaged app paths can accidentally point DB/settings/media at the wrong location.
- Running Flask in-process can complicate shutdown or signal handling.
- PySide6/PyInstaller packaging for Qt WebEngine can require extra resource handling.
- Schwab OAuth may require exact callback URL consistency; changing ports/callbacks casually can break auth.

## 12. Rollback Plan

The wrapper must be additive.

If any wrapper phase fails:

- keep `python3 ezoptionsschwab.py` as the supported launch path,
- remove or ignore `desktop_launcher.py`,
- do not touch the analytics/trading code,
- do not migrate data paths until the wrapper is proven.

The browser workflow is the fallback until the desktop app is stable.

## 13. Suggested First Implementation Prompt

```text
We are in /Users/scottmunger/Desktop/Trading/Dashboards/GEX-Dashboard.

Read AGENTS.md, then read docs/DESKTOP_APP_WRAPPER_IMPLEMENTATION_PLAN.md.

Build Phase 1 only: a small pywebview desktop launcher prototype.

Constraints:
- Do not change analytics formulas.
- Do not change Schwab order endpoints, order JSON, preview/place/cancel safety gates, or trading logic.
- Do not change the dashboard UI except for the smallest desktop-compatibility hook if absolutely required.
- Keep ezoptionsschwab.py runnable directly with python3 ezoptionsschwab.py.
- Add wrapper code separately, preferably desktop_launcher.py.
- Leave Trading_from_dashboard.txt alone unless explicitly asked.

Implementation target:
- Start the existing Flask app on 127.0.0.1 with debug off and reloader off.
- Open a native desktop window pointed at the local URL.
- Shut down the wrapper-owned server when the last window closes.
- Document any pywebview localStorage or window.open limitation found during smoke testing.

Verification:
- python3 -m py_compile ezoptionsschwab.py
- python3 -m py_compile desktop_launcher.py
- git diff --check
- python3 -m unittest tests.test_session_levels tests.test_trade_preview if tests exist
- Smoke normal browser launch and wrapper launch.
```

## 14. Reference Links

- pywebview usage and multiple windows: https://pywebview.idepy.com/en/guide/usage
- pywebview application architecture / Flask app support: https://pywebview.idepy.com/en/guide/architecture
- PySide6 `QWebEngineView`: https://doc.qt.io/qtforpython-6/PySide6/QtWebEngineWidgets/QWebEngineView.html
- PyInstaller macOS app bundle notes: https://pyinstaller.org/en/stable/usage.html
