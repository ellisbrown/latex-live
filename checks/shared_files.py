"""Shared mode's Files tab (file_checks): editors and the owner make, upload, rename and remove files (viewers and
commenters can't: role_checks); a coauthor's figures are the document's; the upload limits and body size; and every
change is committed as its writer's, a file another wrote first committed as theirs before it is renamed."""

import base64
import os

from .base import SHARED_PORT, fail, ok
from .helpers import git_out, post_length, put_bytes
from .shared_helpers import PROXY_HOST, PUBLIC, commit_user, expect, expect_dict, header, send, via_proxy, with_token

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def file_checks(sh):
    """As the editor ed (through the proxy) and the editor tk (a personal link): a source made, a figure uploaded (with
    a sandboxing CSP when sent back), the same names refused again, a source refused as an upload; a figure the document
    doesn't use is the owner's alone; tk renames ed's figure, and ed removes the source made: the owner's POST commit
    holds ed's commit (the two files added), then tk's (the rename) and ed's removal."""
    root, tk = sh["root"], sh["users"]["tk"]["token"]
    tip = git_out(root, "rev-parse", "shared")
    as_tk = lambda path, body: with_token(tk, path, "POST", body, host="paper.invalid:8443", origin=PUBLIC)
    data = base64.b64encode(PNG).decode()
    expect(via_proxy("ed", "/file/create", "POST", {"path": "sections/ed-new.tex", "text": "New section.\n"}), 200, "ed making a source")
    expect(via_proxy("ed", "/file/upload", "POST", {"path": "figures/ed-plot.png", "data": data}), 200, "ed uploading a figure")
    for path, body, status, what in (("/file/create", {"path": "sections/ed-new.tex"}, 409, "making that source again"),
                                     ("/file/upload", {"path": "figures/ed-plot.png", "data": data}, 409, "an upload over that figure"),
                                     ("/file/upload", {"path": "sections/up.tex", "data": data}, 400, "uploading a source"),
                                     ("/file/upload", {"path": "Build/x.png", "data": data}, 400, "an upload into the build folder"),
                                     ("/file/rename", {"from": "main.tex", "to": "paper.tex"}, 404, "renaming the main file"),
                                     ("/file/delete", {"path": "main.tex"}, 404, "removing the main file")):
        expect(via_proxy("ed", path, "POST", body), status, f"{what}, for the editor")
    listing = expect_dict(via_proxy("ed", "/files"), 200, "GET files for the editor")
    if "sections/ed-new.tex" not in [f["path"] for f in listing.get("files", [])] \
            or [a["path"] for a in listing.get("assets", [])] != ["figures/ed-plot.png"]:
        fail(f"GET files for the editor, after making a source and uploading a figure: {listing}")
    figure = via_proxy("ed", "/asset?path=figures/ed-plot.png")
    if figure[0] != 200 or figure[2] != PNG or header(figure, "Content-Type") != "image/png" \
            or "sandbox" not in (header(figure, "Content-Security-Policy") or ""):
        fail(f"GET asset for the editor: {figure[0]} {header(figure, 'Content-Type')} {header(figure, 'Content-Security-Policy')}")
    if "sandbox" in (header(via_proxy("ed", "/viewer.html?file=main.pdf"), "Content-Security-Policy") or ""):
        fail("the viewer's page is sent with a figure's sandboxing CSP")
    ok("in shared mode, an editor makes a source and uploads a figure (sent back with a CSP that runs nothing), not over"
       " another, nor a source as an upload, nor into the build folder; the main file stays")

    expect_dict(with_token(sh["token"], "/commit", "POST", {}), 200, "the owner's POST commit")  # (ed's two files, at once)
    put_bytes(os.path.join(root, "unused", "owners.png"), PNG)  # (in the copy, but not the document's)
    for result, what in ((via_proxy("ed", "/asset?path=unused/owners.png"), "reading"),
                         (via_proxy("ed", "/file/rename", "POST", {"from": "unused/owners.png", "to": "figures/mine.png"}), "renaming"),
                         (via_proxy("ed", "/file/delete", "POST", {"path": "unused/owners.png"}), "removing")):
        expect(result, 404, f"the editor {what} a figure the document doesn't use")
    expect(with_token(sh["token"], "/asset?path=unused/owners.png"), 200, "the owner reading a figure the document doesn't use")
    os.remove(os.path.join(root, "unused", "owners.png"))
    os.rmdir(os.path.join(root, "unused"))
    ok("a coauthor's figures are the document's (those its build read and those added); the owner's, every one")

    expect(as_tk("/file/rename", {"from": "figures/ed-plot.png", "to": "figures/tk-plot.png"}), 200, "tk renaming ed's figure")
    expect(via_proxy("ed", "/file/delete", "POST", {"path": "sections/ed-new.tex"}), 200, "ed removing the source made")
    flushed = expect_dict(with_token(sh["token"], "/commit", "POST", {}), 200, "the owner's POST commit")
    shas = git_out(root, "rev-list", "--reverse", f"{tip}..shared").split()
    made = [(commit_user(root, sha), sorted(git_out(root, "show", "--name-status", "--format=", "--no-renames", sha).splitlines()))
            for sha in shas]
    want = [("ed", ["A\tfigures/ed-plot.png", "A\tsections/ed-new.tex"]),
            ("tk", ["A\tfigures/tk-plot.png", "D\tfigures/ed-plot.png"]),
            ("ed", ["D\tsections/ed-new.tex"])]
    if made[:1] != want[:1] or sorted(made[1:]) != sorted(want[1:]):
        fail(f"the Files tab's changes, committed: {made} ({flushed}); want ed's two files added first, then tk's rename and"
             " ed's removal, each as its writer's")
    if git_out(root, "status", "--porcelain", "--untracked-files=all", "--", "figures", "sections"):
        fail(f"the Files tab's changes left something uncommitted: {git_out(root, 'status', '--porcelain', '--', 'figures', 'sections')}")
    expect(as_tk("/file/delete", {"path": "figures/tk-plot.png"}), 200, "tk removing the figure")
    expect_dict(with_token(sh["token"], "/commit", "POST", {}), 200, "the owner's POST commit")
    ok("the Files tab's changes are committed as each writer's: files added, a rename (after the first writer's commit of"
       " the file), removals")

    proxy = [("Host", PROXY_HOST), ("X-Test-User", "ed"), ("Origin", PUBLIC)]
    if (status := post_length(SHARED_PORT, "/file/upload", 14_000_001, proxy)) != 413:
        fail(f"a POST file/upload announcing 14 000 001 bytes: {status}, not 413")
    expect(send("POST", "/file/upload", [("Host", PROXY_HOST), ("X-Test-User", "vw"), ("Origin", PUBLIC)],
                {"path": "figures/v.png", "data": data}), 403, "an upload by a viewer")
    ok("an upload's body may be 14 MB (a 10 MB figure as base64), not more")
