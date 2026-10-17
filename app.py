"""Start the Article Writer app in your browser:  python app.py
(The packaged app runs this same file when you double-click it.)"""

import argparse
import json
import logging
import os
import socket
import sys
import threading
import urllib.request
import warnings
import webbrowser

# macOS system Python ships LibreSSL, which makes urllib3 print a harmless warning.
warnings.filterwarnings("ignore", message=".*urllib3 v2 only supports OpenSSL.*")

from article_pipeline.config import APP_PORT_FILE as PORT_FILE, DATA_DIR, FROZEN, LOG_DIR  # noqa: E402


def redirect_output_if_windowless() -> None:
    """The packaged app has no terminal window; send any stray output to a log file."""
    if sys.stdout is None or sys.stderr is None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        stream = open(LOG_DIR / "app-output.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or stream
        sys.stderr = sys.stderr or stream


def running_instance_url():
    """If the app is already running, return its address."""
    try:
        port = json.loads(PORT_FILE.read_text())["port"]
        url = f"http://localhost:{port}"
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=1.5) as resp:
            if json.load(resp).get("app") == "article-writer":
                return url
    except Exception:
        pass
    return None


def free_port(start: int) -> int:
    for port in range(start, start + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    sys.exit(f"No free port found between {start} and {start + 19}.")


def main() -> None:
    redirect_output_if_windowless()
    parser = argparse.ArgumentParser(description="Open the Article Writer app in your browser.")
    parser.add_argument("--port", type=int, default=8000, help="Port to use (default: 8000, or the next free one)")
    parser.add_argument("--no-browser", action="store_true", help="Don't open the browser automatically")
    args = parser.parse_args()

    # Double-clicking again just reopens the browser instead of starting a second copy.
    existing = running_instance_url()
    if existing:
        print(f"Article Writer is already running at {existing}")
        if not args.no_browser:
            webbrowser.open(existing)
        return

    from article_pipeline.logging_setup import setup_logging
    from article_pipeline.web import create_app

    setup_logging(LOG_DIR)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)  # hide per-request lines
    app = create_app()
    port = free_port(args.port)
    url = f"http://localhost:{port}"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PORT_FILE.write_text(json.dumps({"port": port, "pid": os.getpid()}))

    how_to_stop = "Use Quit in the app to stop it." if FROZEN else "Press Ctrl+C (or Quit in the app) to stop."
    print(f"\n  Article Writer is running at {url}\n  {how_to_stop}\n")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()

    def serve() -> None:
        app.run(host="127.0.0.1", port=port, threaded=True, debug=False, use_reloader=False)

    try:
        if FROZEN and sys.platform == "darwin":
            # The Mac app needs the main thread for its menu bar icon; the server runs alongside.
            from article_pipeline.macos_menu import run_menu_bar

            threading.Thread(target=serve, daemon=True).start()
            run_menu_bar(url, on_quit=lambda: PORT_FILE.unlink(missing_ok=True), open_browser=not args.no_browser)
        else:
            serve()
    finally:
        PORT_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
