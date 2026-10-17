"""Menu bar icon for the packaged Mac app.

macOS only runs one copy of an app: double-clicking it again asks the running copy to
"reopen". A plain Python program can't answer that (macOS then says the app "is not open
anymore"), so the packaged app runs a tiny Cocoa loop that handles reopen by opening the
browser, and adds a menu bar icon with Open and Quit.
"""

from __future__ import annotations

import webbrowser
from typing import Callable

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

    def show() -> None:
        if open_browser:
            webbrowser.open(url)

    class Delegate(NSObject):
        def openApp_(self, _sender):
            show()

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
    image = None
    if hasattr(NSImage, "imageWithSystemSymbolName_accessibilityDescription_"):
        image = NSImage.imageWithSystemSymbolName_accessibilityDescription_("doc.richtext", "Article Writer")
    if image is not None:
        image.setTemplate_(True)
        item.button().setImage_(image)
    else:
        item.button().setTitle_("AW")
    item.button().setToolTip_(f"Article Writer is running at {url}")

    menu = NSMenu.alloc().init()
    for title, action in (("Open Article Writer", "openApp:"), (None, None), ("Quit Article Writer", "quitApp:")):
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
