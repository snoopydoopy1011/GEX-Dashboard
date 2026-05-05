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

## Local Signing

For local smoke testing, create a clean signed copy outside Desktop/iCloud/File
Provider-managed paths:

```bash
bash packaging/sign_macos_app.sh
```

The helper copies `dist/GEX Dashboard.app` with `ditto --norsrc --noextattr`,
ad-hoc signs the clean copy by default, and runs strict verification:

```bash
codesign --verify --deep --strict --verbose=2 "/private/tmp/.../GEX Dashboard.app"
```

To use a Developer ID identity instead of ad-hoc signing:

```bash
GEX_CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" \
GEX_CODESIGN_OPTIONS=runtime \
bash packaging/sign_macos_app.sh
```

Gatekeeper assessment for distribution still requires a valid Developer ID
certificate and notarization. Run it explicitly after Developer ID signing:

```bash
GEX_RUN_SPCTL=1 GEX_CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" \
GEX_CODESIGN_OPTIONS=runtime \
bash packaging/sign_macos_app.sh
```

If strict verification fails in `dist/` with `resource fork, Finder information,
or similar detritus not allowed`, treat that as a local extended-attribute issue
on the build path. Use the clean signed copy produced by the helper for local
validation or distribution prep.

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
