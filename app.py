"""Start the Article Writer app in your browser:  python app.py"""

import argparse
import logging
import socket
import sys
import threading
import warnings
import webbrowser

# macOS system Python ships LibreSSL, which makes urllib3 print a harmless warning.
warnings.filterwarnings("ignore", message=".*urllib3 v2 only supports OpenSSL.*")

from article_pipeline.config import LOG_DIR  # noqa: E402
from article_pipeline.logging_setup import setup_logging  # noqa: E402
from article_pipeline.web import create_app  # noqa: E402


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
    parser = argparse.ArgumentParser(description="Open the Article Writer app in your browser.")
    parser.add_argument("--port", type=int, default=8000, help="Port to use (default: 8000, or the next free one)")
    parser.add_argument("--no-browser", action="store_true", help="Don't open the browser automatically")
    args = parser.parse_args()

    setup_logging(LOG_DIR)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)  # hide per-request lines
    app = create_app()
    port = free_port(args.port)
    url = f"http://localhost:{port}"
    print(f"\n  Article Writer is running at {url}\n  Keep this window open while you use it. Press Ctrl+C to stop.\n")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    app.run(host="127.0.0.1", port=port, threaded=True, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
