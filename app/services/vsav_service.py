"""Parse VASL .vsav save files into normalized board state.

Pipeline:
  1. Decode the .vsav container (zip with an obfuscated ``savedGame`` entry).
  2. Parse ``bd`` lines  -> board layout (name, grid slot, crop, SSR, overlays).
  3. Parse ``+/`` lines  -> AddPiece commands (pieces) and stack definitions.
  4. Resolve piece positions: a piece in a stack uses the STACK position
     (a piece's own saved position goes stale once it joins a stack).
  5. Convert map pixel (x, y) -> board + hex label using VASL geo geometry.
  6. Best-effort dynamic state (broken / concealed / HIP / skis / labels)
     from layer (emb2) and conceal/hide trait states.
  7. Emit a normalized dict (``parse_vsav``) and a compact text block for
     prompt injection (``render_board_state``).

Container decoding is reimplemented from pywargame's
``pywargame.vassal.save.SaveIO.readSave``
(https://gitlab.com/wargames_tex/pywargame) — the save is a zip whose
``savedGame`` entry starts with ``!VCSK``, followed by a 2-hex-char key;
every subsequent pair of hex chars decodes to ``(hi<<4 | lo) ^ key``, and the
decoded text splits into lines on ESC (0x1B). Reimplemented here to avoid
pulling in pywargame's heavy dependency tree.

Geometry (derived from VASL ASLBoard/ASLHexGrid and verified against the
save's own ``OldLocationName`` breadcrumbs — every save with moved pieces
self-validates; see the ``validation`` field of the parsed state):
  * Standard geo board image: 1800 x 645 px, 33 cols (A..GG) x 10 rows.
  * Hex width  dx = 56.25  (col i center at x = i * dx)
  * Hex height dy = 64.5
  * EVEN col index (A, C, E, ...): 10 hexes, centers y = 32.25 + 64.5*(r-1),
    r = 1..10.
  * ODD col index (B, D, F, ...): 11 hex centers y = 64.5*k, k = 0..10
    (k=0 and k=10 are the half hexes at the board edges).
  * Map edge buffer: 400 px on every side (ASLMap edgeWidth/edgeHeight).
  * ``bd`` line args after the board name are cropBounds x,y,w,h in
    UNREVERSED image coords (-1 = uncropped). An "r" board-name prefix means
    the (cropped) image is rotated 180 degrees for display, but hex labels
    are NOT renumbered — hex A1 of board r57 is the same physical hex as
    on board 57.
"""
import base64
import logging
import re
import uuid
import zipfile
from collections import defaultdict
from pathlib import Path

DX = 56.25          # hex column pitch (px)
DY = 64.5           # hex row pitch (px)
EDGE = 400          # ASLMap edge buffer (px)
BOARD_W = 1800      # uncropped geo board image (px)
BOARD_H = 645

LETTERS = [chr(ord('A') + i) for i in range(26)] + \
          [2 * chr(ord('A') + i) for i in range(7)]   # A..Z, AA..GG

UPLOADS_DIR = Path("data/uploads")
MAX_VSAV_BYTES = 2 * 1024 * 1024  # 2 MB cap — real .vsav saves are ~50-200 KB

_VCSK_HEADER = b"!VCSK"
_VK_ESC = chr(27)


class VsavError(ValueError):
    """Base error; message is safe to show to the user."""


class VsavValidationError(VsavError):
    """Upload rejected before parsing (bad data URL / too big / not a save)."""


class VsavParseError(VsavError):
    """The save decoded but could not be parsed into board state."""


# --------------------------------------------------------------------------
# Upload storage (mirrors app/services/image_storage.py)
# --------------------------------------------------------------------------

_DATA_URL_RE = re.compile(r"^data:[^,;]*;base64,(.+)$", re.IGNORECASE | re.DOTALL)


def save_vsav_data_url(data_url: str, conversation_id) -> str:
    """Decode + validate + write an uploaded .vsav to disk.

    Accepts a base64 data URL (any/no mime — browsers report .vsav as
    application/octet-stream or nothing). Returns the relative path under
    UPLOADS_DIR, e.g. ``"27/abcd.vsav"`` or ``"demo/abcd.vsav"``.
    """
    m = _DATA_URL_RE.match(data_url.strip())
    if not m:
        raise VsavValidationError("Invalid .vsav upload (expected a base64 data URL)")
    try:
        raw = base64.b64decode(m.group(1), validate=True)
    except Exception as e:
        raise VsavValidationError(f"Invalid base64 .vsav data: {e}")
    validate_vsav_bytes(raw)
    conv_dir = UPLOADS_DIR / str(conversation_id)
    conv_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{uuid.uuid4().hex}.vsav"
    (conv_dir / fname).write_bytes(raw)
    return f"{conversation_id}/{fname}"


def validate_vsav_bytes(raw: bytes) -> None:
    """Raise VsavValidationError unless raw looks like a real VASL save."""
    if len(raw) > MAX_VSAV_BYTES:
        raise VsavValidationError(
            f".vsav exceeds {MAX_VSAV_BYTES // (1024 * 1024)} MB limit"
        )
    import io
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            names = z.namelist()
            if "savedGame" not in names:
                raise VsavValidationError(
                    "Not a VASL save: zip has no 'savedGame' entry"
                )
            with z.open("savedGame") as f:
                head = f.read(len(_VCSK_HEADER))
            if head != _VCSK_HEADER:
                raise VsavValidationError("Not a VASL save: bad savedGame header")
    except VsavValidationError:
        raise
    except zipfile.BadZipFile:
        raise VsavValidationError("Not a VASL save: file is not a zip archive")
    except Exception as e:
        raise VsavValidationError(f"Could not read .vsav: {e}")


def resolve_vsav_path(rel_path: str) -> Path:
    """Map a stored relative path back to disk; raises if outside UPLOADS_DIR."""
    fpath = (UPLOADS_DIR / rel_path).resolve()
    base = UPLOADS_DIR.resolve()
    if base not in fpath.parents and fpath != base:
        raise VsavValidationError("Path escapes uploads directory")
    return fpath


# --------------------------------------------------------------------------
# Low-level save decoding (reimplemented from pywargame SaveIO.readSave)
# --------------------------------------------------------------------------

def _dec_hex(b: int) -> int:
    """Decode one obfuscation hex char (byte value) to its 4-bit value."""
    if 0x30 <= b <= 0x39:        # '0'-'9'
        return b - 0x30
    if 0x41 <= b <= 0x46:        # 'A'-'F'
        return b - 0x37
    if 0x61 <= b <= 0x66:        # 'a'-'f'
        return b - 0x57
    raise VsavParseError("Corrupt save: invalid obfuscation character")


def read_save_lines(path) -> list:
    """Decode the savedGame entry of a .vsav into its command lines."""
    try:
        with zipfile.ZipFile(path, "r") as z:
            if "savedGame" not in z.namelist():
                raise VsavValidationError(
                    "Not a VASL save: zip has no 'savedGame' entry"
                )
            with z.open("savedGame", "r") as f:
                head = f.read(len(_VCSK_HEADER))
                if head != _VCSK_HEADER:
                    raise VsavValidationError("Not a VASL save: bad savedGame header")
                key_pair = f.read(2)
                if len(key_pair) < 2:
                    raise VsavParseError("Corrupt save: truncated key")
                key = (_dec_hex(key_pair[0]) << 4) | _dec_hex(key_pair[1])
                raw = f.read()
    except VsavError:
        raise
    except zipfile.BadZipFile:
        raise VsavValidationError("Not a VASL save: file is not a zip archive")
    except FileNotFoundError:
        raise VsavValidationError("Save file not found")
    content = "".join(
        chr(((_dec_hex(hi) << 4 | _dec_hex(lo)) ^ key) & 0xFF)
        for hi, lo in zip(raw[::2], raw[1::2])
    )
    return content.split(_VK_ESC)


def split_unescaped(s: str, sep: str = "/", maxsplit: int = 0) -> list:
    """Split on sep not preceded by a backslash."""
    return re.split(r"(?<!\\)" + re.escape(sep), s, maxsplit=maxsplit)


def unescape(s: str) -> str:
    return s.replace("\\/", "/").replace("\\;", ";").replace("\\,", ",")


# --------------------------------------------------------------------------
# Boards
# --------------------------------------------------------------------------

def parse_boards(lines: list) -> list:
    """Parse 'bd' lines. Returns list of board dicts (deduped by grid slot)."""
    boards = {}
    for ln in lines:
        if not ln.startswith("bd\t"):
            continue
        # A save can contain several concatenated bd commands on one line
        # ("...GrainToBrushbd\t0\t1\tr57..."); split them apart.
        for chunk in re.split(r"(?:^|(?<=.))bd\t", ln):
            if not chunk:
                continue
            f = chunk.split("\t")
            try:
                col, row, name = int(f[0]), int(f[1]), f[2]
                crop = [int(f[3]), int(f[4]), int(f[5]), int(f[6])]
            except (ValueError, IndexError):
                logging.warning("vsav: skipping malformed bd chunk: %r", chunk[:80])
                continue
            rest = f[7:]
            ssr, ovr, ver = [], [], None
            i = 0
            while i < len(rest):
                tok = rest[i]
                if tok == "VER":
                    ver = rest[i + 1] if i + 1 < len(rest) else None
                    i += 2
                elif tok == "OVR":
                    ovr.append(" ".join(t for t in rest[i + 1:i + 4] if t))
                    i += 4
                elif tok == "SSR":
                    ssr = [t for t in rest[i + 1:] if t]
                    break
                else:
                    i += 1
            reversed_ = name.startswith("r")
            base = name[1:] if reversed_ else name
            cx, cy, cw, ch = crop
            disp_w = cw if cw > 0 else BOARD_W
            disp_h = ch if ch > 0 else BOARD_H
            boards[(col, row)] = dict(
                name=name, base=base, reversed=reversed_, slot=[col, row],
                version=ver, crop=dict(x=cx, y=cy, w=cw, h=ch),
                display_w=disp_w, display_h=disp_h,
                ssr_transforms=ssr, overlays=ovr,
            )
    out = list(boards.values())
    # Map-pixel bounding boxes (uniform display size assumed per row/col)
    for b in out:
        c, r = b["slot"]
        b["box"] = dict(x0=EDGE + c * b["display_w"],
                        y0=EDGE + r * b["display_h"],
                        x1=EDGE + (c + 1) * b["display_w"],
                        y1=EDGE + (r + 1) * b["display_h"])
    return out


# --------------------------------------------------------------------------
# Pixel -> hex
# --------------------------------------------------------------------------

def map_xy_to_hex(boards, x, y):
    """Map-pixel (x, y) -> (board, hexlabel) or (None, 'offboard')."""
    for b in boards:
        bx = b["box"]
        if bx["x0"] <= x < bx["x1"] and bx["y0"] <= y <= bx["y1"]:
            lx, ly = x - bx["x0"], y - bx["y0"]
            crop = b["crop"]
            cw = crop["w"] if crop["w"] > 0 else BOARD_W
            ch = crop["h"] if crop["h"] > 0 else BOARD_H
            if b["reversed"]:
                xo = crop["x"] + cw - lx
                yo = crop["y"] + ch - ly
            else:
                xo = crop["x"] + lx
                yo = crop["y"] + ly
            return b, board_xy_to_hex(xo, yo)
    return None, "offboard"


def board_xy_to_hex(xo, yo) -> str:
    """Unreversed-board-image pixel -> hex label like 'H10'."""
    i = int(round(xo / DX))
    i = max(0, min(len(LETTERS) - 1, i))
    if i % 2 == 1:                      # B, D, F ... : rows 0..10
        r = int(round(yo / DY))
        r = max(0, min(10, r))
    else:                               # A, C, E ... : rows 1..10
        r = int(round((yo - DY / 2) / DY)) + 1
        r = max(1, min(10, r))
    return f"{LETTERS[i]}{r}"


# --------------------------------------------------------------------------
# Pieces
# --------------------------------------------------------------------------

# basic trait: piece;<gpid?>;<?>;<image>;<name>. The image field carries the
# counter art for single-art pieces (SW, leaders: 'fi\\/firLMG.svg'); squads
# leave it blank and get their art from the unit-identity Layer instead (see
# piece_dynamic_state).
PIECE_NAME_RE = re.compile(r"piece;[^;]*;[^;]*;([^;]*);(.*)$")

# Nationality prefixes used in VASL image paths (fi/fi648S.svg, ru/...).
# This is what determines a unit's SIDE — NOT the PROP;Owner trait, which
# only records the last player who touched the piece.
NATIONALITY_RE = re.compile(r"(?:^|[;,\\])(fi|ru|ge|am|br|it|ja|ax|al|fr|ch|pa)\\/")
SIDE_NAMES = {"fi": "Finnish", "ru": "Russian", "ge": "German",
              "am": "American", "br": "British", "it": "Italian",
              "ja": "Japanese", "ax": "AxisMinor", "al": "AlliedMinor",
              "fr": "French", "ch": "Chinese", "pa": "Partisan"}

_CRUMB_KEYS = ("OldLocationName", "OldX", "OldY", "OldBoard", "OldMap",
               "UniqueID", "ClickedX", "ClickedY")


def parse_add_piece(line: str):
    """Parse one '+/id/type/state' AddPiece command -> piece or stack dict."""
    parts = split_unescaped(line, "/", maxsplit=3)
    if len(parts) < 4:
        return None
    _cmd, pid, typ, sta = parts

    if typ.startswith("stack"):
        # state: MAP;x;y;id1;id2;...;@@layer
        body = sta.split(";@@")[0]
        f = body.split(";")
        return dict(kind="stack", id=pid, map=f[0],
                    x=int(f[1]), y=int(f[2]), members=f[3:])

    m = PIECE_NAME_RE.search(typ)
    name = unescape(m.group(2)) if m else "<unknown>"
    base_art = unescape(m.group(1)).strip() if m else ""

    # trait/state pairing: tab-separated, aligned from the END
    # (the basic 'piece' trait and its 'Map;x;y;...' state are always last)
    ttraits = typ.split("\t")
    tstates = sta.split("\t")
    n = min(len(ttraits), len(tstates))
    pairs = list(zip([t.strip("\\") for t in ttraits[-n:]],
                     [s.strip("\\") for s in tstates[-n:]]))

    # basic piece state: MAP;x;y;... plus key;value breadcrumbs
    basic = pairs[-1][1] if pairs else ""
    bf = basic.split(";")
    pmap, px, py = None, None, None
    if len(bf) >= 3:
        pmap = bf[0]
        try:
            px, py = int(bf[1]), int(bf[2])
        except ValueError:
            pass
    crumbs = {}
    for j in range(3, len(bf) - 1):
        if bf[j] in _CRUMB_KEYS:
            crumbs[bf[j]] = bf[j + 1]

    nat = None
    mnat = NATIONALITY_RE.search(typ)
    if mnat:
        nat = mnat.group(1)

    return dict(kind="piece", id=pid, name=name, base_art=base_art or None,
                map=pmap, x=px, y=py,
                crumbs=crumbs, nationality=nat, pairs=pairs, type=typ)


def piece_dynamic_state(p):
    """Best-effort (flags, owner, effective_name, effective_art) from
    trait/state pairs.

    Note on concealment/HIP: the conceal ('conceal') and HIP ('hide') trait
    states store VASSAL player IDs, which generally differ from the profile
    names seen in PROP;Owner. Both get mapped to sides by nationality voting
    in parse_vsav; for visibility masking, the piece's own nationality is
    what matters (a concealed Russian unit is hidden from the Finn).
    """
    flags = {}
    owner = None
    effective_name = None
    effective_art = None
    for t, s in p["pairs"]:
        tf = split_unescaped(t, ";")
        tid = tf[0]
        if tid == "PROP" and len(tf) > 1 and tf[1] == "Owner" and s:
            owner = s.split(";")[0] or owner
        elif tid == "hide":
            # Hideable (HIP): state = hiding player id, or 'null'/''
            v = s.strip()
            if v and v not in ("null", "null;"):
                flags["hip_by"] = unescape(v)
        elif tid == "conceal":
            # Obscurable (concealment): state = concealing player id or null
            v = s.split(";")[0].strip()
            if v and v != "null":
                flags["concealed_by"] = unescape(v)
        elif tid == "emb2":
            # Layer: tf[16] = comma-list of image names, tf[17] = level names
            try:
                images = tf[16]
                lnames = tf[17]
            except IndexError:
                continue
            try:
                val = int(s.split(";")[0])
            except (ValueError, IndexError):
                continue
            lname_list = [unescape(v) for v in lnames.split(",")]
            if "broken" in lnames or "broken" in images:
                if val > 0:
                    flags["broken"] = True
            elif "Skis" in t and val > 1:
                flags["skis"] = True
            elif "Bicycle" in t and val > 1:
                flags["bicycle"] = True
            # unit-identity layer: level names look like unit names and the
            # base piece name is one of them -> active level = display name,
            # and the level's image is the counter art actually shown
            # (e.g. 'fi\\/fi648S.svg,fi\\/fi538S.svg' / '6-4-8 1sq,5-3-8 Gsq')
            elif p["name"] in lname_list and 0 < val <= len(lname_list):
                effective_name = lname_list[val - 1]
                imgs = [unescape(v).strip() for v in images.split(",")]
                if val <= len(imgs) and imgs[val - 1]:
                    effective_art = imgs[val - 1]
        elif tid == "label":
            v = s.strip()
            if v and v != "null":
                flags["label"] = unescape(v)
    return flags, owner, effective_name, effective_art


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------

MARKER_NAMES = {"DM", "Pin", "CC", "Melee", "Prep Fire", "First Fire",
                "Final Fire", "CX", "Residual FP", "Fire Lane", "Acquired",
                "Labor", "TI", "Wire", "Foxhole", "Trench", "Roadblock",
                "Smoke", "WP", "Blaze", "Rubble", "Motion", "Immob",
                "Shock", "UK", "Stun", "Buttoned Up", "BU", "Abandoned",
                "Bog", "Berserk", "Fanatic", "Encircled",
                "Skis", "?", "2-hex"}

# Entrenchment counters: units BELOW one in a stack are IN it (B27) and get
# a per-unit `entrenched_by` field instead of a `markers` entry.
ENTRENCHMENT_NAMES = ("Foxhole", "Trench")


def is_marker(p) -> bool:
    name = p["effective_name"]
    if name in MARKER_NAMES or p["name"] in MARKER_NAMES:
        return True
    n = name.lower()
    if any(k in n for k in ("concealment", "turn", "attitude", "acq", "radius")):
        return True
    # non-stackable play aids (2-hex radius circles, info chits, ...)
    return bool(re.search(r"(?:^|\t)immob;", p["type"]))


def _parse_raw(path):
    """Decode + parse a .vsav into a raw (non-normalized) state dict."""
    lines = read_save_lines(path)
    boards = parse_boards(lines)

    info = next((ln for ln in lines if ln.startswith("INFO\t")), None)
    saver_side = info.split("\t")[1] if info else None

    pieces, stacks = {}, []
    for ln in lines:
        if not ln.startswith("+/"):
            continue
        try:
            rec = parse_add_piece(ln)
        except Exception as e:
            logging.warning("vsav: failed to parse piece line: %s", e)
            continue
        if rec is None:
            continue
        if rec["kind"] == "stack":
            stacks.append(rec)
        else:
            pieces[rec["id"]] = rec

    # resolve positions: stack position wins (a piece's own saved coords go
    # stale once it joins a stack). Member order is meaningful: VASSAL's
    # Stack.getState() serializes contents[0..n-1] BOTTOM -> TOP, so
    # stack_pos 0 is the bottom counter and the last member is the top.
    # (Verified against this fixture's ground truths: DM / "?" counters —
    # which sit ON TOP of the units they affect — are always last.)
    in_stack = {}
    for st in stacks:
        for pos, mid in enumerate(st["members"]):
            in_stack[mid] = (st, pos)
    for pid, p in pieces.items():
        if pid in in_stack:
            st, pos = in_stack[pid]
            p["map"], p["x"], p["y"] = st["map"], st["x"], st["y"]
            p["stack_id"] = st["id"]
            p["stack_pos"] = pos

    # player-name/player-id -> nationality voting, to map names to sides.
    # PROP;Owner values are profile names; conceal/hide states are VASSAL
    # player IDs — both vote with the piece's nationality.
    player_nat = defaultdict(lambda: defaultdict(int))

    out_pieces = []
    for p in pieces.values():
        flags, owner, eff, eff_art = piece_dynamic_state(p)
        p["flags"], p["owner"] = flags, owner
        p["effective_name"] = eff or p["name"]
        # counter art shown on the map: identity-layer image if the piece has
        # one (squads), else the basic-trait image (SW, leaders, markers)
        p["art"] = eff_art or p["base_art"]
        if p["nationality"]:
            for voter in (owner, flags.get("concealed_by"), flags.get("hip_by")):
                if voter:
                    player_nat[voter][p["nationality"]] += 1
        out_pieces.append(p)

    player_side = {o: max(d, key=d.get) for o, d in player_nat.items()}

    # hex assignment for on-map pieces
    hexes = defaultdict(list)
    offmap = []
    for p in out_pieces:
        if p["map"] != "Main Map" or p["x"] is None:
            continue
        b, hx = map_xy_to_hex(boards, p["x"], p["y"])
        if b is None:
            p["hex"] = None
            offmap.append(p)
        else:
            p["hex"] = f"{b['base']}-{hx}"
            hexes[p["hex"]].append(p)

    return dict(boards=boards, pieces=out_pieces, stacks=stacks,
                hexes=hexes, offmap=offmap, saver_side=saver_side,
                player_side=player_side)


# --------------------------------------------------------------------------
# Validation: VASL's own OldLocationName breadcrumbs as ground truth
# --------------------------------------------------------------------------

LOC_RE = re.compile(r"^(\d+[a-z]*)([A-Z]+\d+)$")


def _validate(raw_state):
    """Check pixel->hex math against (OldX, OldY) -> OldLocationName pairs.

    Built-in health check: every save with moved pieces self-validates via
    the breadcrumbs VASSAL writes into each piece's basic state.
    """
    boards = raw_state["boards"]
    ok, bad, n = 0, [], 0
    for p in raw_state["pieces"]:
        c = p["crumbs"]
        if not all(k in c for k in ("OldX", "OldY", "OldLocationName")):
            continue
        m = LOC_RE.match(c["OldLocationName"])
        if not m:
            continue
        n += 1
        try:
            b, hx = map_xy_to_hex(boards, int(c["OldX"]), int(c["OldY"]))
        except ValueError:
            bad.append(dict(piece=p["name"], expected=c["OldLocationName"],
                            got="unparseable"))
            continue
        got = f'{b["base"]}{hx}' if b else "offboard"
        if got == c["OldLocationName"]:
            ok += 1
        else:
            bad.append(dict(piece=p["name"], x=c["OldX"], y=c["OldY"],
                            expected=c["OldLocationName"], got=got))
    return ok, n, bad


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def parse_vsav(path) -> dict:
    """Parse a .vsav save into normalized board state.

    `path` may be absolute, or relative to data/uploads (the form returned
    by save_vsav_data_url).

    Returns a dict with keys: source, saver_side, player_sides, boards,
    ssr_transforms, overlays, hexes ({"<board>-<hex>": {units, markers}}),
    offboard, validation ({n_breadcrumbs_checked, n_matched, mismatches}).

    Marker semantics follow VASL stack order (bottom -> top): each unit's
    `markers` list holds only the markers stacked above it; `entrenched_by`
    is set to "Foxhole"/"Trench" when such a counter sits above the unit;
    the hex-level `markers` list holds only unattributed markers (bottom of
    a stack, or loose in the hex).

    Raises VsavError (a ValueError with a user-presentable message) on any
    failure.
    """
    p = Path(path)
    if not p.is_absolute() and not p.is_file():
        p = UPLOADS_DIR / path
    if not p.is_file():
        raise VsavValidationError("Save file not found")
    if p.stat().st_size > MAX_VSAV_BYTES:
        raise VsavValidationError(
            f".vsav exceeds {MAX_VSAV_BYTES // (1024 * 1024)} MB limit"
        )

    try:
        raw_state = _parse_raw(p)
    except VsavError:
        raise
    except Exception as e:
        logging.warning("vsav: parse failed for %s: %s", p, e, exc_info=True)
        raise VsavParseError(f"Could not parse VASL save: {e}")

    ok, n, bad = _validate(raw_state)
    if bad:
        logging.warning("vsav: %d/%d breadcrumb mismatches: %s",
                        len(bad), n, bad[:5])

    boards = raw_state["boards"]
    hexes_out = {}
    for hx, plist in sorted(raw_state["hexes"].items()):
        # group stack members together, ordered bottom -> top within a stack
        plist = sorted(plist, key=lambda q: (str(q.get("stack_id")),
                                             q.get("stack_pos", 0)))
        units, markers = [], []
        for p_ in plist:
            entry = dict(name=p_["effective_name"])
            if p_["name"] != p_["effective_name"]:
                entry["counter"] = p_["name"]
            if p_.get("art"):
                # counter art path (e.g. "fi/fi648S.svg") — identifies the
                # exact counter type for capability lookups (A7.36 underscore)
                entry["art"] = p_["art"]
            if p_["nationality"]:
                entry["side"] = SIDE_NAMES.get(p_["nationality"], p_["nationality"])
            if p_["owner"]:
                entry["owner"] = p_["owner"]
            entry.update(p_["flags"])
            entry["_stack"] = p_.get("stack_id")
            entry["_pos"] = p_.get("stack_pos", 0)
            if is_marker(p_):
                markers.append(entry)
            else:
                units.append(entry)
        # Stack-order marker semantics: a marker applies to the units BELOW
        # it in its VASL stack (member lists serialize bottom -> top).
        # Foxhole/Trench counters become a per-unit `entrenched_by` field
        # (nearest entrenchment counter above the unit, in the rare case of
        # several); every other applicable marker goes in the unit's
        # `markers` list. Markers with no unit beneath them in their stack —
        # including loose markers not in any stack — apply to no unit and
        # stay on the hex-level markers list.
        for u in units:
            applies = [m for m in markers
                       if u["_stack"] is not None
                       and m["_stack"] == u["_stack"]
                       and m["_pos"] > u["_pos"]]
            ent = [m for m in applies if m["name"] in ENTRENCHMENT_NAMES]
            if ent:
                u["entrenched_by"] = min(ent, key=lambda m: m["_pos"])["name"]
            mk = [m["name"] for m in applies
                  if m["name"] not in ENTRENCHMENT_NAMES]
            if mk:
                u["markers"] = mk
            for m in applies:
                m["_applied"] = True
        hex_markers = [m["name"] for m in markers if not m.get("_applied")]
        for e in units + markers:
            e.pop("_stack", None)
            e.pop("_pos", None)
            e.pop("_applied", None)
        hexes_out[hx] = dict(units=units, markers=hex_markers)

    state = dict(
        source="vsav",
        saver_side=raw_state["saver_side"],
        player_sides={o: SIDE_NAMES.get(s, s)
                      for o, s in raw_state["player_side"].items()},
        boards=[dict(name=b["name"], base=b["base"], slot=b["slot"],
                     version=b["version"], reversed=b["reversed"],
                     crop=b["crop"], ssr_transforms=b["ssr_transforms"])
                for b in boards],
        ssr_transforms=sorted({t for b in boards for t in b["ssr_transforms"]}),
        overlays=[dict(board=b["name"], spec=o)
                  for b in boards for o in b["overlays"]],
        hexes=hexes_out,
        # pieces on the Main Map but outside any board (margin area, e.g.
        # staged reinforcements); positions are raw map pixels
        offboard=[dict(name=p_["effective_name"], x=p_["x"], y=p_["y"],
                       side=SIDE_NAMES.get(p_["nationality"], p_["nationality"]),
                       owner=p_["owner"], **p_["flags"])
                  for p_ in raw_state["offmap"] if not is_marker(p_)],
        validation=dict(n_breadcrumbs_checked=n, n_matched=ok,
                        mismatches=bad[:20]),
    )

    # Per-hex terrain from local VASL board archives (best-effort: boards
    # without archives are noted and their hexes left un-annotated).
    try:
        from app.services import board_terrain
        board_terrain.annotate_state_with_terrain(state)
    except Exception as e:
        logging.warning("vsav: terrain annotation failed: %s", e, exc_info=True)

    return state


_FLAG_LABELS = (("broken", "BROKEN"), ("skis", "skis"), ("bicycle", "bicycle"))


def _unit_braces(u) -> str:
    """' {Foxhole: in, DM, ...}' — the markers that apply to THIS unit
    (counters above it in its VASL stack), or '' if none."""
    items = []
    if u.get("entrenched_by"):
        items.append(f"{u['entrenched_by']}: in")
    items += u.get("markers") or []
    return " {" + ", ".join(items) + "}" if items else ""


def _render_unit(u, perspective_side=None):
    """One unit -> compact text. Returns None if the unit must be hidden.

    When perspective_side is set, render the board the way that player sees
    it on screen: the OTHER side's HIP units are invisible (dropped) and its
    concealed units show only a "?" counter (side is visible — concealment
    counters are nationality-colored — but identity and state are not).
    """
    side = u.get("side")
    is_enemy = (perspective_side is not None and side is not None
                and side != perspective_side)
    if is_enemy and "hip_by" in u:
        return None  # HIP: not on the opponent's screen at all
    if is_enemy and "concealed_by" in u:
        return f"{side} ? (concealed — identity unknown)" + _unit_braces(u)

    s = f"{side} {u['name']}" if side else u["name"]
    if u.get("counter"):
        s += f" (counter: {u['counter']})"
    flags = [label for key, label in _FLAG_LABELS if u.get(key)]
    if "concealed_by" in u:
        flags.append("concealed")
    if "hip_by" in u:
        flags.append("HIP")
    if u.get("label"):
        flags.append(f"label: {u['label']}")
    if flags:
        s += " [" + ", ".join(flags) + "]"
    return s + _unit_braces(u)


def render_board_state(state: dict, perspective_side: str = None) -> str:
    """Render parsed .vsav state as a compact text block for prompt injection.

    perspective_side: optional side name ("Finnish", "Russian", ...). When
    set, the OTHER side's concealed units are masked to "?" and its HIP
    units are dropped — i.e., what that player legitimately sees on screen.
    When None, everything in the save is shown (full-information view).
    """
    lines = ["=== BOARD STATE (parsed from attached VASL .vsav save) ==="]

    binfo = []
    for b in state.get("boards", []):
        desc = f"board {b['name']}"
        if b.get("reversed"):
            desc += " (image rotated 180°; hex labels unchanged)"
        binfo.append(desc)
    if binfo:
        lines.append("Boards: " + "; ".join(binfo))
    if state.get("ssr_transforms"):
        lines.append("SSR/board transforms in effect: "
                     + ", ".join(state["ssr_transforms"]))
    if state.get("overlays"):
        lines.append("Overlays: " + "; ".join(
            f"{o['board']}: {o['spec']}" for o in state["overlays"]))
    if state.get("player_sides"):
        lines.append("Players: " + "; ".join(
            f"{p} = {s}" for p, s in sorted(state["player_sides"].items())))
    val = state.get("validation") or {}
    n = val.get("n_breadcrumbs_checked", 0)
    ok = val.get("n_matched", 0)
    if n:
        lines.append(f"Parser self-check: {ok}/{n} position breadcrumbs matched.")
    if perspective_side:
        lines.append(
            f"Perspective: {perspective_side} player's view — enemy concealed "
            "units shown as '?', enemy HIP units omitted.")

    tinfo = state.get("terrain_info") or {}
    has_terrain = any(v.get("terrain") for v in state.get("hexes", {}).values())
    if has_terrain:
        note = ("Hex terrain in [..] after each hex ID is read from VASL "
                "board data (LOSData grid), SSR terrain transforms applied.")
        if tinfo.get("has_overlays"):
            note += (" Overlays are NOT applied to terrain; overlay hexes "
                     "may differ.")
        lines.append(note)
    if tinfo.get("missing_boards"):
        lines.append("Terrain unavailable for board(s) "
                     + ", ".join(tinfo["missing_boards"])
                     + " (no local board data).")

    lines.append("")
    lines.append("Units by hex (<board>-<hex> [terrain]; unit flags in [..]; "
                 "{..} lists ONLY the markers that apply to that unit, i.e. "
                 "counters stacked ABOVE it in VASL — 'Foxhole: in' / "
                 "'Trench: in' means the unit is beneath that counter and IN "
                 "the entrenchment; a unit in the same hex without the "
                 "annotation is NOT. 'hex markers' after | sit at the bottom "
                 "of a stack or alone and apply to no listed unit):")
    for hx, v in sorted(state.get("hexes", {}).items()):
        rendered = [r for u in v["units"]
                    if (r := _render_unit(u, perspective_side)) is not None]
        if not rendered and not v["markers"]:
            continue
        terr = v.get("terrain")
        hx_id = f"{hx} [{terr['terrain']}]" if terr else hx
        line = f"  {hx_id}: " + ("; ".join(rendered) if rendered else "(empty)")
        if v["markers"]:
            line += " | hex markers: " + ", ".join(v["markers"])
        lines.append(line)

    off = [r for u in state.get("offboard", [])
           if (r := _render_unit(u, perspective_side)) is not None]
    if off:
        lines.append("")
        lines.append("Off-board (staged in the map margin, not in any hex): "
                     + "; ".join(off))

    lines.append("=== END BOARD STATE ===")
    return "\n".join(lines)
