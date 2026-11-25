"""The shared viewer in a browser, as each role (shared_browser_checks): browser_checks on the shared instance at its
public origin, each request answered as the trusted proxy forwards it for that user, so that the page's
Content-Security-Policy and the role's own page are what a browser gets."""

from .browser import browser_checks
from .shared_helpers import PUBLIC, via_proxy


def shared_browser_checks(sh):
    """The viewer, a commenter and an editor load the shared viewer in headless Chromium (if Playwright is there) with
    no page or console error, nor anything its policy refuses; the editor opens the source pane too."""
    for uid in ("vw", "cm", "ed"):
        role = sh["users"][uid]["role"]
        browser_checks(f"{PUBLIC}/viewer.html?file=main.pdf", editor=role == "editor",
                       answer=lambda method, path, body, uid=uid: via_proxy(uid, path, method, body),
                       who=f"the shared viewer for the {role}")
