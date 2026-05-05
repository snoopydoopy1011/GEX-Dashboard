"""PySide6 desktop shell for the GEX Dashboard.

This launcher supplements desktop_launcher.py. It keeps the existing Flask
dashboard runtime in ezoptionsschwab.py, serves it in-process with Werkzeug,
and hosts the route-backed dashboard pages inside QWebEngineView windows.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from desktop_launcher import (
    DEFAULT_HEIGHT,
    DEFAULT_MIN_SIZE,
    DEFAULT_TITLE,
    DEFAULT_WIDTH,
    DashboardServer,
    desktop_dashboard_url,
)


QT_IMPORT_ERROR = None
try:
    from PySide6.QtCore import QTimer, QUrl
    from PySide6.QtGui import QAction, QKeySequence
    from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEngineScript
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QApplication, QMainWindow
except ImportError as exc:  # pragma: no cover - exercised when optional deps are absent
    QT_IMPORT_ERROR = exc


HOSTED_CHILD_WIDTH = 1100
HOSTED_CHILD_HEIGHT = 760
HOSTED_CHILD_MIN_SIZE = (900, 600)


def desktop_app_support_dir() -> Path:
    override = os.getenv("GEX_DESKTOP_APP_SUPPORT_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / "Library" / "Application Support" / "GEX Dashboard"


def pyside_storage_dir() -> Path:
    override = os.getenv("GEX_QTWEBENGINE_STORAGE_DIR")
    if override:
        return Path(override).expanduser()
    return desktop_app_support_dir() / "pyside6"


def configure_profile(profile) -> None:
    root = pyside_storage_dir()
    storage_path = root / "storage"
    cache_path = root / "cache"
    storage_path.mkdir(parents=True, exist_ok=True)
    cache_path.mkdir(parents=True, exist_ok=True)

    profile.setPersistentStoragePath(str(storage_path))
    profile.setCachePath(str(cache_path))

    try:
        profile.setPersistentCookiesPolicy(QWebEngineProfile.ForcePersistentCookies)
    except AttributeError:
        profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
        )

    try:
        profile.setHttpCacheType(QWebEngineProfile.DiskHttpCache)
    except AttributeError:
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)

    try:
        profile.setHttpCacheMaximumSize(64 * 1024 * 1024)
    except AttributeError:
        pass


def _qt_enum(container, name: str, group: str):
    direct = getattr(container, name, None)
    if direct is not None:
        return direct
    return getattr(getattr(container, group), name)


def install_desktop_bridge(profile) -> None:
    """Expose a pywebview-compatible bridge inside Qt-hosted dashboard pages."""

    script = QWebEngineScript()
    script.setName("GEX PySide6 Desktop Bridge")
    script.setInjectionPoint(_qt_enum(QWebEngineScript, "DocumentCreation", "InjectionPoint"))
    script.setWorldId(_qt_enum(QWebEngineScript, "MainWorld", "ScriptWorldId"))
    script.setRunsOnSubFrames(False)
    script.setSourceCode(
        r"""
(function () {
  if (!window.pywebview) window.pywebview = {};
  if (!window.pywebview.api) window.pywebview.api = {};
  if (typeof window.pywebview.api.open_window === 'function') return;

  function routeFor(kind, params) {
    const normalized = String(kind || '').trim().toLowerCase();
    const state = params && typeof params === 'object' ? params : {};
    const query = new URLSearchParams({ desktop: '1' });
    const stateId = state.state_id || state.state;
    if (stateId) query.set('state', String(stateId));

    let path;
    if (normalized === 'price' || normalized === 'price-chart') {
      path = '/desktop/window/price';
    } else if (normalized === 'chart') {
      const chartId = String(state.chart_id || 'price-chart').trim() || 'price-chart';
      path = '/desktop/window/chart/' + encodeURIComponent(chartId);
    } else {
      throw new Error('Unsupported desktop window kind: ' + kind);
    }
    return new URL(path + '?' + query.toString(), window.location.origin).toString();
  }

  window.pywebview.api.open_window = function (kind, params) {
    const url = routeFor(kind, params || {});
    const target = 'gex_desktop_' + String(kind || 'window').replace(/[^a-z0-9_-]/gi, '_') + '_' + Date.now();
    const features = 'width=1100,height=760,menubar=no,toolbar=no,location=no,status=no,resizable=yes,scrollbars=yes';
    const opened = window.open(url, target, features);
    if (!opened) return Promise.reject(new Error('Desktop shell could not open a new window'));
    return Promise.resolve({ ok: true, url: url });
  };
})();
"""
    )
    profile.scripts().insert(script)


if QT_IMPORT_ERROR is None:

    class DashboardWebEngineView(QWebEngineView):
        def __init__(self, controller, parent=None):
            super().__init__(parent)
            self._controller = controller
            self.setPage(controller.create_page(self))

        def createWindow(self, window_type):  # noqa: N802 - Qt override name
            window = self._controller.open_native_window(
                url=None,
                title=DEFAULT_TITLE,
                width=HOSTED_CHILD_WIDTH,
                height=HOSTED_CHILD_HEIGHT,
                min_size=HOSTED_CHILD_MIN_SIZE,
            )
            return window.view


    class DashboardWindow(QMainWindow):
        def __init__(
            self,
            controller,
            url: str | None,
            title: str,
            width: int,
            height: int,
            min_size: tuple[int, int],
        ):
            super().__init__()
            self._controller = controller
            self._default_title = title

            self.view = DashboardWebEngineView(controller, self)
            self.setCentralWidget(self.view)
            self.setWindowTitle(title)
            self.resize(width, height)
            self.setMinimumSize(*min_size)
            self._build_menus()

            self.view.titleChanged.connect(self._sync_title)
            self.view.urlChanged.connect(self._sync_status_url)
            self.view.loadFinished.connect(self._load_finished)

            if url:
                self.view.load(QUrl(url))

        def _build_menus(self) -> None:
            file_menu = self.menuBar().addMenu("File")

            new_dashboard = QAction("New Dashboard Window", self)
            new_dashboard.triggered.connect(self._controller.open_dashboard_window)
            file_menu.addAction(new_dashboard)

            new_price = QAction("New Price Chart Window", self)
            new_price.triggered.connect(self.open_price_window_from_page)
            file_menu.addAction(new_price)

            file_menu.addSeparator()

            quit_action = QAction("Quit and Stop Server", self)
            quit_action.setShortcut(QKeySequence("Ctrl+Q"))
            quit_action.triggered.connect(self._controller.quit)
            file_menu.addAction(quit_action)

            view_menu = self.menuBar().addMenu("View")

            reload_action = QAction("Reload", self)
            reload_action.setShortcut(QKeySequence("Ctrl+R"))
            reload_action.triggered.connect(self.view.reload)
            view_menu.addAction(reload_action)

            fullscreen_action = QAction("Toggle Full Screen", self)
            fullscreen_action.setShortcut(QKeySequence("F11"))
            fullscreen_action.triggered.connect(self._toggle_full_screen)
            view_menu.addAction(fullscreen_action)

        def open_price_window_from_page(self) -> None:
            script = r"""
(() => {
  try {
    if (typeof openDashboardWindow !== 'function') {
      return { ok: false, error: 'Desktop window bridge is not ready' };
    }
    if (typeof buildPricePayload !== 'function') {
      return { ok: false, error: 'Price chart state is not available in this window' };
    }
    openDashboardWindow('price', buildPricePayload()).catch(error => {
      console.warn('Desktop price window failed:', error);
    });
    return { ok: true };
  } catch (error) {
    return { ok: false, error: String(error && error.message ? error.message : error) };
  }
})()
"""
            self.view.page().runJavaScript(script, self._price_window_result)

        def _price_window_result(self, result) -> None:
            if isinstance(result, dict) and result.get("ok"):
                self.statusBar().showMessage("Opening price chart window...", 3500)
                return
            error = "Price chart state is not available yet"
            if isinstance(result, dict) and result.get("error"):
                error = str(result["error"])
            self.statusBar().showMessage(error, 6500)

        def _toggle_full_screen(self) -> None:
            if self.isFullScreen():
                self.showNormal()
            else:
                self.showFullScreen()

        def _sync_title(self, title: str) -> None:
            self.setWindowTitle(title or self._default_title)

        def _sync_status_url(self, url) -> None:
            self.statusBar().showMessage(url.toString(), 2500)

        def _load_finished(self, ok: bool) -> None:
            if ok:
                self.statusBar().showMessage("Loaded", 1500)
            else:
                self.statusBar().showMessage("Page failed to load", 6500)

        def closeEvent(self, event):  # noqa: N802 - Qt override name
            self._controller.unregister_window(self)
            super().closeEvent(event)


    class DashboardController:
        def __init__(self, qt_app, server: DashboardServer, base_url: str, profile):
            self.qt_app = qt_app
            self.server = server
            self.base_url = base_url.rstrip("/")
            self.profile = profile
            self.windows: list[DashboardWindow] = []
            self._server_stopped = False
            self._quitting = False
            self.qt_app.aboutToQuit.connect(self.shutdown)

        def create_page(self, parent):
            from PySide6.QtWebEngineCore import QWebEnginePage

            return QWebEnginePage(self.profile, parent)

        def dashboard_url(self) -> str:
            return desktop_dashboard_url(f"{self.base_url}/")

        def open_dashboard_window(self) -> None:
            self.open_native_window(
                url=self.dashboard_url(),
                title=DEFAULT_TITLE,
                width=DEFAULT_WIDTH,
                height=DEFAULT_HEIGHT,
                min_size=DEFAULT_MIN_SIZE,
            )

        def open_native_window(
            self,
            url: str | None,
            title: str,
            width: int,
            height: int,
            min_size: tuple[int, int],
        ) -> DashboardWindow:
            window = DashboardWindow(self, url, title, width, height, min_size)
            self.windows.append(window)
            window.show()
            return window

        def open_price_window_from_first_window(self) -> None:
            if not self.windows:
                return
            self.windows[0].open_price_window_from_page()

        def unregister_window(self, window: DashboardWindow) -> None:
            if window in self.windows:
                self.windows.remove(window)
            if not self.windows and not self._quitting:
                self.shutdown()
                QTimer.singleShot(0, lambda: self.qt_app.exit(0))

        def quit(self) -> None:
            if self._quitting:
                return
            self._quitting = True
            for window in list(self.windows):
                window.close()
            self.shutdown()
            QTimer.singleShot(0, lambda: self.qt_app.exit(0))

        def shutdown(self) -> None:
            if self._server_stopped:
                return
            self._server_stopped = True
            self.server.shutdown()
            try:
                from ezoptionsschwab import price_streamer

                price_streamer.stop()
            except Exception:
                pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch the GEX Dashboard in a PySide6 shell.")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("GEX_DESKTOP_PORT") or os.getenv("PORT") or "5001"),
        help="Preferred local port. If busy, the launcher selects an open port.",
    )
    parser.add_argument("--title", default=DEFAULT_TITLE, help="Desktop window title.")
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH, help="Initial window width.")
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT, help="Initial window height.")
    parser.add_argument(
        "--remote-debugging-port",
        type=int,
        default=None,
        help="Enable Qt WebEngine remote debugging on the given port.",
    )
    parser.add_argument(
        "--auto-close-after",
        type=float,
        default=None,
        help="Close the PySide6 shell after N seconds. Intended for launcher smoke tests.",
    )
    parser.add_argument(
        "--smoke-open-price-window-after",
        type=float,
        default=None,
        help="Open a route-backed price chart window after N seconds. Intended for shell smoke tests.",
    )
    return parser.parse_args(argv)


def _print_missing_qt_error() -> None:
    print(
        "PySide6 is not installed. Install desktop dependencies with "
        "`python3 -m pip install -r requirements-desktop.txt`.",
        file=sys.stderr,
    )
    if QT_IMPORT_ERROR:
        print(f"Import error: {QT_IMPORT_ERROR}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if QT_IMPORT_ERROR is not None:
        _print_missing_qt_error()
        return 1

    if args.remote_debugging_port:
        os.environ["QTWEBENGINE_REMOTE_DEBUGGING"] = str(args.remote_debugging_port)

    from ezoptionsschwab import app as flask_app

    server = DashboardServer(flask_app, args.port)
    controller = None
    try:
        url = server.start()
        print(f"GEX Dashboard PySide6 shell serving {url}")

        qt_app = QApplication.instance() or QApplication([sys.argv[0]])
        qt_app.setApplicationName(DEFAULT_TITLE)
        qt_app.setQuitOnLastWindowClosed(False)

        profile = QWebEngineProfile("GEX Dashboard", qt_app)
        configure_profile(profile)
        install_desktop_bridge(profile)

        controller = DashboardController(qt_app, server, url, profile)
        controller.open_native_window(
            url=desktop_dashboard_url(url),
            title=args.title,
            width=args.width,
            height=args.height,
            min_size=DEFAULT_MIN_SIZE,
        )

        if args.auto_close_after is not None:
            QTimer.singleShot(int(max(0.0, args.auto_close_after) * 1000), controller.quit)
        if args.smoke_open_price_window_after is not None:
            QTimer.singleShot(
                int(max(0.0, args.smoke_open_price_window_after) * 1000),
                controller.open_price_window_from_first_window,
            )

        return int(qt_app.exec())
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"PySide6 desktop shell failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if controller is not None:
            controller.shutdown()
        else:
            server.shutdown()
            try:
                from ezoptionsschwab import price_streamer

                price_streamer.stop()
            except Exception:
                pass


if __name__ == "__main__":
    exit_code = main()
    if getattr(sys, "frozen", False):
        os._exit(exit_code)
    raise SystemExit(exit_code)
