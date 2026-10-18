# PyInstaller build recipe:  pyinstaller packaging/article_writer.spec
# Produces dist/Article Writer.app (macOS) or dist/ArticleWriter/ArticleWriter.exe (Windows).
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent
sys.path.insert(0, str(ROOT))
from article_pipeline import APP_NAME, AUTHOR, COPYRIGHT, __version__  # noqa: E402

LICENSES = ROOT / "build" / "licenses"  # written by packaging/collect_licenses.py
if not (LICENSES / "THIRD_PARTY_LICENSES.txt").exists():
    raise SystemExit("Run `python packaging/collect_licenses.py` before building.")

datas = [
    (str(ROOT / "article_pipeline" / "web" / "templates"), "article_pipeline/web/templates"),
    (str(ROOT / "article_pipeline" / "web" / "static"), "article_pipeline/web/static"),
    (str(ROOT / "prompts"), "prompts"),
    (str(ROOT / "config.yaml"), "."),
    (str(ROOT / "LICENSE"), "."),
    (str(LICENSES / "THIRD_PARTY_LICENSES.txt"), "."),
    (str(LICENSES / "third_party.json"), "."),
]

version_info = None
if sys.platform == "win32":
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo,
    )

    nums = tuple(int(n) for n in __version__.split(".")) + (0,)
    version_info = VSVersionInfo(
        ffi=FixedFileInfo(filevers=nums, prodvers=nums),
        kids=[
            StringFileInfo([StringTable("040904B0", [
                StringStruct("CompanyName", AUTHOR),
                StringStruct("FileDescription", APP_NAME),
                StringStruct("FileVersion", __version__),
                StringStruct("InternalName", "ArticleWriter"),
                StringStruct("LegalCopyright", f"{COPYRIGHT}. MIT License."),
                StringStruct("OriginalFilename", "ArticleWriter.exe"),
                StringStruct("ProductName", APP_NAME),
                StringStruct("ProductVersion", __version__),
            ])]),
            VarFileInfo([VarStruct("Translation", [1033, 1200])]),
        ],
    )

a = Analysis(
    [str(ROOT / "app.py")],
    pathex=[str(ROOT)],
    datas=datas,
    # Markdown loads its extensions by name at runtime, so list them explicitly.
    hiddenimports=collect_submodules("markdown.extensions")
    + (["AppKit", "Foundation", "objc", "article_pipeline.macos_menu"] if sys.platform == "darwin" else []),
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
    icon=str(ROOT / "packaging" / "icons" / ("app.icns" if sys.platform == "darwin" else "app.ico")),
    version=version_info,
)
coll = COLLECT(exe, a.binaries, a.datas, name="ArticleWriter", upx=False)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Article Writer.app",
        icon=str(ROOT / "packaging" / "icons" / "app.icns"),
        bundle_identifier="io.github.saadey.articlewriter",
        version=__version__,
        info_plist={
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": __version__,
            "CFBundleVersion": __version__,
            "NSHumanReadableCopyright": f"{COPYRIGHT}. MIT License.",
            "LSApplicationCategoryType": "public.app-category.productivity",
            "LSMinimumSystemVersion": "11.0",
            "LSUIElement": True,  # menu bar icon only, no Dock icon
            "NSHighResolutionCapable": True,
        },
    )
