# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files


ROOT = Path(SPECPATH).resolve().parent

# PyInstaller's PySide6 hooks usually collect Qt WebEngine correctly. These
# explicit WebEngine resources keep the spec usable on local environments where
# the generic hook misses Chromium data files or translations.
pyside6_webengine_datas = collect_data_files(
    "PySide6",
    includes=[
        "Qt/lib/QtWebEngineCore.framework/Resources/**",
        "Qt/translations/qtwebengine_*.qm",
        "Qt/plugins/imageformats/**",
        "Qt/plugins/networkinformation/**",
        "Qt/plugins/platforms/**",
        "Qt/plugins/tls/**",
        "Qt/plugins/webview/**",
    ],
)

a = Analysis(
    [str(ROOT / "desktop_app.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=pyside6_webengine_datas,
    hiddenimports=[
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtNetwork",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWidgets",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GEX Dashboard",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="GEX Dashboard",
)
app = BUNDLE(
    coll,
    name="GEX Dashboard.app",
    icon=None,
    bundle_identifier="com.gexdashboard.desktop",
    info_plist={
        "CFBundleDisplayName": "GEX Dashboard",
        "CFBundleName": "GEX Dashboard",
        "NSHighResolutionCapable": "True",
    },
)
