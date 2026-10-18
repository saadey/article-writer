"""Credits for the open-source software this app is built on.

The downloadable apps bundle these libraries (and Python itself), and their licenses ask
that their copyright notices travel with them. At build time packaging/collect_licenses.py
writes them into the app; when running from source they're read from the installed packages.
"""

from __future__ import annotations

import json
import re
import sys
import sysconfig
from importlib import metadata
from pathlib import Path

from .config import RESOURCE_ROOT

BUNDLED_TEXT = RESOURCE_ROOT / "THIRD_PARTY_LICENSES.txt"
BUNDLED_JSON = RESOURCE_ROOT / "third_party.json"

# Tools used only to build or develop the app; they are not shipped inside it.
BUILD_ONLY = {
    "pip", "setuptools", "wheel", "pyinstaller", "pyinstaller-hooks-contrib", "altgraph", "macholib",
    "pefile", "pywin32-ctypes", "packaging", "pillow",
}
LICENSE_FILE = re.compile(r"(^|/)(licen[cs]e|copying|notice|authors)[^/]*$", re.I)


def _license_name(dist) -> str:
    meta = dist.metadata
    expression = meta.get("License-Expression")
    if expression:
        return expression
    classifiers = [c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    if classifiers:
        return ", ".join(classifiers)
    text = (meta.get("License") or "").strip()
    return text.splitlines()[0][:60] if text else "See license text"


def _homepage(dist) -> str:
    meta = dist.metadata
    for entry in meta.get_all("Project-URL") or []:
        label, _, url = entry.partition(",")
        if label.strip().lower() in ("source", "source code", "homepage", "home", "repository", "github"):
            return url.strip()
    return (meta.get("Home-page") or "").strip()


def collect() -> list:
    """Installed runtime packages: name, version, license, homepage and license texts."""
    seen, packages = set(), []
    for dist in metadata.distributions():
        name = dist.metadata.get("Name") or ""
        key = name.lower().replace("_", "-")
        if not name or key in BUILD_ONLY or key in seen:
            continue
        seen.add(key)
        texts = []
        for f in dist.files or []:
            if LICENSE_FILE.search(str(f)):
                try:
                    texts.append(Path(dist.locate_file(f)).read_text(encoding="utf-8", errors="replace").strip())
                except OSError:
                    pass
        packages.append({
            "name": name, "version": dist.version, "license": _license_name(dist),
            "url": _homepage(dist), "texts": texts,
        })
    packages.sort(key=lambda p: p["name"].lower())
    packages.insert(0, {
        "name": "Python", "version": sys.version.split()[0], "license": "PSF-2.0",
        "url": "https://www.python.org", "texts": [_python_license()],
    })
    return packages


def _python_license() -> str:
    for base in (Path(sysconfig.get_paths()["stdlib"]), Path(sys.base_prefix)):
        for candidate in (base / "LICENSE.txt", base / "LICENSE"):
            if candidate.exists():
                return candidate.read_text(encoding="utf-8", errors="replace").strip()
    return "Python Software Foundation License Version 2. https://docs.python.org/3/license.html"


def as_text(packages: list) -> str:
    out = [
        "Article Writer includes the following open-source software.",
        "Each is the property of its respective authors and is used under its license, reproduced below.",
        "",
    ]
    for p in packages:
        out += ["=" * 78, f"{p['name']} {p['version']}  ({p['license']})", p["url"], "=" * 78, ""]
        out += [t + "\n" for t in p["texts"]] or ["(No license file was included with this package.)\n"]
    return "\n".join(out)


def write_bundle_files(target_dir: Path) -> None:
    packages = collect()
    (target_dir / BUNDLED_TEXT.name).write_text(as_text(packages), encoding="utf-8")
    summary = [{k: p[k] for k in ("name", "version", "license", "url")} for p in packages]
    (target_dir / BUNDLED_JSON.name).write_text(json.dumps(summary, indent=1), encoding="utf-8")


def summary() -> list:
    """For the About page: [{name, version, license, url}]."""
    if BUNDLED_JSON.exists():
        return json.loads(BUNDLED_JSON.read_text(encoding="utf-8"))
    return [{k: p[k] for k in ("name", "version", "license", "url")} for p in collect()]


def full_text() -> str:
    if BUNDLED_TEXT.exists():
        return BUNDLED_TEXT.read_text(encoding="utf-8")
    return as_text(collect())
