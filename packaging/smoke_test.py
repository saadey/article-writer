"""Start a built app with an empty data folder and check that its pages load.

Usage: python packaging/smoke_test.py PATH_TO_EXECUTABLE
"""

import os
import subprocess
import sys
import tempfile
import time
import urllib.request

PORT = 8765
PAGES = ["/", "/p/1/", "/p/1/found", "/p/1/drafts", "/p/1/settings", "/p/1/prompts", "/keys", "/projects",
         "/about", "/about/licenses", "/help", "/terms", "/welcome", "/static/logo.svg",
         "/static/style.css", "/static/app.js"]


def get(path: str) -> int:
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", headers={"Host": f"localhost:{PORT}"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status


def main() -> int:
    exe = sys.argv[1]
    home = tempfile.mkdtemp(prefix="article-writer-smoke-")
    env = dict(os.environ, ARTICLE_WRITER_HOME=home)
    proc = subprocess.Popen([exe, "--no-browser", "--port", str(PORT)], env=env)
    try:
        for _ in range(60):
            try:
                get("/api/ping")
                break
            except Exception:
                if proc.poll() is not None:
                    print(f"The app exited early with code {proc.returncode}.")
                    return 1
                time.sleep(1)
        else:
            print("The app did not start within 60 seconds.")
            return 1
        failures = 0
        for page in PAGES:
            try:
                status = get(page)
            except Exception as exc:
                status = exc
            print(f"{page}: {status}")
            failures += status != 200
        return 1 if failures else 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        for name in ("pipeline.log", "app-output.log"):
            log = os.path.join(home, "logs", name)
            if os.path.exists(log):
                print(f"--- {name} ---")
                print(open(log, encoding="utf-8").read()[-3000:])


if __name__ == "__main__":
    sys.exit(main())
