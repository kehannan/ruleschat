#!/usr/bin/env python
"""
Tests for the visual board viewer backend: render manifest
(vsav_service.render_manifest / render_pieces), background compositing +
counter-art extraction (app/services/board_render.py), and the authed
preview/asset endpoints (app/api/board_viewer.py).

Route handlers are called directly (FastAPI TestClient is broken in this
env); auth is asserted by inspecting the declared dependencies. Tests that
need local VASL content (board archives / the .vmod) no-op with a notice
when it is absent, mirroring test_vsav_service.py.

Runnable directly (`python tests/test_board_render.py`) or under pytest.
"""
import asyncio
import base64
import inspect
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import HTTPException
from fastapi.params import Depends as DependsParam

from app.core.auth import require_user
from app.services import board_render, board_terrain
from app.services.vsav_service import (
    BOARD_W, BOARD_H, DX, DY, LETTERS, parse_vsav, render_manifest,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "Hazmo-52-After-Finn-4.vsav"

_STATE = None


def _state():
    global _STATE
    if _STATE is None:
        _STATE = parse_vsav(FIXTURE)
    return _STATE


def _boards_available():
    return (board_terrain.find_board_archive("57") is not None
            and board_terrain.find_board_archive("69") is not None)


def _hex_center_map(manifest, board_base, hex_label):
    """Expected map-pixel center of a hex, from manifest board placement."""
    b = next(bb for bb in manifest["boards"] if bb["base"] == board_base)
    m = re.fullmatch(r"([A-Z]+)(\d+)", hex_label)
    i = LETTERS.index(m.group(1))
    r = int(m.group(2))
    xo = i * DX
    yo = r * DY if i % 2 == 1 else DY / 2 + (r - 1) * DY
    crop = b["crop"]
    cw = crop["w"] if crop["w"] > 0 else BOARD_W
    ch = crop["h"] if crop["h"] > 0 else BOARD_H
    if b["reversed"]:
        lx, ly = crop["x"] + cw - xo, crop["y"] + ch - yo
    else:
        lx, ly = xo - crop["x"], yo - crop["y"]
    return b["x"] + lx, b["y"] + ly


# --------------------------------------------------------------------------- #
# Render manifest
# --------------------------------------------------------------------------- #

def test_manifest_shape_and_geometry():
    man = render_manifest(_state())
    assert man["map"]["width"] == 1644 and man["map"]["height"] == 2090, man["map"]
    assert man["geometry"] == dict(dx=DX, dy=DY, edge=400,
                                   board_w=1800, board_h=645)
    # Hazmo: two half-width reversed boards stacked vertically, 69 on top
    boards = {b["base"]: b for b in man["boards"]}
    assert boards["69"]["x"] == 400 and boards["69"]["y"] == 400
    assert boards["57"]["x"] == 400 and boards["57"]["y"] == 1045
    assert boards["57"]["width"] == 844 and boards["57"]["height"] == 645
    assert boards["57"]["reversed"] and boards["69"]["reversed"]
    assert man["map"]["background_url"] is None  # no background passed


def test_manifest_known_hex_positions_match_geometry():
    """Pieces at 57-H9 / 57-K3 / 69-F1 must sit within half a hex pitch of
    the hex center computed from the manifest's own board placement —
    i.e. the per-piece px/py is consistent with the validated geometry."""
    man = render_manifest(_state())
    for hx in ("57-H9", "57-K3", "69-F1"):
        board, label = hx.split("-")
        pieces = [p for p in man["pieces"] if p["hex"] == hx]
        assert pieces, f"no pieces in {hx}"
        ex, ey = _hex_center_map(man, board, label)
        for p in pieces:
            assert abs(p["px"] - ex) <= DX / 2 + 1, (hx, p, ex)
            assert abs(p["py"] - ey) <= DY / 2 + 1, (hx, p, ey)


def test_manifest_all_pieces_hex_consistent():
    """Client-side inverse check: replaying map_xy_to_hex over the manifest
    boards must reproduce every piece's hex (the formula board-viewer.js
    ports)."""
    from app.services.vsav_service import map_xy_to_hex, EDGE
    man = render_manifest(_state())
    # rebuild parse-time board boxes from the manifest entries
    boards = [dict(base=b["base"], reversed=b["reversed"], crop=b["crop"],
                   box=dict(x0=b["x"], y0=b["y"],
                            x1=b["x"] + b["width"], y1=b["y"] + b["height"]))
              for b in man["boards"]]
    n = 0
    for p in man["pieces"]:
        if p["hex"] is None:
            continue
        b, hx = map_xy_to_hex(boards, p["px"], p["py"])
        assert b is not None and f"{b['base']}-{hx}" == p["hex"], p
        n += 1
    assert n > 50, f"expected many on-board pieces, got {n}"


def test_manifest_stack_order_and_draw_order():
    """Within a stack: stack_index runs bottom->top and pieces appear in
    draw order (the 57-H9 Foxhole sits above the units, Skis on top)."""
    man = render_manifest(_state())
    h9 = [p for p in man["pieces"] if p["hex"] == "57-H9"]
    assert [p["stack_index"] for p in h9] == list(range(len(h9))), h9
    names = [p["name"] for p in h9]
    assert names.index("Foxhole") > names.index("6-4-8 1sq"), names
    assert all(p["stack"] == h9[0]["stack"] for p in h9), h9
    assert all(p["stack_size"] == len(h9) for p in h9), h9
    # draw order: indices in the global list are consecutive per stack
    idxs = [man["pieces"].index(p) for p in h9]
    assert idxs == list(range(idxs[0], idxs[0] + len(h9))), idxs


def test_manifest_art_layers():
    """Art layers: squads expose their identity-layer SVG; pieces whose
    basic image is blank (Foxhole, leaders, Pin) still get art from their
    active Layer traits, composited bottom->top."""
    man = render_manifest(_state())
    h9 = {p["name"]: p for p in man["pieces"] if p["hex"] == "57-H9"}
    assert h9["6-4-8 1sq"]["art"] == ["fi/fi648S.svg"], h9["6-4-8 1sq"]
    fox = h9["Foxhole"]["art"]
    assert fox and fox[0] == "ML/_brownd58.svg" and "ML/Fox1" in fox, fox
    assert h9["Foxhole"]["is_marker"] is True
    k3 = {p["name"]: p for p in man["pieces"] if p["hex"] == "57-K3"}
    assert k3["37L AT PTP obr. 30"]["art"] == ["ru/gun/ruAT37L.svg"], k3
    f1 = [p for p in man["pieces"] if p["hex"] == "69-F1"]
    assert f1[0]["art"][0] == "ru/veh/T26M332.svg", f1


def test_manifest_includes_margin_pieces():
    """Pieces staged in the map margin (offboard) are included with
    hex=None — they render against the empty backdrop."""
    man = render_manifest(_state())
    off = [p for p in man["pieces"] if p["hex"] is None]
    assert off, "fixture has staged margin pieces"
    assert all(p["px"] is not None for p in off)


# --------------------------------------------------------------------------- #
# Background compositing
# --------------------------------------------------------------------------- #

def test_background_build_and_cache_reuse():
    if not _boards_available():
        print("    (skipped: no local board archives)")
        return
    bg = board_render.build_background(_state())
    assert bg is not None
    assert bg["x"] == 400 and bg["y"] == 400, bg
    assert bg["width"] == 844 and bg["height"] == 1290, bg
    assert bg["missing_boards"] == [], bg
    assert re.fullmatch(r"[0-9a-f]{16}", bg["cache_key"]), bg
    assert bg["url"] == f"/api/board-bg/{bg['cache_key']}.png", bg
    path = board_render.cached_background_path(bg["cache_key"])
    assert path.is_file(), path
    from PIL import Image
    with Image.open(path) as im:
        assert im.size == (844, 1290), im.size
    # second call must reuse the cached file (no rewrite)
    mtime = path.stat().st_mtime_ns
    bg2 = board_render.build_background(_state())
    assert bg2["cache_key"] == bg["cache_key"]
    assert path.stat().st_mtime_ns == mtime, "cache was rebuilt"


def test_background_missing_archives_degrade_gracefully():
    orig = board_terrain.BOARD_SEARCH_DIRS
    board_terrain.BOARD_SEARCH_DIRS = [Path("/nonexistent-boards-dir")]
    orig_cache = board_render.CACHE_DIR
    board_render.CACHE_DIR = Path(tempfile.mkdtemp()) / "render_cache"
    try:
        bg = board_render.build_background(_state())
        assert bg is not None
        assert sorted(bg["missing_boards"]) == ["57", "69"], bg
        assert board_render.cached_background_path(bg["cache_key"]).is_file()
    finally:
        board_terrain.BOARD_SEARCH_DIRS = orig
        board_render.CACHE_DIR = orig_cache


def test_background_cache_key_strictness():
    for bad in ("../../etc/passwd", "ABCDEF0123456789", "0" * 15, "0" * 17,  # gitleaks:allow (test value: uppercase hex must be rejected)
                "g" * 16, "", "0" * 16 + "/x"):
        try:
            board_render.cached_background_path(bad)
        except board_render.BoardRenderError:
            continue
        raise AssertionError(f"cache key {bad!r} should be rejected")
    assert board_render.cached_background_path("0123456789abcdef").name == \
        "bg_0123456789abcdef.png"


# --------------------------------------------------------------------------- #
# Counter art extraction + sanitization
# --------------------------------------------------------------------------- #

def test_art_path_allowlist():
    ok = ["fi/fi648S.svg", "ru/veh/T26M332.svg", "MS/dm.svg", "sh/skis.png",
          "ML/_white58", "mBMG malf.svg", "sh/skis off.png", "fi/fiL8+1.svg"]
    for p in ok:
        assert board_render.ART_PATH_RE.fullmatch(p), p
    bad = ["../images/x.svg", "fi/../../etc/passwd", "/etc/passwd",
           "fi\\fi648S.svg", "fi/.hidden.svg", "a.b.svg", "x.svg.exe",
           "fi//x.svg", "", ".", "..", "x.jpg", "con%2e%2e/x.svg",
           " leading.svg"]
    for p in bad:
        assert not board_render.ART_PATH_RE.fullmatch(p), p


def test_art_extraction_and_traversal_rejection():
    if board_render.find_vmod() is None:
        print("    (skipped: no local VASL vmod)")
        return
    orig_cache = board_render.CACHE_DIR
    orig_art = board_render.ART_CACHE_DIR
    tmp = Path(tempfile.mkdtemp())
    board_render.CACHE_DIR = tmp
    board_render.ART_CACHE_DIR = tmp / "art"
    try:
        path, media = board_render.extract_counter_art("fi/fi648S.svg")
        assert path.is_file() and media == "image/svg+xml", (path, media)
        assert path.read_bytes().lstrip().startswith(b"<?xml")
        # extension-less VASL reference resolves (tries .gif/.png/.svg)
        path2, media2 = board_render.extract_counter_art("ML/_white58")
        assert path2.suffix == ".gif" and media2 == "image/gif"
        # cached: second call returns the same file
        path3, _ = board_render.extract_counter_art("fi/fi648S.svg")
        assert path3 == path
        for bad in ("../secret.svg", "a/../../b.svg", "/abs.svg",
                    "savedGame", "no-such-counter-xyz.svg"):
            try:
                board_render.extract_counter_art(bad)
            except board_render.BoardRenderError:
                continue
            raise AssertionError(f"{bad!r} should be rejected")
    finally:
        board_render.CACHE_DIR = orig_cache
        board_render.ART_CACHE_DIR = orig_art


# --------------------------------------------------------------------------- #
# API endpoints (handlers called directly; TestClient is broken in this env)
# --------------------------------------------------------------------------- #

# Load the router module by file path: importing the app.api package would
# pull in chat.py, which constructs an OpenAI client at import time (needs
# network config this test env doesn't have).
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "board_viewer_under_test",
    Path(__file__).resolve().parent.parent / "app" / "api" / "board_viewer.py")
board_viewer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(board_viewer)


class _FakeUser:
    id = 1
    email = "tester@example.com"


def _requires_auth(handler):
    """True if the route handler declares a Depends(require_user) parameter."""
    for p in inspect.signature(handler).parameters.values():
        d = p.default
        if isinstance(d, DependsParam) and d.dependency is require_user:
            return True
    return False


def test_endpoints_are_public_by_design():
    """The board viewer runs on the public /demo page too, so these
    endpoints are deliberately unauthenticated (decided 2026-06-12).
    Safety relies on input validation instead: size caps + zip checks on
    preview, strict hex keys on board-bg, the allowlist on counter-art."""
    assert not _requires_auth(board_viewer.vsav_preview)
    assert not _requires_auth(board_viewer.get_board_background)
    assert not _requires_auth(board_viewer.get_counter_art)


class _StubRequest:
    """Just enough of fastapi.Request for direct handler calls."""
    def __init__(self, ip="203.0.113.1", forwarded=None):
        self.headers = ({"x-forwarded-for": forwarded} if forwarded else {})
        self.client = type("_C", (), {"host": ip})()


def test_preview_endpoint_happy_path():
    raw = FIXTURE.read_bytes()
    data_url = ("data:application/octet-stream;base64,"
                + base64.b64encode(raw).decode())
    man = asyncio.run(
        board_viewer.vsav_preview(_StubRequest(), {"vsav": data_url}))
    assert man["map"]["width"] == 1644
    assert len(man["pieces"]) > 100, len(man["pieces"])
    assert any(p["hex"] == "57-H9" for p in man["pieces"])
    if _boards_available():
        assert man["background"] is not None
        assert man["map"]["background_url"].startswith("/api/board-bg/")


def test_preview_endpoint_rejects_bad_input():
    for bad in ("not a data url",
                "data:application/octet-stream;base64,bm9wZQ==",  # not a zip
                ""):
        try:
            asyncio.run(
                board_viewer.vsav_preview(_StubRequest(), {"vsav": bad}))
        except HTTPException as e:
            assert e.status_code == 400, e
            continue
        raise AssertionError(f"payload {bad!r} should 400")


def test_preview_rate_limit_per_ip():
    """31st preview within the window from one IP gets a 429 with
    Retry-After; other IPs are unaffected; X-Forwarded-For is honored."""
    ip = "198.51.100.7"  # unique to this test; state is module-global
    for _ in range(board_viewer.PREVIEW_RATE_LIMIT):
        board_viewer._check_preview_rate(ip)
    try:
        board_viewer._check_preview_rate(ip)
    except HTTPException as e:
        assert e.status_code == 429, e
        assert "Retry-After" in (e.headers or {}), e.headers
    else:
        raise AssertionError("expected 429 after limit")
    board_viewer._check_preview_rate("198.51.100.8")  # other IP still fine
    # X-Forwarded-For wins over the socket peer (nginx proxying)
    req = _StubRequest(ip="127.0.0.1", forwarded="198.51.100.9, 10.0.0.1")
    assert board_viewer._client_ip(req) == "198.51.100.9"


def test_board_bg_endpoint_validates_key():
    for bad in ("nope", "0123456789abcdef", "../../x.png",
                "ABCDEF0123456789.png", "0123456789abcdef.gif"):
        try:
            board_viewer.get_board_background(bad)
        except HTTPException as e:
            assert e.status_code == 404, e
            continue
        raise AssertionError(f"{bad!r} should 404")
    if _boards_available():
        bg = board_render.build_background(_state())
        resp = board_viewer.get_board_background(
            f"{bg['cache_key']}.png")
        assert resp.media_type == "image/png"


def test_counter_art_endpoint_sanitizes():
    for bad in ("../x.svg", "a/../b.svg", "%2e%2e/x.svg", "x.jpg",
                ".hidden", "fi/..%2f..%2fsecret.svg"):
        try:
            board_viewer.get_counter_art(bad)
        except HTTPException as e:
            assert e.status_code == 404, e
            continue
        raise AssertionError(f"{bad!r} should 404")
    if board_render.find_vmod() is not None:
        resp = board_viewer.get_counter_art("fi/fi648S.svg")
        assert resp.media_type == "image/svg+xml"


def test_svg_art_is_self_contained():
    """SVGs served for <img> use must not reference external resources.

    Browsers refuse to fetch external files referenced inside an SVG loaded
    via <img> (SVG-as-image security model), so vmod SVGs — thin wrappers
    around raster faces plus @font-face urls — must be rewritten at
    extraction: rasters inlined as data URIs, font-face blocks dropped.
    """
    import re
    if board_render.find_vmod() is None:
        print("(skipped: no vmod)", end=" ")
        return
    path, media = board_render.extract_counter_art("fi/fi648S.svg")
    svg = path.read_text(encoding="utf-8")
    assert media == "image/svg+xml"
    assert "data:image/" in svg, "raster face should be inlined"
    external = [r for r in re.findall(r'(?:xlink:href|href)="([^"#]+)"', svg)
                if not r.startswith("data:")]
    assert not external, f"external refs survived: {external}"
    assert "@font-face" not in svg


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"  ✅ {t.__name__}")
        except Exception as e:
            failures += 1
            print(f"  ❌ {t.__name__}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
