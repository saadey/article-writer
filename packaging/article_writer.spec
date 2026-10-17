# PyInstaller build recipe:  pyinstaller packaging/article_writer.spec
# Produces dist/Article Writer.app (macOS) or dist/ArticleWriter/ArticleWriter.exe (Windows).
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent
sys.path.insert(0, str(ROOT))
from article_pipeline import __version__  # noqa: E402

datas = [
    (str(ROOT / "article_pipeline" / "web" / "templates"), "article_pipeline/web/templates"),
    (str(ROOT / "article_pipeline" / "web" / "static"), "article_pipeline/web/static"),
    (str(ROOT / "prompts"), "prompts"),
    (str(ROOT / "config.yaml"), "."),
]

a = Analysis(
    [str(ROOT / "app.py")],
    pathex=[str(ROOT)],
    datas=datas,
    # Markdown loads its extensions by name at runtime, so list them explicitly.
    hiddenimports=collect_submodules("markdown.extensions"),
    excludes=["tkinter", "unittest", "pydoc_data"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ArticleWriter",
    console=False,  # no terminal window: the app lives in your browser (use Quit there)
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="ArticleWriter", upx=False)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Article Writer.app",
        bundle_identifier="local.articlewriter.app",
        version=__version__,
        info_plist={
            "CFBundleShortVersionString": __version__,
            "LSUIElement": True,  # runs in the background; no Dock icon or menu bar
            "NSHighResolutionCapable": True,
        },
    )
