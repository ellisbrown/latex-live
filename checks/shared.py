"""Shared mode's checks, which take the longest: the first ones (shared_setup.shared_start_checks) beside the owner's
preview, then the rest in a thread of their own (shared_run), in order, beside the rebuild checks, which have an
instance and a thread of their own (shared_rebuild.rebuild_run). A new area of checks goes in a module of its own,
below this one, and in shared_run's list."""

from .base import in_thread
from .shared_access import identity_checks, role_checks
from .shared_browser import shared_browser_checks
from .shared_commits import commit_checks, shared_stop_checks
from .shared_downloads import export_checks, review_shared_checks
from .shared_editor import editor_checks
from .shared_files import file_checks
from .shared_notes import note_checks
from .shared_rebuild import rebuild_run
from .shared_sandbox import sandbox_checks
from .shared_sessions import session_checks
from .shared_setup import shared_start_checks


def shared_checks(sh, sharing, own, token):
    """Start the shared-mode checks, which take the longest, in threads of their own: they have instances and papers
    of their own, so they run beside the others. The first ones run here, before the takeover checks replace the
    owner's preview, which they reach. Returns the threads; each one's .failed says whether a check failed."""
    shared_start_checks(sh, own, token)
    return [in_thread("shared-mode checks", shared_run, sh, sharing),
            in_thread("shared-mode rebuild checks", rebuild_run, sh["rebuild"], sharing)]


def shared_run(sh, sharing):
    """The shared-mode checks after the first ones, in order, ending with the instance's stop."""
    for check in (identity_checks, role_checks, session_checks, sandbox_checks, export_checks, review_shared_checks, note_checks,
                  editor_checks, commit_checks, file_checks, shared_browser_checks):
        check(sh)
    shared_stop_checks(sh, sharing)
