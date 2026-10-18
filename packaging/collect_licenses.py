"""Write THIRD_PARTY_LICENSES.txt and third_party.json (bundled into the app at build time).

Run in the build environment, before PyInstaller:  python packaging/collect_licenses.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from article_pipeline.licenses import write_bundle_files  # noqa: E402

if __name__ == "__main__":
    out = ROOT / "build" / "licenses"
    out.mkdir(parents=True, exist_ok=True)
    write_bundle_files(out)
    print(f"Wrote third-party licenses to {out}")
