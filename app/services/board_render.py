"""Server-side assembly of VASL board backgrounds + counter-art extraction.

Backgrounds
-----------
``build_background(state)`` composites the save's board configuration into
ONE cached PNG: each ``bdNN.gif`` (1800x645 geo board image, found inside the
``bdNN`` zip archive via ``board_terrain.find_board_archive`` — search order
``data/boards/`` then ``~/vasl/boards/``) is cropped with the save's
cropBounds (given in UNREVERSED image coords), rotated 180 degrees when the
board is reversed ("r57"), and pasted at its grid-slot position.

Coordinate convention (documented in vsav_service.render_manifest): the
manifest works in raw VASL MAP PIXELS, 400px edge margin included. The
background PNG covers only the union bounding box of the board boxes; its
top-left placement in map space is returned as ``x``/``y`` and the client
offsets the <img> accordingly. Piece coords are never shifted.

Out of scope (Phase 1): overlays and SSR board recolors are NOT drawn —
the background is the plain board image.

Counter art
-----------
``extract_counter_art(rel_path)`` pulls one counter image out of the local
VASL .vmod (a zip; ``images/<rel_path>``) into ``data/render_cache/art/``
and returns the cached file. Art paths are strictly allowlisted (see
``ART_PATH_RE``) — no dots outside a final .svg/.png/.gif extension, so
traversal is impossible — and the resolved cache path is verified to stay
inside the cache dir anyway. VASL image references sometimes omit the
extension ("ML/_white58"); those are resolved by trying .gif/.png/.svg.

Everything in ``data/render_cache/`` is derived, gitignored (MMP/VASL
copyrighted content is never committed), and safe to delete.
"""
import hashlib
import io
import json
import logging
import re
import zipfile
from functools import lru_cache
from pathlib import Path

from app.services.board_terrain import find_board_archive
from app.services.vsav_service import BOARD_W, BOARD_H, EDGE

CACHE_DIR = Path("data/render_cache")
ART_CACHE_DIR = CACHE_DIR / "art"

# Where to look for a VASL module (zip with an images/ tree of counter art).
VMOD_SEARCH_DIRS = [
    Path("data"),                 # repo-local copy (gitignored), if any
    Path.home() / "vasl",         # local VASL install
]

# Background canvas color for any gap not covered by a board image.
_CANVAS_RGB = (208, 203, 192)

CACHE_KEY_RE = re.compile(r"^[0-9a-f]{16}$")

# Counter-art allowlist: slash-separated segments of [A-Za-z0-9_+- ] (VASL
# image names can contain spaces, e.g. "mBMG malf.svg") with an optional
# final .svg/.png/.gif. No dots anywhere else => no "..", no hidden files,
# no absolute paths, no backslashes. Segments cannot start with a space.
_SEG = r"[A-Za-z0-9_+\-][A-Za-z0-9_+\- ]*"
ART_PATH_RE = re.compile(
    rf"^{_SEG}(?:/{_SEG})*(?:\.(svg|png|gif))?$")

ART_MEDIA_TYPES = {
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".gif": "image/gif",
}


class BoardRenderError(ValueError):
    """User-presentable board-render failure."""


# --------------------------------------------------------------------------
# Background
# --------------------------------------------------------------------------

def _board_boxes(state_boards):
    """Manifest-style placement for each board (same math as render_manifest)."""
    out = []
    for b in state_boards:
        crop = b["crop"]
        dw = crop["w"] if crop["w"] > 0 else BOARD_W
        dh = crop["h"] if crop["h"] > 0 else BOARD_H
        c, r = b["slot"]
        out.append(dict(base=b["base"], reversed=b["reversed"], crop=crop,
                        x=EDGE + c * dw, y=EDGE + r * dh, width=dw, height=dh))
    return out


def background_cache_key(state_boards) -> str:
    """Stable key for one board configuration (name+slot+crop+version)."""
    spec = sorted(
        [b["name"], b["slot"], [b["crop"][k] for k in ("x", "y", "w", "h")],
         b.get("version")]
        for b in state_boards
    )
    return hashlib.sha256(
        json.dumps(spec, sort_keys=True).encode()).hexdigest()[:16]


def build_background(state: dict):
    """Composite the save's boards into one cached PNG.

    Returns dict(url, cache_key, x, y, width, height, missing_boards) —
    x/y is the PNG's top-left in map-pixel space — or None when the save
    has no boards. Boards whose archive is missing locally are skipped
    (listed in missing_boards); the canvas shows a plain gap there.
    """
    boards = state.get("boards") or []
    if not boards:
        return None
    boxes = _board_boxes(boards)
    x_min = min(b["x"] for b in boxes)
    y_min = min(b["y"] for b in boxes)
    width = max(b["x"] + b["width"] for b in boxes) - x_min
    height = max(b["y"] + b["height"] for b in boxes) - y_min

    key = background_cache_key(boards)
    out_path = cached_background_path(key)
    missing = sorted({b["base"] for b in boxes
                      if find_board_archive(b["base"]) is None})
    meta = dict(url=f"/api/board-bg/{key}.png", cache_key=key,
                x=x_min, y=y_min, width=width, height=height,
                missing_boards=missing)
    if out_path.is_file():
        return meta

    from PIL import Image
    canvas = Image.new("RGB", (width, height), _CANVAS_RGB)
    for b in boxes:
        img = _load_board_image(b["base"])
        if img is None:
            continue
        crop = b["crop"]
        cx = crop["x"] if crop["x"] > 0 else 0
        cy = crop["y"] if crop["y"] > 0 else 0
        img = img.crop((cx, cy, cx + b["width"], cy + b["height"]))
        if b["reversed"]:
            img = img.rotate(180)
        canvas.paste(img, (b["x"] - x_min, b["y"] - y_min))

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tmp.png")
    canvas.save(tmp, format="PNG")
    tmp.replace(out_path)
    logging.info("board_render: built background %s (%dx%d, %d boards, "
                 "%d missing)", out_path.name, width, height,
                 len(boxes), len(missing))
    return meta


def _load_board_image(board_base: str):
    """The uncropped 1800x645 board image from the bdNN archive, or None."""
    archive = find_board_archive(board_base)
    if archive is None:
        return None
    from PIL import Image
    try:
        with zipfile.ZipFile(archive) as z:
            want = f"bd{board_base}.gif".lower()
            name = next((n for n in z.namelist() if n.lower() == want), None)
            if name is None:  # any top-level gif starting with the base name
                name = next((n for n in z.namelist()
                             if n.lower().startswith(f"bd{board_base}".lower())
                             and n.lower().endswith(".gif")), None)
            if name is None:
                logging.warning("board_render: no board gif in %s", archive)
                return None
            return Image.open(io.BytesIO(z.read(name))).convert("RGB")
    except Exception as e:
        logging.warning("board_render: failed to read %s: %s", archive, e)
        return None


def cached_background_path(cache_key: str) -> Path:
    """Cache file for a background key. Raises on a malformed key."""
    if not CACHE_KEY_RE.fullmatch(cache_key):
        raise BoardRenderError("Invalid background cache key")
    return CACHE_DIR / f"bg_{cache_key}.png"


# --------------------------------------------------------------------------
# Counter art
# --------------------------------------------------------------------------

@lru_cache(maxsize=1)
def find_vmod():
    """Locate a local VASL .vmod (newest version wins); None if absent."""
    candidates = []
    for d in VMOD_SEARCH_DIRS:
        candidates += sorted(Path(d).glob("vasl-*.vmod"))
    return candidates[-1] if candidates else None


def extract_counter_art(rel_path: str):
    """Counter image from the vmod -> cached file under ART_CACHE_DIR.

    Returns (Path, media_type). Raises BoardRenderError when the path fails
    the allowlist, no vmod is available, or the image doesn't exist.
    """
    if not ART_PATH_RE.fullmatch(rel_path or ""):
        raise BoardRenderError("Invalid counter art path")

    # already cached? (try the given name, then extension variants)
    stems = ([rel_path] if "." in rel_path
             else [rel_path + e for e in (".gif", ".png", ".svg")])
    for s in stems:
        cached = (ART_CACHE_DIR / s)
        if cached.is_file():
            return _checked(cached)

    vmod = find_vmod()
    if vmod is None:
        raise BoardRenderError("No VASL module available for counter art")
    try:
        with zipfile.ZipFile(vmod) as z:
            names = set(z.namelist())
            entry = next((f"images/{s}" for s in stems
                          if f"images/{s}" in names), None)
            if entry is None:
                raise BoardRenderError("Counter art not found")
            data = z.read(entry)
    except BoardRenderError:
        raise
    except Exception as e:
        raise BoardRenderError(f"Could not read VASL module: {e}")

    out = ART_CACHE_DIR / entry[len("images/"):]
    out.parent.mkdir(parents=True, exist_ok=True)
    if entry.endswith(".svg"):
        data = _inline_svg_resources(data, entry, names, z_path=vmod)
    out.write_bytes(data)
    return _checked(out)


_SVG_HREF_RE = re.compile(r'(xlink:href|href)="([^"#][^"]*?\.(?:png|gif|jpg|jpeg))"')
_SVG_FONTFACE_RE = re.compile(r'@font-face\s*\{[^}]*\}')

_RASTER_MIME = {".png": "image/png", ".gif": "image/gif",
                ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def _inline_svg_resources(data: bytes, entry: str, names: set, z_path) -> bytes:
    """Make a vmod SVG self-contained for <img> rendering.

    Browsers loading an SVG via <img> refuse to fetch ANY external resource
    referenced inside it (SVG-as-image security model). VASL counter SVGs are
    thin wrappers around raster faces (xlink:href="svg/foo.png") plus
    @font-face urls — without rewriting, counters render blank. Inline the
    rasters as base64 data URIs and drop the @font-face blocks (text falls
    back to the browser's sans-serif, visually close to the counter font).
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    base_dir = entry.rsplit("/", 1)[0]  # e.g. images/fi

    def resolve(rel: str):
        parts = (base_dir + "/" + rel).split("/")
        stack = []
        for p in parts:
            if p == "..":
                if stack:
                    stack.pop()
            elif p not in ("", "."):
                stack.append(p)
        return "/".join(stack)

    import base64
    with zipfile.ZipFile(z_path) as z:
        def repl(m):
            target = resolve(m.group(2))
            if target not in names:
                return m.group(0)
            ext = "." + target.rsplit(".", 1)[-1].lower()
            mime = _RASTER_MIME.get(ext)
            if not mime:
                return m.group(0)
            b64 = base64.b64encode(z.read(target)).decode()
            return f'{m.group(1)}="data:{mime};base64,{b64}"'

        text = _SVG_HREF_RE.sub(repl, text)
    text = _SVG_FONTFACE_RE.sub("", text)
    return text.encode("utf-8")


def _checked(path: Path):
    """Resolve-and-contain check + media type for a cached art file."""
    resolved = path.resolve()
    base = ART_CACHE_DIR.resolve()
    if base not in resolved.parents:
        raise BoardRenderError("Counter art path escapes cache dir")
    media = ART_MEDIA_TYPES.get(resolved.suffix.lower())
    if media is None:
        raise BoardRenderError("Unsupported counter art type")
    return resolved, media
