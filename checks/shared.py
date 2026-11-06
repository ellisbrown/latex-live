"""Shared mode's checks, which take the longest: the first ones (shared_setup.shared_start_checks) beside the owner's
preview, then the rest in a thread of their own (shared_run), in order. A new area of checks goes in a module of
its own, below this one, and in shared_run's list."""

from .base import in_thread
from .shared_access import identity_checks, role_checks
from .shared_commits import commit_checks, shared_stop_checks
from .shared_downloads import export_checks, review_shared_checks
from .shared_editor import editor_checks
from .shared_notes import note_checks
from .shared_rebuild import rebuild_stop_checks
from .shared_sandbox import sandbox_checks
from .shared_setup import shared_start_checks


def shared_checks(sh, sharing, own, token):
    """Start the shared-mode checks, which take the longest, in a thread of their own: they have an instance and a
    paper of their own, so they run beside the others. The first ones run here, before the takeover checks replace
    the owner's preview, which they reach. Returns the thread; its .failed says whether a check failed."""
    shared_start_checks(sh, own, token)
    return in_thread("shared-mode checks", shared_run, sh, sharing)


def shared_run(sh, sharing):
    """The shared-mode checks after the first ones, in order, ending with the instance's stop."""
    for check in (identity_checks, role_checks, sandbox_checks, export_checks, review_shared_checks, note_checks, editor_checks,
                  rebuild_stop_checks, commit_checks):
        check(sh)
    shared_stop_checks(sh, sharing)
