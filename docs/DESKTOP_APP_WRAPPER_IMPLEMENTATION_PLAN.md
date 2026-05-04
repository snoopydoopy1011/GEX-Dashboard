# Desktop App Wrapper Implementation Plan

**Created:** 2026-05-04
**Status:** Planning only; no implementation yet
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
