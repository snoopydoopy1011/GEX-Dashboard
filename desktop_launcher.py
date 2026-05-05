"""Small pywebview desktop launcher for the GEX Dashboard.

This wrapper intentionally keeps the dashboard runtime in ezoptionsschwab.py.
It imports the existing Flask app, serves it with Werkzeug in-process, and
points a native pywebview window at the local URL.
"""

from __future__ import annotations

import argparse
import inspect
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from werkzeug.serving import make_server


HOST = "127.0.0.1"
DEFAULT_TITLE = "GEX Dashboard"
DEFAULT_WIDTH = 1440
DEFAULT_HEIGHT = 960
DEFAULT_MIN_SIZE = (1024, 720)
READY_TIMEOUT_SECONDS = 30.0


class DashboardWindowBridge:
    """Small JS API exposed to pywebview pages for route-backed windows."""

    def __init__(self, webview, base_url: str):
        self._webview = webview
        self._base_url = base_url.rstrip("/")

    def open_window(self, kind: str, params: dict | None = None) -> dict:
        params = params or {}
        url = self._window_url(kind, params)
        title = self._window_title(kind, params)
        kwargs = {
            "width": 1100,
            "height": 760,
            "min_size": (900, 600),
            "js_api": self,
        }
        self._webview.create_window(
            title,
            url,
            **_supported_kwargs(self._webview.create_window, kwargs),
        )
        return {"ok": True, "url": url}

    def _window_url(self, kind: str, params: dict) -> str:
        normalized = (kind or "").strip().lower()
        query = {"desktop": "1"}
        state_id = params.get("state_id") or params.get("state")
        if state_id:
            query["state"] = str(state_id)
        if normalized in {"price", "price-chart"}:
            path = "/desktop/window/price"
        elif normalized == "chart":
            chart_id = str(params.get("chart_id") or "price-chart").strip() or "price-chart"
            path = f"/desktop/window/chart/{urllib.parse.quote(chart_id)}"
        else:
            raise ValueError(f"Unsupported desktop window kind: {kind}")
        return f"{self._base_url}{path}?{urllib.parse.urlencode(query)}"

    def _window_title(self, kind: str, params: dict) -> str:
        normalized = (kind or "").strip().lower()
        ticker = str(params.get("ticker") or "").strip().upper()
        if normalized in {"price", "price-chart"}:
            return f"{ticker + ' ' if ticker else ''}Price Chart"
        return DEFAULT_TITLE


class DashboardServer:
    """Owns the in-process Werkzeug server used by the desktop wrapper."""

    def __init__(self, flask_app, preferred_port: int):
        self._flask_app = flask_app
        self._preferred_port = preferred_port
        self._server = None
        self._thread = None
        self.port = None

    def start(self) -> str:
        self._flask_app.debug = False
        self._server = self._make_server()
        self.port = _server_port(self._server)
        os.environ["PORT"] = str(self.port)
        os.environ["FLASK_DEBUG"] = "0"
        os.environ["FLASK_USE_RELOADER"] = "0"

        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="gex-dashboard-werkzeug",
            daemon=True,
        )
        self._thread.start()

        url = f"http://{HOST}:{self.port}/"
        wait_for_ready(url, READY_TIMEOUT_SECONDS)
        return url

    def shutdown(self) -> None:
        if self._server is None:
            return

        try:
            self._server.shutdown()
        finally:
            if self._thread is not None:
                self._thread.join(timeout=5.0)
            self._server.server_close()
            self._server = None
            self._thread = None

    def _make_server(self):
        try:
            return make_server(HOST, self._preferred_port, self._flask_app, threaded=True)
        except OSError as exc:
            if not _is_bind_failure(exc):
                raise
            print(
                f"Port {self._preferred_port} is unavailable; selecting an open port.",
                file=sys.stderr,
            )
            return make_server(HOST, 0, self._flask_app, threaded=True)


def _server_port(server) -> int:
    if getattr(server, "server_port", None):
        return int(server.server_port)
    return int(server.socket.getsockname()[1])


def _is_bind_failure(exc: OSError) -> bool:
    return getattr(exc, "errno", None) in {
        48,  # EADDRINUSE on macOS
        98,  # EADDRINUSE on Linux
        10048,  # WSAEADDRINUSE on Windows
    }


def wait_for_ready(url: str, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = None

    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:
                if 200 <= response.status < 400:
                    response.read(1)
                    return
                last_error = RuntimeError(f"Unexpected status {response.status} from {url}")
        except urllib.error.HTTPError as exc:
            last_error = exc
        except OSError as exc:
            last_error = exc
        time.sleep(0.2)

    raise RuntimeError(f"Dashboard server did not become ready at {url}") from last_error


def desktop_storage_path() -> str:
    override = os.getenv("GEX_WEBVIEW_STORAGE_DIR")
    if override:
        path = Path(override).expanduser()
    else:
        path = Path.home() / "Library" / "Application Support" / "GEX Dashboard" / "pywebview"
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def desktop_dashboard_url(url: str) -> str:
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{urllib.parse.urlencode({'desktop': '1'})}"


def create_window(webview, url: str, title: str, width: int, height: int, js_api=None):
    kwargs = {
        "width": width,
        "height": height,
        "min_size": DEFAULT_MIN_SIZE,
    }
    if js_api is not None:
        kwargs["js_api"] = js_api
    return webview.create_window(title, url, **_supported_kwargs(webview.create_window, kwargs))


def start_webview(
    webview,
    window,
    debug: bool,
    storage_path: str,
    auto_close_after: float | None,
    probe_desktop_features: bool,
) -> None:
    kwargs = {
        "debug": debug,
        "private_mode": False,
        "storage_path": storage_path,
    }
    supported = _supported_kwargs(webview.start, kwargs)
    if auto_close_after is None and not probe_desktop_features:
        webview.start(**supported)
    else:
        webview.start(
            _run_startup_tasks,
            (window, auto_close_after, probe_desktop_features),
            **supported,
        )


def _run_startup_tasks(window, auto_close_after: float | None, probe_desktop_features: bool) -> None:
    if probe_desktop_features:
        threading.Thread(
            target=_probe_desktop_features,
            args=(window,),
            name="gex-dashboard-feature-probe",
            daemon=True,
        ).start()
    if auto_close_after is not None:
        threading.Thread(
            target=_close_later,
            args=(window, auto_close_after),
            name="gex-dashboard-auto-close",
            daemon=True,
        ).start()


def _close_later(window, seconds: float) -> None:
    time.sleep(seconds)
    try:
        window.destroy()
    except Exception as exc:
        print(f"Auto-close failed: {exc}", file=sys.stderr)


def _probe_desktop_features(window) -> None:
    time.sleep(2.0)
    js = """
(() => {
  const result = {
    localStorageRoundTrip: false,
    localStoragePreviousValuePresent: false,
    windowOpenReturnedWindow: false,
    windowOpenHasOpener: false,
    windowOpenDocumentWritable: false,
    windowOpenError: null
  };
  try {
    const key = '__gex_desktop_probe';
    const previous = localStorage.getItem(key);
    const next = 'probe-' + Date.now();
    result.localStoragePreviousValuePresent = !!previous;
    localStorage.setItem(key, next);
    result.localStorageRoundTrip = localStorage.getItem(key) === next;
  } catch (error) {
    result.localStorageError = String(error && error.message ? error.message : error);
  }
  try {
    const popup = window.open('', 'gex_desktop_probe_popup', 'width=320,height=240');
    result.windowOpenReturnedWindow = !!popup;
    if (popup) {
      result.windowOpenHasOpener = !!popup.opener;
      try {
        popup.document.write('<!doctype html><title>GEX Probe</title><p>probe</p>');
        popup.document.close();
        result.windowOpenDocumentWritable = true;
      } catch (error) {
        result.windowOpenError = String(error && error.message ? error.message : error);
      }
      try { popup.close(); } catch (error) {}
    }
  } catch (error) {
    result.windowOpenError = String(error && error.message ? error.message : error);
  }
  return result;
})()
"""
    try:
        result = window.evaluate_js(js)
        print("Desktop feature probe: " + json.dumps(result, sort_keys=True))
    except Exception as exc:
        print(f"Desktop feature probe failed: {exc}", file=sys.stderr)


def _supported_kwargs(func, requested: dict) -> dict:
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return {}

    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()):
        return requested

    return {key: value for key, value in requested.items() if key in signature.parameters}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch the GEX Dashboard in a desktop window.")
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
        "--debug-webview",
        action="store_true",
        help="Enable pywebview debug tooling. Flask debug and reloader stay off.",
    )
    parser.add_argument(
        "--auto-close-after",
        type=float,
        default=None,
        help="Close the desktop window after N seconds. Intended for launcher smoke tests.",
    )
    parser.add_argument(
        "--probe-desktop-features",
        action="store_true",
        help="Print localStorage/window.open behavior. Intended for Phase 1 discovery.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        import webview
    except ImportError:
        print(
            "pywebview is not installed. Install desktop dependencies with "
            "`python3 -m pip install -r requirements-desktop.txt`.",
            file=sys.stderr,
        )
        return 1

    from ezoptionsschwab import app as flask_app

    server = DashboardServer(flask_app, args.port)
    try:
        url = server.start()
        print(f"GEX Dashboard desktop wrapper serving {url}")
        bridge = DashboardWindowBridge(webview, url)
        window = create_window(
            webview,
            desktop_dashboard_url(url),
            args.title,
            args.width,
            args.height,
            js_api=bridge,
        )
        start_webview(
            webview,
            window,
            args.debug_webview,
            desktop_storage_path(),
            args.auto_close_after,
            args.probe_desktop_features,
        )
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"Desktop launcher failed: {exc}", file=sys.stderr)
        return 1
    finally:
        server.shutdown()
        try:
            from ezoptionsschwab import price_streamer

            price_streamer.stop()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
