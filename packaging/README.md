# macOS Desktop Packaging

Build the PySide6 shell as an onedir macOS app:

```bash
python3 -m pip install -r requirements-packaging.txt
python3 -m PyInstaller --clean --noconfirm packaging/gex_dashboard_macos.spec
```

The app bundle is written to:

```text
dist/GEX Dashboard.app
```

## User Data Policy

Direct dev runs keep the existing repo-local paths:

- `.env`
- `options_data.db`
- `settings.json`
- `Screenshots/trade_journal`

Packaged runs use:

```text
~/Library/Application Support/GEX Dashboard/
```

Expected packaged files:

- `~/Library/Application Support/GEX Dashboard/.env`
- `~/Library/Application Support/GEX Dashboard/options_data.db`
- `~/Library/Application Support/GEX Dashboard/settings.json`
- `~/Library/Application Support/GEX Dashboard/Screenshots/trade_journal`

`GEX_DASHBOARD_DATA_DIR` overrides the dashboard data directory in both dev and packaged runs. Schwab tokens still default to `~/.schwabdev/tokens.db`; `SCHWAB_TOKENS_DB` overrides that path. Existing repo-local data is not moved automatically.

## Qt WebEngine Packaging

`packaging/gex_dashboard_macos.spec` explicitly collects the PySide6 Qt WebEngine resources, translations, and key plugins that Chromium-backed `QWebEngineView` needs. If a future PyInstaller/PySide6 combination still launches to a blank WebEngine page or reports missing Qt resources, rebuild with the broader fallback:

```bash
python3 -m PyInstaller --clean --noconfirm --onedir --windowed --name "GEX Dashboard" --collect-all PySide6 desktop_app.py
```
