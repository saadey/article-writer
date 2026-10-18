"""Menu bar icon for the packaged Mac app.

macOS only runs one copy of an app: double-clicking it again asks the running copy to
"reopen". A plain Python program can't answer that (macOS then says the app "is not open
anymore"), so the packaged app runs a tiny Cocoa loop that handles reopen by opening the
browser, and adds a menu bar icon with Open and Quit.
"""

from __future__ import annotations

import webbrowser
from pathlib import Path
from typing import Callable

from . import __version__
from .logging_setup import get_logger

log = get_logger()


def run_menu_bar(url: str, on_quit: Callable[[], None], open_browser: bool = True) -> None:
    """Show the menu bar icon and block until the user quits. Call from the main thread."""
    import objc
    from AppKit import (
        NSApplication,
        NSApplicationActivationPolicyAccessory,
        NSImage,
        NSMenu,
        NSMenuItem,
        NSStatusBar,
        NSVariableStatusItemLength,
    )
    from Foundation import NSObject

    def show(path: str = "") -> None:
        if open_browser:
            webbrowser.open(url + path)

    class Delegate(NSObject):
        def openApp_(self, _sender):
            show()

        def aboutApp_(self, _sender):
            show("/about")

        def quitApp_(self, _sender):
            on_quit()
            NSApplication.sharedApplication().terminate_(None)

        # Double-clicking the app while it's running lands here.
        @objc.typedSelector(b"Z@:@Z")
        def applicationShouldHandleReopen_hasVisibleWindows_(self, _app, _visible):
            log.info("App opened again; showing it in the browser.")
            show()
            return False

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)  # menu bar only, no Dock icon
    delegate = Delegate.alloc().init()
    app.setDelegate_(delegate)

    item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
    glyph = Path(__file__).resolve().parent / "web" / "static" / "menubar@2x.png"
    image = NSImage.alloc().initWithContentsOfFile_(str(glyph)) if glyph.exists() else None
    if image is not None:
        image.setSize_((18, 18))  # the file is 36px: sharp on Retina screens
        image.setTemplate_(True)
        item.button().setImage_(image)
    else:
        item.button().setTitle_("AW")
    item.button().setToolTip_(f"Article Writer v{__version__} is running at {url}")

    menu = NSMenu.alloc().init()
    header = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(f"Article Writer v{__version__}", None, "")
    header.setEnabled_(False)
    menu.addItem_(header)
    menu.addItem_(NSMenuItem.separatorItem())
    entries = (("Open Article Writer", "openApp:"), ("About Article Writer", "aboutApp:"), (None, None),
               ("Quit Article Writer", "quitApp:"))
    for title, action in entries:
        if title is None:
            menu.addItem_(NSMenuItem.separatorItem())
            continue
        entry = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, "")
        entry.setTarget_(delegate)
        menu.addItem_(entry)
    item.setMenu_(menu)

    # Keep references alive for the lifetime of the app.
    run_menu_bar._keep = (delegate, item, menu)  # type: ignore[attr-defined]
    app.run()
